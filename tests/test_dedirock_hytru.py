import unittest

from deploy.repair_dedirock_hytru import (
    HyTruRepairError,
    build_hytru_routing,
)


MANAGED = [
    "sparklink:usr_abing:advanced",
    "sparklink:usr_dangbin:advanced",
    "sparklink:usr_hegin:advanced",
    "sparklink:usr_plus_manual_01:advanced",
]


def base_config():
    return {
        "outbounds": [
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "blocked", "protocol": "blackhole"},
            {"tag": "warp", "protocol": "wireguard", "settings": {"peers": [{}]}},
        ],
        "routing": {"rules": [
            {"type": "field", "inboundTag": ["warp-bridge-in"], "outboundTag": "warp"},
            {"type": "field", "user": ["hytru-reality"], "outboundTag": "warp"},
            {"type": "field", "user": ["origin-reality"], "outboundTag": "direct"},
        ]},
    }


class DediRockHyTruTests(unittest.TestCase):
    def test_adds_one_exact_managed_warp_rule_before_static_user_rules(self):
        config = base_config()
        candidate, changed = build_hytru_routing(config, MANAGED)

        self.assertTrue(changed)
        self.assertEqual(candidate["routing"]["rules"][0], config["routing"]["rules"][0])
        self.assertEqual(candidate["routing"]["rules"][1]["user"], sorted(MANAGED))
        self.assertEqual(candidate["routing"]["rules"][1]["outboundTag"], "warp")
        self.assertEqual(candidate["routing"]["rules"][2:], config["routing"]["rules"][1:])
        self.assertEqual(config["routing"]["rules"], base_config()["routing"]["rules"])

    def test_route_repair_is_idempotent(self):
        first, changed = build_hytru_routing(base_config(), MANAGED)
        self.assertTrue(changed)
        second, changed_again = build_hytru_routing(first, MANAGED)

        self.assertFalse(changed_again)
        self.assertEqual(second, first)

    def test_existing_managed_direct_rule_is_moved_to_warp(self):
        config = base_config()
        config["routing"]["rules"].append({
            "type": "field",
            "user": [MANAGED[0]],
            "outboundTag": "direct",
        })

        candidate, changed = build_hytru_routing(config, MANAGED)
        self.assertTrue(changed)
        matches = [rule for rule in candidate["routing"]["rules"]
                   if MANAGED[0] in rule.get("user", [])]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["outboundTag"], "warp")

    def test_missing_wireguard_outbound_fails_closed(self):
        config = base_config()
        config["outbounds"][-1]["protocol"] = "freedom"
        with self.assertRaises(HyTruRepairError):
            build_hytru_routing(config, MANAGED)


if __name__ == "__main__":
    unittest.main()
