"""Standardize current subscription display names without touching credentials.

The command reads current public projections through protected bundles, derives
only non-secret entry metadata from the Admin API, and asks the Admin-only
Control Plane endpoint to replace URI fragments.  URI core fields are held in
memory for before/after comparison and are never printed or written.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT))

from deploy import issue_user_tokens as operator  # noqa: E402
from src.sparklink_control_plane import PLAN_ORDER  # noqa: E402
from src.sparklink_subscription_naming import (  # noqa: E402
    alias_from_uri,
    canonical_alias,
    uri_core,
)


COMMAND_SOURCE = "subscription-naming-operator-v1"


def _decode_public_projection(url: str) -> list[str]:
    status, raw = operator._request_url(
        url, headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*"}
    )
    if status != 200:
        raise operator.OperatorError("subscription_naming_public_fetch_failed")
    try:
        decoded = base64.b64decode(raw.strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeError) as exc:
        raise operator.OperatorError("subscription_naming_projection_invalid") from exc
    lines = [line for line in decoded.splitlines() if line]
    if not lines:
        raise operator.OperatorError("subscription_naming_projection_empty")
    return lines


def _current_entries(detail: dict, plan: str, accessible_only: bool = False) -> list[dict]:
    access = {
        item["node_id"]: item
        for item in detail.get("effective_access", [])
        if isinstance(item, dict) and isinstance(item.get("node_id"), str)
    }
    result = []
    for entry in detail.get("subscription_entries", []):
        if (
            not isinstance(entry, dict)
            or not entry.get("enabled")
            or entry.get("projection_status") != "current"
            or str(entry.get("protocol", "")).lower() != "vless"
        ):
            continue
        minimum_plan = entry.get("minimum_plan")
        if minimum_plan not in PLAN_ORDER or PLAN_ORDER[plan] < PLAN_ORDER[minimum_plan]:
            continue
        node_id = entry.get("node_id")
        if accessible_only and node_id and access.get(node_id, {}).get("decision") != "allow":
            continue
        result.append(entry)
    result.sort(key=lambda item: item["entry_id"])
    return result


def _safe_bundle(username: str, user_id: str, delivery_dir: Path) -> tuple[Path, dict]:
    path = operator.user_bundle_path(username, delivery_dir)
    value = operator.read_bundle(path)
    if value.get("user_id") != user_id:
        raise operator.OperatorError("subscription_naming_bundle_user_mismatch")
    return path, value


def build_plan(
    endpoint: str,
    admin_token: str,
    delivery_dir: Path,
    public_subscription_base_url: str,
) -> dict:
    users = operator.read_admin_users(endpoint, admin_token)
    operator.validate_reconciliation_scope(users)
    snapshots = []
    for user in sorted(users, key=lambda item: item["display_name"]):
        username = user["display_name"]
        _path, bundle = _safe_bundle(username, user["user_id"], delivery_dir)
        portal_token = bundle.get("portal_access_token") or bundle.get("portal_token")
        if not isinstance(portal_token, str) or not portal_token:
            raise operator.OperatorError("subscription_naming_bundle_portal_missing")
        operator.verify_portal(endpoint, portal_token, user["user_id"])
        detail = operator.admin_json(
            endpoint,
            admin_token,
            "/api/admin/users/" + urllib.parse.quote(user["user_id"], safe=""),
            "GET",
        )
        entries = _current_entries(detail, user["plan"])
        snapshot = {
            "user": username,
            "user_id": user["user_id"],
            "plan": user["plan"],
            "entry_count": user["subscription_entry_count"],
            "pool_ids": list(user["subscription_pool_ids"]),
            "protocols": list(user["subscription_protocols"]),
            "subscription_status": user["subscription_status"],
            "entries": [],
        }
        if user["subscription_status"] == "not_configured":
            if entries:
                raise operator.OperatorError("subscription_naming_unprojected_entries_for_free_user")
            snapshots.append(snapshot)
            continue

        subscription_url = bundle.get("subscription_url")
        if not isinstance(subscription_url, str):
            raise operator.OperatorError("subscription_naming_bundle_subscription_missing")
        operator.verify_public_subscription_projection(
            subscription_url,
            user["plan"],
            user["subscription_status"],
            user["subscription_entry_count"],
            user["subscription_pool_ids"],
            user["subscription_protocols"],
            public_subscription_base_url,
        )
        lines = _decode_public_projection(subscription_url)
        projected_entries = _current_entries(detail, user["plan"], accessible_only=True)
        if len(projected_entries) != len(lines):
            raise operator.OperatorError("subscription_naming_entry_alignment_failed")
        public_lines_by_id = {}
        for entry, line in zip(projected_entries, lines):
            if alias_from_uri(line) != entry.get("display_alias"):
                raise operator.OperatorError("subscription_naming_display_alias_alignment_failed")
            public_lines_by_id[entry["entry_id"]] = line
        seen_aliases: set[str] = set()
        for entry in entries:
            old_alias = entry.get("display_alias")
            if not isinstance(old_alias, str) or not old_alias:
                raise operator.OperatorError("subscription_naming_display_alias_missing")
            new_alias = canonical_alias(str(entry["node_id"]), old_alias)
            if new_alias in seen_aliases:
                raise operator.OperatorError("subscription_naming_alias_collision")
            seen_aliases.add(new_alias)
            snapshot["entries"].append(
                {
                    "entry_id": entry["entry_id"],
                    "node_id": entry["node_id"],
                    "old_alias": old_alias,
                    "new_alias": new_alias,
                    "old_core": uri_core(public_lines_by_id[entry["entry_id"]])
                    if entry["entry_id"] in public_lines_by_id else None,
                    "projected": entry["entry_id"] in public_lines_by_id,
                }
            )
        snapshots.append(snapshot)
    return {"users": snapshots}


def _changes(plan: dict, use_new: bool) -> list[dict]:
    result = []
    for user in plan["users"]:
        for entry in user["entries"]:
            if entry["new_alias"] == entry["old_alias"]:
                continue
            alias = entry["new_alias"] if use_new else entry["old_alias"]
            result.append({"entry_id": entry["entry_id"], "alias": alias})
    return result


def _all_entries(plan: dict) -> list[dict]:
    return [entry for user in plan["users"] for entry in user["entries"]]


def _safe_report(plan: dict, applied: bool = False) -> dict:
    changes = []
    for user in plan["users"]:
        for entry in user["entries"]:
            if entry["old_alias"] == entry["new_alias"]:
                continue
            changes.append(
                {
                    "user": user["user"],
                    "node_id": entry["node_id"],
                    "old_alias": entry["old_alias"],
                    "new_alias": entry["new_alias"],
                }
            )
    return {
        "ok": True,
        "applied": applied,
        "users": len(plan["users"]),
        "current_entries": sum(len(user["entries"]) for user in plan["users"]),
        "projected_entries": sum(
            sum(bool(entry.get("projected")) for entry in user["entries"])
            for user in plan["users"]
        ),
        "changed_entries": len(changes),
        "changes": changes,
        "plaintext_not_printed": True,
    }


def verify_plan(
    endpoint: str,
    admin_token: str,
    delivery_dir: Path,
    public_subscription_base_url: str,
    plan: dict,
    expected_aliases: dict[str, str],
) -> dict:
    verified_entries = 0
    verified_public_entries = 0
    for user in plan["users"]:
        if not user["entries"]:
            continue
        _path, bundle = _safe_bundle(user["user"], user["user_id"], delivery_dir)
        portal_token = bundle.get("portal_access_token") or bundle.get("portal_token")
        if not isinstance(portal_token, str) or not portal_token:
            raise operator.OperatorError("subscription_naming_bundle_portal_missing")
        operator.verify_portal(endpoint, portal_token, user["user_id"])
        detail = operator.admin_json(
            endpoint,
            admin_token,
            "/api/admin/users/" + urllib.parse.quote(user["user_id"], safe=""),
            "GET",
        )
        entries = _current_entries(detail, user["plan"])
        if len(entries) != len(user["entries"]):
            raise operator.OperatorError("subscription_naming_verify_entry_count_changed")
        before_by_id = {item["entry_id"]: item for item in user["entries"]}
        for entry in entries:
            expected_alias = expected_aliases.get(entry["entry_id"])
            if expected_alias is None or entry.get("display_alias") != expected_alias:
                raise operator.OperatorError("subscription_naming_alias_verification_failed")
        verified_entries += len(entries)
        projected_entries = _current_entries(detail, user["plan"], accessible_only=True)
        if not projected_entries:
            continue
        subscription_url = bundle.get("subscription_url")
        if not isinstance(subscription_url, str):
            raise operator.OperatorError("subscription_naming_bundle_subscription_missing")
        operator.verify_public_subscription_projection(
            subscription_url,
            user["plan"],
            user["subscription_status"],
            user["entry_count"],
            user["pool_ids"],
            user["protocols"],
            public_subscription_base_url,
        )
        lines = _decode_public_projection(subscription_url)
        if len(projected_entries) != len(lines):
            raise operator.OperatorError("subscription_naming_verify_alignment_failed")
        for entry, line in zip(projected_entries, lines):
            expected_alias = expected_aliases.get(entry["entry_id"])
            if expected_alias is None or alias_from_uri(line) != expected_alias:
                raise operator.OperatorError("subscription_naming_alias_verification_failed")
            before = before_by_id.get(entry["entry_id"])
            if before is None:
                raise operator.OperatorError("subscription_naming_verify_entry_missing")
            if before["old_core"] is None or uri_core(line) != before["old_core"]:
                raise operator.OperatorError("subscription_naming_uri_core_changed")
            verified_public_entries += 1
    return {
        "verified_users": len(plan["users"]),
        "verified_entries": verified_entries,
        "verified_public_entries": verified_public_entries,
    }


def _post_aliases(endpoint: str, admin_token: str, entries: list[dict]) -> dict:
    return operator.admin_json(
        endpoint,
        admin_token,
        "/api/admin/subscription-aliases",
        "POST",
        {"entries": entries, "source": COMMAND_SOURCE},
    )


def run(args: argparse.Namespace) -> int:
    admin_token = operator._admin_token(Path(args.secret_path))
    delivery_dir = Path(args.delivery_dir)
    public_base = operator.validate_url(args.public_subscription_base_url)
    with operator.selected_endpoint(args) as endpoint:
        plan = build_plan(endpoint, admin_token, delivery_dir, public_base)
        report = _safe_report(plan)
        if args.command == "preview":
            print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
            return 0

        changes = _changes(plan, use_new=True)
        response = _post_aliases(endpoint, admin_token, changes) if changes else {
            "changed": 0, "unchanged": 0, "requested": 0
        }
        if response.get("changed") != len(changes):
            raise operator.OperatorError("subscription_naming_apply_count_mismatch")
        expected_aliases = {
            entry["entry_id"]: entry["new_alias"]
            for entry in _all_entries(plan)
        }
        try:
            verification = verify_plan(
                endpoint, admin_token, delivery_dir, public_base, plan, expected_aliases
            )
        except operator.OperatorError:
            rollback = _changes(plan, use_new=False)
            if changes:
                rollback_result = _post_aliases(endpoint, admin_token, rollback)
                if rollback_result.get("changed") != len(rollback):
                    raise operator.OperatorError("subscription_naming_rollback_failed")
                raise operator.OperatorError("subscription_naming_verification_failed_rolled_back")
            raise operator.OperatorError("subscription_naming_verification_failed")
    print(json.dumps({**report, "applied": True, "verification": verification},
                     ensure_ascii=False, separators=(",", ":")))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Standardize SparkLink subscription node display names")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("preview", "apply"):
        command = commands.add_parser(name)
        command.add_argument("--secret-path", type=Path, default=operator.DEFAULT_SECRET_PATH)
        command.add_argument("--delivery-dir", type=Path, default=operator.DEFAULT_DELIVERY_DIR)
        command.add_argument("--public-subscription-base-url", default=operator.DEFAULT_SUBSCRIPTION_BASE_URL)
        operator.add_endpoint_options(command)
    args = parser.parse_args(argv)
    try:
        return run(args)
    except operator.OperatorError as exc:
        print(json.dumps({"ok": False, "error": exc.code, "plaintext_not_printed": True},
                         separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
