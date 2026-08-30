import unittest

from deploy import apply_xray_identity_migration as migration


class IdentityMigrationTests(unittest.TestCase):
    def test_source_tag_disambiguates_duplicate_uuid_across_inbounds(self):
        old_uuid = "11111111-1111-4111-8111-111111111111"
        new_uuid = "22222222-2222-4222-8222-222222222222"
        config = {
            "inbounds": [
                {"tag": "xhttp-in", "settings": {"clients": [{"id": old_uuid, "email": "xhttp"}]}},
                {"tag": "reality-in", "settings": {"clients": [{"id": old_uuid, "email": "reality"}]}},
            ]
        }
        additions, sources = migration.mutate_config(config, [{
            "source_entry_id": "usr_test",
            "source_tag": "reality-in",
            "old_uuid": old_uuid,
            "new_uuid": new_uuid,
            "new_email": "sparklink:usr_test:advanced",
        }])
        self.assertEqual(additions, 1)
        self.assertEqual(sources, [(old_uuid, "reality", new_uuid, "sparklink:usr_test:advanced")])
        reality_clients = config["inbounds"][1]["settings"]["clients"]
        self.assertEqual(len(reality_clients), 2)
        self.assertEqual(reality_clients[-1]["email"], "sparklink:usr_test:advanced")
        self.assertEqual(len(config["inbounds"][0]["settings"]["clients"]), 1)

    def test_source_tag_validation_rejects_whitespace(self):
        with self.assertRaises(migration.MigrationError):
            migration.validate_entry({
                "source_entry_id": "usr_test",
                "source_tag": "reality in",
                "old_uuid": "11111111-1111-4111-8111-111111111111",
                "new_uuid": "22222222-2222-4222-8222-222222222222",
                "new_email": "managed",
            })


if __name__ == "__main__":
    unittest.main()
