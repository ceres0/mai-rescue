import copy
from datetime import datetime, timezone
import json
import unittest

from mai_rescue.engine import build_plan
from mai_rescue.normalize import normalize_capture, pointer


class NormalizeTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.capture = {"mode": "live", "server": "https://mcp.mcd.cn", "captured_at": self.now.isoformat(),
                        "tool_results": {"query-my-coupons": {"structuredContent": {"coupons": [
                            {"id": "one", "name": "午餐权益", "discount": "8.50", "minimum": 2500}
                        ]}}}}
        self.mapping = {"schema_version": 1, "collections": [{"tool": "query-my-coupons", "kind": "coupon",
            "records_path": "/coupons", "fields": {"id": "/id", "title": "/name", "expires_at": "/until",
                "benefit_cents": {"path": "/discount", "multiply": 100}, "min_spend_cents": "/minimum"}}]}

    def normalize(self):
        return normalize_capture(self.capture, self.mapping)

    def test_explicit_mapping_preserves_unknown_and_provenance(self):
        snapshot = self.normalize()
        record = snapshot["records"][0]
        self.assertEqual(record["id"], "query-my-coupons:one")
        self.assertEqual(record["benefit_cents"], 850)
        self.assertEqual(record["source"], {"tool": "query-my-coupons", "ref": "structuredContent/coupons/0"})
        plan = build_plan(snapshot, now=self.now, planned_visit=True, budget_cents=3500)
        self.assertEqual(plan["actions"][0]["urgency"], "unknown")
        self.assertEqual(plan["actions"][0]["decision"], "check")
        self.assertTrue(plan["warnings"])

    def test_non_json_and_multiblock_response_need_explicit_handling(self):
        for content in ([{"type": "text", "text": "明天到期"}],
                        [{"type": "text", "text": "{}"}, {"type": "text", "text": "{}"}]):
            with self.subTest(content=content):
                self.capture["tool_results"]["query-my-coupons"] = {"content": content}
                with self.assertRaises(ValueError):
                    self.normalize()

    def test_single_json_text_is_supported(self):
        result = self.capture["tool_results"]["query-my-coupons"]
        result["content"] = [{"type": "text", "text": json.dumps(result.pop("structuredContent"))}]
        self.assertIn("content:text:json", self.normalize()["records"][0]["source"]["ref"])

    def test_pointer_escapes_and_array_indexes(self):
        value = {"a/b": {"~key": ["first"]}}
        self.assertEqual(pointer(value, "/a~1b/~0key/0"), "first")
        self.assertIs(pointer(value, ""), value)
        for path in ("a/b", "/~2"):
            with self.assertRaises(ValueError):
                pointer(value, path)

    def test_unverified_server_and_kind_mapping_are_rejected(self):
        self.capture["server"] = "https://example.invalid"
        with self.assertRaises(ValueError):
            self.normalize()
        self.capture["server"] = "https://mcp.mcd.cn"
        self.mapping["collections"][0]["kind"] = "points"
        with self.assertRaises(ValueError):
            self.normalize()

    def test_forbidden_fabricated_deadline_and_nonintegral_money(self):
        fields = self.mapping["collections"][0]["fields"]
        fields["expires_at"] = {"value": "2026-10-31"}
        with self.assertRaises(ValueError):
            self.normalize()
        fields["expires_at"] = "/until"
        self.capture["tool_results"]["query-my-coupons"]["structuredContent"]["coupons"][0]["discount"] = "1.234"
        with self.assertRaises(ValueError):
            self.normalize()

    def test_single_object_points_use_label_but_never_fake_amounts(self):
        self.capture["tool_results"] = {"query-my-account": {"structuredContent": {"expiring": 100}}}
        self.mapping["collections"] = [{"tool": "query-my-account", "kind": "points", "records_path": "",
            "fields": {"id": {"value": "expiring-batch"}, "title": {"value": "即将到期积分"}, "points": "/expiring"}}]
        record = self.normalize()["records"][0]
        self.assertEqual(record["points"], 100)
        self.assertIsNone(record["expires_at"])
        self.assertIsNone(record["benefit_cents"])

    def test_empty_collection_is_valid_but_wrong_collection_path_fails(self):
        self.capture["tool_results"]["query-my-coupons"]["structuredContent"]["coupons"] = []
        self.assertEqual(self.normalize()["records"], [])
        self.mapping["collections"][0]["records_path"] = "/unknown"
        with self.assertRaises(ValueError):
            self.normalize()

    def test_duplicate_and_blank_identifiers_are_rejected(self):
        entries = self.capture["tool_results"]["query-my-coupons"]["structuredContent"]["coupons"]
        entries.append(copy.deepcopy(entries[0]))
        with self.assertRaises(ValueError):
            build_plan(self.normalize(), now=self.now)
        entries.pop()
        entries[0]["id"] = " "
        with self.assertRaises(ValueError):
            self.normalize()


if __name__ == "__main__":
    unittest.main()
