"""Validate the Day 7 pilot dataset and annotation template (zero cost).

    python scripts/pilot_check.py

Checks only structure: 10 cases, 2 per scene, 5 sarcastic / 5 literal, required
gold fields, and that the annotation template covers the same ids with 5 overlaps.
It deliberately produces no evaluation metrics.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

PILOT = ROOT / "data/eval/pilot_10.jsonl"
TEMPLATE = ROOT / "data/eval/annotations/pilot_annotations_template.csv"
SCENES = {"casual", "comfort", "banter", "boundary", "work"}
PROFILES = {"short_burst", "single_msg", "long_explain"}
FIELDS = {
    "sample_id",
    "annotator_id",
    "is_overlap",
    "user_sarcasm",
    "user_sarcasm_evidence",
    "user_emotion_label",
    "user_emotion_intensity",
    "persona_consistency",
    "rhythm_profile",
    "omission_naturalness",
    "ab_preference",
    "notes",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot", default=str(PILOT))
    parser.add_argument("--template", default=str(TEMPLATE))
    args = parser.parse_args()

    cases = []
    for line in Path(args.pilot).read_text(encoding="utf-8").splitlines():
        if line.strip():
            cases.append(json.loads(line))

    problems: list[str] = []
    ids = [case["id"] for case in cases]
    if len(cases) != 10:
        problems.append(f"expected 10 cases, got {len(cases)}")
    if len(set(ids)) != len(ids):
        problems.append("duplicate id")

    scene_counts = Counter(case["scene"] for case in cases)
    if set(scene_counts) != SCENES or any(count != 2 for count in scene_counts.values()):
        problems.append(
            f"scene distribution must be 2 each of {sorted(SCENES)}: "
            f"{dict(scene_counts)}"
        )

    sarcasm = Counter(case["gold"]["user_sarcasm"] for case in cases)
    if sarcasm.get("sarcastic") != 5 or sarcasm.get("literal") != 5:
        problems.append(f"sarcasm must be 5/5: {dict(sarcasm)}")

    for case in cases:
        if case.get("source") != "synthetic":
            problems.append(f"{case.get('id')}: pilot must be source=synthetic")
        if case.get("rhythm_profile") not in PROFILES:
            problems.append(f"{case.get('id')}: bad rhythm_profile")
        gold = case.get("gold") or {}
        if gold.get("user_sarcasm") not in {"sarcastic", "literal", "uncertain"}:
            problems.append(f"{case.get('id')}: bad gold.user_sarcasm")
        if not gold.get("user_emotion"):
            problems.append(f"{case.get('id')}: missing gold.user_emotion")

    with Path(args.template).open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        header = set(reader.fieldnames or [])
        rows = list(reader)
    if header != FIELDS:
        problems.append(
            f"template header mismatch: missing={FIELDS - header}, "
            f"extra={header - FIELDS}"
        )
    template_ids = [row["sample_id"] for row in rows]
    if template_ids != ids:
        problems.append("template ids do not match pilot ids")
    overlap = sum(1 for row in rows if row["is_overlap"] == "1")
    if overlap != 5:
        problems.append(f"expected 5 overlap rows, got {overlap}")

    print(f"pilot={Path(args.pilot).relative_to(ROOT)} cases={len(cases)}")
    print(f"scenes: {dict(scene_counts)}")
    print(f"sarcasm: {dict(sarcasm)}")
    print(f"profiles: {dict(Counter(case['rhythm_profile'] for case in cases))}")
    print(f"template rows={len(rows)} overlap={overlap}")
    if problems:
        print("FAILED:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("ok: pilot structure and template coverage valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
