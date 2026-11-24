# humanize

一个可插拔的 **AI 回复拟人化重写层**：输入「模型原始回复 + 对话上下文 + 人设状态」，
输出「重写正文 + 决策记录 + 审计日志」。

它不是聊天机器人，也不生成回复 —— 它只负责把模型默认的"AI 味"压回真人即时聊天的分布，
并且在拿不准的时候**允许不改**。

> 当前状态：**第 1 阶段 / Day 2 上半段**。五段管线已打通；`DeepSeekClient` 与 4 个 v0 prompt
> 已通过单条真实 smoke。默认仍走 `--provider mock`（离线、零成本），真实调用用
> `--provider deepseek`。仓库里只有 4 条 `source=synthetic` 的 smoke 样例，
> **还没有评估集，也没有评估数字**；磁盘缓存与成本记账在 Day 2 下半段。

---

## 快速开始（Windows / Python 3.10）

```powershell
cd D:\dsh\humanize
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

如果 `pip install` 卡在构建隔离阶段，**可能是网络问题**（下载 setuptools/依赖超时），
可尝试关闭构建隔离：

```powershell
pip install -e ".[dev]" --no-build-isolation
```

配置真实模型（`.env` 已被 gitignore）：

```powershell
Copy-Item .env.example .env
notepad .env          # 填 DEEPSEEK_API_KEY=sk-...
```

跑一遍离线管线（默认 mock，不花钱）：

```powershell
python -m humanize run
```

跑真实模型（单条 smoke）：

```powershell
python -m humanize run --provider deepseek --sample e002_sarcasm_late
```

预期输出（每条样本一行摘要，并写入 `data/runs/<日期>/`）：

```
[e001_overwork_advice] scene=casual ai_smell=1.00 gate=rewrite ratio=0.80 risk=1 residue=0 sensitive=pass
    raw: 其实，换个工作并不是一件坏事。首先，你需要评估自己的核心诉求；其次，观察市场机会……
    out: 换个工作也不算啥坏事。
    ops: 删掉列举句式; 删掉列举句式; 删掉列举句式
    -> data\runs\2026-11-23\e001_overwork_advice__c2ae8cd8.json
[e004_natural_noop] scene=casual ai_smell=0.00 gate=noop ratio=0.00 risk=0 residue=0 sensitive=pass
```

其他命令：

```powershell
python -m humanize validate           # 校验 config / persona / lexicon / prompt 版本 / 输入
python -m humanize schema signals     # 打印某一层的 JSON Schema
python -m humanize run --sample e002_sarcasm_late --dry-run
python -m humanize run --provider deepseek --sample e002_sarcasm_late
```

> 当前 key 对应的网关会把请求的 `deepseek-chat` 实际路由到 `deepseek-flash`。
> 记录里 `signals.meta.model` 是 provider 实回模型，`versions.models` 是请求模型，
> 两者都保留，避免对账时混淆。

跑测试：

```powershell
python -m pytest        # 48 passed
python -m ruff check src tests
```

> 中文在终端显示为乱码时：先 `chcp 65001`，再 `$env:PYTHONUTF8="1"`。

---

## 五段数据流

```
RunInput
   │
   ├─[1] 信号层 ──┬─ signal.sarcasm  ─┐
   │              ├─ signal.emotion  ─┼─> SignalBundle (frozen)
   │              └─ signal.ai_smell  ─┘        │
   │              persona_state ← config/persona.yaml
   │                                            │
   ├─[2] 重写层 <───────────────────────────────┘
   │        raw_reply + signals + persona  ->  text + change_notes + kept_facts
   │
   ├─[3] 治理层   residue 规则扫描 + 敏感词桩 + AuditLog
   │
   ├─[4] 热梗层   未实现（第 2 周，静态候选池）
   │
   └─[5] 评估层   RunRecord 落盘 -> 后续批量指标 / A-B 盲评
```

模块之间只传 pydantic 模型，不传自然语言。信号模型 `frozen=True`，重写层在类型层面
就无法回写信号（ADR-002）。

## 目录

```
config/
  pipeline.yaml            # 模型路由 / 阈值 / prompt 版本 / 成本
  persona.yaml             # 三套人设：损友（默认）、温和倾听者、克制同事
  ai_smell_patterns.yaml   # AI 味规则词典 v1（mock、prompt、治理层共用同一份）
prompts/                   # 真实 prompt 目录（Day 3 起填充，当前只有 README 约定）
src/humanize/
  schemas.py               # 全部 JSON 契约
  pipeline.py              # 五段编排 + 降级策略
  llm.py                   # LLMClient 协议 + MockClient + JSON 解析
  lexicon.py               # 规则词典加载与扫描
  cache.py                 # 缓存键 / 信号指纹（Day 2 接上磁盘缓存）
  config.py  io.py  cli.py
data/eval/seed_examples.jsonl   # 4 条 synthetic smoke 样例
data/runs/                      # 每次运行的完整记录（gitignore）
tests/                          # 35 条测试
docs/decisions.md               # 工程决策记录（ADR）
docs/phase1_plan.md             # 第一阶段计划与进度
```

## 一次运行记录里有什么

`data/runs/<日期>/<sample_id>__<run_id8>.json` 是一条自包含的记录：

| 字段 | 内容 |
|---|---|
| `input` | 原始回复、上下文、人设引用、选项 |
| `signals` | 反讽三态 + 置信度 + 证据、情绪与语气目标、AI 味命中列表、人设状态 |
| `rewrite` | 重写正文、改动说明（每条带 `signal_ref`）、`kept_facts`、`risk_flags`、`noop` |
| `govern` | 残留命中、敏感分类、审计决策列表 |
| `usage` / `timing` | 调用次数、token、成本、各段耗时 |
| `versions` | git commit、config hash、prompt 版本、模型名 |
| `meta` | provider、`signal_fingerprint`、lexicon 版本、降级模块 |

`signal_fingerprint` 是 Day 2 缓存键的一部分：原始回复没变但信号变了（换 prompt / 换模型 /
改词典），重写缓存必须失效（ADR-004）。

## 当前限制（诚实版）

1. **重写质量不代表最终效果**：`MockClient` 的改写是"删句子 + 词典替换"，粗暴且会过度删除
   （e003 的 `rewrite_ratio` 达到 0.91，已触发 `rewrite_ratio_over_limit`）。它只证明管线形状。
2. **反讽/情绪是关键词规则**，不是模型判断；e001 里"换个工作"没被识别成焦虑就是例证。
3. **没有评估集**，也没有任何指标数字；4 条样例只用于 smoke test，不能当结论。
4. **治理层只检测不重写**：命中残留只记录 + 写明"deferred (phase 1.5)"。
5. 本机未安装 git，仓库尚未 `git init`；`.gitignore` 已就绪，装好后一条命令即可。

## Roadmap

| Day | 内容 | 状态 |
|---|---|---|
| 1 | 骨架 / 契约 / CLI / mock 端到端 / 缓存指纹 | ✅ |
| 2 | DeepSeek 客户端、重试、磁盘缓存、成本记账 | 待做 |
| 3 | AI 味诊断（规则 + LLM 融合） | 待做 |
| 4 | 反讽 + 情绪 + 并行聚合 + 降级 | 待做 |
| 5 | 人设状态 + 重写层 v1 + gate | 待做 |
| 6 | 治理层最小版（残留 + 审计） | 待做 |
| 7–9 | 评估集 60 条 / 批量指标 / 盲评 | 待做 |
| 10 | 复盘、ADR、README、v0.1 | 待做 |

设计取舍见 [docs/decisions.md](docs/decisions.md)，接口契约见
`python -m humanize schema <stage>`。
