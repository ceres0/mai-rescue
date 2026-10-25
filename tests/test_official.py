"""Synthetic fixtures for the optional official-2026-10 response profile."""

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mai_rescue.engine import build_plan
from mai_rescue.official import PROFILE, normalize_official_capture


CAPTURED_AT = "2026-10-25T12:00:00+08:00"


def result(data, *, text=False, code=200, success=True):
    document = {"code": code, "success": success, "data": data}
    if text:
        return {"content": [{"type": "image", "data": "synthetic"},
                            {"type": "text", "text": json.dumps(document)}]}
    return {"structuredContent": document}


def capture(results=None, pages=None):
    output = {"mode": "live", "server": "https://mcp.mcd.cn", "captured_at": CAPTURED_AT,
              "tool_results": results or {}, "warnings": []}
    if pages is not None:
        output["tool_pages"] = pages
    return output


def coupon(identifier="synthetic-a", **updates):
    value = {
        "id": identifier, "title": "合成测试券", "subtitle": "合成使用条件",
        "datetimeText": "合成展示时间", "discountInfo": {"discountDesc": "合成优惠说明", "discountValue": "12.5"},
        "tradeStartDate": "2026-10-25", "tradeEndDate": "2026-10-27",
        "tradeStartDateTime": "2026-10-25 00:00:00", "tradeEndDateTime": "2026-10-27 23:59:59",
        "enable": 1, "code": "synthetic-private-code-never-imported",
    }
    value.update(updates)
    return value


class OfficialProfileTests(unittest.TestCase):
    def test_profile_name_and_canonical_validation(self):
        self.assertEqual(PROFILE, "official-2026-10")
        raw = capture({"query-my-coupons": result({"coupons": [coupon()]})})
        snapshot = normalize_official_capture(raw)
        self.assertEqual(snapshot["captured_at"], CAPTURED_AT)
        self.assertEqual(snapshot["mode"], "live")
        self.assertEqual(snapshot["timezone"], "Asia/Shanghai")
        plan = build_plan(snapshot, now=datetime.fromisoformat(CAPTURED_AT), planned_visit=True)
        self.assertEqual(plan["actions"][0]["decision"], "check")
        self.assertEqual(plan["summary"]["max_saving_cents"], 0)
        self.assertTrue(any("不猜测截止日期" in warning for warning in snapshot["warnings"]))

    def test_coupons_use_explicit_dates_without_guessing_datetime_or_savings(self):
        snapshot = normalize_official_capture(capture({"query-my-coupons": result({"coupons": [coupon()]})}))
        record = snapshot["records"][0]
        self.assertEqual(record["id"], "query-my-coupons:synthetic-a")
        self.assertEqual(record["title"], "合成测试券")
        self.assertEqual(record["starts_at"], "2026-10-25")
        self.assertEqual(record["expires_at"], "2026-10-27")
        self.assertIsNone(record["benefit_cents"])
        self.assertIsNone(record["min_spend_cents"])
        self.assertIn("合成使用条件", record["conditions"])
        self.assertIn("合成展示时间", record["conditions"])
        self.assertIn("合成优惠说明", record["conditions"])
        self.assertEqual(record["source"], {"tool": "query-my-coupons", "ref": "/tool_results/query-my-coupons/structuredContent/data/coupons/0"})
        self.assertNotIn("synthetic-private-code-never-imported", json.dumps(snapshot))

    def test_datetimes_never_replace_missing_or_invalid_explicit_dates(self):
        rows = [coupon("missing", tradeStartDate=None, tradeEndDate=None),
                coupon("invalid", tradeStartDate="2026-02-30", tradeEndDate="2026-10-27T22:00:00+08:00")]
        snapshot = normalize_official_capture(capture({"query-my-coupons": result({"coupons": rows})}))
        for record in snapshot["records"]:
            self.assertIsNone(record["starts_at"])
            self.assertIsNone(record["expires_at"])

    def test_naive_non_boundary_times_do_not_expand_dates_to_full_days(self):
        row = coupon(tradeStartDateTime="2026-10-25 10:30:00", tradeEndDateTime="2026-10-27 22:00:00")
        snapshot = normalize_official_capture(capture({"query-my-coupons": result({"coupons": [row]})}))
        record = snapshot["records"][0]
        self.assertIsNone(record["starts_at"])
        self.assertIsNone(record["expires_at"])
        self.assertIn("合成展示时间", record["conditions"])
        self.assertTrue(any("开始时间缺少时区" in warning for warning in snapshot["warnings"]))
        self.assertTrue(any("截止时间缺少时区" in warning for warning in snapshot["warnings"]))

    def test_start_non_boundary_does_not_discard_valid_end_boundary(self):
        row = coupon(tradeStartDateTime="2026-10-25 10:30:00")
        snapshot = normalize_official_capture(capture({"query-my-coupons": result({"coupons": [row]})}))
        self.assertIsNone(snapshot["records"][0]["starts_at"])
        self.assertEqual(snapshot["records"][0]["expires_at"], "2026-10-27")

    def test_unavailable_coupon_retains_status_condition(self):
        snapshot = normalize_official_capture(capture({"query-my-coupons": result({"coupons": [coupon(enable=0)]})}))
        self.assertTrue(any("不可用" in item for item in snapshot["records"][0]["conditions"]))

    def test_pages_take_precedence_and_deduplicate_coupon_id(self):
        raw = capture(
            {"query-my-coupons": result({"coupons": [coupon("obsolete")]})},
            {"query-my-coupons": [result({"coupons": [coupon("a")]}),
                                  result({"coupons": [coupon("a"), coupon("b")]})]},
        )
        snapshot = normalize_official_capture(raw)
        self.assertEqual([row["id"] for row in snapshot["records"]], ["query-my-coupons:a", "query-my-coupons:b"])
        self.assertEqual(snapshot["records"][1]["source"]["ref"], "/tool_pages/query-my-coupons/1/structuredContent/data/coupons/1")

    def test_conflicting_duplicate_coupon_is_rejected(self):
        raw = capture(pages={"query-my-coupons": [result({"coupons": [coupon("a")]}),
                                                          result({"coupons": [coupon("a", tradeEndDate="2026-10-29")]})]})
        with self.assertRaisesRegex(ValueError, "冲突"):
            normalize_official_capture(raw)

    def test_points_numeric_strings_only_create_positive_month_windows(self):
        account = {"currentMouthExpirePoint": "12", "nextMouthExpirePoint": "0", "accountId": "synthetic-account-never-imported"}
        snapshot = normalize_official_capture(capture({"query-my-account": result(account)}))
        self.assertEqual(len(snapshot["records"]), 1)
        record = snapshot["records"][0]
        self.assertEqual(record["id"], "query-my-account:current")
        self.assertEqual(record["points"], 12)
        self.assertIsNone(record["expires_at"])
        self.assertIsNone(record["starts_at"])
        self.assertTrue(any("月度到期窗口" in item for item in record["conditions"]))
        self.assertEqual(record["source"]["ref"], "/tool_results/query-my-account/structuredContent/data/currentMouthExpirePoint")
        self.assertNotIn("synthetic-account-never-imported", json.dumps(snapshot))
        plan = build_plan(snapshot, now=datetime.fromisoformat(CAPTURED_AT), planned_visit=True)
        self.assertEqual(plan["actions"][0]["decision"], "check")
        self.assertEqual(plan["summary"]["expiring_points"], 0)

    def test_points_both_windows_use_fixed_internal_ids(self):
        snapshot = normalize_official_capture(capture({"query-my-account": result({"currentMouthExpirePoint": "1", "nextMouthExpirePoint": "2"})}))
        self.assertEqual([row["id"] for row in snapshot["records"]], ["query-my-account:current", "query-my-account:next"])

    def test_points_invalid_values_are_not_rounded_or_coerced(self):
        for invalid in (True, "1.5", -1, None, "unknown"):
            raw = capture({"query-my-account": result({"currentMouthExpirePoint": invalid, "nextMouthExpirePoint": "0"})})
            with self.subTest(value=invalid), self.assertRaisesRegex(ValueError, "非负整数"):
                normalize_official_capture(raw)

    def test_available_same_titles_get_unique_stable_internal_ids(self):
        raw = capture({"available-coupons": result([
            {"couponName": "合成同名券", "couponStatus": "合成状态", "label": "合成标签"},
            {"couponName": "合成同名券", "couponStatus": "合成状态", "label": "合成标签"},
        ])})
        snapshot = normalize_official_capture(raw)
        self.assertEqual(len({row["id"] for row in snapshot["records"]}), 2)
        self.assertEqual(snapshot, normalize_official_capture(deepcopy(raw)))
        for record in snapshot["records"]:
            self.assertIsNone(record["expires_at"])
            self.assertIsNone(record["benefit_cents"])
            self.assertIsNone(record["min_spend_cents"])
            self.assertIn("合成状态", record["conditions"])
            self.assertIn("合成标签", record["conditions"])

    def test_campaign_repeated_days_merge_without_inventing_deadline(self):
        event = {"activityCode": "synthetic-campaign", "resourceId": "synthetic-resource", "activityTitle": "合成活动", "price": "12.5"}
        raw = capture({"campaign-calendar": result({"dailyList": [
            {"date": "2026-10-25", "events": [event]},
            {"date": "2026-10-26", "events": [event]},
        ]})})
        snapshot = normalize_official_capture(raw)
        self.assertEqual(len(snapshot["records"]), 1)
        record = snapshot["records"][0]
        self.assertIsNone(record["expires_at"])
        self.assertIsNone(record["starts_at"])
        self.assertIsNone(record["benefit_cents"])
        self.assertIn("日历展示日期：2026-10-25", record["conditions"])
        self.assertIn("日历展示日期：2026-10-26", record["conditions"])
        self.assertEqual(record["source"]["ref"], "/tool_results/campaign-calendar/structuredContent/data/dailyList/0/events/0")

    def test_campaign_falls_back_to_article_title_and_title_identity(self):
        raw = capture({"campaign-calendar": result({"dailyList": [{"date": "2026-10-25", "events": [
            {"activityTitle": "", "articleDto": {"title": "合成文章活动"}},
            {"articleDto": {"title": "合成文章活动"}},
            {"resourceId": "synthetic-other", "activityTitle": "合成独立活动"},
        ]}]})})
        snapshot = normalize_official_capture(raw)
        self.assertEqual(len(snapshot["records"]), 2)
        self.assertEqual(snapshot["records"][0]["title"], "合成文章活动")

    def test_payload_json_text_provenance_preserves_actual_block_index(self):
        raw = capture({"available-coupons": result([{"couponName": "合成文本券"}], text=True)})
        snapshot = normalize_official_capture(raw)
        self.assertEqual(snapshot["records"][0]["source"]["ref"], "/tool_results/available-coupons/content/1/text#json/data/0")

    def test_rejects_business_and_mcp_errors(self):
        for entry in (result([], code=500), result([], success=False), {"isError": True, "content": []}):
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                normalize_official_capture(capture({"available-coupons": entry}))

    def test_does_not_fall_back_to_results_when_explicit_pages_fail(self):
        raw = capture({"query-my-coupons": result({"coupons": [coupon()]})},
                      {"query-my-coupons": [result({}, code=500)]})
        with self.assertRaisesRegex(ValueError, "业务失败"):
            normalize_official_capture(raw)

    def test_empty_successful_collections_remain_valid(self):
        snapshot = normalize_official_capture(capture({
            "query-my-coupons": result({"coupons": []}),
            "query-my-account": result({"currentMouthExpirePoint": "0", "nextMouthExpirePoint": "0"}),
            "available-coupons": result([]),
            "campaign-calendar": result({"dailyList": []}),
        }))
        self.assertEqual(snapshot["records"], [])

    def test_rejects_unrecognized_or_malformed_capture(self):
        cases = [capture(), capture(pages={"query-my-coupons": []}),
                 capture({"query-my-coupons": result({"unexpected": []})}),
                 capture({"available-coupons": result([{"couponName": ""}])}),
                 dict(capture({"available-coupons": result([])}), mode="demo"),
                 dict(capture({"available-coupons": result([])}), server="https://other.invalid"),
                 dict(capture({"available-coupons": result([])}), captured_at="2026-10-25T12:00:00"),
                 dict(capture({"available-coupons": result([])}), warnings="invalid")]
        for raw in cases:
            with self.subTest(keys=sorted(raw)), self.assertRaises(ValueError):
                normalize_official_capture(raw)


if __name__ == "__main__":
    unittest.main()
