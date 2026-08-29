#!/usr/bin/env python3
"""Reconcile Product Owner identity/cycle metadata on a Control Plane host.

The input is supplied through stdin and the generated token bundle must stay in
root-only runtime storage.  No token is printed.  This utility intentionally
does not modify Node proxy configuration or invent allowance/price values.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.sparklink_control_plane import ControlPlane, PLAN_ORDER, POOL_NAMES, utc_now


class ReconciliationError(RuntimeError):
    pass


def load_payload() -> dict:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError) as exc:
        raise ReconciliationError("input_invalid") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("users"), list):
        raise ReconciliationError("input_users_required")
    if not payload["users"]:
        raise ReconciliationError("input_users_empty")
    return payload


def validate_payload(payload: dict) -> None:
    seen: set[str] = set()
    for user in payload["users"]:
        if not isinstance(user, dict):
            raise ReconciliationError("user_invalid")
        user_id = user.get("user_id")
        if not isinstance(user_id, str) or not user_id or user_id in seen:
            raise ReconciliationError("user_id_invalid")
        if not isinstance(user.get("display_name"), str) or not user["display_name"]:
            raise ReconciliationError("display_name_invalid")
        if user.get("plan") not in PLAN_ORDER:
            raise ReconciliationError("plan_invalid")
        if user.get("role", "CUSTOMER") not in {"CUSTOMER", "OWNER"}:
            raise ReconciliationError("role_invalid")
        if "portal_token" in user or "subscription_token" in user:
            raise ReconciliationError("tokens_must_not_be_supplied")
        seen.add(user_id)
        for entitlement in user.get("entitlements", []):
            if not isinstance(entitlement, dict):
                raise ReconciliationError("entitlement_invalid")
            if entitlement.get("pool_id") not in POOL_NAMES:
                raise ReconciliationError("pool_invalid")
            if entitlement.get("plan") not in PLAN_ORDER:
                raise ReconciliationError("entitlement_plan_invalid")
            if entitlement.get("allowance_bytes") is not None:
                raise ReconciliationError("allowance_must_remain_undecided")
    for resource in payload.get("infrastructure_resources", []):
        if not isinstance(resource, dict):
            raise ReconciliationError("resource_invalid")
        for key in ("resource_id", "provider_name", "provider_instance_id", "location",
                    "network_label", "local_timezone", "timezone_source",
                    "resource_cycle_status", "resource_cycle_source"):
            if not isinstance(resource.get(key), str) or not resource[key]:
                raise ReconciliationError("resource_field_invalid")
        if "provider_resource_cycle" in resource:
            raise ReconciliationError("provider_cycle_requires_independent_evidence")


def pending_bundle_path(path: Path) -> Path:
    return path.with_name(path.name + ".pending")


def read_existing_pending(path: Path) -> dict:
    pending = pending_bundle_path(path)
    if not pending.is_file():
        return {}
    try:
        value = json.loads(pending.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReconciliationError("pending_bundle_invalid") from exc
    return value if isinstance(value, dict) else {}


def write_private_json(path: Path, value: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except OSError as exc:
        raise ReconciliationError("bundle_write_failed") from exc
    finally:
        if temporary is not None and temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


def active_entitlement_matches(cp: ControlPlane, user_id: str, pool_id: str, plan: str) -> bool:
    with cp.connect() as db:
        row = db.execute(
            """SELECT plan,allowance_bytes FROM entitlements
               WHERE user_id=? AND pool_id=? AND status='active'
               ORDER BY effective_from DESC LIMIT 1""",
            (user_id, pool_id),
        ).fetchone()
    return bool(row and row["plan"] == plan and row["allowance_bytes"] is None)


def main() -> int:
    parser = __import__("argparse").ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--token-bundle", type=Path, required=True)
    args = parser.parse_args()
    try:
        payload = load_payload()
        validate_payload(payload)
        cp = ControlPlane(args.db)
        cp.init_db()
        token_users: dict[str, dict] = {}
        user_ids = [user["user_id"] for user in payload["users"]]
        for user in payload["users"]:
            user_id = user["user_id"]
            with cp.connect() as db:
                existing = db.execute(
                    "SELECT user_id FROM users WHERE user_id=?", (user_id,)
                ).fetchone()
            result = cp.reconcile_user(
                user_id, user["display_name"], user["plan"], user.get("role", "CUSTOMER"),
            )
            # Existing users deliberately produce no plaintext token here. A
            # lost token is never read back from the database; use the
            # Admin-only issue/rotate workflow for a fresh delivery.
            if not existing:
                token_users[user_id] = {
                    "portal_token": result["portal_token"],
                    "subscription_token": result["subscription_token"],
                }
                write_private_json(args.token_bundle.with_name(args.token_bundle.name + ".pending"), {
                    "generated_at": utc_now(), "users": token_users,
                })

        for resource in payload.get("infrastructure_resources", []):
            cp.upsert_infrastructure_resource(resource)
        for user in payload["users"]:
            for entitlement in user.get("entitlements", []):
                if not active_entitlement_matches(cp, user["user_id"], entitlement["pool_id"], entitlement["plan"]):
                    cp.set_entitlement(user["user_id"], entitlement["pool_id"], entitlement["plan"], None)
        cycle_result = cp.reconcile_customer_cycles(user_ids)
        write_private_json(args.token_bundle, {
            "generated_at": utc_now(), "users": token_users,
        })
        try:
            pending_bundle_path(args.token_bundle).unlink()
        except FileNotFoundError:
            pass
        print(json.dumps({
            "ok": True,
            "users": len(user_ids),
            "resources": len(payload.get("infrastructure_resources", [])),
            "cycles": cycle_result,
            "token_bundle": str(args.token_bundle),
        }, separators=(",", ":")))
        return 0
    except ReconciliationError as exc:
        print(json.dumps({"ok": False, "error": str(exc)[:120]}, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
