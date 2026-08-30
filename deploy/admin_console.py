#!/usr/bin/env python3
"""Local OWNER operations console.

The console binds only to loopback, authenticates to the private Control Plane
with the existing DPAPI-protected Admin token, and serves only non-secret
metadata to the browser. Copy and rotate actions read/write protected delivery
bundles locally; plaintext is never returned in an HTTP response or printed.
"""

from __future__ import annotations

import argparse
import http.cookies
import json
import secrets
import subprocess
import sys
import urllib.parse
from pathlib import Path
from wsgiref.simple_server import WSGIRequestHandler, make_server

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deploy import issue_user_tokens as operator  # noqa: E402


DEFAULT_CONSOLE_HOST = "127.0.0.1"
DEFAULT_CONSOLE_PORT = 47831
SESSION_COOKIE = "sparklink_owner_console"


class ConsoleError(RuntimeError):
    """Safe local-console failure code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class QuietHandler(WSGIRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


def _json_body(environ: dict) -> dict:
    try:
        length = min(int(environ.get("CONTENT_LENGTH") or 0), 100_000)
        value = json.loads(environ["wsgi.input"].read(length) if length else b"{}")
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ConsoleError("invalid_request") from exc
    if not isinstance(value, dict):
        raise ConsoleError("invalid_request")
    return value


def _cookie_value(environ: dict, name: str) -> str | None:
    cookies = http.cookies.SimpleCookie()
    try:
        cookies.load(environ.get("HTTP_COOKIE", ""))
    except http.cookies.CookieError:
        return None
    morsel = cookies.get(name)
    return morsel.value if morsel else None


class ConsoleApp:
    def __init__(self, endpoint: str, admin_token: str, delivery_dir: Path,
                 portal_url: str, public_subscription_base_url: str):
        self.endpoint = endpoint.rstrip("/")
        self.admin_token = admin_token
        self.delivery_dir = delivery_dir
        self.portal_url = operator.validate_url(portal_url)
        self.public_subscription_base_url = operator.validate_url(public_subscription_base_url)
        self.session_token = secrets.token_urlsafe(32)

    @staticmethod
    def _reply(start_response, status: int, body: bytes,
               content_type: str = "application/json; charset=utf-8", extra: list[tuple[str, str]] | None = None):
        headers = [
            ("Content-Type", content_type),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store, private"),
            ("X-Content-Type-Options", "nosniff"),
            ("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'"),
        ]
        headers.extend(extra or [])
        start_response(f"{status} {'OK' if status < 400 else 'Error'}", headers)
        return [body]

    def _authorized(self, environ: dict) -> bool:
        return secrets.compare_digest(_cookie_value(environ, SESSION_COOKIE) or "", self.session_token)

    def _safe_users(self) -> list[dict]:
        return operator.read_admin_users(self.endpoint, self.admin_token)

    def _resolve_user(self, requested: object, users: list[dict] | None = None) -> dict:
        value = str(requested or "")
        users = users if users is not None else self._safe_users()
        matches = [user for user in users if user["user_id"] == value or user["display_name"] == value]
        if len(matches) != 1:
            raise ConsoleError("user_not_found")
        return matches[0]

    def _bundle(self, user: dict) -> tuple[Path, dict]:
        path = operator.user_bundle_path(user["display_name"], self.delivery_dir)
        try:
            value = operator.read_bundle(path)
        except operator.OperatorError as exc:
            raise ConsoleError(exc.code) from exc
        if value.get("user_id") != user["user_id"]:
            raise ConsoleError("delivery_bundle_user_mismatch")
        return path, value

    def _record(self, user_id: str, subject_kind: str, state: str, detail: str) -> None:
        try:
            operator.admin_json(
                self.endpoint, self.admin_token, "/api/admin/migration-event", "POST",
                {"user_id": user_id, "subject_kind": subject_kind, "subject_ref": "current",
                 "state": state, "source": "owner-console", "detail": detail},
            )
        except operator.OperatorError as exc:
            raise ConsoleError(exc.code) from exc

    def state(self) -> dict:
        users = self._safe_users()
        overview = operator.admin_json(self.endpoint, self.admin_token, "/api/admin/overview", "GET")
        overview_users = overview.get("users")
        if isinstance(overview_users, list):
            details_by_id = {
                item.get("user_id"): item for item in overview_users
                if isinstance(item, dict) and isinstance(item.get("user_id"), str)
            }
            # The Admin users endpoint is the validated identity source. The
            # overview contributes only safe, read-oriented usage/access
            # metadata so the OWNER table can answer per-pool/per-node status
            # without exposing any credential-bearing fields.
            users = [
                {
                    **user,
                    **{
                        key: details_by_id[user["user_id"]][key]
                        for key in (
                            "usage_by_pool_bytes", "usage_by_node", "usage_bytes",
                            "effective_access", "migration_latest",
                        )
                        if user["user_id"] in details_by_id and key in details_by_id[user["user_id"]]
                    },
                }
                for user in users
            ]
        delivery = {}
        for user in users:
            path = operator.user_bundle_path(user["display_name"], self.delivery_dir)
            present = path.is_file()
            metadata = {}
            if present:
                try:
                    value = operator.read_bundle(path)
                    if value.get("user_id") == user["user_id"]:
                        metadata = {
                            "bundle_present": True,
                            "portal_available": bool(value.get("portal_access_token") or value.get("portal_token")),
                            "subscription_url_available": isinstance(value.get("subscription_url"), str),
                            "issue_rotation_timestamp": value.get("issue_rotation_timestamp"),
                        }
                except operator.OperatorError:
                    pass
            delivery[user["user_id"]] = metadata or {"bundle_present": False}
        return {"overview": overview, "users": users, "delivery": delivery}

    def detail(self, requested: object) -> dict:
        user = self._resolve_user(requested)
        detail = operator.admin_json(
            self.endpoint, self.admin_token, "/api/admin/users/" + urllib.parse.quote(user["user_id"], safe=""), "GET"
        )
        path = operator.user_bundle_path(user["display_name"], self.delivery_dir)
        delivery = {"bundle_present": False, "portal_available": False, "subscription_url_available": False}
        if path.is_file():
            try:
                value = operator.read_bundle(path)
                if value.get("user_id") == user["user_id"]:
                    delivery = {
                        "bundle_present": True,
                        "portal_available": bool(value.get("portal_access_token") or value.get("portal_token")),
                        "subscription_url_available": isinstance(value.get("subscription_url"), str),
                        "issue_rotation_timestamp": value.get("issue_rotation_timestamp"),
                        "migration_status": value.get("migration_status"),
                    }
            except operator.OperatorError:
                pass
        detail["local_delivery"] = delivery
        return detail

    def copy_secret(self, requested: object, kind: object) -> dict:
        if kind not in {"portal", "subscription"}:
            raise ConsoleError("invalid_copy_kind")
        user = self._resolve_user(requested)
        _path, value = self._bundle(user)
        secret = value.get("portal_access_token") or value.get("portal_token") if kind == "portal" else value.get("subscription_url")
        if not isinstance(secret, str) or not secret:
            raise ConsoleError("secret_not_available")
        if kind == "subscription":
            try:
                operator.subscription_token_from_url(secret)
            except operator.OperatorError as exc:
                raise ConsoleError(exc.code) from exc
        if operator.os.name != "nt":
            raise ConsoleError("clipboard_requires_windows")
        result = subprocess.run(
            ["clip.exe"], input=secret.encode("utf-8"), stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, check=False,
        )
        if result.returncode != 0:
            raise ConsoleError("clipboard_copy_failed")
        self._record(
            user["user_id"], "portal_token" if kind == "portal" else "subscription_token",
            "delivered", "explicit local clipboard copy",
        )
        return {"ok": True, "user_id": user["user_id"], "kind": kind,
                "clipboard": "updated", "secret_not_printed": True}

    def rotate(self, requested: object, kind: object, confirmation: object) -> dict:
        if kind not in {"portal", "subscription"}:
            raise ConsoleError("invalid_rotate_kind")
        user = self._resolve_user(requested)
        expected = f"ROTATE {user['user_id']} {kind}"
        if confirmation != expected:
            raise ConsoleError("rotation_confirmation_required")
        _old_path, old = self._bundle(user)
        old_portal = old.get("portal_access_token") or old.get("portal_token")
        old_subscription_url = old.get("subscription_url")
        if not isinstance(old_portal, str) or not isinstance(old_subscription_url, str):
            raise ConsoleError("existing_bundle_incomplete")
        try:
            response = operator.issue_request(
                self.endpoint, self.admin_token, user["user_id"], kind, revoke_old=True
            )
            issued = response["tokens"]
            portal = issued.get("portal", old_portal)
            subscription_url_value = (
                operator.subscription_url(self.public_subscription_base_url, issued["subscription"])
                if "subscription" in issued else old_subscription_url
            )
            value = operator.user_delivery_bundle(
                user, portal, subscription_url_value, self.portal_url,
                str(response["issued_at"]), "rotated_via_owner_console",
            )
            path = operator.user_bundle_path(user["display_name"], self.delivery_dir)
            operator.write_delivery_bundle(path, value)
            new_tokens = dict(issued)
            new_tokens_for_verify = {
                "portal": portal,
                "subscription": operator.subscription_token_from_url(subscription_url_value),
            }
            old_tokens = {
                kind: old_portal if kind == "portal" else operator.subscription_token_from_url(old_subscription_url)
            }
            checks = operator.verify_issued_tokens(
                self.endpoint, user["user_id"], new_tokens_for_verify, old_tokens,
                subscription_expected_status=(200 if user["subscription_status"] == "available" else 503),
            )
            operator.verify_public_subscription_projection(
                subscription_url_value, user["plan"], user["subscription_status"],
                user["subscription_entry_count"], user["subscription_pool_ids"],
                user["subscription_protocols"], self.public_subscription_base_url,
            )
        except operator.OperatorError as exc:
            raise ConsoleError(exc.code) from exc
        self._record(user["user_id"], "portal_token" if kind == "portal" else "subscription_token", "delivered", "rotated bundle written")
        return {"ok": True, "user_id": user["user_id"], "kind": kind,
                "verification": checks, "delivery_bundle_updated": True,
                "secret_not_printed": True}

    def __call__(self, environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO", "/")
        if path == "/" and method == "GET":
            page = (ROOT / "web" / "admin.html").read_bytes()
            return self._reply(
                start_response, 200, page, "text/html; charset=utf-8",
                [("Set-Cookie", f"{SESSION_COOKIE}={self.session_token}; HttpOnly; SameSite=Strict; Path=/")],
            )
        if not self._authorized(environ):
            return self._reply(start_response, 401, json.dumps({"error": "local console session required"}).encode())
        try:
            if path == "/api/console/state" and method == "GET":
                return self._reply(start_response, 200, json.dumps(self.state(), ensure_ascii=False).encode())
            if path.startswith("/api/console/users/") and method == "GET":
                requested = urllib.parse.unquote(path[len("/api/console/users/"):])
                return self._reply(start_response, 200, json.dumps(self.detail(requested), ensure_ascii=False).encode())
            if path == "/api/console/copy" and method == "POST":
                value = _json_body(environ)
                return self._reply(start_response, 200, json.dumps(self.copy_secret(value.get("user"), value.get("kind"))).encode())
            if path == "/api/console/rotate" and method == "POST":
                value = _json_body(environ)
                return self._reply(start_response, 200, json.dumps(self.rotate(value.get("user"), value.get("kind"), value.get("confirmation"))).encode())
            return self._reply(start_response, 404, json.dumps({"error": "not found"}).encode())
        except (ConsoleError, operator.OperatorError) as exc:
            code = exc.code if isinstance(exc, (ConsoleError, operator.OperatorError)) else "console_error"
            return self._reply(start_response, 400, json.dumps({"error": code}).encode())
        except (KeyError, TypeError, ValueError) as exc:
            return self._reply(start_response, 400, json.dumps({"error": "invalid_request"}).encode())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local loopback SparkLink OWNER operations console")
    parser.add_argument("--secret-path", type=Path, default=operator.DEFAULT_SECRET_PATH)
    parser.add_argument("--delivery-dir", type=Path, default=operator.DEFAULT_DELIVERY_DIR)
    parser.add_argument("--portal-url", default=operator.DEFAULT_PORTAL_URL)
    parser.add_argument("--public-subscription-base-url", default=operator.DEFAULT_SUBSCRIPTION_BASE_URL)
    parser.add_argument("--host", default=DEFAULT_CONSOLE_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_CONSOLE_PORT)
    operator.add_endpoint_options(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise ConsoleError("console_must_bind_loopback")
    if not 1024 <= args.port <= 65535:
        raise ConsoleError("console_port_invalid")
    admin_token = operator._admin_token(Path(args.secret_path))
    operator.ensure_delivery_directory(Path(args.delivery_dir))
    with operator.selected_endpoint(args) as endpoint:
        app = ConsoleApp(
            endpoint, admin_token, Path(args.delivery_dir), args.portal_url,
            args.public_subscription_base_url,
        )
        server = make_server(args.host, args.port, app, handler_class=QuietHandler)
        print(f"SparkLink OWNER console: http://{args.host}:{server.server_port}/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            return 0
        finally:
            server.server_close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ConsoleError, operator.OperatorError) as exc:
        print(json.dumps({"ok": False, "error": exc.code}, separators=(",", ":")))
        raise SystemExit(1)
