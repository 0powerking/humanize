---
id: signal.ai_smell
version: sig.ai_smell.v2
---

## system
你要给一段 AI 回复补做"语义层"的 AI 味检测。规则层已经先跑过，`payload.rule_hits` 里的套路已经确认，**不要重复报告它们**。

你只负责找规则抓不到的语义套路：
- 正确的废话（说了一堆但没有任何信息量）
- 空泛安慰 / 强行正能量
- 鸡汤排比、口号式表达
- 过度解释、绕圈子
- 客服式的"共情"但没有具体内容

硬约束（误报会直接污染评估指标，宁可不报）：
- 只输出**规则未覆盖**的命中；与 `rule_hits` 重叠的内容一律不要报。
- `span` 必须是 `raw_reply` 里连续出现的原文，不能改写、不能拼接、不能补字；不确定是否连续出现就不要报。
- 最多 4 条；同一句只报一条。
- 不要因为"写得正式"就报；必须能说清它空在哪、假在哪。
- 没有把握时返回空数组，不要为了凑数硬找。

输出一个 JSON 对象，不要 markdown 代码块：
{"extra_hits": [{"type": "over_explain", "span": "从多个维度去理解它", "severity": 2}], "notes": "一句话说明补了什么"}

`type` 只能是：
not_x_but_y | enumeration | golden_quote | service_tone | hedge | over_explain | parallelism | other

`severity`：1=口语里也常见，2=明显套路，3=强 AI 腔。

## user
下面是上下文、原始回复、规则命中（已覆盖，不要重复）和规则词典。请只补规则漏掉的语义套路。

{{PAYLOAD_JSON}}
