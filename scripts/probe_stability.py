"""Run the 3x signal-layer stability probe with the disk cache DISABLED.

    python scripts/probe_stability.py

This script constructs a raw ``DeepSeekClient`` and never wraps it in
``CachedClient``; it also asserts ``usage.cache_hits == 0`` for every run, so the
three samples per case are three real provider calls. A cache hit here would
make 3/3 agreement meaningless (Days 2 proved the signal layer is cached).

Decision line: 95% on the 30-case base cohort only. probe_031/032 are
supplemental observations and never enter the verdict. Comfort's two sarcastic
cases are reported as their own subgroup.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from humanize.cache import CachedClient  # noqa: E402
from humanize.config import load_config, load_personas  # noqa: E402
from humanize.env import load_env  # noqa: E402
from humanize.io import load_run_inputs, now_iso, write_json  # noqa: E402
from humanize.lexicon import load_lexicon  # noqa: E402
from humanize.llm import DeepSeekClient  # noqa: E402
from humanize.pipeline import HumanizePipeline  # noqa: E402
from humanize.prompts import PromptStore  # noqa: E402

FIELDS = ("sarcasm.label", "emotion.label", "tone", "ai_smell.types")
BASE_SIZE = 30
SUPPLEMENTAL_IDS = ("probe_031", "probe_032")
COMFORT_SUBGROUP = ("probe_008", "probe_011")


def extract(signals) -> dict[str, Any]:
    return {
        "sarcasm.label": signals.sarcasm.label.value,
        "emotion.label": signals.emotion.user_emotion.label,
        "tone": signals.emotion.assistant_tone_target.value,
        "ai_smell.types": tuple(sorted({hit.type.value for hit in signals.ai_smell.hits})),
    }


def summarize(cases: list[dict], runs: int) -> tuple[dict, list[dict]]:
    summary: dict[str, dict] = {}
    failures: list[dict] = []
    for field in FIELDS:
        full = 0
        majority = 0
        for case in cases:
            values = [sample[field] for sample in case["samples"]]
            top = Counter(values).most_common(1)[0][1]
            full += top == runs
            majority += top >= 2
            if top < runs:
                failures.append(
                    {
                        "id": case["id"],
                        "cohort": case["cohort"],
                        "scene": case["scene"],
                        "field": field,
                        "values": [
                            list(value) if isinstance(value, tuple) else value
                            for value in values
                        ],
                    }
                )
        summary[field] = {
            "full_agreement": full,
            "majority_agreement": majority,
            "total": len(cases),
            "rate": round(full / len(cases), 3) if cases else 0.0,
        }
    return summary, failures


def format_rate(summary: dict) -> str:
    return (
        f"{summary['full_agreement']}/{summary['total']} "
        f"({summary['rate']:.1%})"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", default=str(ROOT / "data/probe/stability_probe.jsonl"))
    parser.add_argument("--manifest", default=str(ROOT / "data/probe/manifest.json"))
    parser.add_argument("--config", default=str(ROOT / "config/pipeline.yaml"))
    parser.add_argument("--personas", default=str(ROOT / "config/persona.yaml"))
    parser.add_argument("--lexicon", default=str(ROOT / "config/ai_smell_patterns.yaml"))
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--threshold", type=float, default=0.95)
    args = parser.parse_args()

    if args.runs < 2:
        print("error: --runs must be >= 2", file=sys.stderr)
        return 2

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
    if isinstance(client, CachedClient):
        print("error: probe refuses a cached client", file=sys.stderr)
        return 2

    pipeline = HumanizePipeline(
        config=config, personas=personas, lexicon=lexicon, client=client
    )

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    by_id = {case.sample_id: case for case in load_run_inputs(args.fixture)}

    records: list[dict] = []
    reported_models: set[str] = set()
    total_cache_hits = 0
    total_cost = 0.0
    total_calls = 0
    for item in manifest["cases"]:
        inp = by_id[item["id"]]
        samples: list[dict] = []
        cache_hits: list[int] = []
        latencies: list[int] = []
        for _ in range(args.runs):
            signals, usage = pipeline.run_signals(inp)
            reported_models.add(signals.meta.model)
            samples.append(extract(signals))
            cache_hits.append(usage.cache_hits)
            latencies.append(usage.calls)
            total_cache_hits += usage.cache_hits
            total_calls += usage.calls
            total_cost += usage.cost_yuan
        records.append(
            {
                "id": item["id"],
                "cohort": item["cohort"],
                "scene": item["scene"],
                "intended_sarcasm": item["intended_sarcasm"],
                "sarcasm_strength": item["sarcasm_strength"],
                "intended_emotion": item["intended_emotion"],
                "note": item.get("note", ""),
                "samples": samples,
                "cache_hits": cache_hits,
                "calls_per_run": latencies,
            }
        )

    if total_cache_hits != 0:
        print(
            f"error: cache_hits={total_cache_hits}; probe aborted because samples "
            "would not be independent real calls",
            file=sys.stderr,
        )
        return 2

    base = [record for record in records if record["cohort"] == "base"]
    supplemental = [record for record in records if record["cohort"] == "supplemental"]
    comfort_subgroup = [record for record in base if record["id"] in COMFORT_SUBGROUP]
    probe_031 = next(record for record in records if record["id"] == "probe_031")

    base_summary, base_failures = summarize(base, args.runs)
    comfort_summary, comfort_failures = summarize(comfort_subgroup, args.runs)
    supplemental_summary, supplemental_failures = summarize(supplemental, args.runs)

    label_fields = ("sarcasm.label", "emotion.label", "tone")
    labels_stable = all(
        base_summary[field]["rate"] >= args.threshold for field in label_fields
    )
    types_stable = base_summary["ai_smell.types"]["rate"] >= args.threshold
    if labels_stable and types_stable:
        recommendation = "plan_b"
    elif labels_stable:
        recommendation = "plan_b_but_review_ai_smell_types"
    else:
        recommendation = "plan_a_plus_majority_vote"

    report = {
        "created_at": now_iso(),
        "fixture": str(Path(args.fixture).relative_to(ROOT)),
        "runs_per_case": args.runs,
        "cases": len(records),
        "signal_runs": len(records) * args.runs,
        "provider_calls": total_calls,
        "cache_hits": total_cache_hits,
        "cache_disabled": True,
        "threshold_on_base": args.threshold,
        "threshold_note": (
            "30-case base provisional line; Day 8 must re-confirm on 60-item eval set"
        ),
        "requested_model": config.models.signal.model,
        "reported_models": sorted(reported_models),
        "seeds": {"signal": config.models.signal.seed},
        "base": {"summary": base_summary, "failures": base_failures},
        "comfort_subgroup": {
            "ids": list(COMFORT_SUBGROUP),
            "summary": comfort_summary,
            "failures": comfort_failures,
        },
        "supplemental": {
            "ids": list(SUPPLEMENTAL_IDS),
            "summary": supplemental_summary,
            "failures": supplemental_failures,
        },
        "probe_031_observation": {
            "scene": probe_031["scene"],
            "intended_emotion": probe_031["intended_emotion"],
            "sarcasm_strength": probe_031["sarcasm_strength"],
            "samples": probe_031["samples"],
        },
        "recommendation": recommendation,
        "estimated_cost_yuan": round(total_cost, 6),
        "records": records,
    }
    stamp = now_iso().replace(":", "").replace("+", "_")
    out_path = write_json(ROOT / "data/probe/reports" / f"{stamp}__stability.json", report)

    print(f"cases={len(records)} runs={args.runs} signal_runs={len(records) * args.runs}")
    print(f"cache_hits={total_cache_hits} (must be 0; probe uses a raw DeepSeekClient)")
    print(f"provider_calls={total_calls} estimated_cost={total_cost:.4f} CNY")
    print(f"threshold={args.threshold:.0%} on base {len(base)} only")
    print()
    print(f"{'field':16s} {'base 3/3':>12s} {'base >=2/3':>12s}")
    for field in FIELDS:
        item = base_summary[field]
        print(
            f"{field:16s} {format_rate(item):>12s} "
            f"{item['majority_agreement']}/{item['total']:>2d}"
        )
    print()
    print(f"comfort base subgroup ({', '.join(COMFORT_SUBGROUP)}):")
    for field in FIELDS:
        print(f"  {field:16s} {format_rate(comfort_summary[field])}")
    print()
    print("supplemental (observation only):")
    for record in supplemental:
        label = (
            f"{record['id']} {record['scene']}/"
            f"{record['intended_emotion']}/{record['sarcasm_strength']}:"
        )
        print(f"  {label}")
        for field in FIELDS:
            values = [sample[field] for sample in record["samples"]]
            print(f"    {field:16s} {values}")
    print()
    print(f"probe_031 observation: {probe_031['samples']}")
    print()

    if base_failures:
        print(f"base failures: {len(base_failures)}")
        for failure in base_failures[:12]:
            print(
                f"  {failure['id']} [{failure['scene']}] {failure['field']}: "
                f"{failure['values']}"
            )
    else:
        print("base failures: 0")

    print()
    print(f"recommendation: {recommendation}")
    print("note: 30 samples -> wide CI; this is provisional, Day 8 re-confirms on 60.")
    print(f"report -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
