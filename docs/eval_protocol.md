# 评估协议 v1

状态：Day 2 定稿，Day 7–9 执行。适用于第一阶段（离线、单轮重写 + 上下文窗口）。
本文件是评估的**唯一口径**；改口径要先改这里，再改代码。

---

## 1. 目的与边界

回答四个问题：

1. 管线是否真的减少了套路化表达？
2. 反讽判断是否可靠，错在哪里？
3. 输出是否仍然像指定人设？
4. 盲评下，人是否更愿意选管线输出？

**不回答**：线上留存、真实用户满意度、多轮对话效果。这些需要 IM 接入后另做。

---

## 2. 评估集

- 规模：60 条起，目标 80 条；先做 10 条 pilot 定标注规范，再扩。
- 配比：日常闲聊 15 / 情绪安慰 12 / 反讽阴阳 15 / 边界拒绝 8 / 工作正式 10。
- 反讽与非反讽各约一半，避免类别不平衡污染指标。
- 每条来源必须标 `source: real | synthetic`；混用可以，但报告必须分开报，不能混在一起当真实分布。
- 真实样本必须脱敏（人名/地名/手机号/链接），`anonymized: true`。

每条字段：

```json
{
  "id": "e001",
  "scene": "banter",
  "turns": [{"role": "user", "content": "..."}],
  "raw_reply": "待重写的 AI 原始回复",
  "gold": {
    "user_sarcasm": "sarcastic | literal | uncertain",
    "user_emotion": "不满",
    "persona_id": "p_sunyou"
  },
  "source": "synthetic",
  "anonymized": true,
  "notes": "标注分歧点"
}
```

Day 5 起新增 `rhythm_profile: short_burst | single_msg | long_explain`，用于区分
“该拆没拆”和“不该拆却拆了”；现在不加字段。

---

## 3. 标注规范

### 3.1 反讽（gold.user_sarcasm）

- **只标用户最后一条消息**是否反讽，不标 AI 回复。
- 三态：`sarcastic / literal / uncertain`；证据不足就标 `uncertain`，不要硬判。
- 必须能指出证据片段（原文连续子串），否则视为标注不合格。
- 双人标 20 条重叠算 Cohen's κ；κ < 0.6 先改规范，不扩数据集。

### 3.2 情绪（gold.user_emotion）

- 用 2–6 个中文字，优先落入固定集合：
  失落、不满、疲惫、焦虑、开心、平静、委屈、生气、尴尬、期待。
- 集合外的词允许，但要在 `notes` 写同义词映射，否则统计时会被当成新类别。

### 3.3 人设一致性（人工 1–5）

| 分 | 含义 |
|---|---|
| 5 | 完全像该人设，且自然；换个人写也就这样 |
| 4 | 基本像，个别措辞偏书面/偏客服 |
| 3 | 能看出人设，但机械、像在“扮演” |
| 2 | 风格漂移（忽冷忽热、突然正式或突然玩梗） |
| 1 | 完全不像，或违反硬约束（客服腔、金句收尾） |

评分前先读 `config/persona.yaml` 的 constraints；违反任意一条硬约束，最高 3 分。

### 3.4 A/B 盲评

- 随机左右、隐藏来源，评“更愿意继续聊的是哪个”，允许“差不多”。
- 一次 20 对起；同一批评完再换批，避免标准漂移。
- 主指标：剔除平局后的胜率；副指标：平局率。两者都要报。

### 3.5 节奏与省略（Day 5 起）

- 「分条合理性」1–3：1=不该拆却拆了，2=一般，3=拆得自然。
- 「省略自然度」1–3：1=省到看不懂，2=能懂但别扭，3=省得自然。
- **评估范围**：4 条验证样本 + 探针中 `message_count > 1` 的**全部**样本；不固定抽 20 条。
- 两项与 A/B 放**同一张盲评表**，不增加评分轮次。

> **规则覆盖边界（报告必须原样写）**：`unsafe_omission` 规则检查只覆盖具体实体省略
> （时间/日期/数字/金额）。主语省略、指代省略、重复信息省略不进入规则检查，由 prompt 规则
> 和人工「省略自然度」兜底。“规则 clean”不等于“所有省略都安全”。

### 3.6 Pilot（Day 7）

- 数据：`data/eval/pilot_10.jsonl`，5 场景 × 2 条，反讽/非反讽 5/5，全部 `source=synthetic`。
- 标注表：`data/eval/annotations/pilot_annotations_template.csv`（A，全部 10 条）；
  `pilot_annotations_template_B.csv`（B，5 条重叠，`annotator_B` 列）。字段至少包含
  `user_sarcasm`（三态 + evidence）、`user_emotion_label` + `user_emotion_intensity`
  （0.0–1.0，步长 0.1）、`persona_consistency` 1–5、`rhythm_profile`、
  `omission_naturalness` 1–3（无省略填 NA）、`ab_preference`（pipe/baseline/tie/NA）。
- **pilot 的唯一产出**：标注规范 v1 定稿、标注耗时估计、歧义字段清单；**不产出指标**，
  10 条的数字不能写进结论。Day 7 结果：10/10 重写“接近预期”，无阻断性歧义，规范 v1 定稿；
  但**未填逐字段数值、未测 core 耗时、未测 κ**，不能写成“一致性高/耗时 X 分钟”。
  Day 8a 扩 60 条前先做 **5 条计时校准**（只取 core 中位数）。
- 计时：`pilot_timing_template.csv`；第 1 条 warmup、最后 1–2 条 fatigue，**只取 core 段**
  的中位数/范围/样本数。歧义当场记在 `docs/annotation_guide_v1.md` 末尾，不要标完回忆。
- 双人：A 标全部 10 条，B 标 5 条重叠算 κ；B 模板留空/没人时注明“单标注者，无 κ”，
  Day 8 不得写双人 κ。
- 真实样本：pilot 可全合成；Day 8 正式集**至少 30% 真实脱敏样本**（60 条里至少 18 条）。
  候选现在开始存 `data/eval/real_candidates.jsonl`，每天 3–5 条、20 分钟，用
  `scripts/real_candidates_check.py` 校验；不要等 Day 8 当天找。
- Day 8 拆两段：**8a 建集 + 标注；8b 跑评估 + 写报告**。60 条 × 8 分钟约 8 小时，
  不要压成一天。

---

## 4. 运行与采样协议（ADR-008）

`seed` 会被发送并记录，但实测**不保证复现**；同 `system_fingerprint` 下仍会变。因此：

| 对象 | 采样方式 | 报告方式 |
|---|---|---|
| 标签类（sarcasm.label / emotion.label / tone） | 单次；Day 4 探针后默认 **3 次多数投票**（Day 8 复核 ≥95% 可回退单次） | 比例 + 混淆矩阵 + 投票一致率 |
| 连续值（valence / arousal / confidence） | 3 次运行取**中位数** | 中位数 + 极差 |
| 重写文本质量 | 3 次采样，每次独立评分 | 均值 ± 标准差，不报单次 |
| A/B 偏好 | 预注册 1 次采样；预算允许时 3 次取多数 | 胜率 + Wilson CI，注明采样次数 |

Day 4 探针（`docs/stability_probe.md`）：两轮 base 30 的 `emotion.label` 3/3 一致率分别为
28/30（93.3%）与 29/30（96.7%），Wilson 95% CI 都覆盖 95%。探针不足以区分方案 A/B，
因此按风险控制选保守方案 A（分桶指纹）并对标签指标默认做 3 次多数投票；这是临时决定，
Day 8 用 60 条正式确认，若稳定 ≥95% 可回退单次采样。多数投票使评估调用数约 ×2（ADR-008）。

硬性要求：

1. 评估跑批必须 `--no-cache`，或断言 `usage.cache_hits == 0`；否则复用旧输出，等于伪造样本。
2. 每次运行记录 `requested_model` 与 `reported_model`。当前 key 实际路由到 **`deepseek-flash`**，
   报告里不能写成 `deepseek-chat`。
3. 记录 `system_fingerprint`、`config_hash`、git commit、prompt 版本；缺一项则该批结果不可复现。
4. 同一批评估必须在同一时间段跑完（峰谷价不同，成本对账会偏）；成本报告注明 `pricing_mode`。

---

## 5. 指标定义

| 指标 | 公式 | 备注 |
|---|---|---|
| 套路化表达频率 | 命中 ≥1 个套路的回复数 / 总回复数 | 规则词典口径，附词典版本；另报每千字密度 |
| 反讽误判率 | 错误数 / gold 与预测都属确定类的样本数 | 另报 P/R/F1 与 `uncertain` 覆盖率 |
| 人设一致性 | 人工 1–5 均值 | bootstrap 95% CI；按 persona 分开报 |
| 整体像人偏好 | 管线胜 / (管线胜 + 基线胜) | Wilson CI；同时报平局率 |

零人工成本附加指标：

- 消息长度分布：P50/P95、超 35 字占比（Day 5 起按 `rhythm_profile` 分层）；
  目标值来自 `rhythm.target_msg_chars_p50=12` / `p95=35`，报告要同时写实际分布与目标。
- 事实点保留率：`kept_facts` 是否仍出现在输出中；`kept_facts` 必须通过具体锚点测试
  （时间/日期/数字/金额或原文字面片段），抽象概括单独放 `semantic_points`，不混入保留率。
  **主指标用覆盖率 0.7 口径**，精确子串作为“严格下界”辅助展示；同义替换（“处理→弄”）
  两者都会漏，人工语义保留判定只针对覆盖率口径下剩余 3 条（不全量），成本可控；
- 承诺弱化：**逐条承诺判断**，每条被弱化的承诺在 `risk_flags` 里单独一项
  `commitment_weakened:<fact>:<coverage>`；报告两个数——
  `commitment_weakened_runs`（有弱化的回复数）和 `commitment_weakened_items`
  （被弱化的承诺条目数），不要只报其中一个；
- work 场景扩写率：`compression < -0.3` 的 work 样本数（正式场景话变多）；
- `unsafe_omission` / `partial_omission` 比例（只统计具体实体的缺失；抽象改写与省略不在
  规则判定范围，靠人工「省略自然度」补充）；
- boundary / work 场景的**省略统计**（`message_count`、`final_text/raw` 长度比、被删具体 span 数）：
  这两个场景 prompt 禁止省略，规则不再判 unsafe，统计值用于衡量 prompt 遵守率；
- 延迟 P50/P95、单条成本、`cache_hits`、`degraded` 比例。

---

## 6. 报告必须包含

1. 样本量：**有效样本 N 条（schema 1.2）+ 跳过 M 条旧 schema 记录**，两个数都要写；
   来源配比（real/synthetic）、标注者数量与 κ（单标注者写明）；
2. requested / reported model、`system_fingerprint`、prompt 版本、git commit、config hash；
3. 四个核心指标 + 置信区间 + 采样次数；
4. 失败案例 Top 5（原文、gold、输出、命中的信号、改动说明）；
5. 成本与延迟，注明峰谷价；
6. 明确写“pilot 级结论，不外推”。

---

## 7. 统计

- 比例类：Wilson 区间；均值类：bootstrap 1000 次，百分位 95% CI。
- 不报没有区间的单点提升；不把 60 条上的 5% 差异说成“显著”。
- 多重比较（按场景/按 persona 切片）时，报告原始值并注明未做校正，避免过度解读。

---

## 8. 已知限制

- 60 条是 pilot，只能看方向，不能估计真实分布。
- 目前单标注者；κ 要等第二人。
- 反讽 gold 本身主观，`uncertain` 会吃掉一部分样本。
- 成本模型未建模法定节假日，节假日会高估（ADR-007）。
- 单轮 + 上下文窗口，不代表多轮真实聊天。
- `unsafe_omission` 规则只覆盖具体实体；Day 5 回归 36 条真实输出触发 0 次，
  但观测到 10 个 concrete span 可判定机会，说明规则有机会触发；检出能力由单元测试保证，
  不能写成“规则没实现”。

---

## 9. Day 8 待办（现在不展开）

1. 人工语义保留：只判覆盖率口径下剩余 3 条，不全量。
2. `commitment_weakened` 统计：逐条承诺记录，分别报 `runs` 与 `items` 两个数；
   不和事实丢失合并。
3. work 扩写观察：`compression < -0.3` 的 work 样本，单独看是否啰嗦。
4. 双口径保留率：主指标覆盖率 0.7，精确值作严格下界。
5. 报告样本量：有效 N 条（schema 1.2）+ 跳过 M 条旧 schema；Day 8 真实样本 ≥30%。
6. ratio 调优：与 `kept_facts` 保留率、删除内容类型一起看；本轮不调。

---

## 10. 变更记录

| 日期 | 变更 |
|---|---|
| 2026-11-24 | v1：四指标、3 次采样协议、A/B 盲评、报告字段 |
