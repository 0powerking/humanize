"""Zero-cost rule-lexicon coverage report.

    python scripts/lexicon_report.py

Reads tests/fixtures/ai_smell_cases.jsonl and reports:
  * recall on cases the rules are expected to catch
  * semantic recall when known lexical gaps are counted (the LLM-fallback target)
  * false positives on clean cases
  * per-type coverage

This is a regression/coverage probe, not an evaluation set: the cases are
hand-written and small. Real recall needs the 60-80 item eval set (Day 7-9).
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from humanize.io import read_jsonl  # noqa: E402
from humanize.lexicon import load_lexicon  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture",
        default=str(ROOT / "tests/fixtures/ai_smell_cases.jsonl"),
    )
    parser.add_argument(
        "--lexicon",
        default=str(ROOT / "config/ai_smell_patterns.yaml"),
    )
    args = parser.parse_args()

    lexicon = load_lexicon(args.lexicon)
    cases = read_jsonl(args.fixture)

    hit_cases = [case for case in cases if case["expect"] == "hit"]
    clean_cases = [case for case in cases if case["expect"] == "clean"]
    gap_cases = [case for case in cases if case["expect"] == "gap"]

    caught = 0
    per_type_hit: dict[str, int] = defaultdict(int)
    per_type_total: dict[str, int] = defaultdict(int)
    misses: list[str] = []
    for case in hit_cases:
        actual = {finding.type.value for finding in lexicon.scan(case["text"])}
        for expected_type in case["types"]:
            per_type_total[expected_type] += 1
            if expected_type in actual:
                per_type_hit[expected_type] += 1
        if set(case["types"]).issubset(actual):
            caught += 1
        else:
            misses.append(case["id"])

    gap_caught = [case["id"] for case in gap_cases if lexicon.scan(case["text"])]
    false_positives = [
        (case["id"], sorted({f.type.value for f in lexicon.scan(case["text"])}))
        for case in clean_cases
        if lexicon.scan(case["text"])
    ]

    total_positives = len(hit_cases) + len(gap_cases)
    print(
        f"cases={len(cases)} hit={len(hit_cases)} clean={len(clean_cases)} "
        f"gap={len(gap_cases)} lexicon={lexicon.version}"
    )
    print(f"rule recall (must-catch): {caught}/{len(hit_cases)} ({caught / len(hit_cases):.1%})")
    print(
        f"semantic recall incl gaps: {caught}/{total_positives} "
        f"({caught / total_positives:.1%})  -> LLM fallback must cover "
        f"{len(gap_cases)}/{total_positives} ({(total_positives - caught) / total_positives:.1%})"
    )
    print(f"false positives on clean: {len(false_positives)}/{len(clean_cases)}")

    print("per-type coverage:")
    for rule_type in sorted(per_type_total):
        print(f"  {rule_type:14s} {per_type_hit[rule_type]}/{per_type_total[rule_type]}")

    if misses:
        print("unexpected misses:")
        for case_id in misses:
            print(f"  - {case_id}")

    if gap_caught:
        print("gap cases unexpectedly caught (update fixture):")
        for case_id in gap_caught:
            print(f"  - {case_id}")

    print("known lexical gaps (LLM-fallback targets):")
    for case in gap_cases:
        print(f"  - {case['id']}: {case['text']}")

    return 0 if not misses and not false_positives and not gap_caught else 1


if __name__ == "__main__":
    raise SystemExit(main())
