# 工程决策记录（ADR）

格式：背景 → 决策 → 代价 / 为什么仍然值得。每加一条，先写"如果这个决策错了，
我们会怎么发现"。

---

## ADR-001 规则先行，LLM 兜底

**背景**：AI 味里有一大类是**封闭集合**——"首先/其次/最后""不是…而是…""总之""亲"。
用 LLM 判断它们既贵又不可复现。

**决策**：`config/ai_smell_patterns.yaml` 作为唯一词典，做三件事：mock 信号层打分、
重写 prompt 的"必须消掉清单"、治理层残留检测。LLM 只在规则覆盖不到处做语义判断。

**代价**：词典会误伤（口语里也会说"确实"），召回有上限。
**发现方式**：评估集里规则未命中但人工标为 AI 味的样本，按类型补词；每千字密度不降就回滚词条。

---

## ADR-002 单向数据流，信号模型冻结

**背景**：重写层如果能看到并修改信号，归因就断了——"为什么改成这样"再也说不清。

**决策**：`SignalBundle` 及所有子模型 `frozen=True`，字段用 tuple 而非 list；
重写层只拿到 `model_dump()` 的副本。测试：`test_signal_bundle_is_immutable`。

**代价**：构造信号时要多一次序列化；修改信号必须新建对象。
**为什么值得**：审计日志里的每条 `change_notes.signal_ref` 都能指回当时真正生效的信号。

---

## ADR-003 失败降级，而不是失败

**背景**：信号层是 3 次独立 LLM 调用，任何一次超时/格式错误都不该让整条回复发不出去。

**决策**：单模块失败 → 填中性值 + 模块名写入 `meta.degraded` + 审计记录；重写失败 →
回退原文并标记 `rewrite_failed`。其中 **ai_smell 失败的默认分是 0.5（高于 gate 0.25）**：
"不知道脏不脏"时倾向重写，而不是放行。

**代价**：失败会被"看起来正常"的输出掩盖，必须靠 degraded 字段和监控发现。
**发现方式**：批量跑批后统计 degraded 比例；超过 2% 就查 provider。

---

## ADR-004 重写缓存键必须包含信号指纹

**背景**：只按"原始回复 + prompt 版本"做缓存会错得很隐蔽：原始回复没变，但信号层换了
prompt / 模型 / 词典，产出的 `SignalBundle` 变了，缓存却仍然命中旧重写。

**决策**：
- `cache.signal_fingerprint()` = 对 `SignalBundle` 的语义内容做 sha256；
- **剔除易变字段**（`meta.latency_ms`），否则同一语义的两次运行永远不命中；
- `make_cache_key(stage, model, prompt_version, payload, signal_hash)` 把五者一起哈希；
- `make_cache_key(stage, model, prompt_version, payload, signal_hash, temperature, seed)`
  把这些维度一起哈希；`payload` 里的 `signals` 本身就是信号视图，所以即使没有
  `signal_hash`，"原文没变、信号变了"也会 miss —— `signal_hash` 是对这条依赖的显式声明，
  防止将来payload 改成只传信号 id 时静默退化。
- 原 `cache.include_signal_hash` 开关已删除：payload 已包含信号视图，这个开关**无法**
  制造"安全/不安全"的差异，只会让人误以为可以关掉；留着一个没有正确性含义的开关比没有更糟。

**验收测试**（Day 1 已写，Day 2 接磁盘缓存后继续用）：
- `test_prompt_version_bump_invalidates_key`
- `test_signal_change_invalidates_key`
- `test_signal_fingerprint_ignores_latency`
- `test_signal_fingerprint_tracks_signal_content`
- `test_config_hash_changes_when_prompt_version_bumps`

**代价**：缓存命中率比"只按原文"低；换来的是不会拿到与证据不匹配的重写。
**如果错了会怎么发现**：改一版 signal prompt 后重跑，缓存命中数不降反升 → 键漏了维度。

**2026-11-24 修订（Day 2 缓存落地）**：按"缓存是重写阶段输入的纯函数"执行。

- 新增 `SignalBundle.rewrite_view()`，只含 `sarcasm / emotion / ai_smell / persona_state`
  四个语义块；重写 payload 和 `signal_fingerprint` **用同一个视图**。
- `meta` 不再进入重写 prompt：模型名、prompt 版本、降级标记对改写没有信息价值，
  放进去只会让缓存键依赖"谁产出的信号"而不是"信号说了什么"。
- `rewrite_view` 同时剔除诊断性自由文本（`evidence / reason / cues / notes`）：这些是
  模型生成的解释，运行间会措辞漂移，对改写没有用处，却会让指纹抖动。完整内容仍保留在
  `RunRecord.signals` 里供审计（ADR-009 融合后 `notes` 更长，这一点尤其重要）。
- 因此**换模型但信号内容相同 → 命中**；content 变了 → miss。代价是失去按模型分区，
  由 `config_hash` + `versions` 承担可追溯性。
- provenance 仍写进 `RunRecord`；命中缓存时 `meta.cache_source_models` 记录缓存条目的
  来源模型，避免"用了旧模型的结果却看不出来"。
- 新增测试：`test_signal_fingerprint_ignores_provenance`、
  `test_rewrite_payload_has_no_meta`、`test_signal_content_change_invalidates_rewrite_cache`
  （端到端：改 sarcasm label → 指纹变 → 重写重新调用）、`test_cache_source_models` 语义。

**2026-11-24 三次修订（ADR-008 方案 A）**：`fingerprint_view` 对 valence/arousal 分 5 桶、
confidence 分 3 桶；重写缓存键**不再包含 `signals` 明文**，只包含分桶指纹，否则精确值会让分桶
失效。`rewrite_view` 仍向 prompt 传精确值。新增同桶命中 / 跨桶失效 / 抖动收益测试。

**已知取舍**：信号层本身也被缓存，所以"改一条信号内容"在真实系统里必须伴随信号层
版本/模型/词典变化才能穿过缓存；端到端测试显式 bump 了 `signal.sarcasm` 版本来模拟
这个真实路径，而不是绕过缓存。

---

## ADR-005 改写幅度闸门，并重算 ratio

**背景**：不是所有回复都值得改。原文已经自然时，强行重写只会引入语义漂移。

**决策**：
- 阈值写在 `config/pipeline.yaml` 的 `thresholds.rewrite_gate_ai_smell`（当前 0.25），
  `config.py` 里的同名字段只是 YAML 缺字段时的兜底，不参与正常路径；
- `ai_smell.score < 阈值` 时允许 `noop`（原文直出，`rewrite_ratio=0`）；
- 重写层必须输出 `kept_facts`，治理层据此核对事实是否被改掉；
- **ratio 由管线用 `SequenceMatcher` 重算**，不信任模型自报；偏差 > 0.2 记
  `ratio_self_report_mismatch`，超过 `max_rewrite_ratio` 记 `rewrite_ratio_over_limit`
  （只记录，不截断文本——截断会制造病句）。

**Day 8 调参口径**（写死在这里，避免拍脑袋）：不按 ai_smell 分数分布调，按两类错误调——

1. **该不改却改了**：原文已经自然、却被强制重写的样本比例（`noop` 本应命中的样本）；
2. **该改却没改**：AI 味明显、却因为分数低于阈值被放行的样本比例。

阈值由这两类错误在评估集上的权衡决定，并同时报告两个错误率；只看单项会优化出一个
在另一侧很糟的阈值。

**代价**：多一次 diff 计算；模型"少改"时会被判为 mismatched。
**发现方式**：如果 `noop_without_gate_permission` 频繁出现，说明 prompt 没遵守 gate。

---

## ADR-006 第一阶段治理层只做检测

**背景**：治理层的完整形态是"命中 → 局部重写 → 再检测"，但局部重写本身需要重写 prompt
和一轮评估，容易吞掉第一周。

**决策**：v1 只做规则残留扫描 + 敏感词桩 + 审计日志；命中后**原样输出**并在审计里写
`local rewrite deferred (phase 1.5)`。这样"套路化表达频率"这个指标仍然可以自动算。

**代价**：v1 的输出里可能仍有残留套路。
**发现方式**：评估报告里 `residue_hits > 0` 的比例就是 1.5 阶段的优先级依据。

---

## ADR-007 成本估算与预算闸门

**背景**：模型按输入/输出 token 计费，且官网价格区分高峰/空闲、输入缓存命中/未命中。
只记 token 不换算金额，50 元预算就只是摆设。

**决策**：
- 单价写在 `config/pipeline.yaml` 的 `cost.models.<provider-reported-model>`，单位元/百万 token；
  当前 key 实回 `deepseek-flash`，按 flash 价：输入命中 0.04、输入未命中 2、输出 8（高峰价）。
- 空闲时段 = 高峰价 × `off_peak_factor`（0.5）；高峰窗口按北京时间工作日 9–12、14–18。
- **法定节假日未建模**：节假日会按工作日算，属于**高估**，对预算保护是安全方向。
- 金额用 provider 返回的 `prompt_cache_hit_tokens` 拆分命中/未命中，不把全部输入按未命中价算。
- 未知模型走 `default` 档（按最贵的 v4-pro 估），避免低估。
- `BudgetTracker`：80% 告警，100% 停止后续样本并以 exit code 4 退出。

**实测**：单条 e002 冷跑 0.0095 元（provider 侧 prompt cache 命中时约 0.006 元）；
60 条 × 2 组 × 3 次采样约 3.3 元，50 元预算足够跑完评估集多轮。
若评估启用信号层 3 次多数投票（ADR-008），总调用数约 ×2，预算约 6.6 元，仍在 50 元内。

**如果错了会怎么发现**：连续跑批后把 `usage.cost_yuan` 与账单对账；偏差超过 20% 先查
peak/off-peak 判定和模型名映射，再查 provider 是否改了价。

---

## ADR-008 seed 不保证复现（2026-11-24 实测）

**背景**：重写需要 `temperature=0.9` 的多样性，评估又需要可复现，原计划用固定 `seed` 兼顾。

**实测**（`deepseek-flash`，`system_fingerprint` 三次运行恒为 `bc19ba97…`）：

| 条件 | 结果 |
|---|---|
| `seed=42, temperature=0.9` 两次 | 文本不同 |
| `seed=42, temperature=0.0` 两次 | 完全一致 |
| 管线 3 次（`seed=7/42`, temp 0/0.9） | `sarcasm` 标签/置信度稳定；`emotion.valence` 在 −0.6/−0.7 间抖动，导致 3 个不同的信号指纹 |

**结论**：
1. `seed` 可以被网关接受、也会被记录，但**不能承诺复现**；同 `system_fingerprint` 下仍有采样差异。
2. 评估协议：标签类指标（反讽 label、tone）单次即可；连续值（valence/arousal/confidence）
   取 3 次中位数；重写类指标用 3 次采样均值 + 报告方差，不报单次结果当结论。
3. 缓存：精确 JSON 指纹会被 ±0.1 的浮点抖动打穿，产生无意义 miss。Day 4 按方案 B 处理（见下）。
4. 成本：3 次采样约 0.028 元/样本，预算可控（ADR-007）。
5. `system_fingerprint` 三次运行恒为 `bc19ba97…`、输出仍不同 → 可判定为**网关/后端侧非确定性采样**，
   不是模型版本切换。面试口径：不承诺复现，用“多次采样 + 方差报告”对冲，并明确写出这一点。

**前提校验（Day 2 补查，e002 三次运行）**：
`sarcasm.label` 均 `sarcastic`（confidence 均 0.9）、`emotion.label` 均 `不满`、`tone` 均 `banter`、
`ai_smell` 命中类型集合完全一致；只有 `valence` 在 −0.7/−0.6 抖动。**标签级判断在该样本上是稳定的**。
但只有一个样本，不能外推——Day 4 必须在评估集的 60 条上复核，若标签也抖，方案 B 作废。

**方案 A（已实施，2026-11-24）**：分桶指纹，保留连续值信息但不被 ±0.1 抖动打穿。

1. `fingerprint_view(signals)`：`sarcasm.label` + target + confidence 3 桶；
   `emotion.label` + tone + valence/arousal 5 桶；`ai_smell` 类型集合；persona 档位。
2. `rewrite_view` 继续把**精确**连续值传给 prompt，不降精度。
3. 重写缓存键**不再包含 `signals` 明文**，只包含分桶指纹——否则精确值会让分桶失效。
4. 测试：同桶抖动命中、跨桶/换 label/换类型失效；valence `-0.65/-0.61/-0.63` 从 3 次 miss
   变成 1 次调用 + 2 次命中。

**方案 B（Day 8 回退候选）**：指纹只含 label/tone/类型/persona 档位，连续值完全不进指纹；
成本更低（评估不需要信号层多数投票）。若 60 条确认 `emotion.label` ≥95%，回退 B。

**Day 4 探针方案（30 条 base + 2 条补充）**：

- 数据：`data/probe/stability_probe.jsonl`，32 条 = **30 条 base** + 2 条 supplemental；
  base 为 5 场景各 6 条，反讽/非反讽 14/16；`source=synthetic` + `probe_` 前缀 + 单独目录，
  **不与 `data/eval` 混**。
- 补充 2 条（`probe_031` 生气 / `probe_032` 期待）只作观察，**不计入 95% 判断线**；
  目的是补高频、高强度情绪，看语气目标是否被误判。
- 只跑信号层 3 次，统计逐字段一致率：`sarcasm.label`、`emotion.user_emotion.label`、
  `assistant_tone_target`、`ai_smell` 命中类型集合。
- **comfort 的 2 条反讽单独统计**：`probe_008`（weak，被动攻击）与 `probe_011`（strong，反话），
  不与其它场景混算；如果 weak 那条不稳，comfort 的整体稳定性结论会偏乐观。
- **95% 是 30 条 base 的临时判断线，不是最终结论**：30 条的置信区间很宽，Day 8 必须用 60 条
  评估集正式确认；报告里要同时写探针结论和样本量限制。
- 成本：32 × 3 次 × 3 路 ≈ 288 次信号调用，约 0.12–0.16 元/轮。

**若启用多数投票**：只对**信号层**做 3 次多数投票——3 路并行，每路跑 3 次取多数；
**重写层仍单次**，输出抖动交给评估协议的 3 次均值 + 方差，不在运行时投票。
调用数：每样本信号 3 路 ×3 = 9 次 + 重写 1 次 = 10 次（原 4 次）；按评估协议再叠加 3 次重写采样，
则为 9 + 3 = 12 次 / 样本 / 条件，约为原来的 2 倍（6 次），评估总成本从 ~3.3 元升到 ~6.6 元。
预算仍按 50 元上限留余量。

**Day 4 实测结果（2026-11-24，`docs/stability_probe.md`）**：

- 两轮各 32 条 × 3 次，**`cache_hits=0`**，288 次真实调用/轮，成本 0.1552 / 0.1215 元，
  实回 `deepseek-flash`。
- base 30 的 3/3 一致率：

| 字段 | run 1 | run 2 |
|---|---|---|
| `sarcasm.label` | 29/30（96.7%） | 30/30 |
| `emotion.label` | 28/30（93.3%） | 29/30（96.7%） |
| `tone` | 29/30（96.7%） | 30/30 |
| `ai_smell.types` | 29/30（96.7%） | 29/30（96.7%） |

四个字段的 2/3 多数在两轮里都是 30/30。

- **判定理由（风险控制，不是机械阈值）**：28/30 的 Wilson 95% CI 是 78.7–98.2%，
  29/30 是 83.3–99.4%，**探针信号不足以区分方案 A/B**；两轮结果还在翻转
  （run 2 的机械推荐甚至是 B）。用单轮 95% 切割会让结论随抽样运气变化，因此选保守的 A；
  分桶代价可控，Day 8 用 60 条确认，≥95% 回退 B。
- comfort 子组两轮均 2/2；`probe_031` 稳定判为 `sarcastic/不满/soothe`
  （“生气→不满”是标签边界，不是抖动）；`probe_032` 稳定判为 `literal/期待/banter`
  （tone 是否够共情，Day 5 确认）。
- **系统性风险**：`probe_028`（情绪）与 `probe_004`（语气）在 run 1 中滑向中性，
  说明情绪强度可能被系统性低估。Day 5 重写层要有“信号说平静、上下文明显有情绪”的兜底，
  不能完全依赖信号。
- **运行时口径**：`signals.vote_runs` 配置化，**默认 1**；评估显式 `--vote-runs 3`。
  运行时单次采样，信号层抖动由重写层鲁棒性兜底；Day 8 如果发现抖动导致重写质量显著下降，
  再考虑线上开 3 次。第一版不做生产优化，但风险不藏起来。

**代价**：评估从“跑一次”变成“跑三次取均值”，时间 ×3；换来的是结论不依赖单次采样运气。

---

## ADR-009 ai_smell 融合：规则锚点 + LLM 只补语义缺口

**背景**：规则词典在 20 条 fixture 上 10/10 召回、0/7 误报，但语义召回只有 76.9%——
“正确废话 / 空泛安慰 / 鸡汤排比”没有词面标记。让 LLM 直接重打一遍分会丢掉规则的
可复现性，也可能用更贵的判断覆盖更便宜的正确判断。

**决策**：
1. 管线**永远先跑规则** `lexicon.scan()`，把 `rule_hits` 放进 prompt；
   `signal.ai_smell` v2 只输出 `extra_hits`（规则未覆盖的语义命中）。
2. 合并规则：
   - 同 span、不同 type：**规则优先**，LLM 的 type 记为
     `alt_type: span='…' rule=… llm=…` 写进 `notes`，**不进 `hits`**；
   - 同 span、同 type：普通重复，直接丢弃；
   - 不同 span 但字符区间重叠：规则优先，记 `overlap_skipped`；
   - span 不是原文子串：丢弃（幻觉防护）。
3. **score 由合并后的 hits 用同一公式重算**（`sum(severity)/8`），不信任模型自报。
4. LLM 失败/格式错：只用规则，`meta.degraded` 记 `ai_smell_llm`，不阻断。
5. 诊断性自由文本（`evidence/reason/cues/notes`）不进 `rewrite_view`，避免模型措辞
   抖动污染缓存；完整内容仍保留在 `RunRecord.signals` 里供审计。

**验收（2026-11-24，20 条 fixture，真实 deepseek-flash）**：

| 指标 | 结果 | 口径 |
|---|---|---|
| gap 命中 | 3/3 | 软目标 ≥2/3 |
| clean 新增误报 | 0/7 | **硬约束，必须为 0** |
| 规则命中丢失 | 0/10 | 硬约束 |
| 成本 | 0.0452 元 | 20 次 ai_smell 调用 |

**为什么 clean 误报是硬约束**：套路化表达频率 = 命中回复数 / 总回复数；误报直接推高这个
指标，会让“管线降低了 AI 味”的结论失真。gap 漏检只是召回问题，可以在报告里如实写覆盖率。

**v1 / v2 对比（同一 20 条 fixture，不是评估集）**：

| 版本 | 规则召回 | 语义召回 | clean 误报 |
|---|---|---|---|
| v1 规则 only | 10/10 | 10/13（76.9%） | 0/7 |
| v2 规则 + LLM | 10/10 | **13/13（100%）** | 0/7 |

语义召回 = 规则命中的 + LLM 补的 gap；v1 的 3 条缺口正是 v2 补上的 3 条。两份报告分别由
`scripts/lexicon_report.py` 与 `scripts/ai_smell_fusion_check.py` 生成，保留在 `data/fusion/`。

**代价**：每次 ai_smell 多一次 LLM 调用；`alt_type` 只记录不参与评分，若以后要评估
“类型冲突率”，需要单独统计 notes 里的 `alt_type_count`。

---

## ADR-010 RewriteOutput 改为多消息数组（Day 5 step 1）

**背景**：`RewriteOutput.text` 假设“一条输入对应一条输出”，但真人聊天是 1–4 条短消息的节奏。
先把结构改对，再改 prompt——否则 prompt、治理、评估同时变，出问题无法归因。

**决策**：
- `messages: tuple[str, ...]`，1–4 条；`text` 变成只读属性 `"\n".join(messages)`，不序列化，
  只服务尚未适配多消息的单文本下游。
- `message_count` / `max_msg_chars` **由 schema validator 和管线双重重算**，输入字段不做
  `ge/le` 约束：模型自报 9 条也不能让它校验失败，而是直接覆盖。
- 兼容期：`{"text": "..."}` 旧形状在 `model_validator(mode="before")` 里自动包成
  `messages=[text]`；Step 2 把 prompt 改成输出 `messages` 后，这个兼容层保留一个版本再删。
- 治理层（Step 1）把多条用 `\n` 合并成 `final_text`，审计记
  `messages: N message(s) merged into final_text ...`；评估层下一步直接消费 `messages`。
- `SCHEMA_VERSION` 从 `1.0` 升到 `1.1`（删除 `text` 字段是破坏性变更）。
- **persona / rhythm 边界**：`rhythm.target_msg_chars_p50` 优先于 persona 的 `max_chars`；
  persona 的 `max_chars` **只约束单条消息**，不乘到分条数上。4 条 × 60 字是发小作文，
  不是聊天——分条数由 `max_messages_per_reply` 单独管。

**风险**：多消息会让下游的敏感/残留检查跨消息边界；Step 1 用 `\n` 合并后统一扫描，
分条级检查留到 Step 3。

---

## ADR-011 rhythm 配置与两条硬规则兜底（Day 5 Step 2）

**背景**：Step 2 给 prompt v2/v3 加了节奏规则（12 字目标、35 字上限、日常 2–3 条、正式场景单条）。
6 条目测发现 **prompt-only 守不住 hard rule**：

- `probe_019`（boundary）仍被拆成 2 条，违反“正式场景不拆”；
- `e004`（gate 放行、AI 味为 0）仍被微调“要→得”，不是 noop。

**决策**：
1. `rhythm` 配置：`target_msg_chars_p50=12`、`p95=35`、`max_messages_per_reply=4`、
   `split_allowed_scenes=[casual,banter,comfort]`。prompt 里写同一组数字；改配置必须同步
   bump prompt 版本，否则模型与评估口径不一致。
2. **正式场景硬兜底**：`scene` 不在 `split_allowed_scenes` 且模型输出多条时，管线用 `""` 合并为
   一条，记 `merged_outside_split_scene` 到 `risk_flags`。
3. **trivial edit 硬兜底**：`allow_noop=true`、`ai_smell.score == 0`、实际 ratio ≤
   `thresholds.noop_trivial_ratio`（默认 0.2，配置化）时，管线强制 noop（消息=原文、ratio=0），
   但**保留 change_notes** 并逐条标 `suppressed: true` + `suppressed_by: forced_noop_trivial_edit`。
   这样能统计“模型想改但被拦”的次数，作为 prompt 质量的信号；阈值 Step 4 按回归数据调。
   这是“不要为了改而改”的最低保证；0.2 是经验阈值，先记录，Day 8 评估可调。
4. persona `max_chars` 只约束单条，不乘分条数；分条数由 `max_messages_per_reply` 管（ADR-010）。

**为什么不用 prompt 反复调**：两条规则都是策略，不是措辞问题；模型偶尔遵守、偶尔不遵守，
评估时会变成随机项。管线兜底是确定性的，且用 risk_flags 保留“模型原本想做什么”。

**6 条目测（2026-11-24，prompt rw.v3，真实 deepseek-flash）**：

| 样本 | scene | 条数 | 各条字数 | 结果 |
|---|---|---|---|---|
| e001 | casual | 3 | 10/11/10 | 合理，短句承接 |
| e002 | banter | 2 | 14/10 | 合理，接住反讽 |
| e003 | comfort | 3 | 11/10/14 | 合理，没有拆碎 |
| e004 | casual | 1 | 12 | 强制 noop，正确 |
| probe_019 | boundary | 1 | 22 | 模型拆了 2 条，被合并回 1 条 |
| probe_025 | work | 1 | 13 | noop，正确 |

e001/e003 的 `rewrite_ratio` 分别 0.86/0.83，触发 `rewrite_ratio_over_limit`；内容没问题，
但说明模型压缩偏激进，Step 4 回归时看是否要放宽或调整 prompt。

---

## ADR-012 语用省略检查：精度优先，范围收窄（Day 5 Step 3）

**背景**：Step 3 给 prompt v5 加了省略规则（日常场景可省主语/重复信息/指代，正式场景禁止，
comfort 不省支撑性表达），并要求治理层检查 `unsafe_omission`。

**第一版实现与实测问题**：按“逐子句骨架 + 字符覆盖率”比对，4 条验证出现大量误报：

- e001 一次 7 条，主要是把“评估核心诉求 → 看看自己最想要啥”这类**同义改写**判成省略；
- probe_031 4 条、probe_008 2 条，把“不想麻烦对方 → 想让我来就直说”判成丢失。

规则级字符串比对无法可靠区分“省略”和“改写”；误报会直接污染“省略自然度”指标。

**决策**：
1. 只判定**可判定的具体实体**：`kept_facts` 里带时间/日期/数字/金额标记的条目，
   以及 raw 中被删除的具体时间/数字 span。
2. 匹配用包含 + 字符覆盖率 0.8；`kept_facts` 只出现一部分（如“周六下午 → 周六”）记
   `partial_omission`（进 risk_flags，不阻断）；完全缺失且不在最近 N 轮上下文里记
   `unsafe_omission`。
3. 抽象名词/动词、同义改写、语气词/连接词**不进入规则检查**；这些交给 `kept_facts` 质量
   与人工评估。这是已知局限，不假装规则能判断语义。
4. 上下文窗口 `govern.omission_context_turns=4`，可配置。
5. 检查结果只进 `risk_flags` + 审计细节，不自动改写。

**4 条验证（prompt v5，真实 deepseek-flash，`--no-cache`）**：

| 样本 | scene | 消息 | omission |
|---|---|---|---|
| e001 | casual | 3 条（18/13/11 字） | clean |
| probe_031 | comfort / weak | 2 条（7/14 字） | clean |
| probe_008 | comfort / weak | 2 条（13/12 字） | clean |
| probe_025 | work | 1 条（13 字，noop） | clean |

**遗留问题**：e001 的 `kept_facts` 仍然写成“评估核心诉求/观察市场机会/做好财务缓冲”这类抽象概括，
没有按 prompt 要求列具体实体；Step 4 的“kept_facts 保留率”因此会偏噪声。Step 3 不继续磨 prompt，
留到 Step 4 与 ratio 一起看。

---

## Step 4 前置口径：ratio 和 kept_facts 一起看

- `rewrite_ratio` 高**不等于**信息丢失。删掉“首先/其次/从另一个角度”这类 AI 味套话，
  ratio 0.86 是有效压缩；只有删掉 `kept_facts` 实体或关键限定（“周六下午→周六”、
  “别裸辞→别急”）才是问题。
- 判定顺序：先看 `kept_facts` 保留率，再看被删内容的类型；**全保留 + 高 ratio = 好压缩，
  不调 prompt**；实体丢失才收紧。
- e002 的 `ratio_self_report_mismatch` 同理：对比模型自报与实际 ratio，判断是“自报不准”
  还是“实际改写真的大”，不要凭感觉调。
- 当前 `kept_facts` 质量本身受 prompt 影响（见 ADR-012 遗留问题），Step 4 先修 fact 粒度，
  再谈 ratio 阈值。

---

## Day 5 前置任务：节奏与语用省略（Step 1–3 已实施）

**观察**：真人即时聊天的"人味"很大一部分来自节奏——短句、一条拆成多条发、单条很少
超过 20 字。当前 `RewriteOutput` 只有单个 `text`，等于假设"一条输入对应一条输出"，
结果仍然是"消息块"而不是"聊天"。

**状态：Step 1 / Step 2 / Step 3 已实施（messages + rhythm + 省略检查）；Step 4 回归待做。**

1. ✅ `RewriteOutput` 改为 `messages`（1–4 条）；`message_count`、`max_msg_chars` 重算；
   治理层用 `\n` 合并并写审计（ADR-010）。
2. ✅ `config` 增加 `rhythm` 段：`target_msg_chars_p50=12`、`p95=35`、
   `max_messages_per_reply=4`、`split_allowed_scenes=[casual,banter,comfort]`。
   **与 persona 的边界**：`target_msg_chars_p50` 优先；persona `max_chars` 只约束单条，
   不乘到分条数上。正式场景多条由管线强制合并（ADR-011）。
3. ✅ `prompts/rewrite.md` v5 增加节奏规则：日常闲聊优先拆 2–3 条短消息、每条独立可读、
   承接上一条；正式场景必须单条；单条目标 12 字、上限 35 字，超 35 优先精简不再拆；
   日常场景允许语用省略，正式场景禁止，comfort 不省支撑性表达。

**评估侧同步加**：
- 「消息长度分布」：P50/P95、超 35 字占比（零人工成本，自动算）；
- 「分条合理性」：抽 20 条人工 1–3 分（1=不该拆却拆了，3=拆得自然）；
- 评估集每条加 `rhythm_profile`：`short_burst / single_msg / long_explain`，
  用来区分"该拆没拆"和"不该拆却拆了"。

**风险**：拆条会放大语气词和碎片化，正式场景容易显得轻浮；所以 `split_allowed_scenes`
是硬开关，且评估必须按 `rhythm_profile` 分层报告，不能只看总体均值。

### 第二个语用特征：上下文省略（同一批做）

**观察**：真人聊天大量依赖上下文做语用省略——主语省略、重复信息省略、指代替代。
例如上文已经提过堵车，回复"我编的"就够；当前重写层倾向输出完整句（"堵车这理由我自己都不信"），
和即时聊天的语用习惯相反。完整句不是错，但"每条都完整"本身就是 AI 味。

**状态：Step 1–3 已实施；Step 4 回归待做。**

1. ✅ `prompts/rewrite.md` v5 加规则：日常闲聊允许语用省略，但必须同时满足——
   被省略的信息在**上文出现过**、指代**无歧义**、`kept_facts` 里的实体**不能省**；
   正式场景（`work` / `boundary`）不省略；comfort 不省支撑性表达。
2. ✅ 治理层加“省略合法性”自动检查（规则级、零成本）：
   对比 raw 与 rewritten，检查 `kept_facts` 实体与具体时间/数字 span 是否仍在
   final_text 或最近 N 轮上下文；缺失记 `unsafe_omission`，部分记 `partial_omission`，
   进 `risk_flags`，不阻断。
   **范围收窄（ADR-012）**：只查可判定的具体实体；抽象名词/动词与同义改写不做规则判断
   （第一版误报 7/4/2，精度不可接受），留给 kept_facts 质量与人工评估。
3. 评估加人工指标「省略自然度」1–3 分（1=省得看不懂，3=省得自然），抽 20 条，
   与「分条合理性」放**同一张盲评表**，不增加评分轮次。

**风险**：省略过度会让回复失去可读性，尤其在跨话题、多人群里；所以 `unsafe_omission`
比例要单独报，不能被总体“像人”偏好掩盖。

---

## 自纠记录（Self-corrections）

两次都是实现后自查发现、用户未指出。记录**模式**，不只是记录修复——面试时这两条比“功能列表”
更能说明工程判断。

### 1. 2026-11-24：`include_signal_hash` 是假开关

- **原设计**：`config/pipeline.yaml` 加开关，控制缓存键是否包含信号哈希。
- **问题**：rewrite payload 里已经带完整 `signals`，即使关掉 `signal_hash`，信号一变 key 照样变。
  写“关掉开关后改信号仍 miss”的测试时发现它无法制造任何行为差异——只是一个装饰性配置。
- **修复**：删除字段；`make_cache_key` 保留显式 `signal_hash`，作为“将来 payload 只传信号 id”
  的防退化声明。ADR-004 写明原因。
- **防回归**：`test_signal_content_is_part_of_the_key`。
- **教训**：配置项必须真实改变行为。不能改变行为的开关是负债，不是灵活性。

### 2. 2026-11-24：`ai_smell.notes` 进 `rewrite_view` 污染指纹

- **原设计**：`rewrite_view` 直接 dump 整个 `ai_smell`，包含 `notes`。
- **问题**：融合后 `notes` 是模型自由文本（补了什么、alt_type、overlap），每次措辞不同；
  它进入指纹 → 信号内容没变但缓存 key 变 → 无效 miss。
- **修复**：`rewrite_view` 剔除 `evidence / reason / cues / notes` 四类诊断文本；
  完整内容仍保留在 `RunRecord.signals` 供审计。ADR-004 补修订段。
- **防回归**：`test_rewrite_view_strips_diagnostic_prose`、
  `test_signal_fingerprint_ignores_diagnostic_prose`。
- **教训**：自由文本不进缓存键；诊断信息与决策输入必须分层。
