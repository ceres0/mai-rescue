---
name: mai-rescue
description: 使用麦当劳 MCP 的只读权益查询和本地 MAI RESCUE 程序，按到期时间、预算与既有就餐计划整理优惠券、积分和活动，生成本地报告与日历文件。适用于权益盘点和理性消费规划，不执行领券、下单、兑换或抽奖。
---

# 麦麦不浪费

帮助用户检查已有权益，避免因为用券增加消费。先确认用户是否本来就计划就餐及预算；未提供的信息保留未知，不能把“快过期”解释为应该消费。

## 工具边界

只调用官方 `https://mcp.mcd.cn` 的 `query-my-coupons`、`query-my-account`、`campaign-calendar`、`available-coupons`、`now-time-info`。先获取 `tools/list` 并读取当前 `inputSchema`；有 `outputSchema` 时检查其内容。官方公开指南不完整描述这些字段，不得虚构输入参数、返回结构、日期或金额。

不调用领券、创建/取消订单、积分兑换、抽奖等写工具。凭证只从客户端安全配置、本机 `MCD_MCP_TOKEN` 环境变量或用户通过 `python3 -m mai_rescue auth` 配置的 `.private/mcd-token` 获取；`auth` 需由用户在本机交互终端隐藏输入。不要求用户把真实 Token 粘贴到聊天，不回显凭证，不把账户结果上传至公共服务。

## 生成结果

在 MAI RESCUE 项目根目录运行 CLI；Python 3.11+，无第三方依赖。首次真实接入先用 `doctor` 和 `discover --out .private/tools.json` 检查环境和 schema，再按 schema 填写 `examples/arguments.example.json`，用 `capture --arguments examples/arguments.example.json --out .private/capture.json` 保存只读结果。

需要把真实返回结构适配为项目快照时，读取 [references/normalization.md](references/normalization.md)。`official-2026-10` profile 基于 2026 年 10 月 25 日五个只读工具业务成功的真实验证，适用于该版本结构；仍需核对当前 schema。显式 `--mapping` 可扩展其他经核实结构，与 `--profile` 互斥。遇到必填参数、结构变化或语义不明时，指出具体缺口，不猜测。

```sh
python3 -m mai_rescue sync --profile official-2026-10 --out .private/snapshot.json
python3 -m mai_rescue plan .private/snapshot.json --out .private/report-output --visit --budget 35
```

已有 capture 时可用 `normalize .private/capture.json --profile official-2026-10 --out .private/snapshot.json`。该 profile 保留核销日期和时段条件，不把 `discountValue` 售卖价当成优惠；月度积分窗口不补月末日期，活动展示日期不作为截止日。金额、门槛、期限未知时保持待核实。优惠券最多采集 5 页并保留逐页来源，留意不完整提示。真实资料、券码、Token、capture 与报告只保存在本地 `.private/`，不公开。

`--visit` 仅在用户已有就餐计划时使用；`--budget` 单位为元，替换为用户预算。输出 `plan.json`、`report.html`、`report.md`、`reminders.ics`；日历文件由用户自行导入。

无法使用 MCP 或没有 Token 时，说明尚未查询真实账户，可运行合成演示：

```sh
python3 -m mai_rescue demo --out demo-output --visit --budget 35
```

演示必须标为合成数据，不能称作官方当期活动或真实账户结果。

解释结果时保留以下语义：过期或最低消费超预算的券跳过；未知日期、消费门槛待核实；未知日期不导出提醒；没有就餐计划时券待核实；可领但未领取券仅核实、不计入优惠也不导出使用截止日历；潜在优惠仅取单张已持有券最高明确金额，不累加、不推断叠加，积分不折现。保留原始工具与记录路径，区分已知信息与未核实条件。提醒用户以官方实时结果及券面规则为准，不将输出描述为医疗、营养或其他专业建议。
