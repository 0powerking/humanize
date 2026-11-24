# 标注规范 v1（pilot 用）

状态：Day 7 pilot 版。**pilot 的唯一产出是“规范能否落地”，不产出指标。**
10 条 pilot 的数字没有统计意义，不得写进任何结论。

数据：`data/eval/pilot_10.jsonl`（5 场景 × 2 条，反讽/非反讽 5/5，全部 `source=synthetic`）
标注表：`data/eval/annotations/pilot_annotations_template.csv`

---

## 1. 标注流程

1. 先读 raw_reply 和最近 3 轮上下文，**不要先看管线输出**，先独立判断用户那条消息。
2. 再读管线重写结果，评人设、节奏、省略、A/B。
3. 每条计时（开始到填完最后一项），记录在备注或单独计时表。
4. 遇到拿不准的字段，不要硬选：填 `uncertain`/`NA`，并在 `notes` 写“歧义：字段 + 原因”。
5. pilot 结束后汇总：标注耗时中位数、歧义字段清单、规范 v1 定稿。

**双人标注**：标注者 A 标全部 10 条（`pilot_annotations_template.csv`）；标注者 B 只标
`pilot_annotations_template_B.csv` 的 5 条重叠（pilot_01–05）。B 模板的 `annotator_B` 列留空时，
说明没有第二人；**不要随便找人凑 5 条**。只有一个人时，`annotator_B` 留空，报告注明
“单标注者，无 κ”，Day 8 不得写双人 κ。

---

## 2. 字段定义

### user_sarcasm（三态，必填）

只判断**用户最后一条消息**是否反讽：

- `sarcastic`：字面褒义/客气，但上下文在抱怨、追问、对比承诺、翻旧账。
- `literal`：真心夸奖、普通陈述、单纯提问。
- `uncertain`：证据不足，或既可能玩笑也可能真心。

标 `sarcastic` 时必须在 `user_sarcasm_evidence` 填原文连续片段；填不出证据就改 `uncertain`。
例：“你可真行，现在八点了” → `sarcastic`，证据“可真行”；“最后那个绝杀” → `literal`。

### user_emotion_label + user_emotion_intensity（必填）

- label 用 2–6 个中文字，优先固定集合：
  失落、不满、疲惫、焦虑、开心、平静、委屈、生气、尴尬、期待。
- intensity：0.0–1.0，步长 0.1；0.3=轻微，0.6=明显，0.9=强烈。
- 例：“第三家了” → 失落 0.7；“您这需求改得真及时” → 不满 0.8。

### persona_consistency（1–5，必填）

先读 `config/persona.yaml` 的 constraints，再评分：

| 分 | 含义 |
|---|---|
| 5 | 完全像该人设，且自然 |
| 4 | 基本像，个别措辞偏书面/客服 |
| 3 | 能看出人设，但机械、像在扮演 |
| 2 | 风格漂移（忽冷忽热、突然正式/玩梗） |
| 1 | 完全不像，或违反硬约束（客服腔、金句收尾） |

违反任意一条硬约束，最高 3 分。

### rhythm_profile（预填，可改）

数据集预填，标注者只需确认或改成更合适的：

- `short_burst`：应该拆成 2–3 条短消息（日常闲聊/反讽/安慰）。
- `single_msg`：应该一条说完（boundary/work，或内容很短）。
- `long_explain`：需要一条较长解释（工作说明、复杂背景）。

### omission_naturalness（1–3，无省略填 NA）

比较 raw 与重写，若内容被省略：

- 3 = 省得自然，指代清楚；
- 2 = 能懂但别扭；
- 1 = 省到看不懂/指代不明；
- `NA` = 没有省略，或无法判断。

### ab_preference（pipe / baseline / tie / NA）

盲评：随机左右、隐藏来源，评“更愿意继续聊”的一条。

- `pipe`：管线更好；`baseline`：原始回复更好；`tie`：差不多；
- `NA`：本轮没有 A/B 输出（pilot 初期可空）。

---

## 3. 耗时与歧义记录

### 3.1 计时

用 `data/eval/annotations/pilot_timing_template.csv` 逐条记 `start_time`、`end_time`、
`minutes`、`segment`：

- `warmup`：第 1 条，熟悉 rubric，**不计入**耗时统计；
- `core`：中间段，**只取这段算中位数和范围**；
- `fatigue`：最后 1–2 条，疲劳，**不计入**。

pilot 结束报 `core` 的 **中位数 / 范围 / 样本数**，不报“感觉很快”。Day 8 用这个估
60 条标注工作量。

### 3.2 歧义记录（append-only，当场记）

每犹豫一次，立刻在下面加一行；不要标完回忆。格式：哪个字段、什么情况、当场怎么判、理由。

| # | sample_id | 字段 | 什么情况 | 当场判断 | 理由 |
|---|---|---|---|---|---|
|  |  |  |  |  |  |

标完 10 条整理成“歧义字段清单”，这是 Day 8 报告“已知限制”的素材，也是回答
“怎么保证标注一致性”的依据。规范 v1 定稿条件：歧义字段 ≤2 类，且每类有明确处理规则。

---

## 4. 已知边界

- pilot 全合成，不能代表真实聊天分布；Day 8 正式集至少 **30% 真实脱敏样本**（60 条里至少 18 条）。
- 真实候选现在就开始存：`data/eval/real_candidates.jsonl`，每天 3–5 条、20 分钟，
  用 `scripts/real_candidates_check.py` 校验；不要等 Day 8 当天找。
- `ab_preference` 依赖双输出，pilot 阶段可留空，不阻塞规范定稿。
- 标注结果只用于校准规范，不用于“管线提升 X%”的结论。
