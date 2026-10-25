"""Zero-dependency local CLI. Account data stays in explicitly selected files."""

from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import sys
import tempfile
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from importlib.resources import files

from . import __version__
from .engine import build_plan
from .calendar_export import render_calendar
from .normalize import normalize_capture, payload
from .report import render_html, render_markdown

TOKEN_FILE = Path(".private/mcd-token")


def read_json(path: str | Path) -> dict:
    path = Path(path)
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("输入 JSON 超过 8MB")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("输入 JSON 顶层必须为对象")
    return value


def write_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".mai-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path: Path, value: dict) -> None:
    write_file(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def money(value: str) -> int:
    try:
        parsed = Decimal(value) * 100
        if not parsed.is_finite() or parsed < 0 or parsed != parsed.to_integral_value():
            raise ValueError
        return int(parsed)
    except (InvalidOperation, ValueError, OverflowError) as exc:
        raise argparse.ArgumentTypeError("预算应为非负金额，最多两位小数（单位：元）") from exc


def now_value(value: str | None) -> datetime | None:
    if value is None:
        return None
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("--now 需要明确时区，例如 2026-10-25T12:00:00+08:00")
    return result


def token_value() -> str:
    value = os.environ.get("MCD_MCP_TOKEN", "").strip()
    if not value and TOKEN_FILE.exists():
        if TOKEN_FILE.is_symlink() or not TOKEN_FILE.is_file():
            raise ValueError("本机凭证文件必须是普通文件，不能是符号链接")
        if os.name == "posix" and TOKEN_FILE.stat().st_mode & 0o077:
            raise ValueError("凭证文件权限过宽，请执行 chmod 600 .private/mcd-token")
        if TOKEN_FILE.stat().st_size > 16384:
            raise ValueError("凭证文件过大，请重新运行 auth 配置")
        value = TOKEN_FILE.read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError("未设置 MCD_MCP_TOKEN。请在本机终端运行 python3 -m mai_rescue auth 隐藏输入，或设置环境变量；离线体验运行 demo。")
    return value


def capture_data(arguments: dict) -> dict:
    from .mcp import McpClient, READ_ONLY_TOOLS
    if not arguments.keys() <= READ_ONLY_TOOLS:
        raise ValueError("arguments 文件只能包含只读白名单工具")
    if any(not isinstance(value, dict) for value in arguments.values()):
        raise ValueError("每个工具的 arguments 必须为 JSON 对象")
    client = McpClient(token_value())
    tools = client.list_tools()
    discovered = {tool["name"]: tool for tool in tools}
    core = ["query-my-coupons", "query-my-account", "campaign-calendar", "available-coupons"]
    missing = [name for name in core if name not in discovered]
    if missing:
        raise ValueError("Server 缺少必需只读工具：" + ", ".join(missing))
    selected = core + (["now-time-info"] if "now-time-info" in discovered else [])
    for name in selected:
        schema = discovered[name].get("inputSchema", {})
        absent = set(schema.get("required", [])) - arguments.get(name, {}).keys()
        if absent:
            raise ValueError(f"{name} 缺少必需参数 {', '.join(sorted(absent))}；先运行 discover 读取真实 schema")
    results = {}
    warnings = []
    for name in selected:
        result = client.call_tool(name, arguments.get(name, {}))
        try:
            body, _ = payload(result)
        except ValueError:
            body = None
        if isinstance(body, dict) and (body.get("success") is False or ("code" in body and body["code"] != 200)):
            raise ValueError(f"{name} 返回业务失败；未保存为成功快照，请核实官方账户状态")
        results[name] = result
    # Pagination is specific to the dynamically verified account-coupon schema.
    # Preserve individual pages so provenance still points at the original result.
    try:
        coupon_body, _ = payload(results["query-my-coupons"])
    except ValueError:
        coupon_body = {}
    coupon_data = coupon_body.get("data", {}) if isinstance(coupon_body, dict) else {}
    pages = [results["query-my-coupons"]]
    total = coupon_data.get("totalPages") if isinstance(coupon_data, dict) else None
    current = coupon_data.get("currentPage") if isinstance(coupon_data, dict) else None
    if type(total) is int and type(current) is int and total >= 0 and current >= 1:
        if current > 1:
            warnings.append("采集从优惠券第 2 页或以后开始，前面的页未查询；请从第 1 页重新采集。")
        for number in range(current + 1, min(total, 5) + 1):
            params = dict(arguments.get("query-my-coupons", {}))
            params.update(page=str(number), pageSize=str(coupon_data.get("pageSize", 200)))
            result = client.call_tool("query-my-coupons", params)
            page_body, _ = payload(result)
            if not isinstance(page_body, dict) or page_body.get("code") != 200 or page_body.get("success") is False:
                raise ValueError("优惠券分页查询失败，未保存为完整快照")
            if page_body.get("data", {}).get("currentPage") != number:
                raise ValueError("优惠券分页返回页码不符，已停止以免重复或漏页")
            pages.append(result)
        if total > 5:
            warnings.append("官方优惠券查询最多 5 页，本次结果不包含第 6 页以后的权益。")
    else:
        warnings.append("优惠券响应缺少可确认的分页信息；本次仅保存已返回的记录。")
    return {"schema_version": 1, "mode": "live", "server": "https://mcp.mcd.cn",
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "tool_results": results, "tools": [discovered[name] for name in selected],
            "tool_pages": {"query-my-coupons": pages}, "warnings": warnings}


def normalize_data(capture: dict, args: argparse.Namespace) -> dict:
    if args.profile:
        from .official import normalize_official_capture
        return normalize_official_capture(capture)
    return normalize_capture(capture, read_json(args.mapping))


def emit_plan(snapshot: dict, args: argparse.Namespace, *, demo: bool = False) -> None:
    instant = now_value(args.now)
    if demo and instant is None:
        instant = now_value(snapshot["captured_at"])
    plan = build_plan(snapshot, now=instant, horizon_days=args.horizon,
                      budget_cents=args.budget, planned_visit=args.visit)
    # Render every output before touching disk so validation failures don't leave partial reports.
    html = render_html(plan)
    markdown = render_markdown(plan)
    calendar = render_calendar(plan)
    directory = Path(args.out).resolve()
    write_json(directory / "plan.json", plan)
    write_file(directory / "report.html", html)
    write_file(directory / "report.md", markdown)
    write_file(directory / "reminders.ics", calendar)
    label = "演示（合成权益）" if plan["mode"] == "demo" else "真实 MCP 数据"
    print(f"麦麦不浪费 · {label}\n已生成 {len(plan['actions'])} 条记录。")
    print(f"交互报告：{directory / 'report.html'}")
    print(f"日历文件：{directory / 'reminders.ics'}")
    if plan.get("warnings"):
        print(f"提示：共 {len(plan['warnings'])} 条说明，详见报告。")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="mai-rescue", description="麦麦不浪费：只读查询、到期排序、按需行动。")
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("demo", "plan"):
        sub = commands.add_parser(name, help="生成合成演示报告" if name == "demo" else "为规范快照生成报告")
        if name == "plan":
            sub.add_argument("snapshot")
        sub.add_argument("--out", default="demo-output" if name == "demo" else "report-output")
        sub.add_argument("--visit", action="store_true", help="本来就计划就餐；仅此时推荐合适的已有券")
        sub.add_argument("--budget", type=money, help="本次就餐预算（元），例如 35.00")
        sub.add_argument("--horizon", type=int, default=7, help="关注未来几天，默认 7")
        sub.add_argument("--now", help="带时区的 ISO 时间；demo 默认固定演示时间")
    discover = commands.add_parser("discover", help="查询实时 tools/list 和参数 schema")
    discover.add_argument("--out", default=".private/tools.json")
    for name in ("capture", "sync"):
        sub = commands.add_parser(name, help="只读采集原始响应" if name == "capture" else "采集并按显式映射标准化")
        sub.add_argument("--arguments", help="按真实 inputSchema 准备的工具参数 JSON")
        sub.add_argument("--out", default=f".private/{'capture' if name == 'capture' else 'snapshot'}.json")
        if name == "sync":
            adaptation = sub.add_mutually_exclusive_group(required=True)
            adaptation.add_argument("--mapping")
            adaptation.add_argument("--profile", choices=["official-2026-10"])
    normalize = commands.add_parser("normalize", help="将原始响应按显式 JSON pointer 映射标准化")
    normalize.add_argument("capture")
    adaptation = normalize.add_mutually_exclusive_group(required=True)
    adaptation.add_argument("--mapping")
    adaptation.add_argument("--profile", choices=["official-2026-10"])
    normalize.add_argument("--out", default=".private/snapshot.json")
    commands.add_parser("doctor", help="检查运行环境，不发送网络请求，不输出 Token")
    commands.add_parser("auth", help="本机隐藏输入 Token，保存到仅当前用户可读的 .private/mcd-token")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "auth":
            if not sys.stdin.isatty():
                raise ValueError("auth 必须在你自己的交互终端运行，不能通过聊天、管道或脚本参数传入 Token")
            value = getpass.getpass("MCD MCP Token（输入隐藏，回车保存）：").strip()
            if not value or any(ord(char) < 33 or ord(char) == 127 for char in value):
                raise ValueError("Token 不能为空或包含空白/控制字符")
            TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            write_file(TOKEN_FILE, value + "\n")
            print("已保存本机私密凭证（权限 600），未验证有效性；无需发送到聊天。")
        elif args.command == "doctor":
            try:
                present = bool(token_value())
            except ValueError as exc:
                if TOKEN_FILE.exists():
                    raise exc
                present = False
            print(f"Python {sys.version.split()[0]} · 标准库零运行依赖")
            print("MCD_MCP_TOKEN：" + ("已设置（未验证有效性）" if present else "未设置，可运行 demo"))
            print("本命令未发送网络请求；Token 有效性请以 discover/capture 的实际结果为准。")
        elif args.command == "demo":
            snapshot = json.loads(files("mai_rescue").joinpath("demo.json").read_text(encoding="utf-8"))
            emit_plan(snapshot, args, demo=True)
        elif args.command == "plan":
            emit_plan(read_json(args.snapshot), args)
        elif args.command == "discover":
            from .mcp import McpClient
            tools = McpClient(token_value()).list_tools()
            write_json(Path(args.out), {"server": "https://mcp.mcd.cn", "tools": tools})
            print(f"已保存 {len(tools)} 个工具的真实 schema 至 {Path(args.out).resolve()}")
        elif args.command == "capture":
            data = capture_data(read_json(args.arguments) if args.arguments else {})
            write_json(Path(args.out), data)
            print(f"已保存 {len(data['tool_results'])} 个只读工具的原始结果至 {Path(args.out).resolve()}。请勿公开账户数据。")
        elif args.command == "sync":
            data = capture_data(read_json(args.arguments) if args.arguments else {})
            snapshot = normalize_data(data, args)
            build_plan(snapshot)  # Validate normalized output before publishing a snapshot file.
            output = Path(args.out)
            write_json(output.with_name(output.stem + ".capture.json"), data)
            write_json(Path(args.out), snapshot)
            print(f"已保存 {len(snapshot['records'])} 条可追溯记录至 {Path(args.out).resolve()}")
        elif args.command == "normalize":
            snapshot = normalize_data(read_json(args.capture), args)
            build_plan(snapshot)
            write_json(Path(args.out), snapshot)
            print(f"已保存 {len(snapshot['records'])} 条规范记录至 {Path(args.out).resolve()}")
        return 0
    except Exception as exc:
        # Never print credentials, including when an untrusted upstream echoes them.
        message = str(exc)
        try:
            secret = token_value()
        except (ValueError, OSError):
            secret = ""
        if secret:
            message = message.replace(secret, "[REDACTED]")
        print("错误：" + message, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
