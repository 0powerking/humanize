"""Real-model acceptance check for ai-smell fusion (Day 3).

    python scripts/ai_smell_fusion_check.py

Runs only the ai-smell stage over the 20 fixture cases (no rewrite, no cache),
merges rule + LLM hits with the production merge function, and reports:

  * gap cases caught by the LLM extras (soft target: >= 2/3)
  * false positives on clean cases (hard constraint: 0/7)
  * rule hits preserved (rules must never be lost)

Costs a few cents. Exit code 1 only on a clean-case false positive.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from humanize.ai_smell import merge_ai_smell, rule_hits_payload  # noqa: E402
from humanize.config import load_config  # noqa: E402
from humanize.cost import estimate_cost  # noqa: E402
from humanize.env import load_env  # noqa: E402
from humanize.io import now_iso, read_jsonl, write_json  # noqa: E402
from humanize.lexicon import load_lexicon  # noqa: E402
from humanize.llm import DeepSeekClient, parse_json_object  # noqa: E402
from humanize.prompts import PromptStore  # noqa: E402
from humanize.schemas import AiSmellExtra  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture", default=str(ROOT / "tests/fixtures/ai_smell_cases.jsonl")
    )
    parser.add_argument("--config", default=str(ROOT / "config/pipeline.yaml"))
    parser.add_argument("--lexicon", default=str(ROOT / "config/ai_smell_patterns.yaml"))
    args = parser.parse_args()

    os.chdir(ROOT)
    load_env(ROOT / ".env")
    config = load_config(args.config)
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
    cases = read_jsonl(args.fixture)

    results: list[dict] = []
    reported_models: set[str] = set()
    total_cost = 0.0
    for case in cases:
        text = case["text"]
        rule_findings = lexicon.scan(text)
        payload = {
            "context": {"scene": "casual", "turns": []},
            "raw_reply": text,
            "persona_ref": {"persona_id": "p_sunyou", "version": "1.0"},
            "rule_hits": rule_hits_payload(rule_findings),
            "lexicon": lexicon.to_payload(),
        }
        response = client.complete_json(
            prompt_id="signal.ai_smell",
            payload=payload,
            model=config.models.signal.model,
            temperature=config.models.signal.temperature,
            seed=config.models.signal.seed,
        )
        total_cost += estimate_cost([response], config.cost)
        reported_models.add(response.model)
        extra = AiSmellExtra.model_validate(parse_json_object(response.text))
        merged = merge_ai_smell(text, rule_findings, extra.extra_hits, extra.notes)

        results.append(
            {
                "id": case["id"],
                "expect": case["expect"],
                "text": text,
                "rule_hits": [f.type.value for f in rule_findings],
                "llm_extra_hits": [hit.model_dump(mode="json") for hit in extra.extra_hits],
                "merged_hits": [hit.model_dump(mode="json") for hit in merged.hits],
                "score": merged.score,
                "notes": merged.notes,
            }
        )

    gaps = [item for item in results if item["expect"] == "gap"]
    clean = [item for item in results if item["expect"] == "clean"]
    hits = [item for item in results if item["expect"] == "hit"]

    gap_caught = [item for item in gaps if item["merged_hits"]]
    false_positives = [item for item in clean if item["merged_hits"]]
    rule_lost = [
        item
        for item in hits
        if not set(
            next(case["types"] for case in cases if case["id"] == item["id"])
        ).issubset({hit["type"] for hit in item["merged_hits"]})
    ]

    report = {
        "created_at": now_iso(),
        "model_requested": config.models.signal.model,
        "model_reported": sorted(reported_models),
        "gap_caught": [item["id"] for item in gap_caught],
        "gap_total": len(gaps),
        "false_positive_ids": [item["id"] for item in false_positives],
        "rule_lost_ids": [item["id"] for item in rule_lost],
        "estimated_cost_yuan": round(total_cost, 6),
        "results": results,
    }
    stamp = now_iso().replace(":", "").replace("+", "_")
    out_path = write_json(ROOT / "data/fusion" / f"{stamp}__ai_smell.json", report)

    print(f"cases={len(cases)} model={config.models.signal.model}")
    print(f"gap caught: {len(gap_caught)}/{len(gaps)} (soft target >= 2)")
    print(f"clean false positives: {len(false_positives)}/{len(clean)} (hard target 0)")
    print(f"rule hits lost: {len(rule_lost)}/{len(hits)} (hard target 0)")
    print(f"estimated cost: {total_cost:.4f} CNY")
    for item in gaps:
        status = "CAUGHT" if item["merged_hits"] else "MISS"
        print(f"  [{status}] {item['id']}: {item['text']}")
        if item["merged_hits"]:
            print(f"          -> {item['merged_hits']}")
    for item in false_positives:
        print(f"  [FP] {item['id']}: {item['merged_hits']}")
    print(f"report -> {out_path}")

    return 1 if false_positives or rule_lost else 0


if __name__ == "__main__":
    raise SystemExit(main())
