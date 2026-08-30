import contextlib
import base64
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from wsgiref.simple_server import WSGIRequestHandler, make_server

from deploy import issue_user_tokens as operator
from src.sparklink_control_plane import App, ControlPlane


class QuietTestHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        return


class TokenOperatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "sparklink.db"
        self.cp = ControlPlane(self.db, admin_token="admin", subscription_base_url="https://sub.example.test")
        self.cp.seed_nodes()
        self.cp.create_user("root", "Plus", user_id="usr_root", role="OWNER")
        self.server = make_server("127.0.0.1", 0, App(self.cp), handler_class=QuietTestHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.endpoint = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def issue(self, *extra):
        args = [
            "issue", "--user-id", "usr_root", "--token-kind", "portal",
            "--endpoint", self.endpoint, "--secret-path", str(self.root / "admin.dpapi"),
            "--delivery-dir", str(self.root / "delivery"), *extra,
        ]
        output = io.StringIO()
        with patch.object(operator, "_admin_token", return_value="admin"), \
             patch.object(operator, "_set_private_acl", return_value=None), \
             contextlib.redirect_stdout(output):
            self.assertEqual(operator.main(args), 0)
        return json.loads(output.getvalue())

    def test_issue_writes_bundle_without_secret_on_stdout_and_second_issue_rejects_old(self):
        first = self.issue("--output", str(self.root / "delivery" / "first.json"))
        first_bundle = operator.read_bundle(Path(first["delivery_bundle"]))
        first_token = first_bundle["portal_access_token"]
        self.assertNotIn(first_token, json.dumps(first))
        self.assertIn("new_portal=accepted", first["verification"])
        second = self.issue(
            "--output", str(self.root / "delivery" / "final.json"),
            "--old-bundle", str(self.root / "delivery" / "first.json"),
            "--consume-old-bundle",
        )
        final_bundle = operator.read_bundle(Path(second["delivery_bundle"]))
        self.assertNotEqual(first_token, final_bundle["portal_access_token"])
        self.assertIn("old_portal=rejected", second["verification"])
        self.assertFalse((self.root / "delivery" / "first.json").exists())
        self.assertNotIn(final_bundle["portal_access_token"], json.dumps(second))

    def test_bundle_writer_uses_private_mode_without_acl_switch(self):
        target = self.root / "delivery" / "bundle.json"
        operator.write_delivery_bundle(
            target,
            {"schema": "sparklink.operator-delivery.v1", "user_id": "usr_root", "token_kind": "portal"},
            apply_acl=False,
        )
        if operator.os.name != "nt":
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(operator.read_bundle(target)["user_id"], "usr_root")

    def test_portal_acceptance_validator_checks_owner_cycle_and_independent_pools(self):
        self.cp.create_cycle("usr_root", "cycle-test", None, None)
        issued = self.issue("--output", str(self.root / "delivery" / "acceptance.json"))
        bundle = operator.read_bundle(Path(issued["delivery_bundle"]))
        result = operator.verify_portal_view(
            self.endpoint,
            bundle["portal_access_token"],
            "usr_root",
            "Plus",
            "OWNER",
            "cycle-test",
        )
        self.assertEqual(result["pool_ids"], ["STANDARD", "ADVANCED", "PREMIUM"])
        self.assertTrue(result["self_scoped"])

    def test_public_subscription_projection_checks_status_count_protocol_and_anytls(self):
        encoded = base64.b64encode(b"vless://synthetic\n") + b"\n"
        with patch.object(operator, "_request_url", return_value=(200, encoded)):
            result = operator.verify_public_subscription_projection(
                "https://sub.example.test/u/sub-token",
                "Basic",
                "available",
                1,
                ["STANDARD", "ADVANCED"],
                ["vless"],
                "https://sub.example.test",
            )
        self.assertEqual(result["projection_entries"], 1)
        self.assertFalse(result["anytls"])
        with patch.object(operator, "_request_url", return_value=(503, b"")):
            result = operator.verify_public_subscription_projection(
                "https://sub.example.test/u/sub-token",
                "Free",
                "not_configured",
                0,
                [],
                [],
                "https://sub.example.test",
            )
        self.assertEqual(result["projection_status"], "not_configured")

    def test_copy_by_user_copies_subscription_url_and_never_prints_it(self):
        delivery_dir = self.root / "delivery"
        bundle_path = operator.user_bundle_path("Hegin", delivery_dir)
        token = "portal-secret-for-test"
        url = "https://sub.example.test/u/sub-secret-for-test"
        operator.write_delivery_bundle(
            bundle_path,
            {
                "schema": "sparklink.operator-delivery.v1",
                "user_id": "usr_hegin",
                "username": "Hegin",
                "portal_access_token": token,
                "subscription_url": url,
            },
            apply_acl=False,
        )
        args = operator.build_parser().parse_args([
            "copy", "--user", "Hegin", "--kind", "subscription",
            "--delivery-dir", str(delivery_dir),
        ])
        output = io.StringIO()
        with patch.object(operator.subprocess, "run", return_value=operator.subprocess.CompletedProcess([], 0)) as run:
            with contextlib.redirect_stdout(output):
                self.assertEqual(operator.copy_secret(args), 0)
        self.assertEqual(run.call_args.kwargs["input"], url.encode("utf-8"))
        self.assertNotIn(url, output.getvalue())
        self.assertIn("secret_not_printed", output.getvalue())

    def test_reconcile_reuses_root_and_issues_only_missing_users_with_safe_index(self):
        delivery_dir = self.root / "delivery"
        old_root = delivery_dir / "root-portal-final.json"
        operator.write_delivery_bundle(
            old_root,
            {
                "schema": "sparklink.operator-delivery.v1",
                "user_id": "usr_plus_manual_01",
                "portal_access_token": "root-portal",
                "subscription_url": "https://sub.enrpiglink.top/u/root-subscription",
                "generated_at": "2026-08-29T13:00:00Z",
            },
            apply_acl=False,
        )
        rows = [
            {"user_id": "usr_abing", "display_name": "abing", "plan": "Plus", "role": "CUSTOMER", "status": "active", "subscription_status": "available", "subscription_entry_count": 6, "subscription_pool_ids": ["STANDARD", "ADVANCED", "PREMIUM"], "subscription_protocols": ["vless"], "subscription_anytls_count": 0, "subscription_legacy_retained": False},
            {"user_id": "usr_dangbin", "display_name": "dangbin", "plan": "Basic", "role": "CUSTOMER", "status": "active", "subscription_status": "available", "subscription_entry_count": 2, "subscription_pool_ids": ["STANDARD", "ADVANCED"], "subscription_protocols": ["vless"], "subscription_anytls_count": 0, "subscription_legacy_retained": False},
            {"user_id": "usr_hegin", "display_name": "Hegin", "plan": "Plus", "role": "CUSTOMER", "status": "active", "subscription_status": "available", "subscription_entry_count": 6, "subscription_pool_ids": ["STANDARD", "ADVANCED", "PREMIUM"], "subscription_protocols": ["vless"], "subscription_anytls_count": 0, "subscription_legacy_retained": False},
            {"user_id": "usr_liuwen", "display_name": "liuwen", "plan": "Free", "role": "CUSTOMER", "status": "active", "subscription_status": "not_configured", "subscription_entry_count": 0, "subscription_pool_ids": [], "subscription_protocols": [], "subscription_anytls_count": 0, "subscription_legacy_retained": False},
            {"user_id": "usr_plus_manual_01", "display_name": "root", "plan": "Plus", "role": "OWNER", "status": "active", "subscription_status": "available", "subscription_entry_count": 6, "subscription_pool_ids": ["STANDARD", "ADVANCED", "PREMIUM"], "subscription_protocols": ["vless"], "subscription_anytls_count": 0, "subscription_legacy_retained": False},
            {"user_id": "usr_zhanhao", "display_name": "zhanhao", "plan": "Free", "role": "CUSTOMER", "status": "active", "subscription_status": "not_configured", "subscription_entry_count": 0, "subscription_pool_ids": [], "subscription_protocols": [], "subscription_anytls_count": 0, "subscription_legacy_retained": False},
        ]
        issued = iter(
            {
                "ok": True,
                "user_id": user["user_id"],
                "issued_at": "2026-08-29T14:00:00Z",
                "revoked_previous": False,
                "revoked_previous_kinds": ["portal"],
                "retained_previous_kinds": ["subscription"],
                "tokens": {"portal": f"{user['user_id']}-portal", "subscription": f"{user['user_id']}-subscription"},
            }
            for user in rows
            if user["display_name"] != "root"
        )

        @contextlib.contextmanager
        def fake_endpoint(_args):
            yield "http://127.0.0.1:1"

        args = operator.build_parser().parse_args([
            "reconcile", "--delivery-dir", str(delivery_dir),
        ])
        output = io.StringIO()
        with patch.object(operator, "_admin_token", return_value="admin"), \
             patch.object(operator, "read_admin_users", return_value=rows), \
             patch.object(operator, "selected_endpoint", fake_endpoint), \
             patch.object(operator, "issue_request", side_effect=lambda *call_args, **call_kwargs: next(issued)), \
             patch.object(operator, "verify_portal"), \
             patch.object(operator, "verify_public_subscription_projection"), \
             patch.object(operator, "verify_issued_tokens", return_value=[]), \
             patch.object(operator, "_set_private_acl", return_value=None), \
             contextlib.redirect_stdout(output):
            self.assertEqual(operator.reconcile(args), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(len(result["users"]), 6)
        self.assertTrue((delivery_dir / "root" / "delivery.json").exists())
        self.assertFalse(old_root.exists())
        self.assertTrue((delivery_dir / operator.DEFAULT_INDEX_FILENAME).exists())
        self.assertNotIn("usr_hegin-portal", output.getvalue())
        index = json.loads((delivery_dir / operator.DEFAULT_INDEX_FILENAME).read_text(encoding="utf-8"))
        self.assertEqual(len(index["users"]), 6)
        self.assertEqual(set(index["users"][0]), {"user", "plan", "bundle_path", "portal_token_status", "subscription_status", "migration_status"})


if __name__ == "__main__":
    unittest.main()
