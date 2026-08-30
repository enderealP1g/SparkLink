import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from deploy import collect_provider_snapshots as collector
from src.sparklink_provider_telemetry import (
    ProviderTelemetryError,
    adapter_for,
    normalize_snapshot,
)


class ProviderTelemetryTests(unittest.TestCase):
    def resource(self, provider_name="DediRock"):
        return {"resource_id": "resource-dedirock", "provider_name": provider_name}

    def test_all_current_providers_have_registered_source_priority(self):
        for name in ("RackNerd", "VMISS", "QQGNet", "DediRock"):
            adapter = adapter_for(name)
            self.assertEqual(adapter.source_priority, ("official_api", "stable_endpoint", "dashboard_export"))

    def test_missing_authorized_source_is_explicit_unknown_without_values(self):
        snapshot = adapter_for("DediRock").unknown_snapshot(self.resource())
        self.assertEqual(snapshot["status"], "unknown")
        self.assertEqual(snapshot["source"], "provider-adapter:dedirock:no-authorized-source")
        self.assertIsNone(snapshot["capacity_bytes"])
        self.assertIsNone(snapshot["used_bytes"])
        self.assertIsNone(snapshot["remaining_bytes"])
        self.assertIsNone(snapshot["next_reset_at"])
        self.assertIn("not inferred", snapshot["detail"])

    def test_authoritative_available_snapshot_is_normalized_and_checked(self):
        value = normalize_snapshot({
            "resource_id": "resource-dedirock",
            "observed_at": "2026-08-30T01:02:03+02:00",
            "source": "provider official API",
            "status": "available",
            "capacity_bytes": 100,
            "used_bytes": 30,
            "remaining_bytes": 70,
            "resource_cycle_start": "2026-08-01T00:00:00Z",
            "resource_cycle_end": "2026-09-01T00:00:00Z",
            "next_reset_at": "2026-09-01T00:00:00Z",
            "financial_cycle": "annual",
            "next_due_at": "2027-07-08T00:00:00Z",
            "detail": "authoritative source",
        })
        self.assertEqual(value["observed_at"], "2026-08-29T23:02:03.000000Z")
        self.assertEqual(value["remaining_bytes"], 70)

    def test_unknown_and_available_validation_fails_closed(self):
        with self.assertRaisesRegex(ProviderTelemetryError, "unknown_snapshot_must_not_have_bytes"):
            normalize_snapshot({
                "resource_id": "resource-dedirock", "observed_at": "2026-08-30T00:00:00Z",
                "source": "test", "status": "unknown", "used_bytes": 0,
            })
        with self.assertRaisesRegex(ProviderTelemetryError, "available_snapshot_requires_bytes"):
            normalize_snapshot({
                "resource_id": "resource-dedirock", "observed_at": "2026-08-30T00:00:00Z",
                "source": "test", "status": "available", "capacity_bytes": 100,
            })
        with self.assertRaisesRegex(ProviderTelemetryError, "snapshot_bytes_inconsistent"):
            normalize_snapshot({
                "resource_id": "resource-dedirock", "observed_at": "2026-08-30T00:00:00Z",
                "source": "test", "status": "available", "capacity_bytes": 100,
                "used_bytes": 30, "remaining_bytes": 30,
            })
        with self.assertRaisesRegex(ProviderTelemetryError, "snapshot_fields_not_allowed"):
            normalize_snapshot({
                "resource_id": "resource-dedirock", "observed_at": "2026-08-30T00:00:00Z",
                "source": "test", "status": "unknown", "token": "must-not-enter",
            })
        with self.assertRaisesRegex(ProviderTelemetryError, "snapshot_detail_must_not_contain_credentials"):
            normalize_snapshot({
                "resource_id": "resource-dedirock", "observed_at": "2026-08-30T00:00:00Z",
                "source": "test", "status": "unknown", "detail": "access_token=must-not-enter",
            })

    def test_collector_records_unknown_for_every_inventory_resource_without_source_file(self):
        overview = {"infrastructure_resources": [
            {"resource_id": "r1", "provider_name": "RackNerd"},
            {"resource_id": "r2", "provider_name": "VMISS"},
            {"resource_id": "r3", "provider_name": "QQGNet"},
            {"resource_id": "r4", "provider_name": "DediRock"},
        ]}
        calls = []

        def fake_admin(_endpoint, _token, path, method, body=None):
            if path == "/api/admin/overview":
                return overview
            calls.append((path, method, body))
            return {"snapshot_id": "snapshot"}

        args = SimpleNamespace(
            file=None, dry_run=False, secret_path=Path("admin.dpapi"),
            endpoint="http://127.0.0.1:1", ssh_host="unused", forward_port=1,
        )
        output = io.StringIO()
        with patch.object(collector.operator, "_admin_token", return_value="admin"), \
             patch.object(collector.operator, "admin_json", side_effect=fake_admin), \
             patch.object(collector.operator, "selected_endpoint", return_value=contextlib.nullcontext("http://127.0.0.1:1")), \
             patch("sys.stdout", output):
            result = collector.collect(args)
        self.assertEqual(result, 0)
        self.assertEqual(len(calls), 4)
        self.assertTrue(all(call[2]["status"] == "unknown" for call in calls))
        self.assertTrue(all(call[2]["capacity_bytes"] is None for call in calls))
        self.assertNotIn("admin", output.getvalue())
        self.assertNotIn("must-not-enter", output.getvalue())

    def test_source_file_can_cover_one_resource_and_fill_other_resources_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "provider-export.json"
            path.write_text(json.dumps({
                "schema": "sparklink.provider-telemetry.v1",
                "snapshots": [{
                    "resource_id": "resource-dedirock",
                    "observed_at": "2026-08-30T00:00:00Z",
                    "source": "operator dashboard export",
                    "status": "unknown",
                    "detail": "dashboard did not expose authoritative traffic data",
                }],
            }), encoding="utf-8")
            loaded = collector._load_source_file(path)
        self.assertEqual(loaded["resource-dedirock"]["status"], "unknown")
        self.assertEqual(loaded["resource-dedirock"]["source"], "operator dashboard export")


if __name__ == "__main__":
    unittest.main()
