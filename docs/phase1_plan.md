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
| 4 | 反讽 + 情绪 + 并行聚合 + 单模块失败降级 | 注入失败仍出结果，`degraded` 有记录 | 探针数据 ✅；稳定性跑批待做 |
| 5 | 人设状态 + 重写层 v1 + gate + `change_notes` | 10 条人工目测，说明与信号对得上 | 待做 |
| 6 | 治理层：残留规则 + 审计 + 敏感词桩 | 构造"改坏了"的样本能被检出 | 待做 |
| 7 | 评估集 pilot 10 条 + 标注规范 | `eval_protocol.md` 定稿 | 协议已定稿（`docs/eval_protocol.md`），数据集待做 |
| 8 | 扩到 60 条 + 批量跑 + 自动指标 | `report.md` 生成 | 待做 |
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
- `tests/`：85 条测试，覆盖契约、缓存键失效与端到端重调、降级、ratio 审计、成本、seed、融合

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

## 已知约束

- git 已装（2.56.1），首次提交 `b226712`，作者 `sssyz27 <3064705887@qq.com>`；未配 remote。
- `pip install -e .` 在构建隔离阶段可能卡住，用 `--no-build-isolation` 绕过。
- Mock 重写质量差是预期行为，Day 5 换真模型后重新评估；不要用 Day 1 的输出做任何结论。
