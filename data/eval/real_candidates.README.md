# 真实候选池（Day 7–8）

目标：**18 条**真实脱敏样本，供 Day 8 正式 60 条集使用（≥30%）。

节奏：每天 20 分钟，手动脱敏 3–5 条，追加到 `real_candidates.jsonl`。不要等 Day 8 当天找。

## 每条格式

```json
{"id": "real_001", "scene": "casual", "turns": [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}], "raw_reply": "...", "gold": null, "rhythm_profile": "short_burst", "source": "real", "anonymized": true, "notes": "脱敏：人名→A，地点→某商场，去掉时间"}
```

必填：`id`、`scene`、`turns`（2–4 轮，保留最后一轮用户消息）、`raw_reply`、`rhythm_profile`、
`source=real`、`anonymized=true`、`notes`（写清楚改了什么）。

`gold` 可以先为 `null`；正式标注在 Day 8a 统一做，不要提前猜标签。

## 脱敏清单

- 人名 → A/B/同事/朋友；公司名 → 某公司；地点 → 某地/某商场。
- 手机号、微信号、邮箱、链接、身份证、订单号全部删除或替换。
- 具体日期可保留“周六下午”这类相对时间；精确到日/时刻的改为相对或删除。
- 群聊昵称、头像、聊天背景不进入数据。
- 不粘贴原始聊天截图；只手打脱敏后的文本。
- 不确定是否可脱敏的，直接不用。

## 场景配额建议

| scene | 目标 |
|---|---:|
| casual | 4 |
| comfort | 4 |
| banter | 3 |
| boundary | 3 |
| work | 4 |
| **合计** | **18** |

不需要严格按配额，但 5 个场景都要有。

## 校验

```powershell
python scripts/real_candidates_check.py
```

脚本只做结构和明显 PII 检查（手机号/邮箱/URL），不是脱敏保证；`notes` 必须写清改了什么。
