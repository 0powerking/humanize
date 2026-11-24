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
| 2 | DeepSeek 客户端、重试/超时、磁盘缓存、成本记账 | 第二次运行缓存命中，成本 0；改 prompt 版本后不命中 | 上半段 ✅、缓存/e2e ✅；成本与 seed 待做 |
| 3 | AI 味诊断 v1（规则 + LLM 融合） | 20 条正负 fixture 通过 | 待做 |
| 4 | 反讽 + 情绪 + 并行聚合 + 单模块失败降级 | 注入失败仍出结果，`degraded` 有记录 | 待做 |
| 5 | 人设状态 + 重写层 v1 + gate + `change_notes` | 10 条人工目测，说明与信号对得上 | 待做 |
| 6 | 治理层：残留规则 + 审计 + 敏感词桩 | 构造"改坏了"的样本能被检出 | 待做 |
| 7 | 评估集 pilot 10 条 + 标注规范 | `eval_protocol.md` 定稿 | 待做 |
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
- `tests/`：61 条测试，覆盖契约、缓存键失效与端到端重调、降级、ratio 审计、人设词过滤

## Day 2 下半段进度

- 磁盘缓存 ✅：`CacheStore` + `CachedClient`，键含 `stage/model/prompt_version/temperature/
  payload/signal_fingerprint`；冷跑 4 calls → 热跑 `cache=4`、`calls=0`、token=0、cost=0。
- **端到端验收 ✅**：`test_signal_content_change_invalidates_rewrite_cache`——同一输入下把
  `sarcasm.label` 从 literal 翻成 sarcastic（并 bump signal prompt 版本，模拟真实改动穿过
  信号缓存），断言重写调用次数 1→2，第三次全命中。
- provenance 已从重写 payload 中移除（`rewrite_view`），命中来源写入 `meta.cache_source_models`。
- 待做：成本换算 + 50 元预算告警；rewrite `seed` 固定；同输入 3 次采样方差测试。

## 已知约束

- git 已装（2.56.1），首次提交 `b226712`，作者 `sssyz27 <3064705887@qq.com>`；未配 remote。
- `pip install -e .` 在构建隔离阶段可能卡住，用 `--no-build-isolation` 绕过。
- Mock 重写质量差是预期行为，Day 5 换真模型后重新评估；不要用 Day 1 的输出做任何结论。
