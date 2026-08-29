#!/usr/bin/env python3
"""Commit an already verified Xray identity migration into the Control Plane.

The plan is runtime-only and arrives through stdin.  Node config changes must
have passed independently before this database transaction is run.  Full URIs,
UUIDs and runtime hashes are never printed.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def fail(code: str) -> None:
    raise RuntimeError(code)


def load_plan() -> dict:
    try:
        value = json.load(sys.stdin)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("plan_invalid") from exc
    if not isinstance(value, dict) or not isinstance(value.get("entries"), list):
        fail("plan_entries_required")
    if not value["entries"]:
        fail("plan_entries_empty")
    return value


def validate_entry(entry: dict) -> None:
    required = {
        "source_entry_id", "user_id", "node_id", "pool_id", "protocol",
        "minimum_plan", "new_uuid", "new_email", "new_uri", "old_runtime_ref_hash",
    }
    if not isinstance(entry, dict) or not required.issubset(entry):
        fail("plan_entry_invalid")
    if entry["protocol"].lower() != "vless" or entry["pool_id"] not in {"STANDARD", "PREMIUM"}:
        fail("only_xray_vless_is_supported")
    if len(entry["old_runtime_ref_hash"]) != 64 or any(
        char not in "0123456789abcdef" for char in entry["old_runtime_ref_hash"].lower()
    ):
        fail("old_runtime_hash_invalid")
    try:
        parsed = urlsplit(entry["new_uri"])
        parsed_uuid = parsed.username
        uuid.UUID(entry["new_uuid"])
        if parsed.scheme.lower() != "vless" or parsed_uuid != entry["new_uuid"]:
            fail("new_uri_identity_mismatch")
    except ValueError as exc:
        raise RuntimeError("new_uri_invalid") from exc
    if not entry["new_email"] or len(entry["new_email"]) > 128 or any(
        char.isspace() for char in entry["new_email"]
    ):
        fail("new_email_invalid")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    args = parser.parse_args()
    try:
        plan = load_plan()
        for entry in plan["entries"]:
            validate_entry(entry)
        db = sqlite3.connect(args.db)
        db.row_factory = sqlite3.Row
        legacy_updated = 0
        credentials_created = 0
        subscriptions_created = 0
        seen_targets: set[tuple[str, str]] = set()
        with db:
            for entry in plan["entries"]:
                source = db.execute(
                    "SELECT * FROM subscription_entries WHERE entry_id=?",
                    (entry["source_entry_id"],),
                ).fetchone()
                if source is None:
                    fail("source_subscription_missing")
                if (source["user_id"] != plan.get("legacy_user_id", "usr_plus_manual_01")
                        or source["node_id"] != entry["node_id"]
                        or source["pool_id"] != entry["pool_id"]
                        or source["protocol"].lower() != "vless"):
                    fail("source_subscription_mismatch")
                old_credential = db.execute(
                    """SELECT credential_id,user_id FROM credentials
                       WHERE node_id=? AND runtime_ref_hash=?""",
                    (entry["node_id"], entry["old_runtime_ref_hash"].lower()),
                ).fetchone()
                if old_credential is None:
                    fail("source_credential_missing")
                if old_credential["user_id"] not in {None, source["user_id"]}:
                    fail("source_credential_owner_mismatch")
                db.execute(
                    """UPDATE subscription_entries
                       SET credential_id=?,projection_status='legacy'
                       WHERE entry_id=?""",
                    (old_credential["credential_id"], entry["source_entry_id"]),
                )
                legacy_updated += 1

                new_hash = hashlib.sha256(entry["new_email"].encode("utf-8")).hexdigest()
                target_key = (entry["node_id"], new_hash)
                if target_key in seen_targets:
                    fail("duplicate_target_identity")
                seen_targets.add(target_key)
                credential = db.execute(
                    """SELECT credential_id,user_id,protocol,status FROM credentials
                       WHERE node_id=? AND runtime_ref_hash=?""",
                    target_key,
                ).fetchone()
                if credential is None:
                    credential_id = "cred_" + uuid.uuid4().hex
                    db.execute(
                        """INSERT INTO credentials(
                               credential_id,node_id,user_id,runtime_ref_hash,runtime_family,protocol,
                               credential_kind,status,created_at
                           ) VALUES (?,?,?,?,?,?,?,?,?)""",
                        (credential_id, entry["node_id"], entry["user_id"], new_hash,
                         "xray", "vless", "managed", "active", utc_now()),
                    )
                    credentials_created += 1
                else:
                    if credential["user_id"] != entry["user_id"] or credential["protocol"].lower() != "vless":
                        fail("target_credential_owner_mismatch")
                    credential_id = credential["credential_id"]
                    db.execute(
                        "UPDATE credentials SET status='active',credential_kind='managed' WHERE credential_id=?",
                        (credential_id,),
                    )

                existing = db.execute(
                    """SELECT entry_id FROM subscription_entries
                       WHERE user_id=? AND credential_id=?""",
                    (entry["user_id"], credential_id),
                ).fetchone()
                if existing:
                    db.execute(
                        """UPDATE subscription_entries
                           SET node_id=?,pool_id=?,protocol='vless',uri=?,minimum_plan=?,
                               projection_status='current',enabled=1
                           WHERE entry_id=?""",
                        (entry["node_id"], entry["pool_id"], entry["new_uri"],
                         entry["minimum_plan"], existing["entry_id"]),
                    )
                else:
                    db.execute(
                        """INSERT INTO subscription_entries(
                               entry_id,user_id,node_id,pool_id,credential_id,protocol,uri,minimum_plan,
                               projection_status,enabled,created_at
                           ) VALUES (?,?,?,?,?,?,?,?,?,1,?)""",
                        ("sub_" + uuid.uuid4().hex, entry["user_id"], entry["node_id"], entry["pool_id"],
                         credential_id, "vless", entry["new_uri"], entry["minimum_plan"],
                         "current", utc_now()),
                    )
                    subscriptions_created += 1
        db.close()
        print(json.dumps({
            "ok": True,
            "legacy_entries_updated": legacy_updated,
            "managed_credentials_created": credentials_created,
            "current_subscriptions_created": subscriptions_created,
        }, separators=(",", ":")))
        return 0
    except (RuntimeError, sqlite3.Error) as exc:
        print(json.dumps({"ok": False, "error": str(exc)[:120]}, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
