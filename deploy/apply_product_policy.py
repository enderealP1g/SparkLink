#!/usr/bin/env python3
"""Apply a non-secret, reversible Product Operations policy payload.

This tool changes only Control Plane metadata: pool entitlements, node
admission capability, user access overrides, and policy-only allowances. It
does not change a Node runtime, revoke legacy access, or enable hard quota.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deploy import issue_user_tokens as operator  # noqa: E402


def load_payload(path: Path | None) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8")) if path else json.load(sys.stdin)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise operator.OperatorError("policy_input_invalid") from exc
    if not isinstance(value, dict):
        raise operator.OperatorError("policy_input_invalid")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply non-secret SparkLink Product Operations policy")
    parser.add_argument("--file", type=Path)
    parser.add_argument("--secret-path", type=Path, default=operator.DEFAULT_SECRET_PATH)
    operator.add_endpoint_options(parser)
    args = parser.parse_args(argv)
    try:
        payload = load_payload(args.file)
        admin_token = operator._admin_token(Path(args.secret_path))
        counts = {"entitlements": 0, "node_admissions": 0, "access_overrides": 0, "operational_budgets": 0}
        with operator.selected_endpoint(args) as endpoint:
            for item in payload.get("entitlements", []):
                operator.admin_json(endpoint, admin_token, "/api/admin/entitlement", "POST", item)
                counts["entitlements"] += 1
            for item in payload.get("node_admissions", []):
                operator.admin_json(endpoint, admin_token, "/api/admin/node-admission", "POST", item)
                counts["node_admissions"] += 1
            for item in payload.get("access_overrides", []):
                operator.admin_json(endpoint, admin_token, "/api/admin/access-override", "POST", item)
                counts["access_overrides"] += 1
            for item in payload.get("operational_budgets", []):
                operator.admin_json(endpoint, admin_token, "/api/admin/operational-budget", "POST", item)
                counts["operational_budgets"] += 1
        print(json.dumps({"ok": True, **counts, "hard_quota": "disabled", "runtime_changed": False}, separators=(",", ":")))
        return 0
    except operator.OperatorError as exc:
        print(json.dumps({"ok": False, "error": exc.code}, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
