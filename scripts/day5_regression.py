"""Day 5 Step 4-3 regression: 32 probe cases + 4 seed examples, no cache.

    python scripts/day5_regression.py

Reports only the three things this step is allowed to judge:
  1. chain health: no exceptions, no degraded samples, cache_hits == 0;
  2. kept_facts retention: retained / total concrete facts;
  3. boundary/work omission statistics (message count, compression, dropped
     concrete spans) for the prompt-compliance signal.

Ratio tuning is explicitly out of scope here.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from humanize.config import load_config, load_personas  # noqa: E402
from humanize.env import load_env  # noqa: E402
from humanize.io import load_run_inputs, now_iso, write_json  # noqa: E402
from humanize.lexicon import load_lexicon  # noqa: E402
from humanize.llm import DeepSeekClient  # noqa: E402
from humanize.omission import concrete_spans, fact_preserved  # noqa: E402
from humanize.pipeline import HumanizePipeline  # noqa: E402
from humanize.prompts import PromptStore  # noqa: E402

FORMAL_SCENES = {"work", "boundary"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(ROOT / "config/pipeline.yaml"))
    parser.add_argument("--personas", default=str(ROOT / "config/persona.yaml"))
    parser.add_argument("--lexicon", default=str(ROOT / "config/ai_smell_patterns.yaml"))
    args = parser.parse_args()

    os.chdir(ROOT)
    load_env(ROOT / ".env")
    config = load_config(args.config)
    personas = load_personas(args.personas)
    lexicon = load_lexicon(args.lexicon)
    prompts = PromptStore(config.paths.prompts_dir)
    prompts.check_versions(config.prompt_versions)

    api_key = os.environ.get(config.api_key_env, "").strip()
    if not api_key:
        print(f"error: {config.api_key_env} is not set (put it in .env)", file=sys.stderr)
        return 2

    client = DeepSeekClient(
        api_key=api_key,
        prompt_store=prompts,
        base_url=config.base_url,
        timeout_s=config.api.timeout_s,
        max_retries=config.api.max_retries,
        backoff_base_s=config.api.backoff_base_s,
        json_mode=config.api.json_mode,
    )
    pipeline = HumanizePipeline(
        config=config, personas=personas, lexicon=lexicon, client=client
    )

    inputs = load_run_inputs(ROOT / "data/eval/seed_examples.jsonl") + load_run_inputs(
        ROOT / "data/probe/stability_probe.jsonl"
    )

    records: list[dict] = []
    errors: list[dict] = []
    degraded: list[str] = []
    cache_hits = 0
    total_cost = 0.0
    facts_total = 0
    facts_retained = 0
    facts_retained_coverage = 0
    concrete_observed = 0
    concrete_dropped_total = 0
    unsafe_count = 0
    partial_count = 0

    for inp in inputs:
        try:
            record = pipeline.run(inp)
        except Exception as exc:  # a crash is a regression, keep going to report all
            errors.append({"sample_id": inp.sample_id, "error": f"{type(exc).__name__}: {exc}"})
            continue

        final_text = record.govern.final_text
        kept = record.rewrite.kept_facts
        retained = sum(1 for fact in kept if fact in final_text)
        retained_coverage = sum(1 for fact in kept if fact_preserved(fact, final_text))
        facts_total += len(kept)
        facts_retained += retained
        facts_retained_coverage += retained_coverage
        unsafe_count += int("unsafe_omission" in record.rewrite.risk_flags)
        partial_count += int("partial_omission" in record.rewrite.risk_flags)
        cache_hits += record.usage.cache_hits
        total_cost += record.usage.cost_yuan
        degraded.extend(
            f"{inp.sample_id}:{item}" for item in record.meta.get("degraded", [])
        )

        scene = inp.context.scene.value
        spans = concrete_spans(inp.raw_reply)
        dropped = [span for span in spans if span not in final_text]
        concrete_observed += len(spans)
        concrete_dropped_total += len(dropped)
        records.append(
            {
                "sample_id": inp.sample_id,
                "scene": scene,
                "noop": record.rewrite.noop,
                "message_count": record.rewrite.message_count,
                "ratio": record.rewrite.rewrite_ratio,
                "kept_facts": list(kept),
                "kept_retained_flags": [fact in final_text for fact in kept],
                "kept_retained_coverage_flags": [
                    fact_preserved(fact, final_text) for fact in kept
                ],
                "kept_total": len(kept),
                "kept_retained": retained,
                "kept_retained_coverage": retained_coverage,
                "semantic_points": list(record.rewrite.semantic_points),
                "unsafe_omission": "unsafe_omission" in record.rewrite.risk_flags,
                "partial_omission": "partial_omission" in record.rewrite.risk_flags,
                "kept_facts_demoted": "kept_facts_demoted" in record.rewrite.risk_flags,
                "compression": (
                    round(1 - len(final_text) / len(inp.raw_reply), 3)
                    if inp.raw_reply
                    else 0.0
                ),
                "concrete_dropped": dropped,
                "final_text": final_text,
            }
        )
        print(f"[{inp.sample_id}] scene={scene} msgs={record.rewrite.message_count} "
              f"ratio={record.rewrite.rewrite_ratio:.2f} facts={retained}/{len(kept)}")

    formal = [record for record in records if record["scene"] in FORMAL_SCENES]
    dropped_samples = [record for record in records if record["concrete_dropped"]]
    retention = round(facts_retained / facts_total, 4) if facts_total else None
    retention_coverage = (
        round(facts_retained_coverage / facts_total, 4) if facts_total else None
    )

    report = {
        "created_at": now_iso(),
        "runs": len(inputs),
        "completed": len(records),
        "errors": errors,
        "degraded": degraded,
        "cache_hits": cache_hits,
        "estimated_cost_yuan": round(total_cost, 6),
        "kept_facts": {
            "retained": facts_retained,
            "retained_coverage": facts_retained_coverage,
            "total": facts_total,
            "retention_rate": retention,
            "retention_rate_coverage": retention_coverage,
        },
        "unsafe_omission_samples": unsafe_count,
        "partial_omission_samples": partial_count,
        "concrete_spans_observed": concrete_observed,
        "concrete_spans_dropped": concrete_dropped_total,
        "concrete_dropped_samples": [
            {
                "sample_id": record["sample_id"],
                "scene": record["scene"],
                "dropped": record["concrete_dropped"],
            }
            for record in dropped_samples
        ],
        "formal_scene_stats": formal,
        "records": records,
    }
    stamp = now_iso().replace(":", "").replace("+", "_")
    out_path = write_json(ROOT / "data/regression" / f"{stamp}__day5.json", report)

    print()
    print(f"runs={len(inputs)} completed={len(records)} errors={len(errors)}")
    print(f"degraded={len(degraded)} cache_hits={cache_hits} cost={total_cost:.4f} CNY")
    print(f"kept_facts retention: exact {facts_retained}/{facts_total} = {retention}, "
          f"coverage {facts_retained_coverage}/{facts_total} = {retention_coverage}")
    print(f"unsafe_omission samples={unsafe_count}, partial={partial_count}")
    print(
        f"concrete spans observed={concrete_observed}, dropped={concrete_dropped_total}, "
        f"samples={len(dropped_samples)}"
    )
    for record in dropped_samples[:10]:
        print(
            f"  {record['sample_id']} scene={record['scene']} "
            f"dropped={record['concrete_dropped']}"
        )
    print("formal scene stats:")
    for record in formal:
        print(
            f"  {record['sample_id']} scene={record['scene']} "
            f"msgs={record['message_count']} compression={record['compression']} "
            f"dropped={record['concrete_dropped']}"
        )
    for error in errors:
        print(f"  ERROR {error['sample_id']}: {error['error']}")
    print(f"report -> {out_path}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
