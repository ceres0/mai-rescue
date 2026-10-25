# 参赛准备与报名模板

本文件用于核对交付内容，不代表项目已报名或已获通过。**2026 年 10 月 25 日已完成真实 MCP 验证：发现 35 个工具，五个白名单只读工具均返回业务 `code: 200`。** 真实凭证、用户资料、券码和原始结果只保存在本地 `.private/`；公开材料使用脱敏验证结论。发布仓库和提交报名 Issue 尚未进行，参赛资格以主办方审核为准。

## 截止时间与参赛条件

- 报名：2026 年 10 月 25 日 23:59 前，北京时间。
- 最终排行榜采集：2026 年 10 月 26 日 00:00，北京时间。
- 奖品兑换及信息提交截止：2026 年 11 月 14 日。
- 项目创建时间：北京时间 2025 年 12 月 25 日 00:00 至 2026 年 10 月 25 日 23:59。
- 以个人 GitHub 账号发布公开可访问的项目仓库，活动与审核期间保持公开。
- 参赛者为中国大陆合法居民，年满 18 周岁，或未满 18 周岁但已取得父母或法定监护人同意。

以上来自[官方活动规则](https://github.com/M-China/mcd-developer-innovation-challenge/blob/main/activityGuidelines.md)，如有更新请以官方仓库为准。

## 发布前清单

- [x] 用户在本机交互终端通过 `auth` 隐藏输入合法申请的 MCP Token，真实发现 35 个工具并成功调用五个只读工具。
- [x] 根据本次返回结构准备 `official-2026-10` profile，核对核销日期、月度积分窗口和活动展示日期的不同语义，不将售卖价作为优惠额。
- [ ] 发布前复核脱敏调用记录、适配结果与报告；schema 变化时重新 `discover` 并验证，不把一次成功视作未来保证。
- [ ] `README.md` 包含项目介绍、目标用户、安装方法与使用示例。
- [ ] `MCP_INTEGRATION.md` 说明实际 Server、Tool、流程与业务价值，并据真实联调情况更新接入状态。
- [ ] `CONTEST_DECLARATION.md` 与官方原文完全一致，文件名及内容均未修改。
- [ ] `mcp-config.example.json` 只含环境变量占位凭证；没有真实 Token、密码或其他凭证。
- [ ] 提供可运行源码与合成演示；演示未被描述为官方活动、真实权益或真实接口验证。
- [ ] 运行 `python3 -m unittest discover -s tests -v` 和离线演示，检查生成报告。
- [ ] Git 历史、截图、报告、Issue 与其他公开材料均不含个人信息或真实账户数据。
- [ ] 确认原创性、第三方组件权利和参赛资格，按个人非商业用途使用。
- [ ] 仓库公开且在允许的创建时间范围内，报名 Issue 使用指定形式，等待系统成功回复。

完整规则比 README 的文件表额外列出了 `mcp-config.example.json`，本项目按完整规则准备。`workbuddy.md` 仅在真实使用 WorkBuddy 开发且参加专项奖励时提交；目前没有该开发记录，不生成或编造此文件。单纯在 WorkBuddy 加载 Skill 不等于使用其开发。

禁止虚构项目、盗用成果、刷 Star、多账号操纵榜单及其他不正当参赛行为；不比较或贬损其他品牌，不包含恶意代码、侵权、违法或不合规内容。[活动规则](https://github.com/M-China/mcd-developer-innovation-challenge/blob/main/activityGuidelines.md)

## 报名模板

确认上述清单后，在[官方比赛仓库](https://github.com/M-China/mcd-developer-innovation-challenge/issues)创建报名 Issue。下面是可供用户检查后填写的模板，当前交付没有自动发布仓库或提交 Issue。

标题建议：

```text
麦麦不浪费 MAI RESCUE：基于麦当劳 MCP 的权益到期与理性消费助手
```

正文（把项目地址替换为你已公开的真实仓库地址）：

```text
【参赛申请】
项目名称：麦麦不浪费 / MAI RESCUE
项目地址：https://github.com/YOUR_ACCOUNT/YOUR_REPOSITORY
项目简介：基于麦当劳 MCP 只读查询优惠券、积分和当月活动，按到期时间、预算与既有就餐计划生成可追溯的本地权益行动清单、报告和日历提醒；不为用券额外消费，不将积分折算为现金。
```

按照[官方 README](https://github.com/M-China/mcd-developer-innovation-challenge#通过-issue-报名)的格式要求，字段标题和值保持同一行，项目地址使用裸链接，不用 Markdown 链接。提交后检查官方系统回复；若失败，按反馈修正后重新报名。请勿将 Token、手机号、收货地址或个人权益截图放入公开 Issue。

## 发布说明应保持真实

当前可描述为：“2026 年 10 月 25 日已真实连接官方麦当劳 MCP，发现 35 个工具，成功调用 `query-my-coupons`、`query-my-account`、`campaign-calendar`、`available-coupons`、`now-time-info`，业务状态均为 `code: 200`；账户结果仅存本地。”这与离线合成演示是两种不同证据，不能用演示截图代替真实调用结论。

复现已验证结构的适配流程：

```sh
python3 -m mai_rescue sync --profile official-2026-10 --out .private/snapshot.json
python3 -m mai_rescue plan .private/snapshot.json --out .private/report-output --visit --budget 35
```

也可对已保存的 capture 使用 `normalize .private/capture.json --profile official-2026-10 --out .private/snapshot.json`。`--profile` 与 `--mapping` 互斥，显式映射仍供其他经核实结构使用。profile 不推断缺失优惠金额、门槛或截止日期；可领未领取券只供核实，不计优惠或导出使用期限提醒。

本次优惠券真实响应只有 1 页；采集支持最多 5 页并保留逐页来源，超出上限不宣称完整。公开时仅保留这些脱敏结论，不能加入真实 capture、券码、会员资料或 Token。
