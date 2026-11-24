---
id: signal.emotion
version: sig.emotion.v1
---

## system
你在读一段中文即时聊天，判断用户最后一条消息的情绪状态，以及 AI 回复应该用什么语气。

用户情绪 label 用 2-6 个中文字：失落、不满、疲惫、焦虑、开心、平静、委屈、生气、尴尬、期待。

强度与维度：
- intensity：0 到 1，越强越高。
- valence：-1 到 1，负数是负面情绪，正数是正面。
- arousal：0 到 1，越高越激动；疲惫/低落时低，生气/兴奋时高。

语气目标 assistant_tone_target 只能是：
- soothe：先安抚情绪，不急着给方案
- banter：接梗、玩笑、熟人式互损
- matter_of_fact：就事论事，给信息
- set_boundary：对方越界或施压，需要明确拒绝

判断规则：
- 自嘲、反复否定自己 → 失落（soothe），不要当玩笑。
- 阴阳、翻旧账、带着承诺对比 → 不满（banter 或 matter_of_fact）。
- 单纯求助、问事实 → matter_of_fact。
- 明确越界、侮辱、逼迫 → set_boundary。

cues 必须是原文里真实出现的连续片段，最多 3 条。

只输出一个 JSON 对象，不要 markdown 代码块：
{"user_emotion": {"label": "失落", "intensity": 0.7}, "valence": -0.6, "arousal": 0.3, "assistant_tone_target": "soothe", "cues": ["第三家了"]}

## user
{{PAYLOAD_JSON}}
