import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deploy import admin_console
from deploy import issue_user_tokens as operator


class AdminConsoleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.delivery = self.root / "delivery"
        self.user = {
            "user_id": "usr_root", "display_name": "root", "plan": "Plus", "role": "OWNER",
            "status": "active", "subscription_status": "available", "subscription_entry_count": 3,
            "subscription_pool_ids": ["STANDARD", "ADVANCED", "PREMIUM"],
            "subscription_protocols": ["vless"], "subscription_anytls_count": 0,
            "subscription_legacy_retained": False,
        }
        operator.write_delivery_bundle(
            operator.user_bundle_path("root", self.delivery),
            {
                "schema": "sparklink.operator-delivery.v1", "user_id": "usr_root", "username": "root",
                "portal_access_token": "portal-secret-test", "subscription_url": "https://sub.example.test/u/sub-secret-test",
                "issue_rotation_timestamp": "2026-08-29T00:00:00Z",
            }, apply_acl=False,
        )
        self.app = admin_console.ConsoleApp(
            "http://control.example.test", "admin", self.delivery,
            "https://spark.example.test", "https://sub.example.test",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_page_and_state_never_return_bundle_secrets(self):
        result = {}

        def start(status, headers):
            result["status"] = status
            result["headers"] = headers

        page = b"".join(self.app({"REQUEST_METHOD": "GET", "PATH_INFO": "/"}, start))
        self.assertEqual(result["status"], "200 OK")
        self.assertNotIn(b"portal-secret-test", page)
        self.assertNotIn(b"sub-secret-test", page)
        with patch.object(self.app, "_safe_users", return_value=[self.user]), \
             patch.object(admin_console.operator, "admin_json", side_effect=lambda endpoint, token, path, method, body=None: (
                 {"nodes": [], "users": [], "unresolved_usage_records": 0,
                  "provider_resource_snapshots": [], "collector_heartbeats": []}
                 if path == "/api/admin/overview" else {"ok": True}
             )):
            state = self.app.state()
        encoded = json.dumps(state)
        self.assertNotIn("portal-secret-test", encoded)
        self.assertNotIn("sub-secret-test", encoded)
        self.assertTrue(state["delivery"]["usr_root"]["portal_available"])

    def test_copy_is_local_only_and_response_contains_no_secret(self):
        with patch.object(self.app, "_safe_users", return_value=[self.user]), \
             patch.object(self.app, "_record"), \
             patch.object(admin_console.operator.os, "name", "nt"), \
             patch.object(admin_console.subprocess, "run", return_value=admin_console.subprocess.CompletedProcess([], 0)) as run:
            result = self.app.copy_secret("usr_root", "subscription")
        self.assertTrue(result["secret_not_printed"])
        self.assertNotIn("sub-secret-test", json.dumps(result))
        self.assertEqual(run.call_args.kwargs["input"], b"https://sub.example.test/u/sub-secret-test")

    def test_rotate_requires_exact_confirmation_and_returns_safe_result(self):
        issued = {
            "ok": True, "user_id": "usr_root", "issued_at": "2026-08-29T01:00:00Z",
            "revoked_previous": True, "revoked_previous_kinds": ["portal"],
            "retained_previous_kinds": [], "tokens": {"portal": "new-portal-secret"},
        }
        with patch.object(self.app, "_safe_users", return_value=[self.user]), \
             patch.object(self.app, "_record"), \
             patch.object(admin_console.operator, "issue_request", return_value=issued), \
             patch.object(admin_console.operator, "verify_issued_tokens", return_value=["ok"]), \
             patch.object(admin_console.operator, "verify_public_subscription_projection"), \
             patch.object(admin_console.operator, "_set_private_acl"):
            with self.assertRaises(admin_console.ConsoleError):
                self.app.rotate("usr_root", "portal", "wrong")
            result = self.app.rotate("usr_root", "portal", "ROTATE usr_root portal")
        self.assertTrue(result["secret_not_printed"])
        self.assertNotIn("new-portal-secret", json.dumps(result))
        bundle = operator.read_bundle(operator.user_bundle_path("root", self.delivery))
        self.assertEqual(bundle["portal_access_token"], "new-portal-secret")


if __name__ == "__main__":
    unittest.main()
