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
- 因此**换模型但信号内容相同 → 命中**；content 变了 → miss。代价是失去按模型分区，
  由 `config_hash` + `versions` 承担可追溯性。
- provenance 仍写进 `RunRecord`；命中缓存时 `meta.cache_source_models` 记录缓存条目的
  来源模型，避免"用了旧模型的结果却看不出来"。
- 新增测试：`test_signal_fingerprint_ignores_provenance`、
  `test_rewrite_payload_has_no_meta`、`test_signal_content_change_invalidates_rewrite_cache`
  （端到端：改 sarcasm label → 指纹变 → 重写重新调用）、`test_cache_source_models` 语义。

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

**实测**：单条 e002 冷跑约 0.006–0.014 元（取决于 provider 侧 prompt cache 命中）；
60 条 × 2 组 × 3 次采样约 3–5 元，50 元预算足够。

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
3. 缓存：精确 JSON 指纹会被 ±0.1 的浮点抖动打穿，产生无意义 miss。Day 4 考虑把
   `valence/arousal/confidence` 量化到 0.1 后再进指纹——**提案，未实施**，需要评估集验证量化
   不会改变标签。
4. 成本：3 次采样约 0.028 元/样本，预算可控（ADR-007）。

**代价**：评估从“跑一次”变成“跑三次取均值”，时间 ×3；换来的是结论不依赖单次采样运气。

---

## Day 5 前置任务：从"单条消息"到"聊天节奏"（提案，未实施）

**观察**：真人即时聊天的"人味"很大一部分来自节奏——短句、一条拆成多条发、单条很少
超过 20 字。当前 `RewriteOutput` 只有单个 `text`，等于假设"一条输入对应一条输出"，
结果仍然是"消息块"而不是"聊天"。

**状态：未实施，现在不改 schema。** Day 5 重写层定稿前按下面三件事改：

1. `RewriteOutput` 改为 `messages: list[str]`（1–4 条）；`message_count` 和
   `max_msg_chars` 由管线从 `messages` 重算，不信任模型自报；下游不支持多条时由治理层
   合并成一条，并在审计里记录 `merged_by_governance=true`。
2. `config` 增加 `rhythm` 段：`target_msg_chars_p50`、`p95`、`max_messages_per_reply`、
   `split_allowed_scenes`（`work`/`boundary` 等正式场景不拆）。
3. `prompts/rewrite.md` 增加节奏规则：日常闲聊优先拆 2–3 条短消息，每条独立可读、
   承接上一条；**不要为拆而拆**，正式场景保持单条。

**评估侧同步加**：
- 「消息长度分布」：P50/P95、超 35 字占比（零人工成本，自动算）；
- 「分条合理性」：抽 20 条人工 1–3 分（1=不该拆却拆了，3=拆得自然）；
- 评估集每条加 `rhythm_profile`：`short_burst / single_msg / long_explain`，
  用来区分"该拆没拆"和"不该拆却拆了"。

**风险**：拆条会放大语气词和碎片化，正式场景容易显得轻浮；所以 `split_allowed_scenes`
是硬开关，且评估必须按 `rhythm_profile` 分层报告，不能只看总体均值。
