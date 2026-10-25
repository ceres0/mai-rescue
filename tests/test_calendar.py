"""Verify safe export, timing, RFC folding, escaping and stable identities."""

from datetime import datetime, timedelta
import re
import unittest
from zoneinfo import ZoneInfo

from mai_rescue.calendar_export import render_calendar
from mai_rescue.engine import build_plan


NOW = datetime(2026, 10, 25, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def record(record_id, expires, **updates):
    result = {
        "id": record_id, "kind": "coupon", "title": "麦麦优惠券",
        "expires_at": expires, "starts_at": None,
        "benefit_cents": 1000, "min_spend_cents": 0, "points": None,
        "conditions": ["请核实官方条件"],
        "source": {"tool": "query-my-coupons", "ref": "private-source-ref"},
    }
    result.update(updates)
    return result


def plan(*records, now=NOW):
    return build_plan({
        "schema_version": 1, "mode": "demo", "captured_at": NOW.isoformat(),
        "timezone": "Asia/Shanghai", "records": list(records), "warnings": [],
    }, now=now, planned_visit=True)


def unfold(ics):
    return ics.replace("\r\n ", "").replace("\r\n\t", "")


class CalendarTests(unittest.TestCase):
    def test_demo_calendar_and_each_event_explicitly_label_synthetic_data(self):
        ics = unfold(render_calendar(plan(record("demo-a", "2026-10-26"), record("demo-b", "2026-10-27"))))
        calendar_name = next(line for line in ics.split("\r\n") if line.startswith("X-WR-CALNAME:"))
        self.assertIn("合成演示", calendar_name)
        self.assertIn("非真实账户权益", calendar_name)
        event_descriptions = [line for line in ics.split("\r\n") if line.startswith("DESCRIPTION:合成演示")]
        self.assertEqual(len(event_descriptions), 2)
        self.assertTrue(all("非真实账户权益" in line for line in event_descriptions))
        self.assertIn("SUMMARY:【合成演示】", ics)

    def test_live_calendar_retains_original_labels(self):
        data = plan(record("live", "2026-10-26"))
        data["mode"] = "live"
        ics = unfold(render_calendar(data))
        self.assertIn("X-WR-CALNAME:麦麦不浪费 · 权益到期提醒\r\n", ics)
        self.assertNotIn("合成演示", ics)
        self.assertNotIn("非真实账户权益", ics)

    def test_date_only_deadline_is_shanghai_end_of_day_in_utc(self):
        ics = unfold(render_calendar(plan(record("date-only", "2026-10-26"))))
        self.assertIn("DTSTART:20261026T155959Z\r\n", ics)
        self.assertIn("DTSTAMP:20261025T040000Z\r\n", ics)
        self.assertIn("TRIGGER;VALUE=DATE-TIME:20261025T155959Z\r\n", ics)

    def test_only_actionable_known_nonexpired_started_records_are_exported(self):
        ics = render_calendar(plan(
            record("eligible", "2026-10-26"),
            record("unknown", None),
            record("expired", "2026-10-24"),
            record("upcoming", "2026-10-30", starts_at="2026-10-26"),
        ))
        self.assertEqual(ics.count("BEGIN:VEVENT\r\n"), 1)

    def test_unplanned_check_items_can_be_exported_but_skip_cannot(self):
        data = plan(record("check", "2026-10-26"), record("skip", "2026-10-27"))
        data["actions"][0]["decision"] = "check"
        data["actions"][1]["decision"] = "skip"
        self.assertEqual(render_calendar(data).count("BEGIN:VEVENT\r\n"), 1)

    def test_available_coupon_deadline_is_not_exported_as_use_deadline(self):
        data = plan(record("available", "2026-10-26", source={"tool": "available-coupons", "ref": "$.coupons[0]"}))
        self.assertEqual(data["actions"][0]["decision"], "check")
        self.assertNotIn("BEGIN:VEVENT", render_calendar(data))

    def test_near_deadline_alarm_uses_remaining_time_instead_of_immediate_alarm(self):
        ics = unfold(render_calendar(plan(record("near", (NOW + timedelta(hours=1)).isoformat()))))
        self.assertIn("TRIGGER;VALUE=DATE-TIME:20261025T043000Z", ics)
        later = unfold(render_calendar(plan(record("later", (NOW + timedelta(hours=8)).isoformat()))))
        self.assertIn("TRIGGER;VALUE=DATE-TIME:20261025T110000Z", later)

    def test_no_alarm_after_an_imminent_deadline(self):
        ics = render_calendar(plan(record("imminent", (NOW + timedelta(seconds=30)).isoformat())))
        self.assertEqual(ics.count("BEGIN:VEVENT"), 1)
        self.assertNotIn("BEGIN:VALARM", ics)
        exactly_minute = render_calendar(plan(record("minute", (NOW + timedelta(minutes=1)).isoformat())))
        self.assertIn("BEGIN:VALARM", exactly_minute)

    def test_export_rechecks_timestamp_even_if_urgency_is_wrong(self):
        data = plan(record("expired", "2026-10-24"))
        data["actions"][0].update(urgency="soon", decision="check")
        self.assertNotIn("BEGIN:VEVENT", render_calendar(data))
        data = plan(record("upcoming", "2026-10-30", starts_at="2026-10-26"))
        data["actions"][0].update(urgency="soon", decision="check")
        self.assertNotIn("BEGIN:VEVENT", render_calendar(data))

    def test_stable_uid_does_not_change_with_title_or_deadline(self):
        first = unfold(render_calendar(plan(record("same", "2026-10-26"))))
        second = unfold(render_calendar(plan(record("same", "2026-10-27", title="更新优惠券名称", source={"tool": "query-my-coupons", "ref": "updated-ref"}))))
        self.assertEqual(re.search(r"UID:([^\r]+)", first).group(1), re.search(r"UID:([^\r]+)", second).group(1))
        self.assertNotIn("private-source-ref", first)

    def test_uid_is_stable_when_source_response_order_changes(self):
        before = unfold(render_calendar(plan(record("stable-coupon", "2026-10-26", source={"tool": "query-my-coupons", "ref": "$.coupons[0]"}))))
        after = unfold(render_calendar(plan(record("stable-coupon", "2026-10-26", source={"tool": "query-my-coupons", "ref": "$.coupons[4]"}))))
        self.assertEqual(re.search(r"UID:([^\r]+)", before).group(1), re.search(r"UID:([^\r]+)", after).group(1))

    def test_utf8_octet_folding_crlf_and_text_escaping(self):
        title = "超长中文标题" * 35 + "\\路径;逗号,\r\nSUMMARY:注入"
        ics = render_calendar(plan(record("long", "2026-10-26", title=title)))
        self.assertTrue(ics.endswith("\r\n"))
        self.assertNotIn("\n", ics.replace("\r\n", ""))
        self.assertTrue(all(len(line.encode("utf-8")) <= 75 for line in ics.split("\r\n")))
        self.assertIn("\\\\路径\\;逗号\\,\\nSUMMARY:注入", unfold(ics))
        self.assertEqual(len(re.findall(r"^SUMMARY:", unfold(ics), flags=re.MULTILINE)), 1)

    def test_empty_calendar_is_valid_and_has_no_fabricated_events(self):
        ics = render_calendar(plan())
        self.assertTrue(ics.startswith("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"))
        self.assertTrue(ics.endswith("END:VCALENDAR\r\n"))
        self.assertNotIn("BEGIN:VEVENT", ics)


if __name__ == "__main__":
    unittest.main()
