# 真实结构适配与显式字段映射

仅在需要适配 MCP 实际响应时读取本文件。这里定义 MAI RESCUE 的已验证 profile、内部模型和映射语法。显式映射示例**不是官方响应 schema**；profile 仅代表本次验证过的版本，不承诺未来结构固定。

## 已验证 profile

2026 年 10 月 25 日真实发现 35 个工具，五个白名单只读工具均返回业务 `code: 200`。`official-2026-10` 根据该次实际返回结构适配，使用方式：

```sh
python3 -m mai_rescue sync --profile official-2026-10 --out .private/snapshot.json
python3 -m mai_rescue normalize .private/capture.json --profile official-2026-10 --out .private/snapshot.json
```

两条分别用于采集后适配、适配已有 capture，按需选择。`--profile` 与 `--mapping` 互斥。结构不再匹配时应核对新的 `tools/list` 与真实响应，重新编写明确映射或适配代码，不能静默猜测。

- 账户券读取 `data.coupons` 的真实 `id`、`title`；核销 `tradeStartDate`、`tradeEndDate` 映射为有效日期，并保留使用时段条件。
- `discountValue` 可能是售卖价，不用作 `benefit_cents`；真实优惠金额、门槛未明确时保持 `null`，决策继续待核实。
- `currentMouthExpirePoint`、`nextMouthExpirePoint` 是本月/次月待过期积分窗口；保留数值和窗口条件，没有明确截止日时 `expires_at: null`，不虚构月末日期。
- 活动 `dailyList[].date` 是展示日期，放入条件，不作为活动截止。
- 可领券没有明确期限时保持未知；未领取券不计潜在优惠，也不导出使用截止日历。
- 账户券最多采集 5 页，逐页原始结果保留于 `tool_pages`；本次真实结果只有 1 页。profile 汇总已采集各页并保留来源；显式映射只读取 `tool_results` 的首页，多页时会提示，必须另外核对完整性。

不要读取、复制或公开其他用户的 `.private/` 账户结果。用户授权处理的真实资料、券码、Token、capture 和报告只存本地私密目录；用于分享的样例使用合成数据。

## 核对顺序

1. 用 `discover` 读取官方工具当前的 `inputSchema` 和可选 `outputSchema`。
2. 根据 schema 提供参数，用只读白名单 `capture` 获取结果。
3. 检查原始记录所在位置、字段语义、金额单位、日期时区；发现歧义时保留未知并说明缺口。
4. 编写 `collections` 映射，标准化后逐条检查来源与输出，再交给 `plan`。

## 映射形状

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
        "points": "/points",
        "conditions": "/conditions"
      }
    }
  ]
}
```

该代码块及 `examples/mapping.example.json` 仅示意语法，所有路径都必须按本次真实返回结构修改；不要原样当成官方字段。`records_path` 相对于对应工具的 `structuredContent`；如果没有该字段，则相对于唯一一个 JSON 文本块解析后的对象。多段文本或非 JSON 内容不能自动映射，应根据实际输出手工整理规范快照。

`records_path` 可指向记录数组或单个对象。`fields` 中每个 JSON Pointer 相对于选中的单条记录。空指针 `""` 表示当前对象；JSON Pointer 中 `~1` 代表 `/`，`~0` 代表 `~`。`collections` 必须非空，`schema_version` 必须为整数 `1`。

权益种类仅支持 `coupon`、`points`、`campaign`，来源必须匹配：`coupon` 来自 `query-my-coupons` 或 `available-coupons`，`points` 来自 `query-my-account`，`campaign` 来自 `campaign-calendar`。`now-time-info` 用于时间核对，不映射成权益。

## 字段约定

| 字段 | 含义与约束 |
| --- | --- |
| `id` | 必填，来源中的稳定标识或明确的内部标签；程序自动加工具名前缀以避免跨工具冲突 |
| `title` | 必填，可读名称 |
| `expires_at` | 已核实的截止时间；未知保持 `null` |
| `starts_at` | 已核实的开始时间；未知保持 `null` |
| `benefit_cents` | 明确的单券优惠金额，非负整数，单位分；未知为 `null` |
| `min_spend_cents` | 明确的最低消费，非负整数，单位分；未知为 `null`，不能当成零门槛 |
| `points` | 积分数量，保持积分单位；未知为 `null` |
| `conditions` | 条件文本列表；缺少时 `[]`，有歧义的条款保留原意 |

没有可靠来源的时间、金额和积分字段应省略映射，标准化结果为 `null`；没有条件字段时为 `[]`。`id`、`title`、`conditions` 还支持 `{"value": ...}` 常量，仅用于明确的内部标签或经核对的条件，不能把内部标签称作真实账户权益 ID；时间、金额和积分禁止使用常量。`source` 由程序记录来源工具与原始记录路径，不把内部字段名伪装成官方字段。

金额以分计。如果已确认来源以元计，金额字段支持 `{"path": "/已核实的元字段", "multiply": 100}`，通过十进制运算精确转换为非负整数分，不做静默四舍五入。不要把元直接映射进 `_cents` 字段，也不能仅根据字段名猜测单位；积分不得换算成现金。不要仅根据名称推断“一元券”、叠加规则或到期日。

日期时间应带明确时区，例如 `2026-10-25T23:59:00+08:00`。程序把纯日期的截止解释为北京时间 23:59:59、开始解释为 00:00:00；只有确认此语义符合实际规则时才映射纯日期，否则保留 `null`。未知日期进入待核实且不导出日历，无效格式应修正或明确设为未知，不能导出虚构事件。

## 标准化快照

顶层字段为：`schema_version`（整数 `1`）、`mode`（`live` 或 `demo`）、`timezone`（`Asia/Shanghai`）、`captured_at`（带时区的 ISO 日期时间）、`records`（记录数组）、`warnings`（字符串数组）。`normalize` 仅接受固定官方 Server 的 `live` capture；合成数据使用 `demo` 命令，不伪装为真实采集。

每条记录必须包含上表全部字段，另含 `kind` 和 `source: {"tool": "来源工具", "ref": "原始记录路径"}`；允许未知的字段用 `null` 显式保留，`conditions` 用字符串数组。引擎拒绝重复 ID、来源与种类不匹配、未来采集时点、无时区的日期时间和不合法金额。

## 命令

```sh
python3 -m mai_rescue normalize .private/capture.json --mapping mapping.json --out .private/snapshot.json
python3 -m mai_rescue plan .private/snapshot.json --out .private/report-output --visit --budget 35
```

首次适配完成后可用 `sync --mapping mapping.json --arguments examples/arguments.example.json --out .private/snapshot.json` 重新采集并标准化。官方结构变化后重新核对映射，不复用已失效的路径。

检查输出是否保留未知值、来源和正确单位。真实 capture、snapshot 与报告都可能包含账户信息，只存入本地 `.private/`；公开材料只能使用合成数据或彻底脱敏的结构说明。
