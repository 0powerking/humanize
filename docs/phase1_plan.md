# 第一阶段计划与进度

目标：跑通离线管线骨架 + 信号层 + 重写层 + 小评估集。
范围：不接 IM、不做热梗爬虫、不做 Web UI、不做多轮状态持久化。

## 交付物验收标准（DoD）

一条命令从零跑出报告：

```powershell
python -m humanize run --input data/eval/eval_set.jsonl
python -m humanize eval --set data/eval/eval_set.jsonl   # Day 8 提供
```

报告含四个指标 + 置信区间 + 成本/延迟 + 失败案例，且每条样本的决策链可回溯。

## 排期

| Day | 任务 | 验收 | 状态 |
|---|---|---|---|
| 1 | 仓库骨架、契约、config、JSONL IO、CLI、Mock 端到端、缓存键 | `pytest` 绿；产出完整 run record | ✅ |
| 2 | DeepSeek 客户端、重试/超时、磁盘缓存、成本记账 | 第二次运行缓存命中，成本 0；改 prompt 版本后不命中 | ✅ 全部完成（含 seed 与方差实测） |
| 3 | AI 味诊断 v1（规则 + LLM 融合） | 20 条正负 fixture 通过 | ✅ 规则 + 融合；gap 3/3、clean 0 FP、规则 0 丢失 |
| 4 | 反讽 + 情绪 + 并行聚合 + 单模块失败降级 | 注入失败仍出结果，`degraded` 有记录 | ✅ 探针两轮；方案 A + `vote_runs` 已实现 |
| 5 | 人设状态 + 重写层 v1 + gate + `change_notes` | 10 条人工目测，说明与信号对得上 | ✅ Step 1–4 全部完成 |
| 6 | 治理层：残留规则 + 审计 + 敏感词桩 | 构造"改坏了"的样本能被检出 | ✅ 敏感桩 + 评估聚合（压缩完成） |
| 7 | 评估集 pilot 10 条 + 标注规范 | `eval_protocol.md` 定稿 | ✅ pilot + 规范 v1；标注执行/扩 60 待做 |
| 8a | 建 60 条正式集（≥18 真实）+ 标注执行 | 有效样本 + N/M 记录 | 待做（Day 8 第一段） |
| 8b | 批量跑 + 自动指标 + `report.md` | 四指标 + 双口径保留率 + commitment runs/items | 待做（Day 8 第二段） |
| 9 | 盲评表 + 人工评分 + 统计 | 四指标 + CI | 待做 |
| 10 | 复盘、ADR、README、v0.1 | 别人 clone 后可复现 | 待做 |

## 评估设计（Day 7 落地）

评估集 60 条起：日常闲聊 15 / 情绪安慰 12 / 反讽阴阳 15 / 边界拒绝 8 / 工作正式 10。
每条带 `gold.user_sarcasm`（只标"用户最后一条是否反讽"）、`gold.user_emotion`、
`persona_id`、`source(real|synthetic)`。

| 指标 | 口径 |
|---|---|
| 套路化表达频率 | 命中 ≥1 个套路的回复数 / 总回复数；另报每千字密度 |
| 反讽误判率 | 只统计 gold 与预测都属确定类的样本；另报 P/R/F1 与 uncertain 覆盖率 |
| 人设一致性 | 人工 1–5，rubric 见 eval_protocol；报均值 + bootstrap 95% CI |
| 整体像人偏好 | 基线 vs 管线，随机左右、隐藏来源、允许平局；报 win/tie/loss + Wilson CI |

样本量 60 只够看方向：报告写"pilot，效应量 + CI"，不写单点提升百分比。
报告头部固定记录 `requested_model` 与 `reported_model`：本 key 实测请求 `deepseek-chat`
被网关路由到 `deepseek-flash`，Day 8 报告必须写实际模型，不能写成 deepseek-chat。

## Day 1 实际交付

- `src/humanize/schemas.py`：RunInput / SignalBundle / RewriteOutput / GovernOutput / RunRecord
- `src/humanize/pipeline.py`：三段串接、并行信号、降级、ratio 重算、审计
- `src/humanize/llm.py`：`LLMClient` 协议、`MockClient`、fenced-JSON 解析
- `src/humanize/cache.py`：信号指纹 + 缓存键（Day 2 直接接磁盘）
- `src/humanize/lexicon.py` + `config/ai_smell_patterns.yaml`：14 条规则 v1
- `config/pipeline.yaml`、`config/persona.yaml`（3 套人设）
- `src/humanize/cli.py`：run / validate / schema
- `data/eval/seed_examples.jsonl`：4 条 synthetic smoke 样例
- `tests/`：167 条测试，覆盖契约、缓存键分桶/字段覆盖、端到端重调、降级、ratio 审计、成本、seed、融合、投票、多消息、节奏兜底、省略检查、kept_facts 锚点、敏感桩、评估聚合、pilot 结构

## Day 2 下半段进度

- 磁盘缓存 ✅：`CacheStore` + `CachedClient`，键含 `stage/model/prompt_version/temperature/
  seed/payload/signal_fingerprint`；冷跑 4 calls → 热跑 `cache=4`、`calls=0`、token=0、cost=0。
- **端到端验收 ✅**：`test_signal_content_change_invalidates_rewrite_cache`——同一输入下把
  `sarcasm.label` 从 literal 翻成 sarcastic（并 bump signal prompt 版本，模拟真实改动穿过
  信号缓存），断言重写调用次数 1→2，第三次全命中。
- provenance 已从重写 payload 中移除（`rewrite_view`），命中来源写入 `meta.cache_source_models`。
- 成本 ✅：峰谷价 + provider prompt cache 命中价；`BudgetTracker` 80% 告警 / 100% 停止；
  单条冷跑实测 0.0095 元，3 次采样 0.0276 元。
- seed ✅：`signal.seed=7`、`rewrite.seed=42`，进入请求与缓存键。
- 方差实测（ADR-008）：temp 0 两次一致；temp 0.9 同 seed 三次文本不同；temp 0 三次管线中
  sarcasm 标签稳定，但 `emotion.valence` 在 −0.6/−0.7 抖动。结论：seed 不承诺复现，
  评估按 3 次采样取均值/中位数。
- 结果：Day 2 完成。

## Day 3 上半段：规则词典 fixture

- `tests/fixtures/ai_smell_cases.jsonl`：20 条（10 条规则可命中 / 7 条 clean / 3 条已知语义缺口）。
- `tests/test_ai_smell_lexicon.py`：逐条断言期望类型、clean 零误报、gap 仍漏（作为 LLM 兜底目标）。
- `scripts/lexicon_report.py`：零成本覆盖率报告，输出：
  - 规则召回 10/10（100%）
  - 语义召回 10/13（76.9%），LLM 兜底需覆盖 23.1%
  - clean 误报 0/7
  - 7 个类型逐项 100% 命中
- 结论：规则层能稳定覆盖有标记的套路；剩下 3 条“正确废话/空泛安慰/鸡汤排比”没有词面标记，
  必须靠 LLM 语义判断。Day 3 下半段只补这一类，不重写规则层。

## Day 3 下半段：规则 + LLM 融合

- prompt 升 `sig.ai_smell.v2`：输入 `rule_hits`，只输出 `extra_hits`；不重复规则命中。
- `src/humanize/ai_smell.py`：同 span 规则优先、LLM 类型记 `alt_type` 进 notes；重叠记
  `overlap_skipped`；非法 span 丢弃；score 由合并结果重算；LLM 失败回退纯规则并记
  `ai_smell_llm`。
- `scripts/ai_smell_fusion_check.py` 真实模型验收（20 条，deepseek-flash）：
  - gap 命中 **3/3**（软目标 ≥2）
  - clean 新增误报 **0/7**（硬约束）
  - 规则命中丢失 **0/10**（硬约束）
  - 成本 0.0452 元
- 额外修正：`rewrite_view` 剔除 `evidence/reason/cues/notes` 等诊断性自由文本，
  避免模型措辞漂移污染信号指纹；完整内容仍在 `RunRecord.signals`。
- 全管线真实跑通：e002 冷跑 ai_smell 0.875（规则 5 条 + LLM 补 1 条语义套路、1 条重叠跳过），
  热跑 4 次缓存命中、成本 0。

## Day 4 探针数据（待跑）

- `data/probe/stability_probe.jsonl`：32 条 = 30 条 base（5 场景各 6 条）+ 2 条 supplemental
  （`probe_031` 生气 / `probe_032` 期待）；`source=synthetic`，`probe_` 前缀，单独目录。
- `manifest.json`：新增 `cohort`、`sarcasm_strength`；95% 判断只用 base，补充样本只观察。
- `scripts/probe_report.py` 校验（零成本）：

```
scene       base_sc  base_lit  supp  total
banter            3         3     0      6
boundary          3         3     0      6
casual            3         3     1      7
comfort           2         4     1      7
work              3         3     0      6
total            14        16     2     32
```

- 情绪覆盖 10/10：平静 8、不满 13、失落 2、委屈 1、焦虑 1、疲惫 1、开心 3、尴尬 1、
  生气 1、期待 1。
- comfort 反讽子组单列：`probe_008`（weak，被动攻击）与 `probe_011`（strong，反话）；
  探针跑批时单独算这两条的 3/3 一致率，不并入其它场景。
- 待跑：32 × 3 次信号采样；`sarcasm.label`、`emotion.label`、`tone`、`ai_smell` 类型集合
  逐字段一致率；base ≥95% 临时切方案 B，<95% 走方案 A 分桶（ADR-008）。

## Day 4 探针结果（已跑）

- 两轮各 32 条 × 3 次 = 96 次信号运行 / 288 次 provider 调用；**`cache_hits=0`**（硬断言），
  成本 0.1552 / 0.1215 元，实回 `deepseek-flash`。
- base 30 的 3/3 一致率：

| 字段 | run 1 | run 2 |
|---|---|---|
| sarcasm.label | 29/30（96.7%） | 30/30 |
| emotion.label | **28/30（93.3%）** | **29/30（96.7%）** |
| tone | 29/30（96.7%） | 30/30 |
| ai_smell.types | 29/30（96.7%） | 29/30（96.7%） |

- 两轮 2/3 多数均为 30/30。run 1 有 5 条字段级失败，run 2 只剩 probe_003、probe_014。
- **判定不再写成“93.3% < 95% 所以切 A”**：28/30 的 Wilson 95% CI 为 78.7–98.2%、29/30 为
  83.3–99.4%，都覆盖 95%；run 2 的机械推荐甚至变成 B。探针不足以区分 A/B，按风险控制选保守 A，
  Day 8 用 60 条确认，≥95% 回退 B。
- 方案 A 已实现：`fingerprint_view` 分桶（valence/arousal 5 档、confidence 3 档、label/tone/
  type/persona 档位），`rewrite_view` 仍传精确值，重写缓存键不再含 `signals` 明文。
- 缓存收益单测：`-0.65 / -0.61 / -0.63` 三次抖动从 3 次 miss 变成 1 call + 2 hit；
  跨桶 / 换 label / 换 type 仍 miss。
- 运行时口径：`signals.vote_runs` 默认 1，评估用 `--vote-runs 3`（label/type 多数、连续值中位数）。
- Day 5 风险：probe_028（情绪）与 probe_004（语气）指向“情绪强度被低估”，重写层要有兜底；
  probe_031 的“生气→不满”是标签边界；probe_032 的期待场景 tone 待确认。
- 详细报告：`docs/stability_probe.md`。

## Day 5 Step 1：messages schema（已完成）

- `RewriteOutput.messages` 1–4 条；`text` 变成 `"\n".join(messages)` 只读属性，不序列化。
- `message_count` / `max_msg_chars` 由 schema validator 与管线双重重算；输入字段不加约束，
  模型自报错误值也不会校验失败，只会被覆盖。
- 旧 prompt 仍返回 `{"text": ...}`，`model_validator(mode="before")` 自动包成 messages；
  Step 2 改 prompt 后保留一个版本再删兼容层。
- 治理层用 `\n` 合并多条为 `final_text`，审计记 `messages: N message(s) merged ...`。
- `SCHEMA_VERSION` 升到 `1.1`。
- 测试：131 passed；mock e001 回归显示 `messages` 1 条、`message_count=1`、
  `max_msg_chars=11`、`final_text` 与消息一致。
- 未动 prompt / rhythm；Step 2 加 `rhythm` 配置与 prompt 节奏规则。

## Day 5 Step 2：rhythm 配置 + prompt v3 + 6 条目测（已完成）

- `config/pipeline.yaml` 新增 `rhythm`：`target_msg_chars_p50=12`、`p95=35`、
  `max_messages_per_reply=4`、`split_allowed_scenes=[casual,banter,comfort]`。
- `prompts/rewrite.md` v3：日常闲聊 2–3 条、正式场景必须单条、单条目标 12 字/上限 35 字、
  超 35 优先精简不再拆；`allow_noop` 时默认保持原文。
- 6 条目测（真实 deepseek-flash，`--no-cache`）：

| 样本 | scene | 条数 | 字数 | 结果 |
|---|---|---|---|---|
| e001 | casual | 3 | 10/11/10 | 合理 |
| e002 | banter | 2 | 14/10 | 合理，接住反讽 |
| e003 | comfort | 3 | 11/10/14 | 合理，没拆碎 |
| e004 | casual | 1 | 12 | 强制 noop |
| probe_019 | boundary | 1 | 22 | 模型拆 2 条，被合并 |
| probe_025 | work | 1 | 13 | noop |

- prompt-only 没守住两条 hard rule，因此加管线兜底（ADR-011）：
  - 非拆分场景多条 → 合并 + `merged_outside_split_scene`；
  - gate 放行、`ai_smell=0`、实际 ratio ≤0.2 → 强制 noop + `forced_noop_trivial_edit`。
- 代价：e001/e003 的 ratio 0.86/0.83 触发 `rewrite_ratio_over_limit`，内容可接受但压缩偏激进，
  Step 4 回归时再看。
- 测试：138 passed + 1 skipped（Step 4 兼容层删除提醒）。

## Day 5 Step 3：语用省略 + unsafe_omission（已完成）

- prompt v5 加省略规则：日常场景可省主语/重复信息/指代；正式场景禁止；comfort 不省
  “我在 / 听着呢”这类支撑性表达；省略必须满足“上文出现过、指代无歧义、kept_facts 不省”。
- 治理层 `check_omissions`：检查 `kept_facts` 中的具体时间/日期/数字实体 + raw 中被删的
  具体 span；缺失记 `unsafe_omission`，部分记 `partial_omission`，只进 risk_flags，不阻断。
- 上下文窗口 `govern.omission_context_turns=4`。
- **第一版逐句骨架比对误报严重**（e001 7 条、probe_031 4 条、probe_008 2 条），把同义改写
  当成省略；已收窄为“具体实体”口径（ADR-012）。抽象名词/动词的省略留给人工评估。
- 4 条验证（真实 deepseek-flash，`--no-cache`）：

| 样本 | scene | 消息 | omission |
|---|---|---|---|
| e001 | casual | 3 条（18/13/11） | clean |
| probe_031 | comfort / weak | 2 条（7/14） | clean |
| probe_008 | comfort / weak | 2 条（13/12） | clean |
| probe_025 | work | 1 条（13，noop） | clean |

- 遗留：e001 的 `kept_facts` 仍写抽象概括（“评估核心诉求”），Step 4 先修 fact 粒度，
  再谈 ratio 阈值。
- 测试：148 passed + 1 skipped。

## Day 5 Step 4-1：kept_facts 具体锚点（已完成）

- `kept_facts` 每项必须通过具体锚点测试：时间/日期/数字/金额标记，或原文字面片段；
  抽象概括进新字段 `semantic_points`（最多 4 条），不参与保留率与省略检查。
- 管线 `_normalize_kept_facts` 兜底：模型仍混入抽象项时自动移到 `semantic_points`，
  记 `kept_facts_demoted`；schema 升到 `1.2`，prompt v6 已写明两者区别。
- e001 真实跑批（prompt v6）：

```
kept_facts: ['换个工作', '观察市场机会', '做好财务缓冲']
semantic_points: ['换工作不一定是坏事', '先想清楚自己要什么', ..., '评估核心诉求']
flags: kept_facts_demoted, rewrite_ratio_over_limit
```

- 已知风险：“观察市场机会/做好财务缓冲”有字面锚点但语义偏概括；锚点测试只能保证
  “原文有依据”，Step 4 保留率按原始条目报，不二次解释。
- 测试：150 passed + 1 skipped。

## Day 5 Step 4-2 / 4-3：删兼容层 + 回归（已完成）

- 删除 `{"text": ...}` 兼容层；旧形状现在 `ValidationError`，测试从 skip 改为 active。
- 回归：32 条探针 + 4 条 seed，真实 deepseek-flash，`--no-cache`。
  - 36/36 跑通，**0 异常、0 degraded、cache_hits=0**，成本 0.0824 元。
  - `kept_facts` 精确保留 26/36（72.2%），覆盖率口径 33/36（91.7%）；
    3 条 coverage miss：`改天再约→改天呗`（承诺弱化）、`尽快处理→尽快弄`、
    `没有时间→没空`（后两条同义替换）。
  - boundary/work 12 条全部 `message_count=1`，concrete dropped=0；compression
    boundary −0.3～0.2、work −0.69～0.12（负值为扩写，不是省略）。
  - `unsafe_omission` 真实触发 0 次；concrete span 观测 10 个、丢失 0 个，
    属于“模型很少丢具体实体”的分支，不是规则太松；检出能力由单元测试保证。
  - 2 条 `partial_omission`（e004/probe_001 的“周六下午→周六”只记录不阻断）。
- ratio 未调：e001/e003 仍触发 `rewrite_ratio_over_limit`，留给 Day 8 与保留率一起看。
- 测试：149 passed（无 skip）。

## Day 6：敏感桩 + 评估聚合（已完成）

- `src/humanize/sensitive.py`：手机号 / URL / 配置关键词桩，分类 `contact_info` / `keyword`，
  `block_on_hit` 可阻断；扫描合并后的 `final_text`。
- `src/humanize/evaluate.py` + `scripts/eval_summary.py`：从 `RunRecord` 聚合
  - 链路（runs / degraded / cache_hits / cost）；
  - 节奏（`rewrite.messages` 的条数、长度 P50/P95、>35 字比例、拆分率）；
  - 保留率（精确 / 覆盖率）、omission / commitment、治理命中、work 扩写；
  - 旧 schema 记录跳过，不静默混算。
- 口径：敏感读 `final_text`，长度/分条读 `rewrite.messages`；**不混**。
- `commitment_weakened` 独立标记；work `compression < -0.3` 单独统计。
- 评估聚合实测：`data/runs` 里 5 条新 schema 记录可汇总，45 条旧记录被跳过。
- 测试：160 passed。

## Day 7：pilot 10 条 + 标注规范（已完成）

- `data/eval/pilot_10.jsonl`：5 场景 × 2，反讽/非反讽 5/5，全部 synthetic；
  rhythm_profile 为 5 single_msg / 4 short_burst / 1 long_explain。
- `docs/annotation_guide_v1.md`：字段定义、评分 rubric、标注流程、计时、歧义记录、
  双人 κ 与单标注者规则。
- `data/eval/annotations/pilot_annotations_template.csv`：10 行，字段含
  user_sarcasm 三态 + evidence、emotion label/intensity、persona_consistency 1–5、
  rhythm_profile、omission_naturalness 1–3、ab_preference；`is_overlap=1` 5 条。
- **pilot 唯一产出**：规范 v1 定稿、标注耗时估计、歧义字段清单；**不产出指标**。
- 双人：A 全 10 条，B 5 条重叠算 κ；单人时注明单标注者，Day 8 不写双人 κ。
- Day 8 正式集至少 30% 真实脱敏样本；报告写“有效 N 条（schema 1.2）+ 跳过 M 条旧 schema”。
- `commitment_weakened` 改为逐条承诺：risk_flags 每条一项 `<fact>:<coverage>`，
  统计 `runs` 与 `items` 两个数。
- `scripts/pilot_check.py`：结构/模板校验通过；测试 167 passed。

## Day 8 拆分与时间预算

原计划“Day 8 一天出报告”不现实：60 条标注 × 8 分钟 ≈ 8 小时，再加建集、跑评估、写报告。
**拆成两段，不压成一天**：

- **Day 8a：建集 + 标注**
  - 从 `data/eval/real_candidates.jsonl` 选 ≥18 条真实脱敏样本（`real_candidates_check.py` 校验），
    加 42 条 synthetic/pilot，凑 60 条；
  - 按 `docs/annotation_guide_v1.md` 执行标注；计时只取 core 段；
  - 有效样本 N、跳过旧 schema M 记录；单标注者/双人 κ 如实写。
- **Day 8b：跑评估 + 写报告**
  - 批量跑（`--no-cache`，3 次采样/标签，`--vote-runs 3`）；
  - `eval_summary.py` 出节奏/保留率/治理；人工指标汇总；
  - `report.md`：四指标 + CI + 双口径保留率 + `commitment_weakened_runs/items` + work 扩写。

真实候选每天 3–5 条、20 分钟，从 Day 7 就开始存；不要等 8a 当天找 18 条。

## 已知约束

- git 已装（2.56.1），首次提交 `b226712`，作者 `sssyz27 <3064705887@qq.com>`；未配 remote。
- `pip install -e .` 在构建隔离阶段可能卡住，用 `--no-build-isolation` 绕过。
- Mock 重写质量差是预期行为，Day 5 换真模型后重新评估；不要用 Day 1 的输出做任何结论。
