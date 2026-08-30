import unittest

from deploy import ensure_dedirock_dual_routes as dual
from src.sparklink_subscription_naming import alias_from_uri


USER_IDS = {
    "root": "usr_plus_manual_01",
    "Hegin": "usr_hegin",
    "abing": "usr_abing",
    "dangbin": "usr_dangbin",
}


def users():
    return [
        {
            "user_id": user_id,
            "display_name": name,
            "plan": "Basic" if name == "dangbin" else "Plus",
            "role": "OWNER" if name == "root" else "CUSTOMER",
            "status": "active",
            "subscription_status": "available",
            "subscription_entry_count": 7,
            "subscription_pool_ids": ["ADVANCED", "PREMIUM", "STANDARD"],
            "subscription_protocols": ["vless"],
            "subscription_anytls_count": 0,
            "subscription_legacy_retained": True,
        }
        for name, user_id in USER_IDS.items()
    ]


def discovery(origin_users=()):
    managed = {
        "hytru": {
            user_id: {
                "user": user_id,
                "email": dual.managed_email(user_id),
                "uuid": f"11111111-1111-4111-8111-{index:012d}",
            }
            for index, user_id in enumerate(USER_IDS.values(), 1)
        },
        "origin": {
            user_id: {
                "user": user_id,
                "email": dual.managed_email(user_id, "origin"),
                "uuid": f"22222222-2222-4222-8222-{index:012d}",
            }
            for index, user_id in enumerate(origin_users, 1)
        },
    }
    all_ids = [item["uuid"] for kind in managed.values() for item in kind.values()]
    return {
        "config_sha256": "a" * 64,
        "config_mode": "0o640",
        "config_uid": 0,
        "config_gid": 988,
        "service_active": True,
        "config_test": True,
        "managed": managed,
        "route_tags": {},
        "all_client_ids": all_ids,
        "reality": {
            "server_name": "origin.example.test",
            "public_key": "public-key",
            "short_id": "short-id",
        },
        "outbounds": [
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "warp", "protocol": "wireguard"},
        ],
        "xui_db_present": False,
    }


class DediRockDualRouteTests(unittest.TestCase):
    def test_build_plan_adds_one_origin_identity_per_eligible_user(self):
        plan = dual.build_plan(discovery(), users())

        self.assertEqual(len(plan["origin_identities"]), 4)
        self.assertEqual(len(plan["entries"]), 4)
        self.assertTrue(all(item["new"] for item in plan["origin_identities"]))
        self.assertEqual(
            {alias_from_uri(item["uri"]) for item in plan["entries"]},
            {dual.CANONICAL_DEDIROCK_ORIGIN_ALIAS},
        )
        self.assertEqual(
            {item["runtime_ref_hash"] for item in plan["entries"]},
            {dual.runtime_ref_hash(item["email"]) for item in plan["origin_identities"]},
        )
        self.assertEqual(
            {item["source_email"] for item in plan["origin_identities"]},
            {dual.managed_email(user_id) for user_id in USER_IDS.values()},
        )

    def test_existing_origin_identities_are_reused(self):
        existing = tuple(USER_IDS.values())
        value = discovery(existing)
        plan = dual.build_plan(value, users())

        self.assertFalse(any(item["new"] for item in plan["origin_identities"]))
        self.assertEqual(
            {item["uuid"] for item in plan["origin_identities"]},
            {item["uuid"] for item in value["managed"]["origin"].values()},
        )

    def test_missing_hytru_identity_fails_closed(self):
        value = discovery()
        value["managed"]["hytru"].pop(USER_IDS["root"])
        with self.assertRaises(dual.DualRouteError):
            dual.build_plan(value, users())

    def test_remote_scripts_compile_and_safe_discovery_has_no_uuid(self):
        for name in (
            "REMOTE_INSPECT_SCRIPT",
            "REMOTE_APPLY_SCRIPT",
            "REMOTE_ROLLBACK_SCRIPT",
            "REMOTE_ACCEPTANCE_SCRIPT",
        ):
            compile(getattr(dual, name), f"<{name}>", "exec")
        safe = dual._safe_discovery(discovery())
        self.assertNotIn("11111111-1111-4111-8111", repr(safe))
        self.assertEqual(set(safe["hytru_users"]), set(USER_IDS.values()))


if __name__ == "__main__":
    unittest.main()
