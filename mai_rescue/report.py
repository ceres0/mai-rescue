"""Self-contained, escaped HTML and Markdown views of a rescue plan.

Reports are deliberately passive: they never redeem, buy, or claim a saving.
The only browser script changes visible filters and opens the print dialog.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from html import escape
import re
from typing import Any


_KINDS = {"coupon": "优惠券", "points": "积分", "campaign": "活动"}
_URGENCIES = {
    "today": "今天截止",
    "soon": "即将截止",
    "later": "稍后截止",
    "unknown": "时间待核实",
    "expired": "已过期",
    "upcoming": "尚未开始",
}


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _html(value: Any) -> str:
    return escape(_text(value), quote=True)


def _md(value: Any) -> str:
    # Escape HTML as well as Markdown, including embedded lines in table/list data.
    value = escape(_text(value), quote=True).replace("\r", " ").replace("\n", " ")
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~\-])", r"\\\1", value)


def _int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (ValueError, TypeError, OverflowError):
        return 0


def _money(value: Any) -> str:
    return f"¥{_int(value) / 100:,.2f}"


def _mapping(value: Any) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _actions(plan: Mapping) -> list[Mapping]:
    value = plan.get("actions", [])
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, (list, tuple)) else []


def _strings(value: Any) -> list[str]:
    return [_text(item) for item in value] if isinstance(value, (list, tuple)) else []


def _date(value: Any, *, end_of_day: bool = False) -> datetime | None:
    try:
        text = _text(value)
        if len(text) == 10:
            # The planner interprets date-only official deadlines in Shanghai.
            text += "T23:59:59+08:00" if end_of_day else "T00:00:00+08:00"
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _date_label(value: Any, *, end_of_day: bool = False) -> str:
    parsed = _date(value, end_of_day=end_of_day)
    if parsed:
        zone = parsed.strftime("%z")
        zone = f" UTC{zone[:3]}:{zone[3:]}" if zone else "（时区未提供）"
        return parsed.strftime("%Y-%m-%d %H:%M") + zone
    return _text(value) or "未提供"


def _status(action: Mapping) -> str:
    urgency = _text(action.get("urgency", "unknown"))
    if urgency == "expired":
        return "已过期"
    if action.get("decision") == "skip":
        return "已跳过"
    if action.get("decision") == "check":
        return "待核实"
    return _URGENCIES.get(urgency, "待核实")


def _filters(action: Mapping, plan: Mapping) -> str:
    tokens = ["all"]
    urgency = action.get("urgency")
    if urgency == "today":
        tokens.append("today")
    deadline = _date(action.get("expires_at"), end_of_day=True)
    baseline = _date(plan.get("generated_at") or plan.get("snapshot_at"))
    if deadline and baseline:
        try:
            if baseline <= deadline <= baseline + timedelta(days=7):
                tokens.append("week")
        except TypeError:
            pass  # Mixing naive and zoned dates must not invent a deadline.
    elif urgency in ("today", "soon"):
        tokens.append("week")
    if urgency == "unknown" or action.get("decision") == "check":
        tokens.append("check")
    if action.get("decision") == "skip":
        tokens.append("skipped")
    return " ".join(tokens)


def _reason(action: Mapping) -> str:
    if action.get("reason"):
        return _text(action["reason"])
    if action.get("urgency") == "expired":
        return "此权益已过期，保留记录供核对。"
    if action.get("decision") == "skip":
        return "本次不安排使用；不为消耗权益额外消费。"
    if action.get("decision") == "check" or action.get("urgency") == "unknown":
        return "有效期或使用条件信息不足，请先在官方页面核实。"
    return "仅在原本就有消费计划、且满足使用条件时考虑。"


def _benefit(action: Mapping) -> str:
    kind = action.get("kind")
    if kind == "points":
        points = _int(action.get("points"))
        return f"{points:,} 积分待关注" if points else "积分权益待核实"
    amount = _int(action.get("benefit_cents"))
    if amount:
        label = "单券潜在优惠" if kind == "coupon" else "活动标称权益"
        return f"{label} {_money(amount)}"
    return "权益价值待核实"


def _deadline(action: Mapping) -> str:
    return _text(action.get("deadline_label")) or (
        "截止 " + _date_label(action["expires_at"], end_of_day=True) if action.get("expires_at") else "截止时间待核实"
    )


def _summary(plan: Mapping, actions: list[Mapping]) -> dict[str, Any]:
    raw = _mapping(plan.get("summary"))
    return {
        "total": _int(raw.get("total", len(actions))),
        "active": _int(raw.get("active", sum(a.get("urgency") != "expired" for a in actions))),
        "due_soon": _int(raw.get("due_soon", sum(a.get("urgency") in ("today", "soon") for a in actions))),
        "unknown": _int(raw.get("unknown", sum(a.get("urgency") == "unknown" for a in actions))),
        "expired": _int(raw.get("expired", sum(a.get("urgency") == "expired" for a in actions))),
        "max_saving_cents": _int(raw.get("max_saving_cents")),
        "expiring_points": _int(raw.get("expiring_points")),
    }


def _context(plan: Mapping) -> str:
    visit = plan.get("planned_visit")
    if visit is True:
        visit_label = "已有就餐计划"
    elif visit is False:
        visit_label = "暂无就餐计划"
    else:
        visit_label = _text(visit) or "就餐计划未提供"
    budget = "未设置预算" if plan.get("budget_cents") is None else f"预算上限 {_money(plan.get('budget_cents'))}"
    return f"{visit_label} · {budget} · 关注未来 {_int(plan.get('horizon_days', 7))} 天"


def render_html(plan: dict) -> str:
    """Render a portable HTML document, with no external assets or network calls."""
    plan = _mapping(plan)
    actions = _actions(plan)
    summary = _summary(plan, actions)
    demo = plan.get("mode") != "live"
    mode = "DEMO · 假设示例" if demo else "LIVE · 数据快照"
    disclosure = (
        "演示模式：本报告的全部权益、账户数据与建议均为假设示例，不代表真实账户或官方承诺。"
        if demo else
        "实时数据模式：本报告依据读取时的数据快照生成。实际可用性、库存与使用条件，请以官方页面为准。"
    )
    cards = []
    for index, action in enumerate(actions, 1):
        source = _mapping(action.get("source"))
        urgency = action.get("urgency", "unknown")
        style = "expired" if urgency == "expired" else "check" if _status(action) == "待核实" else "normal"
        conditions = _strings(action.get("conditions"))
        condition_html = "".join(f"<li>{_html(item)}</li>" for item in conditions)
        if not condition_html:
            condition_html = "<li>使用条件未提供，请在官方页面核实。</li>"
        min_spend = action.get("min_spend_cents")
        threshold = f"<span>使用门槛：{_money(min_spend)}</span>" if min_spend is not None else "<span>使用门槛：待核实</span>"
        starts = f"<span>开始：{_html(_date_label(action['starts_at']))}</span>" if action.get("starts_at") else ""
        expiry = f"<span>到期：{_html(_date_label(action['expires_at'], end_of_day=True))}</span>" if action.get("expires_at") else "<span>到期：待核实</span>"
        cards.append(f"""<article class="action {style}" data-filters="{_html(_filters(action, plan))}">
          <div class="action-index" aria-hidden="true">{index:02d}</div>
          <div class="action-body">
            <div class="action-top"><span class="kind">{_html(_KINDS.get(action.get('kind'), '权益'))}</span><span class="status">{_html(_status(action))}</span></div>
            <h3>{_html(action.get('title') or '未命名权益')}</h3>
            <div class="benefit">{_html(_benefit(action))}</div>
            <div class="deadline">{_html(_deadline(action))}</div>
            <p class="reason">{_html(_reason(action))}</p>
            <details><summary>查看条件与来源</summary><div class="detail-content"><div class="dates">{threshold}{starts}{expiry}</div><ul>{condition_html}</ul><div class="source"><span>来源工具 <code>{_html(source.get('tool') or '未提供')}</code></span><span>来源记录 <code>{_html(source.get('ref') or '未提供')}</code></span><span>权益编号 <code>{_html(action.get('id') or '未提供')}</code></span></div></div></details>
          </div>
        </article>""")
    warnings = _strings(plan.get("warnings"))
    warning_html = (
        '<aside class="warnings" aria-label="数据提示"><strong>先留意这些信息</strong><ul>'
        + "".join(f"<li>{_html(item)}</li>" for item in warnings) + "</ul></aside>"
        if warnings else ""
    )
    actions_html = "\n".join(cards)
    empty_hidden = " hidden" if actions else ""
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="color-scheme" content="light"><title>麦麦不浪费 · 权益行动清单</title>
<style>
:root{{--paper:#f7f5ee;--card:#fffef9;--ink:#242a24;--muted:#687064;--line:#dfdfd1;--accent:#d34a29;--lemon:#f0df70;--green:#355844}}*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font-family:Inter,"PingFang SC","Microsoft YaHei",system-ui,sans-serif;line-height:1.65;-webkit-font-smoothing:antialiased}}button{{font:inherit}}.shell{{max-width:1500px;margin:auto;display:grid;grid-template-columns:218px minmax(0,1fr);min-height:100vh}}.sidebar{{padding:45px 30px;border-right:1px solid var(--line);display:flex;flex-direction:column;gap:32px}}.brand-mark{{display:flex;align-items:center;gap:10px}}.brand-mark svg{{width:34px;height:34px;flex-shrink:0}}.brand-cn{{font-size:16px;font-weight:750;letter-spacing:1px}}.brand-en{{font-size:9px;letter-spacing:2px;color:var(--muted)}}.sidebar-note{{font-size:12px;line-height:1.9;color:var(--muted);max-width:130px}}.side-rule{{height:1px;background:var(--line);width:26px}}.side-label{{font-size:10px;letter-spacing:2px;color:var(--muted)}}.side-active{{font-size:13px;color:var(--accent);margin-top:13px}}.sidebar-bottom{{margin-top:auto;color:var(--muted);font-size:10px;letter-spacing:1px;padding-top:80px}}main{{padding:35px 55px 28px;min-width:0}}.topbar{{display:flex;align-items:center;justify-content:space-between;gap:20px;font-size:11px;color:var(--muted)}}.mode{{padding:6px 10px;border:1px solid #d8bd70;border-radius:3px;background:#f9efc2;color:#6b571e;font-size:10px;font-weight:700;letter-spacing:1px}}.mode.live{{background:#e7eee2;border-color:#b3c3a8;color:#355844}}.print{{border:0;background:transparent;color:var(--muted);font-size:12px;cursor:pointer;text-decoration:underline;text-underline-offset:4px}}.hero{{position:relative;padding:48px 0 29px;border-bottom:1px solid var(--line);isolation:isolate}}.eyebrow{{color:var(--accent);font-size:10px;letter-spacing:2.5px;font-weight:750;margin-bottom:13px}}h1{{font-size:clamp(31px,3.2vw,47px);line-height:1.35;letter-spacing:-1.7px;font-weight:700;margin:0 0 18px;max-width:720px}}h1 em{{font-style:normal;color:var(--accent)}}.intro{{font-size:13px;color:var(--muted);max-width:600px;margin:0}}.hero-art{{position:absolute;right:3px;top:45px;width:112px;height:112px;opacity:.85;z-index:-1}}.disclosure{{font-size:11px;margin:19px 0 0;padding:10px 13px;border-left:2px solid var(--lemon);background:#f0eee2;color:#666752}}.stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:13px;margin:25px 0 30px}}.stat{{border:1px solid var(--line);border-radius:5px;background:var(--card);padding:20px 18px 16px;min-height:123px}}.stat-label{{font-size:11px;color:var(--muted)}}.stat-value{{display:flex;align-items:baseline;gap:7px;margin:7px 0 2px;font-size:31px;line-height:1.1;font-weight:650;letter-spacing:-1px}}.stat-value small{{font-size:10px;font-weight:400;letter-spacing:0;color:var(--muted)}}.stat-value.money{{font-size:28px;color:var(--accent)}}.stat-foot{{font-size:10px;color:var(--muted);margin-top:8px}}.section-heading{{display:flex;align-items:baseline;justify-content:space-between;gap:12px}}h2{{font-size:19px;margin:0;font-weight:650;letter-spacing:-.5px}}.section-heading span{{font-size:10px;color:var(--muted)}}.context{{font-size:11px;color:var(--muted);margin:8px 0 17px}}.filters{{display:flex;flex-wrap:wrap;gap:7px;padding-bottom:16px;border-bottom:1px solid var(--line)}}.filter{{border:1px solid transparent;background:transparent;padding:5px 12px;font-size:11px;color:var(--muted);border-radius:3px;cursor:pointer}}.filter[aria-pressed="true"]{{background:var(--ink);color:var(--card)}}.filter:hover{{border-color:var(--line)}}.filter:focus-visible,.print:focus-visible,summary:focus-visible{{outline:2px solid var(--accent);outline-offset:3px}}.action{{display:grid;grid-template-columns:42px minmax(0,1fr);gap:10px;padding:25px 0;border-bottom:1px solid var(--line)}}.action-index{{font-size:11px;line-height:24px;color:#999c8a;font-variant-numeric:tabular-nums}}.action-top{{display:flex;align-items:center;gap:9px}}.kind{{font-size:9px;letter-spacing:1px;color:var(--green);background:#e8ecde;border-radius:2px;padding:2px 6px}}.status{{font-size:10px;color:var(--accent)}}h3{{font-size:19px;line-height:1.4;margin:9px 0 4px;font-weight:650;overflow-wrap:anywhere}}.benefit{{font-size:12px;font-weight:600;margin-top:4px}}.deadline{{font-size:11px;color:var(--accent);margin-top:5px}}.reason{{font-size:12px;color:var(--muted);margin:12px 0 9px;max-width:720px;overflow-wrap:anywhere}}details{{font-size:11px}}summary{{cursor:pointer;color:var(--muted);width:max-content;max-width:100%;text-decoration:underline;text-underline-offset:3px}}.detail-content{{padding:11px 14px;margin-top:10px;border:1px solid var(--line);background:#eeeee5;border-radius:3px;color:var(--muted)}}.dates{{display:flex;flex-wrap:wrap;gap:5px 18px}}.detail-content ul{{padding-left:17px;margin:10px 0}}.detail-content li,.source{{overflow-wrap:anywhere}}.source{{display:flex;flex-direction:column;gap:3px;font-size:10px;border-top:1px solid var(--line);padding-top:8px}}code{{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:10px;color:var(--ink)}}.expired .status,.expired .deadline{{color:var(--muted)}}.expired h3{{color:#73786d}}.check .status{{color:#8c6b20}}.empty{{text-align:center;padding:48px 20px;color:var(--muted);border-bottom:1px solid var(--line)}}.empty strong{{display:block;color:var(--ink);font-size:16px;font-weight:600;margin-bottom:7px}}.empty p{{font-size:12px;margin:0}}.warnings{{margin-top:23px;padding:15px 18px;border:1px solid #dbd3a8;background:#f4f0dc;border-radius:4px;font-size:11px;color:#716742}}.warnings strong{{font-weight:650}}.warnings ul{{margin:7px 0 0;padding-left:16px;overflow-wrap:anywhere}}.care-note{{display:flex;gap:14px;margin:30px 0 28px;padding:21px 22px;background:#e9ecdf;border-radius:4px}}.care-icon{{color:var(--green);font-size:20px;line-height:1.4}}.care-note strong{{display:block;font-size:12px;font-weight:650}}.care-note p{{font-size:11px;margin:4px 0 0;color:var(--muted)}}footer{{padding-top:14px;border-top:1px solid var(--line);display:flex;justify-content:space-between;gap:15px;font-size:9px;color:var(--muted);flex-wrap:wrap}}[hidden]{{display:none!important}}@media(min-width:1200px){{.hero .intro{{max-width:calc(100% - 120px)}}}}@media(max-width:1000px){{main{{padding:30px}}.shell{{grid-template-columns:170px minmax(0,1fr)}}.sidebar{{padding:35px 20px}}.brand-cn{{font-size:13px}}.brand-mark{{gap:7px}}.brand-mark svg{{width:27px}}.hero-art{{opacity:.25}}.stats{{gap:8px}}.stat{{padding:17px 12px}}.stat-value{{font-size:27px}}.stat-value.money{{font-size:23px}}}}@media(max-width:720px){{.shell{{display:block}}.sidebar{{border-right:0;border-bottom:1px solid var(--line);padding:18px 22px;display:block}}.sidebar-note,.side-rule,.side-nav,.sidebar-bottom{{display:none}}.brand-cn{{font-size:14px}}main{{padding:22px}}.hero{{padding-top:33px}}h1{{font-size:34px;letter-spacing:-1px}}.hero-art{{right:-8px;top:35px;width:90px}}.stats{{grid-template-columns:repeat(2,1fr);margin-top:20px;gap:10px}}.stat{{min-height:116px}}.stat-value,.stat-value.money{{font-size:29px}}.section-heading{{align-items:center}}.section-heading span{{max-width:110px;text-align:right}}.context{{line-height:1.9}}.filters{{gap:3px}}.filter{{padding:6px 10px}}.action{{grid-template-columns:25px minmax(0,1fr);gap:7px;padding:22px 0}}h3{{font-size:18px}}.care-note{{padding:18px 15px}}.topbar{{gap:8px}}}}@media print{{body{{background:white;color:black;font-size:11px}}.shell{{display:block;max-width:none}}.sidebar,.print,.filters,.hero-art{{display:none}}main{{padding:0}}.hero{{padding-top:20px}}h1{{font-size:31px}}.stats{{margin:18px 0;gap:8px}}.stat{{min-height:100px}}.action{{break-inside:avoid;padding:16px 0}}.action[hidden]{{display:grid!important}}details:not([open]) .detail-content{{display:block}}details summary{{display:none}}.care-note{{margin:20px 0}}.disclosure,.stat,.care-note{{print-color-adjust:exact;-webkit-print-color-adjust:exact}}}}
</style></head><body>
<div class="shell"><aside class="sidebar" aria-label="品牌"><div class="brand-mark"><svg viewBox="0 0 36 36" aria-hidden="true"><rect x="2" y="2" width="32" height="32" rx="9" fill="#d34a29"/><path d="M10 24V14l8 7 8-7v10" fill="none" stroke="#fff8e5" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/><circle cx="18" cy="10" r="2" fill="#f0df70"/></svg><div><div class="brand-cn">麦麦不浪费</div><div class="brand-en">MAI RESCUE</div></div></div><div class="side-rule"></div><p class="sidebar-note">把快过期的心意，<br>放回刚刚好的日常。</p><div class="side-nav"><div class="side-label">YOUR LITTLE HELPER</div><div class="side-active">↳ 权益行动清单</div></div><div class="sidebar-bottom">少一点遗忘<br>多一点刚刚好</div></aside>
<main><div class="topbar"><span class="mode {'live' if not demo else ''}">{mode}</span><button class="print" type="button" id="print-report">打印 / 存为 PDF ↗</button></div>
<header class="hero"><div class="eyebrow">A GOOD MOMENT, BEFORE IT'S GONE</div><h1>让每份心意，<br>都赶上<em>好时候。</em></h1><p class="intro">优惠券、积分和活动，都替你记着。按时间理清值得关注的权益，消费决定仍由你做主。</p><svg class="hero-art" viewBox="0 0 120 120" aria-hidden="true"><circle cx="60" cy="60" r="47" fill="#f0df70"/><path d="M28 76c10-22 24-32 37-20S85 80 99 54" fill="none" stroke="#d34a29" stroke-width="5" stroke-linecap="round"/><path d="M39 22l-5-12m58 19 9-8m-6 72 9 9" stroke="#d34a29" stroke-width="2" stroke-linecap="round"/><circle cx="48" cy="43" r="3" fill="#355844"/><circle cx="75" cy="43" r="3" fill="#355844"/></svg><p class="disclosure">{_html(disclosure)}</p></header>
<section class="stats" aria-label="权益概览"><div class="stat"><div class="stat-label">待关注权益</div><div class="stat-value">{summary['active']}<small>项</small></div><div class="stat-foot">共 {summary['total']} 项记录 · 仍需核实条件</div></div><div class="stat"><div class="stat-label">近期截止</div><div class="stat-value">{summary['due_soon']}<small>项</small></div><div class="stat-foot">优先看一眼，再决定</div></div><div class="stat"><div class="stat-label">即将到期积分</div><div class="stat-value">{summary['expiring_points']:,}<small>积分</small></div><div class="stat-foot">兑换价值以官方规则为准</div></div><div class="stat"><div class="stat-label">单券潜在优惠</div><div class="stat-value money">{_money(summary['max_saving_cents'])}</div><div class="stat-foot">最高单券 · 非已实现节省</div></div></section>
<section aria-labelledby="action-title"><div class="section-heading"><h2 id="action-title">你的行动清单</h2><span>{summary['unknown']} 项时间待核实 · {summary['expired']} 项已过期</span></div><p class="context">{_html(_context(plan))}</p><div class="filters" role="group" aria-label="筛选行动清单"><button type="button" class="filter" data-filter="all" aria-pressed="true">全部</button><button type="button" class="filter" data-filter="today" aria-pressed="false">今日</button><button type="button" class="filter" data-filter="week" aria-pressed="false">七日内</button><button type="button" class="filter" data-filter="check" aria-pressed="false">待核实</button><button type="button" class="filter" data-filter="skipped" aria-pressed="false">已跳过</button></div><div id="actions">{actions_html}</div><div class="empty" id="empty-state" role="status"{empty_hidden}><strong>这里暂时没有需要关注的权益</strong><p>没有合适的权益，也可以安心按原计划生活。</p></div><p class="context" id="filter-count" aria-live="polite">显示 {len(actions)} 项</p></section>
{warning_html}<aside class="care-note"><span class="care-icon" aria-hidden="true">✳</span><div><strong>不为用券，额外消费。</strong><p>只在原本就需要、预算也合适时使用权益。潜在优惠不可简单相加，门槛、叠加规则和适用门店均需核实；本报告不会自动下单或兑换。</p></div></aside>
<footer><span>麦麦不浪费 / MAI RESCUE · 非官方权益整理助手</span><span>数据快照 {_html(_date_label(plan.get('snapshot_at')))}<br>生成时间 {_html(_date_label(plan.get('generated_at')))}</span></footer></main></div>
<script>
(() => {{
  const buttons = document.querySelectorAll('[data-filter]');
  const cards = document.querySelectorAll('[data-filters]');
  const empty = document.getElementById('empty-state');
  const count = document.getElementById('filter-count');
  buttons.forEach(button => button.addEventListener('click', () => {{
    const choice = button.dataset.filter;
    buttons.forEach(item => item.setAttribute('aria-pressed', String(item === button)));
    let visible = 0;
    cards.forEach(card => {{
      card.hidden = !card.dataset.filters.split(' ').includes(choice);
      if (!card.hidden) visible += 1;
    }});
    empty.hidden = visible !== 0;
    count.textContent = '显示 ' + visible + ' 项';
  }}));
  document.getElementById('print-report').addEventListener('click', () => window.print());
}})();
</script></body></html>"""


def render_markdown(plan: dict) -> str:
    """Render the same decisions as a portable, self-contained Markdown report."""
    plan = _mapping(plan)
    actions = _actions(plan)
    summary = _summary(plan, actions)
    demo = plan.get("mode") != "live"
    lines = [
        "# 麦麦不浪费 / MAI RESCUE",
        "",
        "让每份心意，都赶上好时候。",
        "",
        "**DEMO · 假设示例**" if demo else "**LIVE · 数据快照**",
        "",
        "演示模式：本报告的全部权益、账户数据与建议均为假设示例，不代表真实账户或官方承诺。"
        if demo else "本报告依据读取时的数据快照生成。实际可用性、库存与使用条件，请以官方页面为准。",
        "",
        f"- 数据快照：{_md(_date_label(plan.get('snapshot_at')))}",
        f"- 生成时间：{_md(_date_label(plan.get('generated_at')))}",
        f"- 计划条件：{_md(_context(plan))}",
        "",
        "## 权益概览",
        "",
        "| 指标 | 数值 |",
        "| --- | ---: |",
        f"| 权益记录 | {summary['total']} 项 |",
        f"| 待关注权益 | {summary['active']} 项 |",
        f"| 近期截止 | {summary['due_soon']} 项 |",
        f"| 即将到期积分 | {summary['expiring_points']:,} 积分 |",
        f"| 单券潜在优惠（最高单券，非已实现节省） | {_money(summary['max_saving_cents'])} |",
        f"| 时间待核实 | {summary['unknown']} 项 |",
        f"| 已过期 | {summary['expired']} 项 |",
        "",
        "## 行动清单",
        "",
    ]
    if not actions:
        lines.extend(["这里暂时没有需要关注的权益。没有合适的权益，也可以安心按原计划生活。", ""])
    for index, action in enumerate(actions, 1):
        source = _mapping(action.get("source"))
        lines.extend([
            f"### {index}. {_md(action.get('title') or '未命名权益')}",
            "",
            f"- 类型：{_md(_KINDS.get(action.get('kind'), '权益'))}",
            f"- 状态：{_md(_status(action))}",
            f"- 权益：{_md(_benefit(action))}",
            f"- 截止提示：{_md(_deadline(action))}",
            f"- 到期时间：{_md(_date_label(action.get('expires_at'), end_of_day=True))}",
            f"- 建议原因：{_md(_reason(action))}",
            f"- 使用门槛：{_money(action['min_spend_cents']) if action.get('min_spend_cents') is not None else '待核实'}",
        ])
        if action.get("starts_at"):
            lines.append(f"- 开始时间：{_md(_date_label(action.get('starts_at')))}")
        conditions = _strings(action.get("conditions")) or ["使用条件未提供，请在官方页面核实。"]
        lines.extend(["", "使用条件：", ""])
        lines.extend(f"- {_md(condition)}" for condition in conditions)
        lines.extend([
            "",
            f"来源工具：{_md(source.get('tool') or '未提供')}  ",
            f"来源记录：{_md(source.get('ref') or '未提供')}  ",
            f"权益编号：{_md(action.get('id') or '未提供')}",
            "",
        ])
    warnings = _strings(plan.get("warnings"))
    if warnings:
        lines.extend(["## 数据提示", ""])
        lines.extend(f"- {_md(item)}" for item in warnings)
        lines.append("")
    lines.extend([
        "## 不为用券，额外消费",
        "",
        "只在原本就需要、预算也合适时使用权益。潜在优惠不可简单相加，门槛、叠加规则和适用门店均需核实。本报告不会自动下单或兑换，也不代表已实现任何节省。",
        "",
        "麦麦不浪费 / MAI RESCUE · 非官方权益整理助手",
        "",
    ])
    return "\n".join(lines)
