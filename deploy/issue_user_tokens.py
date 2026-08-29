#!/usr/bin/env python3
"""Admin-only User token issuance and protected local delivery.

This operator tool is intentionally separate from the Control Plane process.
It reads the existing Windows DPAPI-protected Admin secret, calls the Admin
issuance endpoint through an SSH tunnel, and writes only the newly issued
plaintext to a Windows ACL-protected, Git-ignored delivery directory. It never
prints token values, response bodies, exception details, or subscription data.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import getpass
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Iterator


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SECRET_PATH = ROOT / "runtime" / "secrets" / "control-plane-admin-token.dpapi"
DEFAULT_DELIVERY_DIR = ROOT / "runtime" / "delivery"
DEFAULT_PORTAL_URL = "https://spark.enrpiglink.top"
DEFAULT_SUBSCRIPTION_BASE_URL = "https://sub.enrpiglink.top"
DEFAULT_SSH_HOST = "sparklink-node-166"
DEFAULT_FORWARD_PORT = 18081
USER_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

sys.path.insert(0, str(ROOT))
from src.sparklink_xray_collector import CollectorError, read_dpapi_token  # noqa: E402


class OperatorError(RuntimeError):
    """A non-secret, user-facing operator failure code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def utc_stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def validate_user_id(user_id: str) -> str:
    if not user_id or not USER_ID_RE.fullmatch(user_id):
        raise OperatorError("user_id_invalid")
    return user_id


def validate_url(value: str, expected_scheme: str = "https") -> str:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != expected_scheme or not parsed.netloc or parsed.query or parsed.fragment:
        raise OperatorError("url_invalid")
    return value.rstrip("/")


def subscription_url(base_url: str, token: str) -> str:
    base = validate_url(base_url)
    if not token or "/" in token or any(ch.isspace() for ch in token):
        raise OperatorError("subscription_token_invalid")
    return f"{base}/u/{token}"


def subscription_token_from_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
        raise OperatorError("subscription_url_invalid")
    prefix = "/u/"
    if not parsed.path.startswith(prefix):
        raise OperatorError("subscription_url_invalid")
    token = urllib.parse.unquote(parsed.path[len(prefix):])
    if not token or "/" in token or any(ch.isspace() for ch in token):
        raise OperatorError("subscription_url_invalid")
    return token


def operator_account() -> str:
    name = os.environ.get("USERNAME") or getpass.getuser()
    domain = os.environ.get("USERDOMAIN")
    return f"{domain}\\{name}" if domain else name


def _set_private_acl(path: Path, directory: bool) -> None:
    if os.name != "nt":
        try:
            os.chmod(path, 0o700 if directory else 0o600)
        except OSError as exc:
            raise OperatorError("delivery_permissions_failed") from exc
        return
    grant = f"{operator_account()}:" + ("(OI)(CI)F" if directory else "F")
    result = subprocess.run(
        ["icacls.exe", str(path), "/inheritance:r", "/grant:r", grant],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        raise OperatorError("delivery_acl_failed")


def ensure_delivery_directory(path: Path) -> Path:
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise OperatorError("delivery_directory_failed") from exc
    _set_private_acl(path, directory=True)
    return path


def delivery_path(path: Path, delivery_dir: Path) -> Path:
    root = delivery_dir.resolve()
    candidate = path.resolve()
    if candidate != root and root not in candidate.parents:
        raise OperatorError("delivery_path_outside_runtime")
    return candidate


def write_delivery_bundle(path: Path, value: dict, apply_acl: bool = True) -> Path:
    parent = ensure_delivery_directory(path.parent) if apply_acl else path.parent
    if not parent.exists():
        try:
            parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as exc:
            raise OperatorError("delivery_directory_failed") from exc
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if apply_acl:
            _set_private_acl(temporary, directory=False)
        else:
            os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        if apply_acl:
            _set_private_acl(path, directory=False)
        else:
            os.chmod(path, 0o600)
    except OSError as exc:
        raise OperatorError("delivery_write_failed") from exc
    finally:
        if temporary is not None and temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass
    return path


def read_bundle(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OperatorError("delivery_bundle_unreadable") from exc
    if not isinstance(value, dict) or value.get("schema") != "sparklink.operator-delivery.v1":
        raise OperatorError("delivery_bundle_invalid")
    return value


def preserved_subscription_url(path: Path, user_id: str) -> str:
    value = read_bundle(path)
    if value.get("user_id") != user_id:
        raise OperatorError("delivery_bundle_user_mismatch")
    result = value.get("subscription_url")
    if not isinstance(result, str):
        raise OperatorError("subscription_url_missing")
    subscription_token_from_url(result)
    return result


def old_tokens_from_bundle(path: Path, token_kind: str, user_id: str) -> dict[str, str]:
    value = read_bundle(path)
    if value.get("user_id") != user_id:
        raise OperatorError("delivery_bundle_user_mismatch")
    tokens: dict[str, str] = {}
    if token_kind in {"portal", "both"}:
        token = value.get("portal_access_token") or value.get("portal_token")
        if not isinstance(token, str) or not token:
            raise OperatorError("old_portal_token_missing")
        tokens["portal"] = token
    if token_kind in {"subscription", "both"}:
        url = value.get("subscription_url")
        if not isinstance(url, str):
            raise OperatorError("old_subscription_token_missing")
        tokens["subscription"] = subscription_token_from_url(url)
    return tokens


@contextlib.contextmanager
def ssh_tunnel(ssh_host: str, forward_port: int) -> Iterator[str]:
    if not 1024 <= forward_port <= 65535:
        raise OperatorError("forward_port_invalid")
    try:
        with socket.create_connection(("127.0.0.1", forward_port), timeout=0.2):
            raise OperatorError("forward_port_in_use")
    except OSError:
        pass
    try:
        tunnel = subprocess.Popen(
            [
                "ssh.exe",
                "-N",
                "-T",
                "-o",
                "BatchMode=yes",
                "-o",
                "ExitOnForwardFailure=yes",
                "-o",
                "ServerAliveInterval=30",
                "-o",
                "ServerAliveCountMax=3",
                "-L",
                f"127.0.0.1:{forward_port}:127.0.0.1:8080",
                ssh_host,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise OperatorError("ssh_tunnel_start_failed") from exc
    try:
        for _ in range(40):
            if tunnel.poll() is not None:
                raise OperatorError("ssh_tunnel_exited_before_ready")
            try:
                with socket.create_connection(("127.0.0.1", forward_port), timeout=0.3):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise OperatorError("ssh_tunnel_not_ready")
        yield f"http://127.0.0.1:{forward_port}"
    finally:
        if tunnel.poll() is None:
            tunnel.terminate()
            try:
                tunnel.wait(timeout=5)
            except subprocess.TimeoutExpired:
                tunnel.kill()
                tunnel.wait(timeout=5)


@contextlib.contextmanager
def selected_endpoint(args: argparse.Namespace) -> Iterator[str]:
    if args.endpoint:
        scheme = urllib.parse.urlsplit(args.endpoint).scheme
        if scheme not in {"http", "https"}:
            raise OperatorError("endpoint_invalid")
        yield validate_url(args.endpoint, expected_scheme=scheme)
        return
    with ssh_tunnel(args.ssh_host, args.forward_port) as endpoint:
        yield endpoint


def _request(
    endpoint: str,
    path: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: dict | None = None,
    read_body: bool = True,
) -> tuple[int, bytes]:
    request_headers = {"Cache-Control": "no-store"}
    if headers:
        request_headers.update(headers)
    data = None
    if body is not None:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        endpoint.rstrip("/") + path, data=data, headers=request_headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return int(response.status), response.read(1_000_000) if read_body else b""
    except urllib.error.HTTPError as exc:
        return int(exc.code), b""
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise OperatorError("control_plane_unreachable") from exc


def _admin_token(path: Path) -> str:
    try:
        token = read_dpapi_token(path)
    except CollectorError as exc:
        raise OperatorError(exc.code) from exc
    if not token:
        raise OperatorError("admin_secret_empty")
    return token


def admin_json(endpoint: str, admin_token: str, path: str, method: str, body: dict | None = None) -> dict:
    status, raw = _request(
        endpoint,
        path,
        method=method,
        headers={"Authorization": f"Bearer {admin_token}"},
        body=body,
    )
    if status < 200 or status >= 300:
        raise OperatorError(f"control_plane_http_{status}")
    try:
        value = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise OperatorError("control_plane_response_invalid") from exc
    if not isinstance(value, dict):
        raise OperatorError("control_plane_response_invalid")
    return value


def issue_request(endpoint: str, admin_token: str, user_id: str, token_kind: str) -> dict:
    value = admin_json(
        endpoint,
        admin_token,
        "/api/admin/token-issuance",
        "POST",
        {"user_id": user_id, "token_kind": token_kind, "revoke_old": True},
    )
    tokens = value.get("tokens")
    if not isinstance(tokens, dict):
        raise OperatorError("issuance_response_missing_tokens")
    expected = {"portal", "subscription"} if token_kind == "both" else {token_kind}
    if set(tokens) != expected or any(not isinstance(tokens[k], str) or not tokens[k] for k in expected):
        raise OperatorError("issuance_response_invalid")
    if value.get("user_id") != user_id or value.get("revoked_previous") is not True:
        raise OperatorError("issuance_response_metadata_invalid")
    return value


def verify_portal(endpoint: str, token: str, user_id: str) -> None:
    status, raw = _request(endpoint, "/api/me", headers={"Authorization": f"Bearer {token}"})
    if status != 200:
        raise OperatorError("verify_new_portal_failed")
    try:
        value = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise OperatorError("verify_portal_response_invalid") from exc
    if not isinstance(value, dict) or value.get("user_id") != user_id:
        raise OperatorError("verify_portal_identity_failed")


def verify_portal_view(
    endpoint: str,
    token: str,
    user_id: str,
    expected_plan: str,
    expected_role: str,
    expected_cycle: str,
) -> dict:
    status, raw = _request(endpoint, "/api/me", headers={"Authorization": f"Bearer {token}"})
    if status != 200:
        raise OperatorError(f"acceptance_portal_http_{status}")
    try:
        value = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise OperatorError("acceptance_portal_response_invalid") from exc
    if not isinstance(value, dict) or value.get("user_id") != user_id:
        raise OperatorError("acceptance_self_scope_failed")
    if value.get("plan") != expected_plan:
        raise OperatorError("acceptance_plan_failed")
    if value.get("role") != expected_role:
        raise OperatorError("acceptance_role_failed")
    cycle = value.get("customer_billing_cycle")
    if not isinstance(cycle, dict) or cycle.get("cycle_key") != expected_cycle:
        raise OperatorError("acceptance_cycle_failed")
    pools = value.get("pools")
    if not isinstance(pools, list):
        raise OperatorError("acceptance_pools_missing")
    pool_ids = [pool.get("pool_id") for pool in pools if isinstance(pool, dict)]
    if len(pool_ids) != 2 or set(pool_ids) != {"STANDARD", "PREMIUM"}:
        raise OperatorError("acceptance_pools_not_independent")
    if "users" in value:
        raise OperatorError("acceptance_self_scope_failed")
    return {
        "user_id": value["user_id"],
        "display_name": value.get("display_name"),
        "plan": value.get("plan"),
        "role": value.get("role"),
        "cycle_key": cycle.get("cycle_key"),
        "cycle_kind": cycle.get("cycle_kind"),
        "timezone": cycle.get("timezone"),
        "pool_ids": pool_ids,
        "self_scoped": True,
    }


def verify_status(endpoint: str, path: str, headers: dict[str, str], expected: int, code: str) -> None:
    status, _ = _request(endpoint, path, headers=headers, read_body=False)
    if status != expected:
        raise OperatorError(code)


def verify_issued_tokens(
    endpoint: str,
    user_id: str,
    tokens: dict[str, str],
    old_tokens: dict[str, str] | None = None,
) -> list[str]:
    checks: list[str] = []
    if "portal" in tokens:
        verify_portal(endpoint, tokens["portal"], user_id)
        checks.append("new_portal=accepted")
        verify_status(
            endpoint,
            "/api/me",
            {"Authorization": f"Bearer {secrets.token_urlsafe(32)}"},
            401,
            "verify_wrong_portal_failed",
        )
        checks.append("wrong_portal=rejected")
        verify_status(
            endpoint,
            "/subscription",
            {"X-SparkLink-Subscription-Token": tokens["portal"]},
            401,
            "verify_portal_as_subscription_failed",
        )
        checks.append("portal_as_subscription=rejected")
    if "subscription" in tokens:
        verify_status(
            endpoint,
            "/subscription",
            {"X-SparkLink-Subscription-Token": tokens["subscription"]},
            200,
            "verify_new_subscription_failed",
        )
        checks.append("new_subscription=accepted")
        verify_status(
            endpoint,
            "/subscription",
            {"X-SparkLink-Subscription-Token": secrets.token_urlsafe(32)},
            401,
            "verify_wrong_subscription_failed",
        )
        checks.append("wrong_subscription=rejected")
        verify_status(
            endpoint,
            "/api/me",
            {"Authorization": f"Bearer {tokens['subscription']}"},
            401,
            "verify_subscription_as_portal_failed",
        )
        checks.append("subscription_as_portal=rejected")
    if old_tokens:
        if "portal" in old_tokens:
            verify_status(
                endpoint,
                "/api/me",
                {"Authorization": f"Bearer {old_tokens['portal']}"},
                401,
                "verify_old_portal_failed",
            )
            checks.append("old_portal=rejected")
        if "subscription" in old_tokens:
            verify_status(
                endpoint,
                "/subscription",
                {"X-SparkLink-Subscription-Token": old_tokens["subscription"]},
                401,
                "verify_old_subscription_failed",
            )
            checks.append("old_subscription=rejected")
    return checks


def bundle_from_issuance(
    response: dict,
    token_kind: str,
    portal_url: str,
    preserved_url: str | None,
    subscription_base_url: str,
) -> tuple[dict, dict[str, str]]:
    tokens = {str(k): str(v) for k, v in response["tokens"].items()}
    value = {
        "schema": "sparklink.operator-delivery.v1",
        "generated_at": str(response["issued_at"]),
        "user_id": str(response["user_id"]),
        "display_name": str(response.get("display_name", "")),
        "plan": str(response.get("plan", "")),
        "role": str(response.get("role", "")),
        "portal_url": validate_url(portal_url),
        "token_kind": token_kind,
        "revoked_previous": True,
    }
    if "portal" in tokens:
        value["portal_access_token"] = tokens["portal"]
    if "subscription" in tokens:
        value["subscription_url"] = subscription_url(subscription_base_url, tokens["subscription"])
    elif preserved_url:
        value["subscription_url"] = preserved_url
    return value, tokens


def legacy_export(args: argparse.Namespace) -> int:
    user_id = validate_user_id(args.user_id)
    if not args.allow_legacy_plaintext_export:
        raise OperatorError("legacy_export_requires_explicit_flag")
    output = delivery_path(Path(args.output), Path(args.delivery_dir))
    ensure_delivery_directory(output.parent)
    escaped_user_id = json.dumps(user_id)
    source = f"""
import json, sqlite3
db_path = "/var/lib/sparklink/control-plane/sparklink.db"
user_id = {escaped_user_id}
connection = sqlite3.connect("file:" + db_path + "?mode=ro", uri=True)
columns = [row[1] for row in connection.execute("PRAGMA table_info(users)")]
if "subscription_token" not in columns:
    print(json.dumps({{"ok": False, "error": "legacy_token_unavailable"}}, separators=(",", ":")))
else:
    row = connection.execute("SELECT subscription_token FROM users WHERE user_id=?", (user_id,)).fetchone()
    if not row or not row[0]:
        print(json.dumps({{"ok": False, "error": "legacy_token_unavailable"}}, separators=(",", ":")))
    else:
        print(json.dumps({{"ok": True, "subscription_token": row[0]}}, separators=(",", ":")))
connection.close()
"""
    try:
        result = subprocess.run(
            ["ssh.exe", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", args.ssh_host, "sudo -n python3 -"],
            input=source.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=45,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OperatorError("legacy_export_ssh_failed") from exc
    if result.returncode != 0:
        raise OperatorError("legacy_export_ssh_failed")
    try:
        remote = json.loads(result.stdout)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise OperatorError("legacy_export_response_invalid") from exc
    if not isinstance(remote, dict) or remote.get("ok") is not True:
        raise OperatorError("legacy_token_unavailable_rotate_required")
    token = remote.get("subscription_token")
    if not isinstance(token, str) or not token:
        raise OperatorError("legacy_token_unavailable_rotate_required")
    value = {
        "schema": "sparklink.operator-delivery.v1",
        "generated_at": utc_stamp(),
        "user_id": user_id,
        "token_kind": "subscription-preserved",
        "subscription_url": subscription_url(args.subscription_base_url, token),
    }
    write_delivery_bundle(output, value)
    print(json.dumps({"ok": True, "user_id": user_id, "delivery_bundle": str(output)}, separators=(",", ":")))
    return 0


def issue(args: argparse.Namespace) -> int:
    user_id = validate_user_id(args.user_id)
    delivery_dir = Path(args.delivery_dir)
    output = Path(args.output) if args.output else delivery_dir / f"{user_id}-{args.token_kind}-{utc_stamp()}.json"
    output = delivery_path(output, delivery_dir)
    old_tokens = None
    if args.old_bundle:
        old_tokens = old_tokens_from_bundle(Path(args.old_bundle), args.token_kind, user_id)
    preserved_url = None
    if args.preserve_subscription_bundle:
        preserved_url = preserved_subscription_url(Path(args.preserve_subscription_bundle), user_id)
    admin_token = _admin_token(Path(args.secret_path))
    with selected_endpoint(args) as endpoint:
        response = issue_request(endpoint, admin_token, user_id, args.token_kind)
        value, tokens = bundle_from_issuance(
            response, args.token_kind, args.portal_url, preserved_url, args.subscription_base_url
        )
        write_delivery_bundle(output, value)
        checks = []
        if not args.no_verify:
            verify_tokens = dict(tokens)
            if preserved_url:
                verify_tokens["subscription"] = subscription_token_from_url(preserved_url)
            checks = verify_issued_tokens(endpoint, user_id, verify_tokens, old_tokens)
    if args.consume_old_bundle:
        if not args.old_bundle:
            raise OperatorError("consume_old_requires_old_bundle")
        old_path = delivery_path(Path(args.old_bundle), delivery_dir)
        if old_path == output:
            raise OperatorError("consume_old_output_same_path")
        try:
            old_path.unlink()
        except OSError as exc:
            raise OperatorError("old_bundle_consume_failed") from exc
    print(json.dumps({
        "ok": True,
        "user_id": user_id,
        "token_kind": args.token_kind,
        "delivery_bundle": str(output),
        "verification": checks or ["skipped"],
        "plaintext_not_printed": True,
    }, separators=(",", ":")))
    return 0


def verify(args: argparse.Namespace) -> int:
    value = read_bundle(Path(args.bundle))
    user_id = validate_user_id(str(value.get("user_id", "")))
    token_kind = str(value.get("token_kind", ""))
    tokens: dict[str, str] = {}
    if token_kind in {"portal", "both"}:
        portal = value.get("portal_access_token") or value.get("portal_token")
        if not isinstance(portal, str) or not portal:
            raise OperatorError("portal_token_missing")
        tokens["portal"] = portal
    if token_kind in {"subscription", "both", "subscription-preserved"} or value.get("subscription_url"):
        url = value.get("subscription_url")
        if not isinstance(url, str):
            raise OperatorError("subscription_url_missing")
        tokens["subscription"] = subscription_token_from_url(url)
    if not tokens:
        raise OperatorError("bundle_has_no_verifiable_token")
    with selected_endpoint(args) as endpoint:
        checks = verify_issued_tokens(endpoint, user_id, tokens)
    print(json.dumps({"ok": True, "user_id": user_id, "verification": checks}, separators=(",", ":")))
    return 0


def acceptance(args: argparse.Namespace) -> int:
    value = read_bundle(Path(args.bundle))
    user_id = validate_user_id(str(value.get("user_id", "")))
    portal = value.get("portal_access_token") or value.get("portal_token")
    if not isinstance(portal, str) or not portal:
        raise OperatorError("portal_token_missing")
    with selected_endpoint(args) as endpoint:
        result = verify_portal_view(
            endpoint, portal, user_id, args.expected_plan, args.expected_role, args.expected_cycle
        )
    print(json.dumps({"ok": True, "acceptance": result}, separators=(",", ":")))
    return 0


def list_users(args: argparse.Namespace) -> int:
    admin_token = _admin_token(Path(args.secret_path))
    with selected_endpoint(args) as endpoint:
        value = admin_json(endpoint, admin_token, "/api/admin/users", "GET")
    users = value.get("users")
    if not isinstance(users, list):
        raise OperatorError("admin_users_response_invalid")
    safe_users = []
    for user in users:
        if not isinstance(user, dict):
            raise OperatorError("admin_users_response_invalid")
        safe_users.append({key: user.get(key) for key in ("user_id", "display_name", "plan", "role", "status")})
    print(json.dumps({"ok": True, "users": safe_users}, ensure_ascii=False, separators=(",", ":")))
    return 0


def copy_secret(args: argparse.Namespace) -> int:
    value = read_bundle(Path(args.bundle))
    if args.kind == "portal":
        token = value.get("portal_access_token") or value.get("portal_token")
        if not isinstance(token, str) or not token:
            raise OperatorError("portal_token_missing")
    else:
        url = value.get("subscription_url")
        if not isinstance(url, str):
            raise OperatorError("subscription_url_missing")
        token = subscription_token_from_url(url)
    if os.name != "nt":
        raise OperatorError("clipboard_requires_windows")
    result = subprocess.run(
        ["clip.exe"], input=token.encode("utf-8"), stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, check=False,
    )
    if result.returncode != 0:
        raise OperatorError("clipboard_copy_failed")
    print(json.dumps({"ok": True, "kind": args.kind, "clipboard": "updated", "secret_not_printed": True}, separators=(",", ":")))
    return 0


def add_endpoint_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--endpoint", help="Direct local/test endpoint; omit to use the SSH tunnel")
    parser.add_argument("--ssh-host", default=DEFAULT_SSH_HOST)
    parser.add_argument("--forward-port", type=int, default=DEFAULT_FORWARD_PORT)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SparkLink protected Admin token operator")
    commands = parser.add_subparsers(dest="command", required=True)

    issue_parser = commands.add_parser("issue", help="issue or rotate one/both token kinds")
    issue_parser.add_argument("--user-id", required=True)
    issue_parser.add_argument("--token-kind", choices=("portal", "subscription", "both"), default="portal")
    issue_parser.add_argument("--secret-path", type=Path, default=DEFAULT_SECRET_PATH)
    issue_parser.add_argument("--delivery-dir", type=Path, default=DEFAULT_DELIVERY_DIR)
    issue_parser.add_argument("--output", type=Path)
    issue_parser.add_argument("--portal-url", default=DEFAULT_PORTAL_URL)
    issue_parser.add_argument("--subscription-base-url", default=DEFAULT_SUBSCRIPTION_BASE_URL)
    issue_parser.add_argument("--preserve-subscription-bundle", type=Path)
    issue_parser.add_argument("--old-bundle", type=Path)
    issue_parser.add_argument("--consume-old-bundle", action="store_true")
    issue_parser.add_argument("--no-verify", action="store_true")
    add_endpoint_options(issue_parser)

    verify_parser = commands.add_parser("verify", help="verify a protected delivery bundle")
    verify_parser.add_argument("--bundle", type=Path, required=True)
    add_endpoint_options(verify_parser)

    acceptance_parser = commands.add_parser("acceptance", help="verify a protected Portal owner acceptance")
    acceptance_parser.add_argument("--bundle", type=Path, required=True)
    acceptance_parser.add_argument("--expected-plan", default="Plus")
    acceptance_parser.add_argument("--expected-role", default="OWNER")
    acceptance_parser.add_argument("--expected-cycle", required=True)
    add_endpoint_options(acceptance_parser)

    list_parser = commands.add_parser("list", help="list non-secret Admin user metadata")
    list_parser.add_argument("--secret-path", type=Path, default=DEFAULT_SECRET_PATH)
    add_endpoint_options(list_parser)

    copy_parser = commands.add_parser("copy", help="explicitly copy one bundle secret to local clipboard")
    copy_parser.add_argument("--bundle", type=Path, required=True)
    copy_parser.add_argument("--kind", choices=("portal", "subscription"), required=True)

    legacy_parser = commands.add_parser(
        "legacy-export",
        help="one-time pre-migration preservation of an existing legacy subscription token",
    )
    legacy_parser.add_argument("--user-id", required=True)
    legacy_parser.add_argument("--ssh-host", default=DEFAULT_SSH_HOST)
    legacy_parser.add_argument("--subscription-base-url", default=DEFAULT_SUBSCRIPTION_BASE_URL)
    legacy_parser.add_argument("--delivery-dir", type=Path, default=DEFAULT_DELIVERY_DIR)
    legacy_parser.add_argument("--output", type=Path, required=True)
    legacy_parser.add_argument("--allow-legacy-plaintext-export", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "issue":
        return issue(args)
    if args.command == "verify":
        return verify(args)
    if args.command == "acceptance":
        return acceptance(args)
    if args.command == "list":
        return list_users(args)
    if args.command == "copy":
        return copy_secret(args)
    if args.command == "legacy-export":
        return legacy_export(args)
    raise OperatorError("operator_command_invalid")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OperatorError as exc:
        print(json.dumps({"ok": False, "error": exc.code}, separators=(",", ":")))
        raise SystemExit(1)
    except Exception:
        print(json.dumps({"ok": False, "error": "operator_failure"}, separators=(",", ":")))
        raise SystemExit(1)
