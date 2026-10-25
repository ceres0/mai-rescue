# MCP 集成说明

## 接入状态

本项目提供面向官方麦当劳 MCP 的 Streamable HTTP 客户端、工具发现、白名单只读采集、已验证结构适配、显式字段映射和本地报告流程。**2026 年 10 月 25 日已真实联调：`tools/list` 发现 35 个工具，本文所列五个只读工具均返回业务 `code: 200`。** 用户通过本机交互终端隐藏输入 Token；真实凭证、用户资料、券码与原始结果仅保留在本地 `.private/`。

本次成功验证不代表所有账户或未来版本具有固定 schema，也不代表已经发布或报名。未来接入须继续检查当前工具 schema 与业务返回状态；参赛资格以主办方审核为准。

## Server 和凭证

- Server：`https://mcp.mcd.cn`，固定官方地址。
- 传输：Streamable HTTP。
- 身份验证：`Authorization: Bearer <MCD_MCP_TOKEN>`，优先从本机环境变量读取；也支持 `auth` 隐藏输入后保存的 `.private/mcd-token`（权限 600，不提交 Git）。
- 本仓库的配置示例只包含 `${MCD_MCP_TOKEN}` 占位符。客户端是否展开环境变量由具体客户端决定，不能把占位符支持当成已经验证的能力。

官方指南给出 Server、传输和工具用途，但没有完整公开所用工具的输入与输出字段。运行时须先通过 `tools/list` 读取 `inputSchema`，有 `outputSchema` 时一并检查；不能用本项目的标准化字段倒推官方字段。[官方 MCP 指南](https://github.com/M-China/mcd-mcp-server)

## 实际使用的 Tool 与业务价值

| Tool | 官方用途 | 项目中的价值 |
| --- | --- | --- |
| `query-my-coupons` | 查询账户可用优惠券 | 集中检查已有权益和到期风险 |
| `query-my-account` | 查询积分账户信息 | 保留积分单位，显示明确可核实的到期信息 |
| `campaign-calendar` | 查询当月活动日历 | 将本月相关活动作为参考，保留规则和来源 |
| `available-coupons` | 查询当前可领取的麦麦省优惠券 | 展示可进一步核实的权益，不执行领取 |
| `now-time-info` | 获取当前时间 | 辅助核对查询时间，避免把历史结果当成当前可用 |

`capture` 查询四个核心工具；`now-time-info` 存在时一并查询。仅允许调用上述五个工具。`discover` 获取工具列表，不执行列表中的业务工具。`auto-bind-coupons`、`create-order`、`cancel-order`、`mall-create-order`、`draw-lottery` 等写入或消耗权益的工具不在采集白名单中。

## 调用流程

```text
本机隐藏输入凭证或环境变量
  → initialize / initialized
  → tools/list（发现真实 schema）
  → 用户或 Agent 依据 schema 填写工具参数
  → tools/call（只读白名单）
  → 本地 capture 原始结果
  → official-2026-10 profile 或经核对的 JSON Pointer 映射
  → 标准化 snapshot
  → 预算、截止时间、就餐计划规则
  → plan.json / report.html / report.md / reminders.ics
```

客户端处理 JSON-RPC 与工具层错误、JSON/SSE 响应和 MCP 会话信息；对错误返回可读原因，不把错误内容伪装成成功结果。网络或认证失败时应修正配置，离线演示仍可独立运行。

## 工具参数

`--arguments` 接受以工具名为键的 JSON 对象：

```json
{
  "query-my-coupons": {},
  "query-my-account": {},
  "campaign-calendar": {},
  "available-coupons": {},
  "now-time-info": {}
}
```

这是参数文件的形状示例，**不保证未来官方工具均允许空参数**。本次真实调用已通过；以后仍以 `discover` 返回的当前 `inputSchema` 为准。若某工具存在未提供的必填参数，采集报错；先核对 schema 并补齐再运行，不猜测日期、分页或其他参数名。

优惠券分页按本次实际 schema 处理，最多采集 5 页，并在 `tool_pages` 保留逐页原始结果与来源。本次真实账户结果只有 1 页。超过 5 页、分页信息不完整或未从第一页开始时保留提示；分页错误不保存为完整成功快照。

## 已验证适配 profile

`official-2026-10` 按 2026 年 10 月 25 日已验证的实际返回结构适配；命令如下：

```sh
python3 -m mai_rescue sync --profile official-2026-10 --out .private/snapshot.json
python3 -m mai_rescue normalize .private/capture.json --profile official-2026-10 --out .private/snapshot.json
```

上面两条分别用于直接采集、适配已保存的 capture，按需选择。`--profile` 与 `--mapping` 互斥，不能同时提供。

| 实际来源字段 | 适配方式 |
| --- | --- |
| 账户券 `data.coupons` 中的真实 `id`、`title` | 保留来源标识和名称，汇总已采集分页 |
| `tradeStartDate`、`tradeEndDate` | 表示核销日期，作为账户券的开始和截止；使用时段条件另行保留 |
| `discountValue` | 可能代表售卖价，不能据此计算节省金额；优惠金额保持未知 |
| `currentMouthExpirePoint`、`nextMouthExpirePoint` | 保留本月/次月待过期积分窗口与数值；没有明确截止日期时不补月末日期 |
| 活动 `dailyList[].date` | 作为展示日期条件，不当作活动截止日期 |
| 可领券没有明确期限 | 保持待核实，不推断使用或领取截止 |

金额、最低消费或期限未知时继续待核实；不因为真实调用成功就补全缺失条件或生成无依据的优惠和日历。可领券尚未领取，只供核实，不纳入最大潜在优惠，也不导出为使用期限提醒。profile 名称是验证版本标签，不是官方兼容承诺。

## 显式标准化映射

映射文件包含整数 `schema_version: 1` 和非空 `collections` 数组。每项说明来源工具、权益类型、记录路径和字段路径：

```json
{
  "schema_version": 1,
  "collections": [
    {
      "tool": "query-my-coupons",
      "kind": "coupon",
      "records_path": "/records",
      "fields": {
        "id": "/id",
        "title": "/title",
        "expires_at": "/expiresAt",
        "starts_at": "/startsAt",
        "benefit_cents": "/benefitCents",
        "min_spend_cents": "/minSpendCents",
        "conditions": "/conditions"
      }
    }
  ]
}
```

上面的 `/records`、`/expiresAt` 等只是**本项目映射语法示意，不是官方字段样例**，不得直接当成线上 schema。`examples/mapping.example.json` 也使用虚构字段，需按真实响应改写。`records_path` 可以指向数组或单个对象；`fields` 中各路径均相对于该条记录。`id` 和 `title` 必须映射到可用值；时间、金额、积分字段没有可信来源时不填，标准化为 `null`；缺少条件时为 `[]`。来源工具与原始记录路径由程序保留，ID 自动加工具名前缀以避免跨工具冲突。

字段路径相对于 `structuredContent`；没有该字段时，仅支持唯一一个内容为 JSON 的文本块。多段或非 JSON 文本需根据实际输出手工整理规范快照，不能猜测其结构。`id`、`title`、`conditions` 还可用 `{"value": ...}` 声明明确的内部标签或条件；时间、金额和积分禁止填常量。

金额字段的单位是分。如果来源已核实以元计，可用 `{"path": "/实际字段", "multiply": 100}` 精确换算为整数分；换算不允许静默四舍五入。不得把元直接填入 `_cents` 字段，也不推断字段单位。日期时间须携带时区；纯日期的开始按北京时间 00:00:00、截止按 23:59:59 解释，映射前应确认此语义适用，否则保留未知。未知截止时间进入待核实且不导出日历。

详细规范与检查点见 [normalization.md](skills/mai-rescue/references/normalization.md)。

## 本地联调步骤

```sh
python3 -m mai_rescue auth
python3 -m mai_rescue doctor
python3 -m mai_rescue discover --out .private/tools.json
python3 -m mai_rescue capture --arguments examples/arguments.example.json --out .private/capture.json
python3 -m mai_rescue normalize .private/capture.json --profile official-2026-10 --out .private/snapshot.json
python3 -m mai_rescue plan .private/snapshot.json --out .private/report-output --visit --budget 35
```

需要自行适配其他已核对结构时：

```sh
python3 -m mai_rescue sync --mapping mapping.json --arguments examples/arguments.example.json --out .private/snapshot.json
```

复现或后续联调应核对：成功发现当前 schema；只读工具返回业务成功；profile 或映射准确对应原始字段且单位一致；报告中来源可追溯；未知值没有被补成零或虚构日期；超预算、过期与未计划就餐的结果符合规则。profile 不适用时须重新适配，不能跳过报错。

为参赛审核准备的记录只保留调用时间、工具名、脱敏结构、字段映射依据和成功状态。删除 Token、手机号、地址、会员标识、真实权益 ID 等敏感内容。不要提交原始账户结果或真实报告。

## 决策限制

- 不因优惠券产生额外就餐或超预算建议。
- 不累加多券优惠，潜在优惠只显示单券最高的明确金额。
- 不把积分折算为现金，不自动兑换或抽奖。
- 过期权益跳过；未知日期、金额或规则保持待核实。
- 有可信截止时间且符合提醒规则时才生成日历事件，用户自行导入。
- `available-coupons` 是未领取权益，仅供核实，不纳入潜在优惠，不导出为使用期限日历。
- 消费门槛未知时，无论是否已填写预算，均先核实。
- 演示结果始终标为合成数据，不用于证明真实 MCP 使用。

本项目由参赛者独立开发，非麦当劳官方产品；信息与实际可用性以官方实时结果及券面规则为准。MCP 服务使用须遵守官方协议，仅用于个人非商业场景。
