import unittest
import uuid

from deploy import admit_dedirock


def user(user_id, display_name, plan):
    return {
        "user_id": user_id,
        "display_name": display_name,
        "plan": plan,
        "role": "OWNER" if display_name == "root" else "CUSTOMER",
        "status": "active",
        "subscription_status": "available",
        "subscription_entry_count": 1,
        "subscription_pool_ids": ["STANDARD"],
        "subscription_protocols": ["vless"],
        "subscription_anytls_count": 0,
        "subscription_legacy_retained": True,
    }


class DediRockAdmissionTests(unittest.TestCase):
    def discovery(self, managed_clients=None):
        return {
            "node_id": "dedirock",
            "service": "xray",
            "binary": "/usr/local/bin/xray",
            "config_path": "/etc/xray/config.json",
            "test_flag": "-config",
            "config_sha256": "a" * 64,
            "reality_tag": "reality-in",
            "server_name": "origin.example.test",
            "short_id": "shortid",
            "public_key": "publickey",
            "template": {
                "id": "11111111-1111-4111-8111-111111111111",
                "email": "legacy-template",
            },
            "managed_clients": managed_clients or [],
            "all_client_ids": ["11111111-1111-4111-8111-111111111111"],
        }

    def users(self):
        return [
            user("usr_root", "root", "Plus"),
            user("usr_hegin", "Hegin", "Plus"),
            user("usr_abing", "abing", "Plus"),
            user("usr_dangbin", "dangbin", "Basic"),
        ]

    def test_missing_managed_identities_create_only_transient_migration_plan(self):
        entries, migrations, metadata = admit_dedirock.build_runtime_plan(
            self.discovery(), self.users()
        )
        self.assertEqual(len(entries), 4)
        self.assertEqual(len(migrations), 4)
        self.assertEqual(metadata["missing_users"], ["root", "Hegin", "abing", "dangbin"])
        for entry in entries:
            parsed = admit_dedirock.urllib.parse.urlsplit(entry["uri"])
            self.assertEqual(parsed.scheme, "vless")
            self.assertEqual(parsed.hostname, "dedirock.enrpiglink.top")
            self.assertEqual(parsed.port, 443)
            self.assertEqual(
                admit_dedirock.urllib.parse.unquote(parsed.fragment),
                "SparkLink-DediRock-Advanced",
            )
            self.assertEqual(entry["credential_kind"], "managed")
            self.assertEqual(entry["minimum_plan"], "Basic")
            self.assertEqual(len(entry["runtime_ref_hash"]), 64)
        for migration in migrations:
            self.assertEqual(migration["source_tag"], "reality-in")
            self.assertNotEqual(migration["old_uuid"], migration["new_uuid"])
            uuid.UUID(migration["new_uuid"])

    def test_existing_stable_managed_identities_are_reused_without_migration(self):
        managed = [
            {"id": f"{index:08d}-1111-4111-8111-111111111111", "email": admit_dedirock.managed_email(user_id)}
            for index, user_id in enumerate(
                ["usr_root", "usr_hegin", "usr_abing", "usr_dangbin"], 1
            )
        ]
        discovery = self.discovery(managed)
        discovery["all_client_ids"] = [item["id"] for item in managed]
        entries, migrations, metadata = admit_dedirock.build_runtime_plan(discovery, self.users())
        self.assertEqual(len(entries), 4)
        self.assertEqual(migrations, [])
        self.assertEqual(metadata["missing_users"], [])


if __name__ == "__main__":
    unittest.main()
