import base64
import io
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.sparklink_control_plane import (
    App,
    Conflict,
    ControlPlane,
    ControlPlaneError,
    CUSTOMER_CYCLE_BASELINE,
    CUSTOMER_CYCLE_POLICY_ID,
    Unauthorized,
    customer_cycle_window,
    token_hash,
)


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

    def test_counter_reset_same_epoch_does_not_create_negative_or_fake_delta(self):
        runtime = "f" * 64
        self.cp.add_credential("hypro02", runtime, "xray", "vless", "usr_test")
        self.cp.ingest_observations(
            "hypro02", "test", "epoch-1",
            [{"runtime_ref_hash": runtime, "uplink_bytes": 100, "downlink_bytes": 200}],
            "2026-09-02T00:00:00Z",
        )
        self.cp.ingest_observations(
            "hypro02", "test", "epoch-1",
            [{"runtime_ref_hash": runtime, "uplink_bytes": 50, "downlink_bytes": 250}],
            "2026-09-02T00:01:00Z",
        )
        self.cp.ingest_observations(
            "hypro02", "test", "epoch-1",
            [{"runtime_ref_hash": runtime, "uplink_bytes": 70, "downlink_bytes": 300}],
            "2026-09-02T00:02:00Z",
        )
        view = self.cp.user_view(self.user["portal_token"])
        premium = next(p for p in view["pools"] if p["pool_id"] == "PREMIUM")
        self.assertEqual(premium["used_bytes"], 70)
        db = self.cp.connect()
        try:
            details = [row[0] for row in db.execute(
                "SELECT detail FROM usage_ledger WHERE node_id='hypro02' ORDER BY observed_at"
            )]
        finally:
            db.close()
        self.assertEqual(details, ["baseline", "counter_reset_or_non_monotonic", "delta"])

    def test_duplicate_observation_is_idempotent_and_conflict_is_rejected(self):
        runtime = "c" * 64
        self.cp.add_credential("hypro02", runtime, "xray", "vless", "usr_test")
        body = [{
            "observation_id": "fixed-observation",
            "runtime_ref_hash": runtime,
            "uplink_bytes": 10,
            "downlink_bytes": 20,
        }]
        first = self.cp.ingest_observations("hypro02", "test", "epoch-1", body, "2026-09-02T00:00:00Z")
        duplicate = self.cp.ingest_observations("hypro02", "test", "epoch-1", body, "2026-09-02T00:00:00Z")
        self.assertEqual(first["inserted"], 1)
        self.assertEqual(duplicate["inserted"], 0)
        self.assertEqual(duplicate["duplicates"], 1)
        with self.assertRaises(Conflict):
            self.cp.ingest_observations(
                "hypro02", "test", "epoch-1",
                [{**body[0], "uplink_bytes": 11}],
                "2026-09-02T00:00:00Z",
            )

    def test_out_of_order_observation_does_not_double_count_later_delta(self):
        runtime = "d" * 64
        self.cp.add_credential("hypro02", runtime, "xray", "vless", "usr_test")
        self.cp.ingest_observations(
            "hypro02", "test", "epoch-1",
            [{"runtime_ref_hash": runtime, "uplink_bytes": 200, "downlink_bytes": 200}],
            "2026-09-02T00:02:00Z",
        )
        self.cp.ingest_observations(
            "hypro02", "test", "epoch-1",
            [{"runtime_ref_hash": runtime, "uplink_bytes": 100, "downlink_bytes": 100}],
            "2026-09-02T00:01:00Z",
        )
        self.cp.ingest_observations(
            "hypro02", "test", "epoch-1",
            [{"runtime_ref_hash": runtime, "uplink_bytes": 250, "downlink_bytes": 250}],
            "2026-09-02T00:03:00Z",
        )
        view = self.cp.user_view(self.user["portal_token"])
        premium = next(p for p in view["pools"] if p["pool_id"] == "PREMIUM")
        self.assertEqual(premium["used_bytes"], 100)
        db = self.cp.connect()
        try:
            details = [row[0] for row in db.execute(
                "SELECT detail FROM usage_ledger WHERE node_id='hypro02' ORDER BY observed_at"
            )]
        finally:
            db.close()
        self.assertEqual(details, ["out_of_order_observation", "baseline", "delta"])

    def test_new_counter_epoch_starts_baseline_and_preserves_previous_ledger(self):
        runtime = "9" * 64
        self.cp.add_credential("hypro02", runtime, "xray", "vless", "usr_test")
        self.cp.ingest_observations(
            "hypro02", "test", "process-1",
            [{"runtime_ref_hash": runtime, "uplink_bytes": 100, "downlink_bytes": 200}],
            "2026-09-02T00:00:00Z",
        )
        self.cp.ingest_observations(
            "hypro02", "test", "process-1",
            [{"runtime_ref_hash": runtime, "uplink_bytes": 150, "downlink_bytes": 260}],
            "2026-09-02T00:01:00Z",
        )
        self.cp.ingest_observations(
            "hypro02", "test", "process-2",
            [{"runtime_ref_hash": runtime, "uplink_bytes": 7, "downlink_bytes": 11}],
            "2026-09-02T00:02:00Z",
        )
        self.cp.ingest_observations(
            "hypro02", "test", "process-2",
            [{"runtime_ref_hash": runtime, "uplink_bytes": 17, "downlink_bytes": 31}],
            "2026-09-02T00:03:00Z",
        )
        view = self.cp.user_view(self.user["portal_token"])
        premium = next(p for p in view["pools"] if p["pool_id"] == "PREMIUM")
        self.assertEqual(premium["used_bytes"], 140)
        db = self.cp.connect()
        try:
            rows = db.execute(
                "SELECT o.counter_epoch,l.detail,l.delta_uplink_bytes,l.delta_downlink_bytes "
                "FROM usage_ledger l JOIN usage_observations o ON o.observation_id=l.observation_id "
                "ORDER BY l.observed_at"
            ).fetchall()
        finally:
            db.close()
        self.assertEqual(
            [(row[0], row[1], row[2], row[3]) for row in rows],
            [("process-1", "baseline", 0, 0),
             ("process-1", "delta", 50, 60),
             ("process-2", "baseline", 0, 0),
             ("process-2", "delta", 10, 20)],
        )

    def test_repeated_counter_observation_has_zero_delta_without_zeroing_history(self):
        runtime = "8" * 64
        self.cp.add_credential("hypro02", runtime, "xray", "vless", "usr_test")
        self.cp.ingest_observations(
            "hypro02", "test", "epoch-1",
            [{"runtime_ref_hash": runtime, "uplink_bytes": 10, "downlink_bytes": 20}],
            "2026-09-02T00:00:00Z",
        )
        self.cp.ingest_observations(
            "hypro02", "test", "epoch-1",
            [{"runtime_ref_hash": runtime, "uplink_bytes": 15, "downlink_bytes": 25}],
            "2026-09-02T00:01:00Z",
        )
        self.cp.ingest_observations(
            "hypro02", "test", "epoch-1",
            [{"runtime_ref_hash": runtime, "uplink_bytes": 15, "downlink_bytes": 25}],
            "2026-09-02T00:02:00Z",
        )
        view = self.cp.user_view(self.user["portal_token"])
        premium = next(p for p in view["pools"] if p["pool_id"] == "PREMIUM")
        self.assertEqual(premium["used_bytes"], 10)

    def test_stale_coverage_is_not_reported_as_available(self):
        runtime = "e" * 64
        self.cp.add_credential("hypro02", runtime, "xray", "vless", "usr_test")
        old = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        short_window = ControlPlane(self.db, coverage_max_age_seconds=60)
        short_window.set_coverage("hypro02", "test", "available", "old sample", old)
        overview = short_window.admin_overview()
        node = next(item for item in overview["nodes"] if item["node_id"] == "hypro02")
        self.assertEqual(node["coverage_status"], "stale")
        view = short_window.user_view(self.user["portal_token"])
        premium = next(p for p in view["pools"] if p["pool_id"] == "PREMIUM")
        self.assertEqual(premium["coverage_status"], "unknown")
        self.assertIsNone(premium["used_bytes"])

    def test_naive_timestamps_are_rejected_for_coverage_and_observations(self):
        with self.assertRaises(ControlPlaneError):
            self.cp.set_coverage("hypro02", "test", "available", "naive", "2026-09-02T00:00:00")
        runtime = "1" * 64
        self.cp.add_credential("hypro02", runtime, "xray", "vless", "usr_test")
        with self.assertRaises(ControlPlaneError):
            self.cp.ingest_observations(
                "hypro02", "test", "epoch-1",
                [{"runtime_ref_hash": runtime, "uplink_bytes": 1, "downlink_bytes": 1}],
                "2026-09-02T00:00:00",
            )

    def test_incomplete_counter_is_rejected_without_zero_fill(self):
        runtime = "7" * 64
        self.cp.add_credential("hypro02", runtime, "xray", "vless", "usr_test")
        with self.assertRaises(ControlPlaneError):
            self.cp.ingest_observations(
                "hypro02", "test", "epoch-1",
                [{"runtime_ref_hash": runtime, "uplink_bytes": 12}],
                "2026-09-02T00:00:00Z",
            )
        db = self.cp.connect()
        try:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM usage_observations").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM usage_ledger").fetchone()[0], 0)
        finally:
            db.close()

    def test_fractional_counter_is_rejected_without_truncation(self):
        runtime = "6" * 64
        self.cp.add_credential("hypro02", runtime, "xray", "vless", "usr_test")
        with self.assertRaises(ControlPlaneError):
            self.cp.ingest_observations(
                "hypro02", "test", "epoch-1",
                [{"runtime_ref_hash": runtime, "uplink_bytes": 12.5, "downlink_bytes": 3}],
                "2026-09-02T00:00:00Z",
            )
        db = self.cp.connect()
        try:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM usage_observations").fetchone()[0], 0)
        finally:
            db.close()

    def test_customer_cycle_uses_asia_shanghai_15th_boundary(self):
        self.assertIsNone(customer_cycle_window("2026-09-14T15:59:59Z"))
        window = customer_cycle_window("2026-09-14T16:00:00Z")
        self.assertEqual(window, (
            "2026-09-15",
            "2026-09-14T16:00:00Z",
            "2026-10-14T16:00:00Z",
            CUSTOMER_CYCLE_POLICY_ID,
        ))
        self.assertEqual(CUSTOMER_CYCLE_BASELINE.isoformat(), "2026-09-15T00:00:00+08:00")

    def test_customer_cycle_reconciliation_preserves_legacy_usage(self):
        result = self.cp.reconcile_customer_cycles(["usr_test"])
        self.assertEqual(result["scheduled_created"], 1)
        runtime = "2" * 64
        self.cp.add_credential("hypro02", runtime, "xray", "vless", "usr_test")
        self.cp.ingest_observations(
            "hypro02", "test", "epoch-1",
            [{"runtime_ref_hash": runtime, "uplink_bytes": 100, "downlink_bytes": 100}],
            "2026-09-14T15:00:00Z",
        )
        self.cp.ingest_observations(
            "hypro02", "test", "epoch-1",
            [{"runtime_ref_hash": runtime, "uplink_bytes": 150, "downlink_bytes": 150}],
            "2026-09-14T16:00:00Z",
        )
        db = self.cp.connect()
        try:
            cycles = db.execute(
                "SELECT cycle_key,starts_at,ends_at,cycle_kind,timezone,commercial_applies FROM billing_cycles WHERE user_id='usr_test' ORDER BY cycle_key"
            ).fetchall()
            ledger = db.execute(
                "SELECT cycle_id,delta_uplink_bytes,delta_downlink_bytes FROM usage_ledger ORDER BY observed_at"
            ).fetchall()
        finally:
            db.close()
        self.assertEqual(len(cycles), 2)
        scheduled = next(row for row in cycles if row[0] == "2026-09-15")
        self.assertEqual(tuple(scheduled[1:]), (
            "2026-09-14T16:00:00Z", "2026-10-14T16:00:00Z", "customer", "Asia/Shanghai", 1
        ))
        self.assertEqual(len(ledger), 2)
        self.assertNotEqual(ledger[0][0], ledger[1][0])
        self.assertEqual((ledger[0][1], ledger[0][2]), (0, 0))
        self.assertEqual((ledger[1][1], ledger[1][2]), (50, 50))

    def test_subscription_token_is_separate_from_portal_token(self):
        self.assertNotEqual(self.user["portal_token"], self.user["subscription_token"])
        self.cp.add_subscription_entry("usr_test", "hypro02", "PREMIUM", "vless", "vless://synthetic")
        body = self.cp.subscription(self.user["subscription_token"], token_kind="subscription")
        self.assertTrue(body)
        view = self.cp.user_view(self.user["portal_token"])
        self.assertIsNone(view["subscription_url"])
        with self.assertRaises(Unauthorized):
            self.cp.subscription(self.user["portal_token"], token_kind="subscription")

    def test_user_view_exposes_role_and_independent_url_without_configured_entries(self):
        user = self.cp.reconcile_user("usr_free", "liuwen", "Free")
        view = self.cp.user_view(user["portal_token"])
        self.assertEqual(view["role"], "CUSTOMER")
        self.assertEqual(view["subscription_status"], "not_configured")
        self.assertIsNone(view["subscription_url"])

    def test_user_tokens_are_hash_only_at_rest(self):
        db = self.cp.connect()
        try:
            columns = {row[1] for row in db.execute("PRAGMA table_info(users)")}
            row = db.execute(
                "SELECT portal_token_hash,subscription_token_hash FROM users WHERE user_id='usr_test'"
            ).fetchone()
        finally:
            db.close()
        self.assertIn("portal_token_hash", columns)
        self.assertIn("subscription_token_hash", columns)
        self.assertIn("subscription_token_legacy_hash", columns)
        self.assertNotIn("subscription_token", columns)
        self.assertEqual(row[0], token_hash(self.user["portal_token"]))
        self.assertEqual(row[1], token_hash(self.user["subscription_token"]))
        raw = self.db.read_bytes()
        self.assertNotIn(self.user["portal_token"].encode(), raw)
        self.assertNotIn(self.user["subscription_token"].encode(), raw)

    def test_legacy_plaintext_subscription_column_is_migrated_and_removed(self):
        legacy_db = Path(self.temp.name) / "legacy.sqlite"
        connection = sqlite3.connect(legacy_db)
        try:
            connection.execute(
                """CREATE TABLE users(
                   user_id TEXT PRIMARY KEY, display_name TEXT NOT NULL,
                   plan TEXT NOT NULL, status TEXT NOT NULL,
                   portal_token_hash TEXT NOT NULL UNIQUE,
                   subscription_token TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                "INSERT INTO users VALUES (?,?,?,?,?,?,?)",
                ("usr_legacy", "legacy", "Plus", "active", "p" * 64, "legacy-sub-secret", "2026-01-01T00:00:00Z"),
            )
            connection.commit()
        finally:
            connection.close()
        cp = ControlPlane(legacy_db, subscription_base_url="https://sub.example.test")
        cp.init_db()
        db = cp.connect()
        try:
            columns = {row[1] for row in db.execute("PRAGMA table_info(users)")}
            row = db.execute(
                "SELECT subscription_token_hash FROM users WHERE user_id='usr_legacy'"
            ).fetchone()
        finally:
            db.close()
        self.assertNotIn("subscription_token", columns)
        self.assertIn("subscription_token_hash", columns)
        self.assertEqual(row[0], token_hash("legacy-sub-secret"))
        self.assertNotIn(b"legacy-sub-secret", legacy_db.read_bytes())
        self.assertEqual(cp._user_by_subscription_token("legacy-sub-secret")["user_id"], "usr_legacy")

    def test_token_issuance_replaces_hash_and_rejects_old_and_wrong_portal_tokens(self):
        old_portal = self.user["portal_token"]
        result = self.cp.issue_tokens("usr_test", "portal")
        new_portal = result["tokens"]["portal"]
        self.assertNotEqual(old_portal, new_portal)
        self.assertEqual(result["token_kind"], "portal")
        self.assertEqual(self.cp.user_view(new_portal)["user_id"], "usr_test")
        with self.assertRaises(Unauthorized):
            self.cp.user_view(old_portal)
        with self.assertRaises(Unauthorized):
            self.cp.user_view("wrong-token")
        self.assertEqual(self.cp._user_by_subscription_token(self.user["subscription_token"])["user_id"], "usr_test")
        db = self.cp.connect()
        try:
            columns = {row[1] for row in db.execute("PRAGMA table_info(users)")}
        finally:
            db.close()
        self.assertNotIn("subscription_token", columns)
        self.assertNotIn(new_portal.encode(), self.db.read_bytes())

    def test_token_issuance_requires_admin_and_api_returns_no_unrequested_kind(self):
        app = App(self.cp)
        body = json.dumps({"user_id": "usr_test", "token_kind": "portal", "revoke_old": True}).encode()

        def call(admin_token):
            environ = {
                "REQUEST_METHOD": "POST", "PATH_INFO": "/api/admin/token-issuance",
                "HTTP_AUTHORIZATION": f"Bearer {admin_token}",
                "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body),
                "wsgi.errors": io.StringIO(), "wsgi.version": (1, 0),
                "wsgi.url_scheme": "http", "wsgi.multithread": False,
                "wsgi.multiprocess": False, "wsgi.run_once": False,
            }
            result = {}

            def start_response(status, headers):
                result["status"] = status
                result["headers"] = headers

            response = b"".join(app(environ, start_response))
            return result["status"], json.loads(response)

        status, rejected = call("not-admin")
        self.assertEqual(status, "401 Error")
        self.assertNotIn("tokens", rejected)
        status, issued = call("admin")
        self.assertEqual(status, "201 OK")
        self.assertEqual(set(issued["tokens"]), {"portal"})
        self.assertNotIn("subscription_token", json.dumps(issued))

    def test_subscription_issuance_can_retain_then_explicitly_revoke_previous_hash(self):
        self.cp.add_subscription_entry("usr_test", "hypro02", "PREMIUM", "vless", "vless://synthetic")
        old_subscription = self.user["subscription_token"]
        result = self.cp.issue_tokens("usr_test", "subscription", revoke_old=False)
        new_subscription = result["tokens"]["subscription"]
        self.assertFalse(result["revoked_previous"])
        self.assertEqual(result["retained_previous_kinds"], ["subscription"])
        self.assertEqual(result["revoked_previous_kinds"], [])
        self.assertEqual(self.cp._user_by_subscription_token(old_subscription)["user_id"], "usr_test")
        self.assertEqual(self.cp._user_by_subscription_token(new_subscription)["user_id"], "usr_test")
        db = self.cp.connect()
        try:
            row = db.execute(
                "SELECT subscription_token_hash,subscription_token_legacy_hash FROM users WHERE user_id='usr_test'"
            ).fetchone()
        finally:
            db.close()
        self.assertEqual(row[0], token_hash(new_subscription))
        self.assertEqual(row[1], token_hash(old_subscription))
        self.cp.revoke_legacy_subscription("usr_test")
        with self.assertRaises(Unauthorized):
            self.cp._user_by_subscription_token(old_subscription)
        self.assertEqual(self.cp._user_by_subscription_token(new_subscription)["user_id"], "usr_test")

    def test_both_issuance_retains_only_subscription_previous_hash(self):
        self.cp.add_subscription_entry("usr_test", "hypro02", "PREMIUM", "vless", "vless://synthetic")
        old_portal = self.user["portal_token"]
        old_subscription = self.user["subscription_token"]
        result = self.cp.issue_tokens("usr_test", "both", revoke_old=False)
        self.assertEqual(result["revoked_previous_kinds"], ["portal"])
        self.assertEqual(result["retained_previous_kinds"], ["subscription"])
        with self.assertRaises(Unauthorized):
            self.cp.user_view(old_portal)
        self.assertEqual(self.cp._user_by_subscription_token(old_subscription)["user_id"], "usr_test")

    def test_admin_users_returns_safe_projection_metadata_without_hashes(self):
        self.cp.add_subscription_entry("usr_test", "hypro02", "PREMIUM", "vless", "vless://synthetic")
        user = next(item for item in self.cp.admin_users() if item["user_id"] == "usr_test")
        self.assertEqual(user["subscription_status"], "available")
        self.assertEqual(user["subscription_entry_count"], 1)
        self.assertEqual(user["subscription_pool_ids"], ["PREMIUM"])
        self.assertEqual(user["subscription_protocols"], ["vless"])
        self.assertFalse(user["subscription_legacy_retained"])
        self.assertNotIn("portal_token_hash", user)
        self.assertNotIn("subscription_token_hash", user)

    def test_subscription_and_portal_tokens_cannot_cross_authentication_boundaries(self):
        self.cp.add_subscription_entry("usr_test", "hypro02", "PREMIUM", "vless", "vless://synthetic")
        with self.assertRaises(Unauthorized):
            self.cp.user_view(self.user["subscription_token"])
        with self.assertRaises(Unauthorized):
            self.cp.subscription(self.user["portal_token"])

    def test_subscription_credential_must_belong_to_same_user(self):
        other = self.cp.reconcile_user("usr_other", "other", "Plus")
        credential = self.cp.add_credential("hypro02", "6" * 64, "xray", "vless", "usr_other")
        with self.assertRaises(ControlPlaneError):
            self.cp.add_subscription_entry(
                "usr_test", "hypro02", "PREMIUM", "vless", "vless://synthetic",
                credential_id=credential,
            )

    def test_provider_resource_cycle_is_separate_metadata(self):
        self.cp.upsert_infrastructure_resource({
            "resource_id": "qqgnet-la-9929",
            "provider_name": "QQGNet",
            "provider_instance_id": "qqgnet-la-9929",
            "node_id": "hypro02",
            "location": "Los Angeles",
            "network_label": "AS9929",
            "local_timezone": "Etc/UTC",
            "timezone_source": "verified host OS discovery",
            "resource_cycle_status": "unknown",
            "resource_cycle_source": "not verified",
            "contract_cycle": "annual",
            "contract_amount": "$28.90",
            "contract_currency": "USD",
            "next_due_local": "2027-08-28 17:30:00",
            "next_due_timezone": "Unknown",
            "next_due_source": "Product Owner supplied metadata",
        })
        self.cp.record_provider_resource_cycle({
            "provider_cycle_id": "prc_qqg_unknown",
            "resource_id": "qqgnet-la-9929",
            "cycle_key": "unknown-unverified",
            "timezone": "Etc/UTC",
            "status": "unknown",
            "source": "not verified",
        })
        overview = self.cp.admin_overview()
        resource = overview["infrastructure_resources"][0]
        provider_cycle = overview["provider_resource_cycles"][0]
        self.assertEqual(resource["local_timezone"], "Etc/UTC")
        self.assertEqual(resource["resource_cycle_status"], "unknown")
        self.assertEqual(provider_cycle["timezone"], "Etc/UTC")
        self.assertFalse(provider_cycle["traffic_reset_authoritative"])

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
        body = base64.b64decode(self.cp.subscription(self.user["subscription_token"]).strip()).decode()
        self.assertEqual(body, "vless://synthetic\n")

    def test_bearer_subscription_endpoint_is_rejected_by_control_plane(self):
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
        self.assertEqual(result["status"], "401 Error")
        self.assertEqual(json.loads(body)["error"], "subscription token required")

    def test_worker_subscription_header_uses_independent_subscription_token(self):
        self.cp.add_subscription_entry("usr_test", "hypro02", "PREMIUM", "vless", "vless://synthetic", "Plus")
        app = App(self.cp)
        environ = {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/subscription",
            "HTTP_X_SPARKLINK_SUBSCRIPTION_TOKEN": self.user["subscription_token"],
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
