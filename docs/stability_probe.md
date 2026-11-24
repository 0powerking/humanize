# 稳定性探针结果（Day 4）

数据：`data/probe/stability_probe.jsonl`（32 条 = 30 base + 2 supplemental）
脚本：`scripts/probe_stability.py`（直接 `DeepSeekClient`，**不挂 `CachedClient`**）
运行：2026-11-24，32 条 × 3 次 = 96 次信号运行 / 288 次 provider 调用

## 缓存与成本

| 项 | 值 |
|---|---|
| `cache_hits` | **0**（每次运行断言，非零则脚本退出） |
| provider calls | 288 |
| 成本 | 0.1552 元 |
| requested / reported model | `deepseek-chat` / `deepseek-flash` |
| seed / temperature | 7 / 0.0 |

三次采样全部是真实调用；如果后两次命中缓存，3/3 一致率会变成假阳性，脚本会直接失败。

## 逐字段 3/3 一致率（base 30）

| 字段 | 3/3 | ≥2/3 | Wilson 95% CI（3/3） |
|---|---|---|---|
| `sarcasm.label` | 29/30（96.7%） | 30/30 | 83.3–99.4% |
| `emotion.label` | **28/30（93.3%）** | 30/30 | 78.7–98.2% |
| `tone` | 29/30（96.7%） | 30/30 | 83.3–99.4% |
| `ai_smell.types` | 29/30（96.7%） | 30/30 | 83.3–99.4% |

**机械判定：`emotion.label` 低于 95% → 方案 A（连续值分桶进指纹）+ 信号层多数投票。**
但 28/30 与 29/30 只差一条，CI 很宽（78.7–98.2% 覆盖 95%），不能据此断言总体不稳；
Day 8 必须用 60 条评估集复核。2/3 多数在所有字段上都是 30/30，说明多数投票能兜住这些抖动。

## 失败 case（base，5 条字段级）

| case | scene | 字段 | 三次值 |
|---|---|---|---|
| probe_010 | comfort | `sarcasm.label` | literal, literal, **uncertain** |
| probe_003 | casual | `emotion.label` | 期待, **平静**, 期待 |
| probe_028 | work | `emotion.label` | 不满, **平静**, 平静 |
| probe_004 | casual | `tone` | soothe, **matter_of_fact**, matter_of_fact |
| probe_014 | banter | `ai_smell.types` | [service_tone], [golden_quote], [golden_quote] |

没有出现“三个值全不同”的情况；全部是 2/3。

## comfort 子组（base：probe_008 weak / probe_011 strong）

| 字段 | 3/3 |
|---|---|
| sarcasm.label | 2/2 |
| emotion.label | 2/2 |
| tone | 2/2 |
| ai_smell.types | 2/2 |

comfort 的两条反讽本身是稳的。但 sample 只有 2 条，不能代表整个 comfort 场景；
结论只用于说明“这两条没有拖后腿”。

## supplemental（观察，不进 95% 判定）

| case | 设计意图 | 三次结果 |
|---|---|---|
| `probe_031` | comfort / 生气 / weak 反讽 | `sarcastic` 3/3，`emotion=不满` 3/3，`tone=soothe` 3/3 |
| `probe_032` | casual / 期待 / literal | `literal` 3/3，`emotion=期待` 3/3，`tone=banter` 3/3 |

两个观察：

1. `probe_031` 的“行，你忙”被稳定判为弱反讽 + `不满`，不是 manifest 里的“生气”。
   这是**标签边界**问题（生气 vs 不满），不是稳定性问题；`tone=soothe` 方向是对的。
   Day 4 调 emotion prompt 时要决定：是否把“冷处理式生气”单列，还是并入不满。
2. `probe_032` 的“期待”稳定，但 `tone=banter`。期待 + 兴奋是否该用 `banter`，
   还是应该有一个更中性的 `matter_of_fact`/`soothe`，值得在重写层定稿前确认。

## 结论与下一步

- 缓存未污染：`cache_hits=0`，288 次真实调用，成本 0.1552 元。
- 按 ADR-008 机械判定：**方案 A + 信号层多数投票**；不是方案 B。
- 30 条样本的 CI 很宽，Day 8 用 60 条正式确认；如果届时 `emotion.label` ≥95%，
  可以回退到方案 B（少一次投票、成本更低）。
- 实施顺序（待确认）：
  1. `fingerprint_view`：label + tone + `ai_smell` 类型集合 + persona 档位 +
     valence/arousal 5 桶 + confidence 3 桶；
  2. 信号层 3 次多数投票：label/type 取多数，连续值取中位数；
  3. 运行时默认 1 次、评估显式 3 次，避免把 3 倍延迟带进线上。
