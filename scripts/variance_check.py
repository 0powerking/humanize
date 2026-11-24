"""Measure output variance across repeated runs of the same input.

    python scripts/variance_check.py --sample e002_sarcasm_late --runs 3

This bypasses the disk cache on purpose: every run must be a real provider call.
It costs money (~0.05 CNY for 3 runs) and writes a report under data/variance/.

The point is to answer two questions separately:
  1. are the signals stable across runs? (signal fingerprint)
  2. is the rewrite stable? (text equality / pairwise similarity)

If seeds are honored, both should be identical. If only the rewrite varies, the
instability is in the rewrite stage / provider sampling, not in the signal layer.
"""

from __future__ import annotations

import argparse
import os
import sys
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from humanize.config import load_config, load_personas  # noqa: E402
from humanize.env import load_env  # noqa: E402
from humanize.io import load_run_inputs, now_iso, write_json  # noqa: E402
from humanize.lexicon import load_lexicon  # noqa: E402
from humanize.llm import DeepSeekClient  # noqa: E402
from humanize.pipeline import HumanizePipeline  # noqa: E402
from humanize.prompts import PromptStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", default="e002_sarcasm_late")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--config", default=str(ROOT / "config/pipeline.yaml"))
    parser.add_argument("--personas", default=str(ROOT / "config/persona.yaml"))
    parser.add_argument("--lexicon", default=str(ROOT / "config/ai_smell_patterns.yaml"))
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

    inputs = load_run_inputs(ROOT / "data/eval/seed_examples.jsonl")
    inp = next((item for item in inputs if item.sample_id == args.sample), None)
    if inp is None:
        print(f"error: sample {args.sample!r} not found", file=sys.stderr)
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

    records = [pipeline.run(inp) for _ in range(args.runs)]
    texts = [record.govern.final_text for record in records]
    similarities = [
        round(SequenceMatcher(None, texts[i], texts[j]).ratio(), 3)
        for i in range(len(texts))
        for j in range(i + 1, len(texts))
    ]

    report = {
        "sample_id": args.sample,
        "created_at": now_iso(),
        "runs": args.runs,
        "provider": client.name,
        "requested_model": config.models.rewrite.model,
        "reported_models": sorted({record.signals.meta.model for record in records}),
        "temperature": config.models.rewrite.temperature,
        "seeds": {
            "signal": config.models.signal.seed,
            "rewrite": config.models.rewrite.seed,
        },
        "identical_texts": len(set(texts)) == 1,
        "pairwise_similarity": similarities,
        "signals_identical": len({record.meta["signal_fingerprint"] for record in records}) == 1,
        "signal_fingerprints": [record.meta["signal_fingerprint"] for record in records],
        "signals": [
            {
                "sarcasm": {
                    "label": record.signals.sarcasm.label.value,
                    "confidence": record.signals.sarcasm.confidence,
                },
                "emotion": {
                    "label": record.signals.emotion.user_emotion.label,
                    "valence": record.signals.emotion.valence,
                    "tone": record.signals.emotion.assistant_tone_target.value,
                },
                "ai_smell": {
                    "score": record.signals.ai_smell.score,
                    "types": [hit.type.value for hit in record.signals.ai_smell.hits],
                },
            }
            for record in records
        ],
        "system_fingerprints": sorted(
            {
                fingerprint
                for record in records
                for fingerprint in record.meta["system_fingerprints"]
            }
        ),
        "outputs": texts,
        "rewrite_ratios": [record.rewrite.rewrite_ratio for record in records],
        "total_ms": [record.timing.total_ms for record in records],
        "usage": [record.usage.model_dump(mode="json") for record in records],
        "estimated_cost_yuan": round(
            sum(record.usage.cost_yuan for record in records), 6
        ),
    }

    stamp = now_iso().replace(":", "").replace("+", "_")
    out_path = write_json(ROOT / "data/variance" / f"{stamp}__{args.sample}.json", report)

    print(f"sample={args.sample} runs={args.runs} provider={client.name}")
    print(f"requested_model={config.models.rewrite.model} reported={report['reported_models']}")
    print(f"seeds={report['seeds']} temperature={report['temperature']}")
    print(f"signals_identical={report['signals_identical']}")
    print(f"identical_texts={report['identical_texts']}")
    print(f"pairwise_similarity={report['pairwise_similarity']}")
    print(f"system_fingerprints={report['system_fingerprints']}")
    print(f"estimated_cost={report['estimated_cost_yuan']} CNY")
    print(f"report -> {out_path}")
    for index, text in enumerate(texts, start=1):
        print(f"  [{index}] {text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
