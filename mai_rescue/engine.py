"""Conservative, deterministic planning for verified McDonald's account data.

This module never calls an API or redeems an offer.  It preserves the provenance
of each record and makes the assumptions behind a suggestion explicit.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
SOURCE_TOOLS = {
    "coupon": frozenset({"query-my-coupons", "available-coupons"}),
    "points": frozenset({"query-my-account"}),
    "campaign": frozenset({"campaign-calendar"}),
}
RECORD_FIELDS = (
    "id", "kind", "title", "expires_at", "starts_at", "benefit_cents",
    "min_spend_cents", "points", "conditions", "source",
)
_URGENCY_ORDER = {
    "today": 0, "soon": 1, "later": 2, "unknown": 3,
    "upcoming": 4, "expired": 5,
}


def _integer(value: Any, field: str, *, nullable: bool = False) -> int | None:
    if nullable and value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} 必须是非负整数，不能是布尔值")
    return value


def _text(value: Any, field: str, *, nonempty: bool = True) -> str:
    if not isinstance(value, str) or (nonempty and not value.strip()):
        raise ValueError(f"{field} 必须是{'非空' if nonempty else ''}字符串")
    return value


def _aware_datetime(value: Any, field: str) -> datetime:
    """Parse a real ISO timestamp, refusing implicit/local time zones."""
    if not isinstance(value, str) or "T" not in value:
        raise ValueError(f"{field} 必须是带时区的 ISO 日期时间")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 不是有效的 ISO 日期时间") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} 必须包含时区")
    return parsed.astimezone(SHANGHAI)


def parse_record_time(value: Any, *, end_of_day: bool, field: str) -> datetime | None:
    """Interpret a date-only value in Shanghai, otherwise require an offset.

    Kept public for the calendar exporter so both features share the exact same
    deadline interpretation.  Missing dates remain missing.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} 必须是 ISO 日期、带时区日期时间或 null")
    if len(value) == 10:
        try:
            parsed_date = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{field} 不是有效日期") from exc
        if parsed_date.isoformat() != value:
            raise ValueError(f"{field} 日期必须使用 YYYY-MM-DD 格式")
        clock = time(23, 59, 59) if end_of_day else time(0, 0, 0)
        return datetime.combine(parsed_date, clock, SHANGHAI)
    return _aware_datetime(value, field)


def _validate_record(record: Any, index: int, seen: set[str]) -> tuple[dict, datetime | None, datetime | None]:
    prefix = f"records[{index}]"
    if not isinstance(record, dict):
        raise ValueError(f"{prefix} 必须是对象")
    missing = [key for key in RECORD_FIELDS if key not in record]
    if missing:
        raise ValueError(f"{prefix} 缺少字段：{', '.join(missing)}")
    record_id = _text(record["id"], f"{prefix}.id")
    if record_id in seen:
        raise ValueError(f"重复的记录 id：{record_id}")
    seen.add(record_id)
    kind = record["kind"]
    if not isinstance(kind, str) or kind not in SOURCE_TOOLS:
        raise ValueError(f"{prefix}.kind 必须是 coupon、points 或 campaign")
    _text(record["title"], f"{prefix}.title")
    for field in ("benefit_cents", "min_spend_cents", "points"):
        _integer(record[field], f"{prefix}.{field}", nullable=True)
    conditions = record["conditions"]
    if not isinstance(conditions, list) or any(not isinstance(item, str) for item in conditions):
        raise ValueError(f"{prefix}.conditions 必须是字符串数组")
    source = record["source"]
    if not isinstance(source, dict):
        raise ValueError(f"{prefix}.source 必须是包含 tool 和 ref 的对象")
    tool = _text(source.get("tool"), f"{prefix}.source.tool")
    if tool not in SOURCE_TOOLS[kind]:
        raise ValueError(f"{prefix}.source.tool 与 {kind} 不匹配或不在官方查询工具白名单内")
    _text(source.get("ref"), f"{prefix}.source.ref")
    expires = parse_record_time(record["expires_at"], end_of_day=True, field=f"{prefix}.expires_at")
    starts = parse_record_time(record["starts_at"], end_of_day=False, field=f"{prefix}.starts_at")
    if starts is not None and expires is not None and starts > expires:
        raise ValueError(f"{prefix}.starts_at 不能晚于 expires_at")
    # Copy nested fields too: callers can edit a returned plan without mutating
    # the imported snapshot, and vice versa.
    canonical = {key: record[key] for key in RECORD_FIELDS}
    canonical["conditions"] = list(conditions)
    canonical["source"] = {"tool": tool, "ref": source["ref"]}
    return canonical, expires, starts


def _money(cents: int) -> str:
    return f"¥{cents // 100}.{cents % 100:02d}"


def _deadline_label(expires: datetime | None, now: datetime) -> str:
    if expires is None:
        return "到期时间待核实"
    if expires < now:
        return f"已于 {expires:%Y-%m-%d %H:%M} 到期"
    if expires.date() == now.date():
        return f"今天 {expires:%H:%M} 到期"
    return f"{expires:%Y-%m-%d %H:%M} 到期"


def build_plan(
    snapshot: dict,
    *,
    now: datetime | None = None,
    horizon_days: int = 7,
    budget_cents: int | None = None,
    planned_visit: bool = False,
) -> dict:
    """Validate a snapshot and produce read-only, budget-aware suggestions.

    The horizon is the next ``horizon_days`` * 24 hours, including its endpoint.
    An offer expiring exactly at ``now`` is still valid.  Expired and not-yet-
    started records never receive a ``consider`` decision.  Savings are the
    largest single eligible coupon value; stacking is never assumed.

    All malformed or untrusted shapes fail with ``ValueError``.  Unknown values
    must be explicit ``None``; the engine does not infer amounts or deadlines.
    """
    if not isinstance(snapshot, dict):
        raise ValueError("snapshot 必须是对象")
    if type(snapshot.get("schema_version")) is not int or snapshot["schema_version"] != 1:
        raise ValueError("schema_version 必须是整数 1")
    mode = snapshot.get("mode")
    if mode not in ("demo", "live"):
        raise ValueError("mode 必须是 demo 或 live")
    if snapshot.get("timezone") != "Asia/Shanghai":
        raise ValueError("timezone 必须是 Asia/Shanghai")
    _integer(horizon_days, "horizon_days")
    if horizon_days == 0:
        raise ValueError("horizon_days 必须大于 0")
    _integer(budget_cents, "budget_cents", nullable=True)
    if type(planned_visit) is not bool:
        raise ValueError("planned_visit 必须是布尔值")
    if now is None:
        now = datetime.now(SHANGHAI)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now 必须是带时区的 datetime")
    now = now.astimezone(SHANGHAI)
    captured_at = _aware_datetime(snapshot.get("captured_at"), "captured_at")
    if captured_at > now:
        raise ValueError("captured_at 不能是未来时间")
    try:
        horizon = now + timedelta(days=horizon_days)
    except OverflowError as exc:
        raise ValueError("horizon_days 超出支持的日期范围") from exc
    warnings = snapshot.get("warnings")
    if not isinstance(warnings, list) or any(not isinstance(item, str) for item in warnings):
        raise ValueError("warnings 必须是字符串数组")
    warnings = list(warnings)
    if now - captured_at > timedelta(hours=24):
        warnings.append("快照已超过 24 小时，请重新查询官方数据；优惠库存、账户状态和活动条件可能已变化。")
    records = snapshot.get("records")
    if not isinstance(records, list):
        raise ValueError("records 必须是数组")

    actions = []
    deadlines: dict[str, datetime | None] = {}
    summary = {
        "total": len(records), "active": 0, "due_soon": 0, "unknown": 0,
        "expired": 0, "max_saving_cents": 0, "expiring_points": 0,
    }
    seen: set[str] = set()
    for index, raw in enumerate(records):
        record, expires, starts = _validate_record(raw, index, seen)
        deadlines[record["id"]] = expires
        is_expired = expires is not None and expires < now
        is_upcoming = starts is not None and starts > now
        if expires is None:
            summary["unknown"] += 1
        if is_expired:
            urgency = "expired"
            summary["expired"] += 1
        elif is_upcoming:
            urgency = "upcoming"
        elif expires is None:
            urgency = "unknown"
        elif expires.date() == now.date():
            urgency = "today"
        elif expires <= horizon:
            urgency = "soon"
        else:
            urgency = "later"
        if not is_expired and not is_upcoming:
            summary["active"] += 1
        if urgency in ("today", "soon"):
            summary["due_soon"] += 1
        if (
            record["kind"] == "points" and expires is not None
            and now <= expires <= horizon and record["points"] is not None
        ):
            summary["expiring_points"] += record["points"]

        if is_expired:
            decision, reason = "skip", "已过期，不能作为可使用权益推荐。"
        elif record["kind"] == "coupon" and budget_cents is not None and record["min_spend_cents"] is not None and record["min_spend_cents"] > budget_cents:
            decision = "skip"
            reason = f"使用门槛 {_money(record['min_spend_cents'])} 超过本次预算 {_money(budget_cents)}，跳过以免额外消费。"
        elif is_upcoming:
            decision, reason = "check", "尚未开始；开始后再到官方页面核实可用状态。"
        elif expires is None:
            decision, reason = "check", "官方数据未提供明确到期时间；请核实后再使用，不猜测截止日。"
        elif record["kind"] == "points":
            decision, reason = "check", "查看官方积分有效期和可兑商品，按实际需要决定；本计划不会自动兑换。"
        elif record["kind"] == "campaign":
            decision, reason = "check", "查看官方活动条件、门店和时段，确认适用后再参加。"
        elif not planned_visit:
            decision, reason = "check", "尚无就餐计划；仅查看并核实优惠，避免为了用券增加消费。"
        elif record["benefit_cents"] is None:
            decision, reason = "check", "优惠金额未知，请在官方页面核实实际优惠和使用条件。"
        elif record["min_spend_cents"] is None:
            decision, reason = "check", "使用门槛未知，无法确认适合原定消费；请先在官方页面核实。"
        else:
            decision = "consider"
            reason = "已有就餐计划，可在原定消费内考虑此券；结算前核实门店、时段、门槛和实际优惠，不假设与其他券叠加。"
        if record["kind"] == "coupon" and record["source"]["tool"] == "available-coupons" and decision != "skip":
            claim_reason = "这是尚未领取的可领券；需在官方渠道自行领取并核实是否成功、可用状态和使用条件，本项目不自动领券。"
            reason = claim_reason + (reason if decision == "check" else "")
            decision = "check"
        if record["kind"] == "coupon" and decision == "consider":
            summary["max_saving_cents"] = max(summary["max_saving_cents"], record["benefit_cents"] or 0)
        actions.append({
            **record,
            "urgency": urgency,
            "decision": decision,
            "reason": reason,
            "deadline_label": _deadline_label(expires, now),
        })
    # Earlier real deadlines come first within an urgency bucket; Python's
    # stable sort retains input order for equal deadlines and unknown dates.
    actions.sort(key=lambda action: (
        _URGENCY_ORDER[action["urgency"]],
        deadlines[action["id"]] or datetime.max.replace(tzinfo=SHANGHAI),
    ))
    return {
        "mode": mode,
        "generated_at": now.isoformat(),
        "snapshot_at": captured_at.isoformat(),
        "horizon_days": horizon_days,
        "budget_cents": budget_cents,
        "planned_visit": planned_visit,
        "summary": summary,
        "actions": actions,
        "warnings": warnings,
    }
