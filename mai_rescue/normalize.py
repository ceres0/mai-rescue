"""Explicit JSON-pointer mappings; no guessed official response schema."""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

KINDS = {"query-my-coupons": "coupon", "available-coupons": "coupon",
         "query-my-account": "points", "campaign-calendar": "campaign"}
FIELDS = {"id", "title", "expires_at", "starts_at", "benefit_cents",
          "min_spend_cents", "points", "conditions"}


def pointer(document: Any, path: str) -> Any:
    """Resolve an RFC 6901 JSON pointer (empty pointer selects the root)."""
    if not isinstance(path, str) or (path and not path.startswith("/")):
        raise ValueError("JSON pointer 必须为空字符串或以 / 开头")
    value = document
    for part in path.split("/")[1:]:
        # Reject malformed escapes rather than silently selecting a different field.
        if re.search(r"~(?![01])", part):
            raise ValueError("JSON pointer 存在无效 ~ 转义")
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            if not key.isascii() or not key.isdigit() or (key.startswith("0") and key != "0"):
                raise KeyError(path)
            value = value[int(key)]
        elif isinstance(value, dict):
            value = value[key]
        else:
            raise KeyError(path)
    return value


def payload(result: dict) -> tuple[Any, str]:
    if result.get("isError"):
        raise ValueError("工具调用失败，不能标准化失败结果")
    if "structuredContent" in result:
        return result["structuredContent"], "structuredContent"
    blocks = result.get("content", [])
    texts = [b.get("text") for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
    if len(texts) != 1:
        raise ValueError("工具没有唯一 JSON 文本或 structuredContent；请依据真实输出手动整理规范快照")
    try:
        return json.loads(texts[0]), "content:text:json"
    except (ValueError, TypeError) as exc:
        raise ValueError("工具文本不是 JSON；请依据真实输出手动整理规范快照，不要猜测字段") from exc


def _field(record: dict, spec: Any, field: str) -> Any:
    if isinstance(spec, str):
        return pointer(record, spec)
    if not isinstance(spec, dict):
        raise ValueError(f"字段 {field} 的映射必须为 JSON pointer 或配置对象")
    if set(spec) == {"value"}:
        if field not in {"id", "title", "conditions"}:
            raise ValueError(f"禁止为 {field} 填写常量，必须来自真实响应")
        return spec["value"]
    if set(spec) == {"path", "multiply"} and field in {"benefit_cents", "min_spend_cents"}:
        raw = pointer(record, spec["path"])
        if raw is None:
            return None
        if isinstance(raw, bool) or isinstance(spec["multiply"], bool):
            raise ValueError("金额和换算倍率不能为布尔值")
        try:
            amount = Decimal(str(raw)) * Decimal(str(spec["multiply"]))
            if not amount.is_finite() or amount < 0 or amount != amount.to_integral_value():
                raise ValueError("金额换算后必须为非负整数分，不做静默舍入")
            return int(amount)
        except InvalidOperation as exc:
            raise ValueError("无效金额或换算倍率") from exc
    raise ValueError(f"不支持字段 {field} 的映射配置")


def normalize_capture(capture: dict, mapping: dict) -> dict:
    if not isinstance(capture, dict) or not isinstance(mapping, dict):
        raise ValueError("capture 和 mapping 必须为 JSON 对象")
    if capture.get("mode") != "live" or capture.get("server") != "https://mcp.mcd.cn":
        raise ValueError("仅接受来自固定官方 Server 的 live capture；演示请使用 demo 命令")
    if type(mapping.get("schema_version")) is not int or mapping["schema_version"] != 1:
        raise ValueError("mapping.schema_version 必须为 1")
    collections = mapping.get("collections")
    if not isinstance(collections, list) or not collections:
        raise ValueError("mapping.collections 必须为非空数组")
    raw_warnings = capture.get("warnings", [])
    if not isinstance(raw_warnings, list) or any(not isinstance(item, str) for item in raw_warnings):
        raise ValueError("capture.warnings 必须为字符串数组")
    if not isinstance(capture.get("tool_results"), dict):
        raise ValueError("capture.tool_results 必须为对象")
    warnings = list(raw_warnings)
    if len(capture.get("tool_pages", {}).get("query-my-coupons", [])) > 1:
        warnings.append("显式映射仅选择 tool_results 中的优惠券首页；完整分页请使用 official-2026-10 profile 或手动核对所有页。")
    records = []
    for collection in collections:
        if not isinstance(collection, dict):
            raise ValueError("collections 中的每一项必须为对象")
        tool = collection.get("tool")
        kind = collection.get("kind")
        if tool not in KINDS or KINDS[tool] != kind:
            raise ValueError("映射的工具与权益种类不匹配")
        result = capture.get("tool_results", {}).get(tool)
        if not isinstance(result, dict):
            raise ValueError(f"capture 中没有 {tool} 成功结果")
        document, origin = payload(result)
        path = collection.get("records_path", "")
        try:
            selected = pointer(document, path)
        except (KeyError, IndexError) as exc:
            raise ValueError(f"{tool}: records_path 不存在，请检查真实响应") from exc
        if not isinstance(selected, (list, dict)):
            raise ValueError("records_path 必须选择数组或对象，不能静默忽略响应")
        entries = selected if isinstance(selected, list) else [selected]
        fields = collection.get("fields", {})
        if not isinstance(fields, dict) or not {"id", "title"} <= fields.keys() or not fields.keys() <= FIELDS:
            raise ValueError("fields 需要 id/title，且仅支持规范字段")
        for index, record in enumerate(entries):
            if not isinstance(record, dict):
                raise ValueError(f"{tool} 记录必须是对象")
            normalized = {key: ([] if key == "conditions" else None) for key in FIELDS}
            for field, spec in fields.items():
                try:
                    normalized[field] = _field(record, spec, field)
                except (KeyError, IndexError):
                    if field in {"id", "title"}:
                        raise ValueError(f"{tool} 第 {index + 1} 条记录缺少 {field}") from None
                    warnings.append(f"{tool} 第 {index + 1} 条缺少 {field}，保留为未知。")
            if not isinstance(normalized["id"], (str, int)) or isinstance(normalized["id"], bool):
                raise ValueError("记录 id 必须为字符串或整数")
            if not str(normalized["id"]).strip():
                raise ValueError("记录 id 不能为空")
            normalized["id"] = f"{tool}:{normalized['id']}"
            normalized["kind"] = kind
            suffix = f"/{index}" if isinstance(selected, list) else ""
            normalized["source"] = {"tool": tool, "ref": f"{origin}{path}{suffix}"}
            records.append(normalized)
    return {"schema_version": 1, "mode": "live", "timezone": "Asia/Shanghai",
            "captured_at": capture["captured_at"], "records": records,
            "warnings": list(dict.fromkeys(warnings))}
