"""Behavioral tests for planning, provenance and timestamp boundaries."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest
from zoneinfo import ZoneInfo

from mai_rescue.engine import build_plan


SHANGHAI = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 10, 25, 12, 0, tzinfo=SHANGHAI)


def record(record_id="coupon-1", *, kind="coupon", expires_at="2026-10-26", **updates):
    source_tool = {
        "coupon": "query-my-coupons", "points": "query-my-account",
        "campaign": "campaign-calendar",
    }[kind]
    result = {
        "id": record_id, "kind": kind, "title": "已知权益",
        "expires_at": expires_at, "starts_at": None,
        "benefit_cents": 1000 if kind == "coupon" else None,
        "min_spend_cents": 2000 if kind == "coupon" else None,
        "points": 100 if kind == "points" else None,
        "conditions": ["部分门店适用"],
        "source": {"tool": source_tool, "ref": "official-record-1"},
    }
    result.update(updates)
    return result


def snapshot(*records, captured_at=None):
    return {
        "schema_version": 1, "mode": "demo", "timezone": "Asia/Shanghai",
        "captured_at": captured_at or NOW.isoformat(),
        "records": list(records), "warnings": [],
    }


class PlanTests(unittest.TestCase):
    def plan(self, *records, **options):
        return build_plan(snapshot(*records), now=NOW, **options)

    def test_unplanned_visit_never_recommends_spending(self):
        plan = self.plan(record())
        self.assertEqual(plan["actions"][0]["decision"], "check")
        self.assertIn("为了用券", plan["actions"][0]["reason"])
        self.assertEqual(plan["summary"]["max_saving_cents"], 0)

    def test_unclaimed_available_coupon_requires_manual_official_claim(self):
        plan = self.plan(record(source={"tool": "available-coupons", "ref": "claimable"}), planned_visit=True, budget_cents=3000)
        action = plan["actions"][0]
        self.assertEqual(action["decision"], "check")
        self.assertIn("尚未领取", action["reason"])
        self.assertIn("官方渠道自行领取", action["reason"])
        self.assertIn("不自动领券", action["reason"])
        self.assertEqual(plan["summary"]["max_saving_cents"], 0)

    def test_unclaimed_coupon_retains_unknown_deadline_explanation(self):
        action = self.plan(record(expires_at=None, source={"tool": "available-coupons", "ref": "claimable"}), planned_visit=True)["actions"][0]
        self.assertEqual(action["decision"], "check")
        self.assertIn("不自动领券", action["reason"])
        self.assertIn("未提供明确到期时间", action["reason"])

    def test_coupon_saving_is_largest_single_coupon_not_sum(self):
        plan = self.plan(
            record("small", benefit_cents=500),
            record("large", benefit_cents=1200),
            record("larger-expired", benefit_cents=2000, expires_at="2026-10-24"),
            planned_visit=True, budget_cents=3000,
        )
        self.assertEqual(plan["summary"]["max_saving_cents"], 1200)
        self.assertEqual([action["decision"] for action in plan["actions"]], ["consider", "consider", "skip"])

    def test_budget_uses_spending_threshold_without_subtracting_discount(self):
        action = self.plan(record(benefit_cents=1000, min_spend_cents=3000), planned_visit=True, budget_cents=2500)["actions"][0]
        self.assertEqual(action["decision"], "skip")
        self.assertIn("¥30.00", action["reason"])

    def test_upcoming_over_budget_coupon_is_still_skipped(self):
        action = self.plan(record(starts_at="2026-10-26", min_spend_cents=3000), planned_visit=True, budget_cents=2500)["actions"][0]
        self.assertEqual(action["urgency"], "upcoming")
        self.assertEqual(action["decision"], "skip")

    def test_missing_deadline_and_amounts_remain_unknown(self):
        plan = self.plan(record("no-date", expires_at=None), record("no-value", benefit_cents=None), record("no-threshold", min_spend_cents=None), planned_visit=True, budget_cents=5000)
        by_id = {action["id"]: action for action in plan["actions"]}
        self.assertIsNone(by_id["no-date"]["expires_at"])
        self.assertEqual(by_id["no-date"]["urgency"], "unknown")
        self.assertEqual(plan["summary"]["unknown"], 1)
        self.assertTrue(all(action["decision"] == "check" for action in plan["actions"]))

    def test_date_only_deadline_is_end_of_shanghai_day(self):
        record_today = record(expires_at="2026-10-25")
        last_second = NOW.replace(hour=23, minute=59, second=59)
        at_deadline = build_plan(snapshot(record_today), now=last_second, planned_visit=True)
        self.assertEqual(at_deadline["actions"][0]["urgency"], "today")
        self.assertEqual(at_deadline["actions"][0]["decision"], "consider")
        after_deadline = build_plan(snapshot(record_today), now=last_second + timedelta(seconds=1), planned_visit=True)
        self.assertEqual(after_deadline["actions"][0]["decision"], "skip")

    def test_start_date_is_midnight_and_upcoming_is_not_active(self):
        plan = self.plan(record("start-today", starts_at="2026-10-25"), record("start-tomorrow", starts_at="2026-10-26"), planned_visit=True)
        self.assertEqual(plan["summary"]["active"], 1)
        self.assertEqual([action["urgency"] for action in plan["actions"]], ["soon", "upcoming"])
        self.assertEqual(plan["actions"][1]["decision"], "check")

    def test_horizon_is_inclusive_and_utc_values_convert_to_shanghai(self):
        edge = NOW + timedelta(days=7)
        plan = self.plan(record("edge", expires_at=edge.astimezone(timezone.utc).isoformat()), record("outside", expires_at=(edge + timedelta(seconds=1)).isoformat()), planned_visit=True)
        self.assertEqual([action["urgency"] for action in plan["actions"]], ["soon", "later"])
        self.assertEqual(plan["summary"]["due_soon"], 1)

    def test_points_and_campaigns_only_prompt_checking(self):
        plan = self.plan(record("points", kind="points", points=80), record("expired-points", kind="points", expires_at="2026-10-24", points=500), record("far-points", kind="points", expires_at="2026-12-31", points=600), record("activity", kind="campaign"), planned_visit=True)
        self.assertEqual(plan["summary"]["expiring_points"], 80)
        by_id = {action["id"]: action for action in plan["actions"]}
        self.assertEqual(by_id["points"]["decision"], "check")
        self.assertEqual(by_id["activity"]["decision"], "check")
        self.assertEqual(by_id["expired-points"]["decision"], "skip")

    def test_sort_is_stable_for_equal_deadlines_within_urgency_bucket(self):
        plan = self.plan(record("soon-1"), record("today-1", expires_at="2026-10-25"), record("soon-2"), record("unknown", expires_at=None), record("expired", expires_at="2026-10-24"), record("today-2", expires_at="2026-10-25"))
        self.assertEqual([action["id"] for action in plan["actions"]], ["today-1", "today-2", "soon-1", "soon-2", "unknown", "expired"])

    def test_sort_uses_real_deadlines_within_urgency_bucket(self):
        plan = self.plan(record("late", expires_at="2026-10-26T20:00:00+08:00"), record("early-utc", expires_at="2026-10-26T00:00:00Z"), record("same-shanghai", expires_at="2026-10-26T08:00:00+08:00"))
        self.assertEqual([action["id"] for action in plan["actions"]], ["early-utc", "same-shanghai", "late"])

    def test_old_snapshot_warns_only_after_24_hours(self):
        at_limit = build_plan(snapshot(captured_at=(NOW - timedelta(hours=24)).isoformat()), now=NOW)
        self.assertEqual(at_limit["warnings"], [])
        old = build_plan(snapshot(captured_at=(NOW - timedelta(hours=24, seconds=1)).isoformat()), now=NOW)
        self.assertTrue(any("24 小时" in warning for warning in old["warnings"]))

    def test_plan_and_snapshot_do_not_share_nested_mutable_values(self):
        original = snapshot(record())
        original["warnings"].append("测试")
        before = deepcopy(original)
        plan = build_plan(original, now=NOW)
        plan["actions"][0]["conditions"].append("changed")
        plan["actions"][0]["source"]["ref"] = "changed"
        plan["warnings"].append("changed")
        self.assertEqual(original, before)

    def test_empty_snapshot_and_live_mode(self):
        imported = snapshot()
        imported["mode"] = "live"
        plan = build_plan(imported, now=NOW)
        self.assertEqual(plan["mode"], "live")
        self.assertEqual(plan["summary"]["total"], 0)
        self.assertEqual(plan["actions"], [])


class ValidationTests(unittest.TestCase):
    def assert_invalid(self, data, **options):
        with self.assertRaises(ValueError):
            build_plan(data, now=NOW, **options)

    def test_duplicate_ids_and_wrong_kind(self):
        self.assert_invalid(snapshot(record(), record()))
        self.assert_invalid(snapshot(record(kind="coupon") | {"kind": "free-food"}))

    def test_official_provenance_must_match_kind(self):
        for tool in ("arbitrary-api", "query-my-account", "campaign-calendar"):
            with self.subTest(tool=tool):
                self.assert_invalid(snapshot(record(source={"tool": tool, "ref": "x"})))
        valid = record(source={"tool": "available-coupons", "ref": "official"})
        self.assertEqual(build_plan(snapshot(valid), now=NOW)["summary"]["total"], 1)

    def test_negative_or_boolean_numeric_fields(self):
        for field in ("benefit_cents", "min_spend_cents", "points"):
            for bad_value in (-1, True, False, 1.5, "100"):
                with self.subTest(field=field, value=bad_value):
                    self.assert_invalid(snapshot(record(**{field: bad_value})))
        for bad_value in (-1, True, 1.5):
            self.assert_invalid(snapshot(record()), budget_cents=bad_value)
        for bad_value in (0, -1, True, "7", 10**20):
            self.assert_invalid(snapshot(record()), horizon_days=bad_value)
        self.assert_invalid(snapshot(record()), planned_visit=1)

    def test_timestamps_must_be_aware_and_snapshot_cannot_be_future(self):
        self.assert_invalid(snapshot(record(), captured_at="2026-10-25T12:00:00"))
        self.assert_invalid(snapshot(record(), captured_at=(NOW + timedelta(seconds=1)).isoformat()))
        self.assert_invalid(snapshot(record(expires_at="2026-10-25T20:00:00")))
        self.assert_invalid(snapshot(record(starts_at="2026-10-25T00:00:00")))
        with self.assertRaises(ValueError):
            build_plan(snapshot(record()), now=datetime(2026, 10, 25, 12))

    def test_malformed_dates_and_reversed_validity(self):
        for bad_date in ("2026-02-30", "2026-W43-7", "not-a-date", 123):
            self.assert_invalid(snapshot(record(expires_at=bad_date)))
        self.assert_invalid(snapshot(record(starts_at="2026-10-27", expires_at="2026-10-26")))

    def test_schema_shapes_are_validated(self):
        for key, value in (("schema_version", True), ("schema_version", 2), ("mode", "guessed"), ("timezone", "UTC"), ("records", {}), ("warnings", "oops")):
            data = snapshot(record())
            data[key] = value
            self.assert_invalid(data)
        for key, value in (("conditions", [None]), ("source", {}), ("id", ""), ("title", "")):
            self.assert_invalid(snapshot(record(**{key: value})))
        missing = record()
        del missing["expires_at"]
        self.assert_invalid(snapshot(missing))


if __name__ == "__main__":
    unittest.main()
