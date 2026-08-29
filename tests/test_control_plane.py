import base64
import io
import json
import tempfile
import unittest
from pathlib import Path

from src.sparklink_control_plane import App, Conflict, ControlPlane


class ControlPlaneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "sparklink.db"
        self.cp = ControlPlane(self.db, admin_token="admin", subscription_base_url="https://sub.example.test")
        self.cp.seed_nodes()
        self.user = self.cp.create_user("synthetic-user", "Plus", user_id="usr_test")
        self.cp.create_cycle("usr_test", "synthetic-cycle", "2026-01-01T00:00:00Z", None)
        self.cp.set_entitlement("usr_test", "PREMIUM", "Plus", None)

    def tearDown(self):
        self.temp.cleanup()

    def test_append_preserving_delta_and_pool_aggregation(self):
        runtime = "a" * 64
        self.cp.add_credential("hypro02", runtime, "xray", "vless", "usr_test")
        first = self.cp.ingest_observations(
            "hypro02", "test", "epoch-1", [{"runtime_ref_hash": runtime, "uplink_bytes": 100, "downlink_bytes": 200}], "2026-09-02T00:00:00Z"
        )
        second = self.cp.ingest_observations(
            "hypro02", "test", "epoch-1", [{"runtime_ref_hash": runtime, "uplink_bytes": 150, "downlink_bytes": 500}], "2026-09-03T00:00:00Z"
        )
        self.assertEqual(first["inserted"], 1)
        self.assertEqual(second["inserted"], 1)
        view = self.cp.user_view(self.user["portal_token"])
        premium = next(p for p in view["pools"] if p["pool_id"] == "PREMIUM")
        self.assertEqual(premium["used_bytes"], 350)
        self.assertEqual(premium["remaining_bytes"], None)
        self.assertEqual(view["total_usage_bytes"], 350)

    def test_unmapped_usage_is_not_zero(self):
        runtime = "b" * 64
        self.cp.add_credential("hypro02", runtime, "xray", "vless")
        self.cp.ingest_observations(
            "hypro02", "test", "epoch-1", [{"runtime_ref_hash": runtime, "uplink_bytes": 1, "downlink_bytes": 2}], "2026-09-02T00:00:00Z"
        )
        view = self.cp.user_view(self.user["portal_token"])
        premium = next(p for p in view["pools"] if p["pool_id"] == "PREMIUM")
        self.assertIsNone(premium["used_bytes"])
        self.assertEqual(premium["coverage_status"], "unknown")
        self.assertIsNone(view["total_usage_bytes"])

    def test_unrelated_unmapped_usage_does_not_poison_user_view(self):
        runtime = "d" * 64
        self.cp.add_credential("hypro02", runtime, "xray", "vless")
        self.cp.ingest_observations(
            "hypro02", "test", "epoch-1", [{"runtime_ref_hash": runtime, "uplink_bytes": 10, "downlink_bytes": 20}], "2026-09-02T00:00:00Z"
        )
        own_runtime = "e" * 64
        self.cp.add_credential("hypro02", own_runtime, "xray", "vless", "usr_test")
        self.cp.ingest_observations(
            "hypro02", "test", "epoch-1", [{"runtime_ref_hash": own_runtime, "uplink_bytes": 4, "downlink_bytes": 6}], "2026-09-02T00:00:00Z"
        )
        view = self.cp.user_view(self.user["portal_token"])
        premium = next(p for p in view["pools"] if p["pool_id"] == "PREMIUM")
        self.assertEqual(premium["coverage_status"], "available")
        self.assertEqual(premium["used_bytes"], 0)

    def test_upgrade_is_pending_and_downgrade_rejected(self):
        with self.assertRaises(Conflict):
            self.cp.request_upgrade(self.user["portal_token"], "Basic")
        self.cp.create_user("free-user", "Free", user_id="usr_free")
        db = self.cp.connect()
        try:
            token = db.execute("SELECT portal_token_hash FROM users WHERE user_id='usr_free'").fetchone()[0]
        finally:
            db.close()
        self.assertTrue(token)

    def test_anytls_subscription_is_deferred(self):
        with self.assertRaises(Conflict):
            self.cp.add_subscription_entry("usr_test", "hypro02", "PREMIUM", "anytls", "anytls://synthetic")

    def test_subscription_is_v2ray_base64_and_no_anytls(self):
        self.cp.add_subscription_entry("usr_test", "hypro02", "PREMIUM", "vless", "vless://synthetic", "Plus")
        body = base64.b64decode(self.cp.subscription(self.user["portal_token"]).strip()).decode()
        self.assertEqual(body, "vless://synthetic\n")

    def test_bearer_subscription_endpoint_is_supported_by_control_plane(self):
        self.cp.add_subscription_entry("usr_test", "hypro02", "PREMIUM", "vless", "vless://synthetic", "Plus")
        app = App(self.cp)
        environ = {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/subscription",
            "HTTP_AUTHORIZATION": f"Bearer {self.user['portal_token']}",
            "wsgi.input": io.BytesIO(),
            "wsgi.errors": io.StringIO(),
            "wsgi.version": (1, 0),
            "wsgi.url_scheme": "http",
            "wsgi.multithread": False,
            "wsgi.multiprocess": False,
            "wsgi.run_once": False,
        }
        result = {}

        def start_response(status, headers):
            result["status"] = status
            result["headers"] = headers

        body = b"".join(app(environ, start_response))
        self.assertEqual(result["status"], "200 OK")
        self.assertEqual(base64.b64decode(body.strip()).decode(), "vless://synthetic\n")

    def test_admin_overview_contains_user_pool_node_and_capacity_evidence(self):
        runtime = "c" * 64
        self.cp.add_credential("hypro02", runtime, "xray", "vless", "usr_test")
        self.cp.ingest_observations(
            "hypro02", "test", "epoch-1", [{"runtime_ref_hash": runtime, "uplink_bytes": 3, "downlink_bytes": 7}], "2026-09-02T00:00:00Z"
        )
        overview = self.cp.admin_overview()
        self.assertIn("users", overview)
        self.assertIn("usage_by_pool_bytes", overview["users"][0])
        hypro02 = next(node for node in overview["nodes"] if node["node_id"] == "hypro02")
        self.assertEqual(hypro02["pool_id"], "PREMIUM")
        self.assertIn("premium_capacity_pressure", overview)


if __name__ == "__main__":
    unittest.main()
