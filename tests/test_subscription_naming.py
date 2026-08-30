import unittest
from urllib.parse import unquote, urlsplit

from deploy import standardize_subscription_names as standardizer
from src.sparklink_subscription_naming import (
    CANONICAL_DEDIROCK_ALIAS,
    CANONICAL_DEDIROCK_ALIASES,
    CANONICAL_DEDIROCK_ORIGIN_ALIAS,
    SubscriptionNamingError,
    alias_from_uri,
    canonical_alias,
    dedirock_alias,
    replace_uri_alias,
    uri_core,
)


class SubscriptionNamingTests(unittest.TestCase):
    def test_legacy_aliases_map_to_the_existing_canonical_route_names(self):
        self.assertEqual(
            canonical_alias("hypro02", "Plus-LA-Xray-VLESS-REALITY-3"),
            "Pro-LA-02-HyTru-Direct-Reality",
        )
        self.assertEqual(
            canonical_alias("vmiss", "Plus-LA-Xray-VLESS-REALITY-4"),
            "Pro-LA-01-Origin-Direct-Reality",
        )
        self.assertEqual(
            canonical_alias("racknerd", "Basic-NY-Xray-VLESS-REALITY-7"),
            "Standard-NY-HyTru-Direct-Reality",
        )

    def test_dedirock_uses_route_name_and_veilshift_is_preserved(self):
        self.assertEqual(
            canonical_alias("dedirock", "SparkLink-Hegin-DediRock-Advanced"),
            CANONICAL_DEDIROCK_ALIAS,
        )
        self.assertEqual(CANONICAL_DEDIROCK_ALIAS, "Advanced-LA-HyTru-Direct-Reality")
        self.assertEqual(
            canonical_alias("dedirock", "Advanced-LA-HyTru-Direct-Reality"),
            "Advanced-LA-HyTru-Direct-Reality",
        )
        self.assertEqual(
            canonical_alias("dedirock", "Advanced-LA-Origin-Direct-Reality"),
            CANONICAL_DEDIROCK_ORIGIN_ALIAS,
        )
        self.assertEqual(
            CANONICAL_DEDIROCK_ALIASES,
            frozenset({
                "Advanced-LA-Origin-Direct-Reality",
                "Advanced-LA-HyTru-Direct-Reality",
            }),
        )
        self.assertEqual(dedirock_alias("origin"), CANONICAL_DEDIROCK_ORIGIN_ALIAS)
        self.assertEqual(dedirock_alias("hytru"), CANONICAL_DEDIROCK_ALIAS)
        veilshift = "VeilShift-Optimized"
        self.assertEqual(canonical_alias("unknown-node", veilshift), veilshift)

    def test_unknown_or_mismatched_alias_fails_closed(self):
        with self.assertRaises(SubscriptionNamingError):
            canonical_alias("hypro02", "Plus-NY-Xray-VLESS-REALITY-3")
        with self.assertRaises(SubscriptionNamingError):
            canonical_alias("vmiss", "operator-made-up-name")
        with self.assertRaises(SubscriptionNamingError):
            canonical_alias("racknerd", "Pro-LA-02-HyTru-Direct-Reality")

    def test_uri_rewrite_changes_only_display_fragment(self):
        uri = (
            "vless://11111111-1111-4111-8111-111111111111@example.test:443"
            "?security=reality&pbk=public-key&sid=short-id#old-name"
        )
        updated = replace_uri_alias(uri, "Pro-LA-02-HyTru-Direct-Reality")
        self.assertEqual(uri_core(uri), uri_core(updated))
        self.assertEqual(alias_from_uri(updated), "Pro-LA-02-HyTru-Direct-Reality")
        self.assertEqual(urlsplit(updated).hostname, "example.test")
        self.assertEqual(unquote(urlsplit(updated).fragment), "Pro-LA-02-HyTru-Direct-Reality")

    def test_operator_plan_contains_only_non_secret_alias_changes(self):
        plan = {
            "users": [
                {
                    "user": "root",
                    "entries": [{
                        "entry_id": "sub_one",
                        "node_id": "hypro02",
                        "old_alias": "legacy",
                        "new_alias": "Pro-LA-02-HyTru-Direct-Reality",
                        "old_core": ("vless", "private-userinfo", "", "security=reality"),
                    }],
                },
                {"user": "liuwen", "entries": []},
            ],
        }
        self.assertEqual(
            standardizer._changes(plan, use_new=True),
            [{"entry_id": "sub_one", "alias": "Pro-LA-02-HyTru-Direct-Reality"}],
        )
        report = standardizer._safe_report(plan)
        self.assertEqual(report["changed_entries"], 1)
        self.assertNotIn("private-userinfo", str(report))
        self.assertTrue(report["plaintext_not_printed"])


if __name__ == "__main__":
    unittest.main()
