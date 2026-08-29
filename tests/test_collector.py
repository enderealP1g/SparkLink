import unittest
from unittest.mock import patch

from src import sparklink_xray_collector as collector


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "endpoint": "https://control.example.test",
            "nodes": [
                {"node_id": "node-a", "ssh_host": "alias-a"},
                {"node_id": "node-b", "ssh_host": "alias-b"},
            ],
        }

    def test_partial_failure_records_gap_and_continues_other_nodes(self):
        successful = {
            "ok": True,
            "counter_epoch": "epoch-a",
            "observed_at": "2026-09-01T00:00:00Z",
            "observations": [{
                "runtime_ref_hash": "a" * 64,
                "uplink_bytes": 10,
                "downlink_bytes": 20,
            }],
        }
        with patch.object(collector, "remote_stats", side_effect=[successful, collector.CollectorError("ssh_or_remote_query_failed")]), \
                patch.object(collector, "post_json", return_value={"inserted": 1, "duplicates": 0, "unresolved": 0}), \
                patch.object(collector, "post_coverage", return_value={} ) as coverage:
            summary = collector.run_once(self.config, "https://control.example.test", "admin")

        self.assertEqual(summary["attempted"], 2)
        self.assertEqual(summary["ingested"], 1)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["nodes"][0]["status"], "ingested")
        self.assertEqual(summary["nodes"][1]["reason"], "ssh_or_remote_query_failed")
        coverage.assert_called_once_with(
            "https://control.example.test", "admin", "node-b", "gap", "ssh_or_remote_query_failed"
        )

    def test_empty_source_is_unknown_and_never_ingested_as_zero(self):
        result = {
            "ok": True,
            "counter_epoch": "epoch-a",
            "observed_at": "2026-09-01T00:00:00Z",
            "observations": [],
        }
        with patch.object(collector, "remote_stats", return_value=result), \
                patch.object(collector, "post_json") as post, \
                patch.object(collector, "post_coverage", return_value={}) as coverage:
            value = collector.collect_node("https://control.example.test", "admin", self.config["nodes"][0])

        self.assertEqual(value["status"], "unknown")
        self.assertEqual(value["reason"], "no_per_user_counters")
        post.assert_not_called()
        coverage.assert_called_once()
        self.assertEqual(coverage.call_args.args[2:4], ("node-a", "unknown"))

    def test_remote_gap_is_recorded_without_exposing_exception_detail(self):
        with patch.object(collector, "remote_stats", return_value={"ok": False, "error": "statsquery_failed"}), \
                patch.object(collector, "post_coverage", return_value={}) as coverage:
            value = collector.collect_node("https://control.example.test", "admin", self.config["nodes"][0])

        self.assertEqual(value, {
            "node_id": "node-a",
            "status": "gap",
            "reason": "statsquery_failed",
            "coverage_recorded": True,
        })
        coverage.assert_called_once_with(
            "https://control.example.test", "admin", "node-a", "gap", "statsquery_failed"
        )

    def test_success_uses_remote_observation_time_and_counter_epoch(self):
        result = {
            "ok": True,
            "counter_epoch": "epoch-a",
            "observed_at": "2026-09-01T00:00:00.123456Z",
            "observations": [{
                "runtime_ref_hash": "b" * 64,
                "uplink_bytes": 7,
                "downlink_bytes": 9,
            }],
        }
        with patch.object(collector, "remote_stats", return_value=result), \
                patch.object(collector, "post_json", return_value={"inserted": 1}) as post:
            value = collector.collect_node("https://control.example.test", "admin", self.config["nodes"][0])

        payload = post.call_args.args[2]
        self.assertEqual(payload["counter_epoch"], "epoch-a")
        self.assertEqual(payload["observed_at"], "2026-09-01T00:00:00.123456Z")
        self.assertEqual(value["status"], "ingested")

    def test_partial_counter_direction_is_gap_and_never_zero_filled(self):
        result = {
            "ok": True,
            "counter_epoch": "epoch-a",
            "observed_at": "2026-09-01T00:00:00Z",
            "observations": [{
                "runtime_ref_hash": "c" * 64,
                "uplink_bytes": 12,
            }],
        }
        with patch.object(collector, "remote_stats", return_value=result), \
                patch.object(collector, "post_json") as post, \
                patch.object(collector, "post_coverage", return_value={}) as coverage:
            value = collector.collect_node("https://control.example.test", "admin", self.config["nodes"][0])

        self.assertEqual(value["status"], "gap")
        self.assertEqual(value["reason"], "partial_per_user_counters")
        post.assert_not_called()
        coverage.assert_called_once_with(
            "https://control.example.test", "admin", "node-a", "gap", "partial_per_user_counters"
        )

    def test_duplicate_runtime_ref_is_gap_and_not_double_counted(self):
        result = {
            "ok": True,
            "counter_epoch": "epoch-a",
            "observed_at": "2026-09-01T00:00:00Z",
            "observations": [
                {"runtime_ref_hash": "d" * 64, "uplink_bytes": 1, "downlink_bytes": 2},
                {"runtime_ref_hash": "d" * 64, "uplink_bytes": 3, "downlink_bytes": 4},
            ],
        }
        with patch.object(collector, "remote_stats", return_value=result), \
                patch.object(collector, "post_json") as post, \
                patch.object(collector, "post_coverage", return_value={}) as coverage:
            value = collector.collect_node("https://control.example.test", "admin", self.config["nodes"][0])

        self.assertEqual(value["status"], "gap")
        self.assertEqual(value["reason"], "duplicate_runtime_ref")
        post.assert_not_called()
        coverage.assert_called_once()


if __name__ == "__main__":
    unittest.main()
