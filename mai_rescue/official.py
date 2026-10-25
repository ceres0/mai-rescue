"""Optional conservative adapter for the observed official October 2026 shape.

This profile complements runtime tools/list discovery; it is not a claim that
these response fields form a published or permanent API contract. No account
identifiers, coupon codes, or account-specific examples belong in this module.
"""

from __future__ import annotations

from datetime import date, datetime, time
import hashlib
import re
from typing import Any, Iterator

from .engine import build_plan
from .normalize import payload


PROFILE = "official-2026-10"
_TOOLS = ("query-my-coupons", "query-my-account", "available-coupons", "campaign-calendar")
_UNKNOWN_WARNING = (
    "优惠金额、使用门槛或精确截止日期未知的权益仅列为待核实；"
    "不估算节省金额，不猜测截止日期。"
)


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{PROFILE}: {field} 必须为非空字符串，响应结构可能已变化")
    return value


def _object(value: Any, field: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{PROFILE}: {field} 必须为对象，响应结构可能已变化")
    return value


def _array(value: Any, field: str) -> list:
    if not isinstance(value, list):
        raise ValueError(f"{PROFILE}: {field} 必须为数组，响应结构可能已变化")
    return value


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _date(value: Any) -> str | None:
    # Never substitute trade*DateTime or calendar display dates for this field.
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def _boundary_date(coupon: dict, *, end: bool) -> tuple[str | None, str | None]:
    prefix = "tradeEnd" if end else "tradeStart"
    pure = _date(coupon.get(prefix + "Date"))
    full = coupon.get(prefix + "DateTime")
    if pure is None or full is None or full == "":
        return pure, None
    label = "截止" if end else "开始"
    try:
        parsed = datetime.fromisoformat(full) if isinstance(full, str) else None
    except ValueError:
        parsed = None
    expected = time(23, 59, 59) if end else time(0, 0, 0)
    if (
        parsed is not None
        and parsed.tzinfo is None
        and parsed.date().isoformat() == pure
        and parsed.time() == expected
    ):
        return pure, None
    if parsed is not None and parsed.tzinfo is None:
        return None, f"精确核销{label}时间缺少时区且与日期全天边界不一致，{label}时间待核实。"
    return None, f"精确核销{label}时间与已验证结构不同，{label}时间待核实。"


def _conditions(*values: Any) -> list[str]:
    return list(dict.fromkeys(value for value in values if isinstance(value, str) and value.strip()))


def _record(tool: str, kind: str, identifier: str, title: str, ref: str) -> dict:
    return {
        "id": f"{tool}:{identifier}",
        "kind": kind,
        "title": title,
        "expires_at": None,
        "starts_at": None,
        "benefit_cents": None,
        "min_spend_cents": None,
        "points": None,
        "conditions": [],
        "source": {"tool": tool, "ref": ref},
    }


def _documents(capture: dict, tool: str) -> Iterator[tuple[dict, str]]:
    pages = capture.get("tool_pages", {})
    if tool in pages:
        results = _array(pages[tool], f"tool_pages.{tool}")
        if not results:
            raise ValueError(f"{PROFILE}: 已提供的工具分页不能为空")
        selected = [(result, f"/tool_pages/{tool}/{index}") for index, result in enumerate(results)]
    elif tool in capture["tool_results"]:
        selected = [(capture["tool_results"][tool], f"/tool_results/{tool}")]
    else:
        return
    for result, root in selected:
        result = _object(result, f"{tool} result")
        document, origin = payload(result)
        document = _object(document, f"{tool} payload")
        if document.get("code") != 200 or document.get("success") is False:
            raise ValueError(f"{PROFILE}: {tool} 返回业务失败，不能生成权益快照")
        if origin == "structuredContent":
            ref = root + "/structuredContent"
        else:
            # payload requires exactly one text block, which may follow images.
            index = next(index for index, block in enumerate(result["content"])
                         if isinstance(block, dict) and block.get("type") == "text")
            ref = root + f"/content/{index}/text#json"
        yield document, ref


def _coupons(document: dict, ref: str) -> list[dict]:
    data = _object(document.get("data"), "query-my-coupons.data")
    coupons = _array(data.get("coupons"), "query-my-coupons.data.coupons")
    records = []
    for index, raw in enumerate(coupons):
        coupon = _object(raw, "query-my-coupons coupon")
        identifier = coupon.get("id")
        if isinstance(identifier, bool) or not isinstance(identifier, (str, int)) or not str(identifier).strip():
            raise ValueError(f"{PROFILE}: 优惠券缺少有效 id")
        title = _text(coupon.get("title"), "query-my-coupons.title")
        record = _record("query-my-coupons", "coupon", str(identifier), title, f"{ref}/data/coupons/{index}")
        record["starts_at"], start_warning = _boundary_date(coupon, end=False)
        record["expires_at"], end_warning = _boundary_date(coupon, end=True)
        discount = coupon.get("discountInfo")
        discount_desc = discount.get("discountDesc") if isinstance(discount, dict) else None
        record["conditions"] = _conditions(
            coupon.get("subtitle"), coupon.get("datetimeText"), discount_desc,
            start_warning, end_warning,
            "优惠金额和使用门槛未确认，请在官方页面核实。",
        )
        if coupon.get("enable") == 0:
            record["conditions"].append("官方当前标记不可用，请核实适用门店、时段和使用条件。")
        if record["starts_at"] is None or record["expires_at"] is None:
            record["conditions"].append("核销开始或截止时间仍有未知，请核实官方展示时段；不猜测截止日。")
        records.append(record)
    return records


def _points(document: dict, ref: str) -> list[dict]:
    data = _object(document.get("data"), "query-my-account.data")
    records = []
    for field, identifier, label in (
        ("currentMouthExpirePoint", "current", "本月到期积分窗口"),
        ("nextMouthExpirePoint", "next", "下月到期积分窗口"),
    ):
        value = data.get(field)
        if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
            value = int(value)
        if type(value) is not int or value < 0:
            raise ValueError(f"{PROFILE}: {field} 必须为非负整数或整数字符串")
        if value == 0:
            continue
        record = _record("query-my-account", "points", identifier, label, f"{ref}/data/{field}")
        record["points"] = value
        record["conditions"] = ["官方仅提供月度到期窗口，未提供精确截止时间；请在官方渠道核实，不推算月末截止日期。"]
        records.append(record)
    return records


def _available(document: dict, ref: str) -> list[dict]:
    records = []
    for index, raw in enumerate(_array(document.get("data"), "available-coupons.data")):
        coupon = _object(raw, "available-coupons coupon")
        title = _text(coupon.get("couponName"), "available-coupons.couponName")
        source = f"{ref}/data/{index}"
        record = _record("available-coupons", "coupon", _hash(title + "\0" + source), title, source)
        record["conditions"] = _conditions(coupon.get("couponStatus"), coupon.get("label"))
        record["conditions"].append("日期、优惠金额和使用门槛未确认，请在官方渠道核实领取与可用状态。")
        records.append(record)
    return records


def _campaigns(document: dict, ref: str) -> list[dict]:
    data = _object(document.get("data"), "campaign-calendar.data")
    records = []
    for day_index, raw_day in enumerate(_array(data.get("dailyList"), "campaign-calendar.data.dailyList")):
        day = _object(raw_day, "campaign-calendar day")
        for event_index, raw_event in enumerate(_array(day.get("events"), "campaign-calendar day.events")):
            event = _object(raw_event, "campaign-calendar event")
            article = event.get("articleDto")
            title = event.get("activityTitle")
            if not isinstance(title, str) or not title.strip():
                title = article.get("title") if isinstance(article, dict) else None
            title = _text(title, "campaign-calendar activity title")
            identity = "title:" + title
            for field in ("resourceId", "activityCode"):
                value = event.get(field)
                if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value).strip():
                    identity = field + ":" + str(value)
            source = f"{ref}/data/dailyList/{day_index}/events/{event_index}"
            record = _record("campaign-calendar", "campaign", _hash(identity), title, source)
            record["conditions"] = ["日历展示日期不代表活动结束日期，活动截止时间和参与条件需在官方页面核实。"]
            display_date = day.get("date")
            if isinstance(display_date, str) and display_date.strip():
                record["conditions"].append("日历展示日期：" + display_date)
            records.append(record)
    return records


def normalize_official_capture(capture: dict) -> dict:
    """Convert successful official capture data using ``official-2026-10``.

    Tool pages take precedence over tool_results. Coupon IDs deduplicate pages;
    campaign IDs deduplicate repeated daily calendar placements. For merged
    records, source.ref identifies the first exact source occurrence.
    """
    capture = _object(capture, "capture")
    if capture.get("mode") != "live" or capture.get("server") != "https://mcp.mcd.cn":
        raise ValueError(f"{PROFILE}: 仅接受来自固定官方 Server 的 live capture")
    results = capture.get("tool_results", {})
    pages = capture.get("tool_pages", {})
    _object(results, "capture.tool_results")
    _object(pages, "capture.tool_pages")
    # Permit a pages-only capture while keeping this helper's lookup explicit.
    capture = {**capture, "tool_results": results}
    raw_warnings = capture.get("warnings", [])
    if not isinstance(raw_warnings, list) or any(not isinstance(item, str) for item in raw_warnings):
        raise ValueError(f"{PROFILE}: capture.warnings 必须为字符串数组")
    warnings = [*raw_warnings, _UNKNOWN_WARNING]
    records: dict[str, dict] = {}
    converters = {
        "query-my-coupons": _coupons,
        "query-my-account": _points,
        "available-coupons": _available,
        "campaign-calendar": _campaigns,
    }
    found = False
    for tool in _TOOLS:
        if tool not in results and tool not in pages:
            warnings.append(f"未采集 {tool}，本快照不包含该来源的数据。")
        for document, ref in _documents(capture, tool):
            found = True
            for record in converters[tool](document, ref):
                for condition in record["conditions"]:
                    if condition.startswith("精确核销"):
                        warnings.append(condition)
                previous = records.get(record["id"])
                if previous is None:
                    records[record["id"]] = record
                elif tool == "campaign-calendar":
                    previous["conditions"] = _conditions(*previous["conditions"], *record["conditions"])
                elif any(previous[field] != record[field] for field in record if field != "source"):
                    raise ValueError(f"{PROFILE}: 同一权益 id 对应冲突内容，请重新采集或手动核实")
    if not found:
        raise ValueError(f"{PROFILE}: capture 没有可适配的官方查询结果")
    snapshot = {
        "schema_version": 1,
        "mode": "live",
        "timezone": "Asia/Shanghai",
        "captured_at": capture.get("captured_at"),
        "records": list(records.values()),
        "warnings": list(dict.fromkeys(warnings)),
    }
    # Validate the complete canonical contract at its capture time; retain the
    # original captured_at string rather than rewriting its representation.
    captured_at = snapshot["captured_at"]
    if not isinstance(captured_at, str):
        raise ValueError(f"{PROFILE}: captured_at 必须是带时区日期时间")
    try:
        now = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"{PROFILE}: captured_at 必须是带时区日期时间") from None
    build_plan(snapshot, now=now)
    return snapshot
