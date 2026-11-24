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
- `config/pipeline.yaml` 里 `cache.include_signal_hash` 默认且应保持 `true`。

**验收测试**（Day 1 已写，Day 2 接磁盘缓存后继续用）：
- `test_prompt_version_bump_invalidates_key`
- `test_signal_change_invalidates_key`
- `test_signal_fingerprint_ignores_latency`
- `test_signal_fingerprint_tracks_signal_content`
- `test_config_hash_changes_when_prompt_version_bumps`

**代价**：缓存命中率比"只按原文"低；换来的是不会拿到与证据不匹配的重写。
**如果错了会怎么发现**：改一版 signal prompt 后重跑，缓存命中数不降反升 → 键漏了维度。

---

## ADR-005 改写幅度闸门，并重算 ratio

**背景**：不是所有回复都值得改。原文已经自然时，强行重写只会引入语义漂移。

**决策**：
- `ai_smell.score < 0.25` 时允许 `noop`（原文直出，`rewrite_ratio=0`）；
- 重写层必须输出 `kept_facts`，治理层据此核对事实是否被改掉；
- **ratio 由管线用 `SequenceMatcher` 重算**，不信任模型自报；偏差 > 0.2 记
  `ratio_self_report_mismatch`，超过 `max_rewrite_ratio` 记 `rewrite_ratio_over_limit`
  （只记录，不截断文本——截断会制造病句）。

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
