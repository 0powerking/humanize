# 稳定性探针结果（Day 4）

数据：`data/probe/stability_probe.jsonl`（32 条 = 30 base + 2 supplemental）
脚本：`scripts/probe_stability.py`（直接 `DeepSeekClient`，**不挂 `CachedClient`**，硬断言 `cache_hits=0`）

两次运行：

| 运行 | 时间 | cache_hits | calls | 成本 | 说明 |
|---|---|---|---|---|---|
| run 1 | 12:20 | 0 | 288 | 0.1552 元 | Day 4 初次探针 |
| run 2 | 12:28 | 0 | 288 | 0.1215 元 | 方案 A 实现后的回归 |

两次都是 32 条 × 3 次 = 96 次信号运行；实回模型 `deepseek-flash`，seed 7 / temp 0。

## 逐字段 3/3 一致率（base 30）

| 字段 | run 1 | run 2 | run 1 Wilson 95% |
|---|---|---|---|
| `sarcasm.label` | 29/30（96.7%） | 30/30（100%） | 83.3–99.4% |
| `emotion.label` | **28/30（93.3%）** | **29/30（96.7%）** | 78.7–98.2% |
| `tone` | 29/30（96.7%） | 30/30（100%） | 83.3–99.4% |
| `ai_smell.types` | 29/30（96.7%） | 29/30（96.7%） | 83.3–99.4% |

四个字段的 **≥2/3 多数在两轮里都是 30/30**。

## 判定：为什么选方案 A，而不是“93.3% < 95% 所以切 A”

- 30 条样本的 CI 很宽：28/30 的 Wilson 95% 是 **78.7–98.2%**，29/30 是 83.3–99.4%，
  两者都覆盖 95%。**探针信号不足以区分方案 A 与方案 B**。
- 两轮结果本身就在翻转：run 1 的 `emotion.label` 93.3%，run 2 是 96.7%；
  run 2 的机械推荐甚至是 `plan_b`。如果用单轮结果做决策，结论会随抽样运气变化。
- 因此按**风险控制**选保守方案 A：分桶指纹对连续值抖动不敏感，代价只是失去“连续值完全一致”
  的严格性；多数投票的成本在评估侧约 ×2（3.3 → 6.6 元），可控。
- **回退路径保留**：Day 8 用 60 条评估集确认；若 `emotion.label` ≥95% 且稳定，
  回退方案 B（少一次投票、成本更低）。

## 失败 case（run 1，5 条字段级）

| case | scene | 字段 | 三次值 | 判定 |
|---|---|---|---|---|
| probe_010 | comfort | `sarcasm.label` | literal, literal, **uncertain** | 三态合法输出，可接受 |
| probe_003 | casual | `emotion.label` | 期待, **平静**, 期待 | 正是要测的“期待/中性”边界，Day 5 看 tone 是否够共情 |
| probe_028 | work | `emotion.label` | 不满, **平静**, 平静 | **系统性风险，见下** |
| probe_004 | casual | `tone` | soothe, **matter_of_fact**, matter_of_fact | **系统性风险，见下** |
| probe_014 | banter | `ai_smell.types` | [service_tone], [golden_quote], [golden_quote] | 类型漂移；整体 96.7%，影响指纹但不改判定 |

run 2 只剩 probe_003（平静/期待/平静）和 probe_014（golden_quote 两次 + service_tone 一次），
再次说明单轮失败列表是抽样的，不是稳定缺陷清单。

### 系统性风险：情绪/语气滑向中性（probe_028 + probe_004）

probe_028 与 probe_004 不是同一条样本，但指向同一个模式：

- probe_028：上下文是工作场景的阴阳（“您这需求改得真及时”），两次被判成 `平静`；
- probe_004：上下文是明确不满（“你可真行，说好的下午三点呢”），两次 tone 被判成
  `matter_of_fact`。

也就是**情绪强度被系统性低估**，不是随机噪声。Day 5 重写层定稿时的已知风险：
emotion 判成“平静”但上下文明显有情绪时，重写层不能完全依赖信号，要有“强度兜底”
（例如扫描上下文里的抱怨/反问/承诺对比标记，强制至少 `soothe` 或 `banter`）。

## comfort 子组（base：probe_008 weak / probe_011 strong）

两轮都是四项 2/2。comfort 的两条反讽本身稳；但只有 2 条，不能代表整个 comfort 场景。

## supplemental（观察，不进 95% 判定）

| case | 设计意图 | 两轮结果 |
|---|---|---|
| `probe_031` | comfort / 生气 / weak 反讽 | `sarcastic` 3/3，`emotion=不满` 3/3，`tone=soothe` 3/3 |
| `probe_032` | casual / 期待 / literal | `literal` 3/3，`emotion=期待` 3/3，`tone=banter` 3/3 |

两个补充发现：

1. **“生气 → 不满”是标签边界，不是抖动。** `probe_031` 的“行，你忙”稳定判成弱反讽 + 不满，
   没有进入“生气”。Day 5 规则：label 是“不满”但 intensity 高时，语气目标应偏向降火/安抚，
   而不是就事论事。
2. **“期待 → banter” 需要 Day 5 确认。** `probe_032` 的期待 + 兴奋稳定识别，但 tone 用
   `banter` 是否足够共情，还是应该更中性/更贴近对方的兴奋，放进 Day 5 任务清单。

## 方案 A 实现与缓存收益

- `fingerprint_view`：label + target + confidence 3 桶 + emotion label/tone + valence/arousal 5 桶 +
  `ai_smell` 类型集合 + persona 档位；`rewrite_view` 仍传精确值给重写层。
- 缓存键不再包含 `signals` 明文字段，只包含分桶指纹；否则精确值会让分桶失去意义。
- 单元测试证据：同一 valence 桶内抖动 `-0.65 / -0.61 / -0.63`，旧精确键是 3 次 miss，
  新分桶键是 1 次调用 + 2 次命中；跨桶（-0.59 vs -0.61）仍然 miss，label/type 变化也 miss。

## 运行时投票口径

- `signals.vote_runs` 配置化，**运行时默认 1**（控制延迟与成本）。
- 评估显式 `--vote-runs 3`：label/type 取多数，连续值取中位数；重写层仍单次。
- 风险写进 ADR-008：运行时单次采样，信号层抖动由重写层鲁棒性兜底；Day 8 如果发现抖动
  导致重写质量显著下降，再考虑线上开 3 次。第一版不做生产优化，但风险不藏起来。

## Day 5 任务清单（由本轮产生）

1. 情绪强度兜底：label=平静但上下文有情绪标记时，重写层不能只信信号。
2. `probe_031`：label=不满 + intensity 高 → 语气目标偏向降火/安抚。
3. `probe_032`：确认期待场景的 tone 是否应为 banter；不够共情就调整。
4. 60 条评估集确认 `emotion.label` 稳定性；≥95% 则回退方案 B。
