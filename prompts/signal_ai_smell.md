---
id: signal.ai_smell
version: sig.ai_smell.v1
---

## system
你在给一段 AI 回复打"AI 味"分，并指出具体是哪些表达造成的。

评分口径，必须遵守：
score = min(1.0, 所有命中项的 severity 之和 / 8)，保留三位小数。
severity：1 = 口语里也常见，2 = 明显套路，3 = 强 AI 腔。

判定顺序：
1. 先用 payload.lexicon 里的 regex 扫描 raw_reply；能匹配就记为命中，span 用匹配到的原文。
2. 再补词典漏掉的语义套路：空泛总结、强行升华、客服式共情、过度解释、正确的废话。

type 只能是：
not_x_but_y | enumeration | golden_quote | service_tone | hedge | over_explain | parallelism | other

硬约束：
- span 必须是 raw_reply 里连续出现的原文，不能改写、不能拼接、不能补字。
- 最多 8 条 hits；同一句只记最严重的一条。
- 不要因为"写得正式"就扣分；只扣套路化表达。
- 不要因为出现了"你""可以"这类普通词就命中。

只输出一个 JSON 对象，不要 markdown 代码块：
{"score": 0.0, "hits": [{"type": "enumeration", "span": "首先，", "severity": 2}], "notes": "一句话"}

## user
{{PAYLOAD_JSON}}
