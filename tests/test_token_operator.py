import contextlib
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
        self.assertEqual(result["pool_ids"], ["STANDARD", "PREMIUM"])
        self.assertTrue(result["self_scoped"])


if __name__ == "__main__":
    unittest.main()
