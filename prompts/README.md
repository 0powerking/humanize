# prompts/

Day 2 起这里有正文：4 个 v0 prompt 已能支撑真实模型跑通单条 smoke。
Day 3–5 会在同一位置迭代内容（并 bump 版本），文件数不增加。

## 文件命名

| 文件 | prompt_id | 状态 | 后续 |
|---|---|---|---|
| `signal_sarcasm.md` | `signal.sarcasm` | v0 已上线 | Day 4 结合评估集调三态/置信度 |
| `signal_emotion.md` | `signal.emotion` | v0 已上线 | Day 4 调 tone 映射 |
| `signal_ai_smell.md` | `signal.ai_smell` | v2 已上线 | 规则锚点 + LLM 只补语义缺口（ADR-009） |
| `rewrite.md` | `rewrite` | v5 已上线 | 节奏 + 语用省略；Step 4 按回归调 kept_facts 粒度 |
| `rewrite_local.md` | `rewrite.local` | 未实现 | 阶段 1.5 |
| `judge_ab.md` | `judge.ab` | 未实现 | Day 9（可选，仅辅助） |

## 硬规则

1. **一个文件一个模块**，不拼超长 system prompt。
2. 每个 prompt 必须显式声明输出 JSON schema，且字段名与 `schemas.py` 一致。
3. 每个 prompt 顶部写 `version:`，并与 `config/pipeline.yaml` 的
   `prompt_versions` 同步 bump —— 否则缓存会错误命中（ADR-004）。
4. 模型输出不合法 JSON 时，管线降级到中性值并记入 `meta.degraded`，
   不在 prompt 里靠"请你一定要输出 JSON"反复哀求。
5. 中文提示词，但字段名、枚举值保持英文，便于代码解析和 grep。
