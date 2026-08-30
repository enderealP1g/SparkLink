#!/usr/bin/env python3
"""Import one normalized, source-labelled provider snapshot.

The input contains provider telemetry only, not tokens or runtime credentials.
It is supplied from a trusted provider API/dashboard export and is sent to the
private Control Plane through the existing operator path. Missing or uncertain
provider facts should be recorded with ``status=unknown`` rather than guessed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deploy import issue_user_tokens as operator  # noqa: E402
from src.sparklink_provider_telemetry import ProviderTelemetryError, normalize_snapshot  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record a source-labelled SparkLink provider snapshot")
    parser.add_argument("--file", type=Path, help="JSON snapshot file; otherwise read JSON from stdin")
    parser.add_argument("--secret-path", type=Path, default=operator.DEFAULT_SECRET_PATH)
    operator.add_endpoint_options(parser)
    args = parser.parse_args(argv)
    try:
        if args.file:
            snapshot = json.loads(args.file.read_text(encoding="utf-8"))
        else:
            snapshot = json.load(sys.stdin)
        if not isinstance(snapshot, dict):
            raise operator.OperatorError("snapshot_invalid")
        try:
            snapshot = normalize_snapshot(snapshot)
        except ProviderTelemetryError as exc:
            raise operator.OperatorError(exc.args[0] or "snapshot_invalid") from exc
        admin_token = operator._admin_token(Path(args.secret_path))
        with operator.selected_endpoint(args) as endpoint:
            result = operator.admin_json(
                endpoint, admin_token, "/api/admin/provider-snapshot", "POST", snapshot,
            )
        if not isinstance(result.get("snapshot_id"), str):
            raise operator.OperatorError("snapshot_response_invalid")
        print(json.dumps({"ok": True, "snapshot_id": result["snapshot_id"], "secret_not_printed": True}, separators=(",", ":")))
        return 0
    except (OSError, UnicodeError, json.JSONDecodeError):
        print(json.dumps({"ok": False, "error": "snapshot_input_invalid"}, separators=(",", ":")))
        return 1
    except operator.OperatorError as exc:
        print(json.dumps({"ok": False, "error": exc.code}, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
