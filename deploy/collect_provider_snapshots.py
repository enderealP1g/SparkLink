#!/usr/bin/env python3
"""Collect and record normalized provider-resource telemetry.

This is a management-plane operator command.  It reads the existing resource
inventory from the private Control Plane, applies the provider adapter/source
contract, and appends one snapshot per known resource.  It never reads proxy
credentials and never touches Node runtime configuration.

Without an authorized provider API, stable endpoint, or operator dashboard
export, the registered adapter records an explicit ``unknown`` snapshot.  A
non-secret JSON export can be supplied with ``--file``; values are validated
before they reach the Control Plane and are never printed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deploy import issue_user_tokens as operator  # noqa: E402
from src.sparklink_provider_telemetry import (  # noqa: E402
    ProviderTelemetryError,
    adapter_for,
    normalize_snapshot,
)


class SnapshotCollectorError(RuntimeError):
    """A safe, non-secret operator error code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _load_source_file(path: Path | None) -> dict[str, dict]:
    if path is None:
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SnapshotCollectorError("source_file_invalid") from exc
    if isinstance(value, dict) and "snapshots" in value:
        snapshots = value["snapshots"]
        if value.get("schema") not in {None, "sparklink.provider-telemetry.v1"}:
            raise SnapshotCollectorError("source_file_schema_invalid")
    elif isinstance(value, list):
        snapshots = value
    elif isinstance(value, dict):
        snapshots = [value]
    else:
        raise SnapshotCollectorError("source_file_invalid")
    if not isinstance(snapshots, list):
        raise SnapshotCollectorError("source_file_invalid")
    result: dict[str, dict] = {}
    for item in snapshots:
        if not isinstance(item, dict):
            raise SnapshotCollectorError("source_snapshot_invalid")
        try:
            normalized = normalize_snapshot(item)
        except ProviderTelemetryError as exc:
            raise SnapshotCollectorError(exc.args[0] or "source_snapshot_invalid") from exc
        resource_id = normalized["resource_id"]
        if resource_id in result:
            raise SnapshotCollectorError("source_snapshot_duplicate")
        result[resource_id] = normalized
    return result


def _resources(overview: dict) -> list[dict]:
    resources = overview.get("infrastructure_resources")
    if not isinstance(resources, list) or not resources:
        raise SnapshotCollectorError("resource_inventory_missing")
    result = []
    seen: set[str] = set()
    for resource in resources:
        if not isinstance(resource, dict) or not isinstance(resource.get("resource_id"), str):
            raise SnapshotCollectorError("resource_inventory_invalid")
        resource_id = resource["resource_id"]
        if resource_id in seen:
            raise SnapshotCollectorError("resource_inventory_duplicate")
        seen.add(resource_id)
        try:
            adapter_for(resource.get("provider_name"))
        except ProviderTelemetryError as exc:
            raise SnapshotCollectorError(exc.args[0] or "provider_adapter_unavailable") from exc
        result.append(resource)
    return result


def _snapshot_for(resource: dict, supplied: dict[str, dict]) -> dict:
    resource_id = resource["resource_id"]
    if resource_id not in supplied:
        return adapter_for(resource["provider_name"]).unknown_snapshot(resource)
    try:
        return normalize_snapshot(supplied[resource_id], resource_id=resource_id)
    except ProviderTelemetryError as exc:
        raise SnapshotCollectorError(exc.args[0] or "source_snapshot_invalid") from exc


def collect(args: argparse.Namespace) -> int:
    source = _load_source_file(Path(args.file) if args.file else None)
    admin_token = operator._admin_token(Path(args.secret_path))
    safe_results: list[dict[str, str]] = []
    recorded = 0
    failed = 0
    with operator.selected_endpoint(args) as endpoint:
        overview = operator.admin_json(endpoint, admin_token, "/api/admin/overview", "GET")
        resources = _resources(overview)
        expected_ids = {resource["resource_id"] for resource in resources}
        unexpected_ids = set(source) - expected_ids
        if unexpected_ids:
            raise SnapshotCollectorError("source_resource_not_in_inventory")
        for resource in resources:
            snapshot = _snapshot_for(resource, source)
            provider_key = adapter_for(resource["provider_name"]).key
            if args.dry_run:
                result = "dry_run"
            else:
                try:
                    response = operator.admin_json(
                        endpoint, admin_token, "/api/admin/provider-snapshot", "POST", snapshot,
                    )
                    if not isinstance(response.get("snapshot_id"), str):
                        raise SnapshotCollectorError("snapshot_response_invalid")
                    recorded += 1
                    result = "recorded"
                except (operator.OperatorError, SnapshotCollectorError) as exc:
                    failed += 1
                    result = getattr(exc, "code", "snapshot_record_failed")
            safe_results.append({
                "provider": provider_key,
                "status": str(snapshot["status"]),
                "result": result,
            })
    print(json.dumps({
        "ok": failed == 0,
        "resources": len(safe_results),
        "recorded": recorded,
        "failed": failed,
        "source_file_used": bool(args.file),
        "results": safe_results,
        "secret_not_printed": True,
    }, separators=(",", ":")))
    return 0 if failed == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Record safe provider resource telemetry snapshots")
    parser.add_argument(
        "--file",
        type=Path,
        help="optional non-secret JSON export; missing resources become explicit Unknown snapshots",
    )
    parser.add_argument("--dry-run", action="store_true", help="validate and preview without writing snapshots")
    parser.add_argument("--secret-path", type=Path, default=operator.DEFAULT_SECRET_PATH)
    operator.add_endpoint_options(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        return collect(build_parser().parse_args(argv))
    except SnapshotCollectorError as exc:
        print(json.dumps({"ok": False, "error": exc.code, "secret_not_printed": True}, separators=(",", ":")))
        return 1
    except (operator.OperatorError, ProviderTelemetryError) as exc:
        code = getattr(exc, "code", None) or (exc.args[0] if exc.args else "provider_snapshot_failed")
        print(json.dumps({"ok": False, "error": code, "secret_not_printed": True}, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
