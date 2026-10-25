# 开发验收记录

验收日期：2026-10-25（北京时间）。运行环境：macOS / Python 3.14.6。
本记录包含本地开发验证和真实只读接口联调。脱敏调用状态见 `LIVE_VERIFICATION.json`，该文件不包含账户、券码或凭证。

## 已验证

`python3 -m unittest discover -s tests -v`：**96 项测试全部通过**。

- MCP：初始化、协商版本、会话请求头、工具分页、JSON/SSE、多行数据、工具与协议错误、401/429/404、响应限额、Token 脱敏、写工具拒绝和必填参数类型检查。
- 规划：上海时区与纯日期边界、明确未知值、重复 ID、来源类型匹配、过期/尚未开始、预算、未计划消费、未知门槛、未领取券、单券潜在优惠、积分独立统计、快照过时和稳定到期排序。
- 日历：有效截止事件、排除不明/过期/未开始/超预算/未领取记录、合成标签、UTC、稳定 UID、未来闹钟、CRLF、75 字节 UTF-8 折行与注入转义。
- 标准化与 CLI：显式字段映射、JSON Pointer、金额精确换算、未知日期、唯一 JSON 文本、来源记录路径、完整演示四类产物、出错不生成报告、无 Token 不伪装实时结果、环境检查不回显凭证、交互隐藏凭证保存与600权限检查、HTML 注入转义。
- 真实结构适配：`official-2026-10` profile 的 19 项合成测试覆盖核销日期和全天边界、月份积分、活动日期去重、业务失败、分页、来源与未知金额；不把优惠价当节省、不把展示日期当截止。

`python3 -m mai_rescue demo --out demo-output --visit --budget 35`：成功生成 8 条合成记录、HTML/Markdown/JSON/ICS 四类产物。
`python3 -m mai_rescue doctor`：成功检查环境；用户已通过本机隐藏输入配置凭证，凭证未公开。

## 真实 MCP 联调

2026-10-25 已通过官方 Server 真实认证，发现 **35 个工具**，下面五个白名单只读工具均返回业务 `code: 200`：

`query-my-coupons`、`query-my-account`、`campaign-calendar`、`available-coupons`、`now-time-info`。

真实 `sync --profile official-2026-10` → `plan` 端到端运行成功。精确时间边界收紧后，再对已保存在本机的真实 capture 执行 `normalize --profile official-2026-10` → `plan` 验证成功；金额和门槛不明时保持待核实，未生成虚构节省额或活动结束日。真实报告、原始结果与凭证仅在 `.private/`，未纳入源码交付包。

实际浏览器预览通过：窄屏概览、中文内容、DEMO 提示和预算展示正常；“今日”筛选只显示 1 条记录，切回“全部”显示 8 条。截图见 `report-preview.png`。

## 官方声明一致性

`CONTEST_DECLARATION.md` 通过 GitHub 公共 Contents API 下载并按 Base64 原样解码，未重写 Markdown。

- 官方源：`M-China/mcd-developer-innovation-challenge` 的 `main/CONTEST_DECLARATION.md`
- Git blob SHA-1：`136e76f3160317049f19f5ba03f4d0fedd70fc3c`
- 本地 Git blob SHA-1：`136e76f3160317049f19f5ba03f4d0fedd70fc3c`
- 大小：1522 字节

## 尚未验证与待用户完成

本次验证不保证未来 schema 不变，也不代表已经核实任何门店或订单的券可用性。首次或结构变化后仍须根据 `tools/list` 与真实结果核实参数与适配。

未发布 GitHub 公开仓库、未提交报名 Issue；准备材料见 `SUBMISSION.md`。未使用 WorkBuddy 开发，不生成 `workbuddy.md`。

Python 3.11–3.14 的 CI 配置已提供，但本次仅在上述 Python 3.14.6 环境执行；不将未运行的 CI 声称为已通过。

Skill 的 YAML frontmatter 已通过系统 YAML 解析检查；系统缺少 PyYAML，未能运行依赖它的 `skill-creator/quick_validate.py`。运行项目本身无需 PyYAML。
