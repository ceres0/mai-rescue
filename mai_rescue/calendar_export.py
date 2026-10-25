"""Privacy-preserving RFC 5545 export of explicit, still-valid deadlines."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

from .engine import _aware_datetime, parse_record_time


def _escape(value: str) -> str:
    """Escape RFC 5545 TEXT, including all newline spellings."""
    return (
        value.replace("\\", "\\\\").replace("\r\n", "\n").replace("\r", "\n")
        .replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")
    )


def _fold(line: str) -> str:
    """Fold at 75 UTF-8 octets without splitting a multibyte character."""
    pieces = []
    current = ""
    octets = 0
    for char in line:
        width = len(char.encode("utf-8"))
        if octets + width > 75:
            pieces.append(current)
            current, octets = " ", 1
        current += char
        octets += width
    pieces.append(current)
    return "\r\n".join(pieces)


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _uid(action: dict) -> str:
    # Do not leak account identifiers, coupon codes, or private source refs into
    # the UID.  Deadline updates retain the same event identity on re-import.
    identity = json.dumps(
        {"id": action["id"], "kind": action["kind"], "tool": action["source"]["tool"]},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return f"{sha256(identity.encode('utf-8')).hexdigest()}@mai-rescue.local"


def render_calendar(plan: dict) -> str:
    """Render a calendar; no external service is contacted and nothing is sent.

    Only ``consider``/``check`` records with explicit unexpired deadlines are
    exported.  The event itself marks the actual deadline.  A display alarm is
    placed one day earlier, otherwise one hour earlier. For deadlines closer
    than an hour, use half the remaining time. Less than one minute gets no
    alarm; importing after an alarm's absolute time cannot trigger it again.
    """
    if not isinstance(plan, dict):
        raise ValueError("plan 必须是 build_plan 返回的对象")
    now = _aware_datetime(plan.get("generated_at"), "generated_at")
    actions = plan.get("actions")
    if not isinstance(actions, list):
        raise ValueError("plan.actions 必须是数组")
    is_demo = plan.get("mode") == "demo"
    demo_notice = "合成演示数据，非真实账户权益；这些事件仅用于体验功能。"
    calendar_name = "麦麦不浪费 · 权益到期提醒"
    if is_demo:
        calendar_name = "麦麦不浪费 · 合成演示提醒（非真实账户权益）"
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Mai Rescue//Deadline Planner 1.0//ZH-CN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:" + _escape(calendar_name),
    ]
    for action in actions:
        if not isinstance(action, dict):
            raise ValueError("plan.actions 中的每条记录必须是对象")
        if action.get("decision") not in ("consider", "check"):
            continue
        if action.get("urgency") in ("expired", "upcoming"):
            continue
        source = action.get("source")
        # Available-coupon deadlines may describe claiming rather than using
        # the coupon.  Without an explicit deadline kind they cannot safely be
        # presented as account-benefit expiry events.
        if isinstance(source, dict) and source.get("tool") == "available-coupons":
            continue
        deadline = parse_record_time(action.get("expires_at"), end_of_day=True, field="expires_at")
        if deadline is None or deadline < now:
            continue
        starts = parse_record_time(action.get("starts_at"), end_of_day=False, field="starts_at")
        if starts is not None and starts > now:
            continue
        title = action.get("title")
        if not isinstance(title, str) or not title:
            raise ValueError("导出记录必须有 title")
        description = "\n".join([
            *([demo_notice] if is_demo else []),
            "到期前请在麦当劳官方页面核实状态和使用条件。",
            str(action.get("reason", "")),
            *(str(condition) for condition in action.get("conditions", [])),
            "此提醒不兑换、不下单；优惠是否可用以官方页面为准。",
        ])
        lines.extend([
            "BEGIN:VEVENT",
            "UID:" + _uid(action),
            "DTSTAMP:" + _utc(now),
            "DTSTART:" + _utc(deadline),
            "SUMMARY:" + _escape(("【合成演示】" if is_demo else "") + title + " · 到期"),
            "DESCRIPTION:" + _escape(description),
            "STATUS:CONFIRMED",
            "TRANSP:TRANSPARENT",
        ])
        alarm = deadline - timedelta(days=1)
        if alarm <= now:
            alarm = deadline - timedelta(hours=1)
        if alarm <= now:
            alarm = now + (deadline - now) / 2
        if deadline - now >= timedelta(minutes=1) and now < alarm <= deadline:
            lines.extend([
                "BEGIN:VALARM",
                "ACTION:DISPLAY",
                "TRIGGER;VALUE=DATE-TIME:" + _utc(alarm),
                "DESCRIPTION:" + _escape(("【合成演示】" if is_demo else "") + title + " 即将到期，请先核实官方条件"),
                "END:VALARM",
            ])
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
