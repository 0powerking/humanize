"""Validate the manually anonymized real-candidate pool (zero cost).

    python scripts/real_candidates_check.py --target 18

Checks structure, scene coverage and obvious PII. It is not a guarantee of
anonymization; `notes` must say what was changed.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "data/eval/real_candidates.jsonl"
SCENES = {"casual", "comfort", "banter", "boundary", "work"}
PROFILES = {"short_burst", "single_msg", "long_explain"}
PII = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)|[\w.+-]+@[\w-]+\.[\w.]+|https?://|www\.")


def validate(path: Path, target: int) -> tuple[list[dict], list[str]]:
    cases: list[dict] = []
    problems: list[str] = []
    if not path.exists():
        return cases, [f"missing file: {path}"]
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            problems.append(f"line {lineno}: invalid JSON: {exc}")
            continue
        cases.append(case)
        case_id = case.get("id", f"line{lineno}")
        if case.get("source") != "real":
            problems.append(f"{case_id}: source must be real")
        if case.get("anonymized") is not True:
            problems.append(f"{case_id}: anonymized must be true")
        if case.get("scene") not in SCENES:
            problems.append(f"{case_id}: bad scene")
        if case.get("rhythm_profile") not in PROFILES:
            problems.append(f"{case_id}: bad rhythm_profile")
        if not case.get("raw_reply"):
            problems.append(f"{case_id}: missing raw_reply")
        if not case.get("notes"):
            problems.append(f"{case_id}: missing anonymization notes")
        turns = case.get("turns") or []
        if not isinstance(turns, list) or len(turns) < 2:
            problems.append(f"{case_id}: need at least 2 context turns")
        haystack = " ".join(
            [case.get("raw_reply", ""), case.get("notes", "")]
            + [turn.get("content", "") for turn in turns if isinstance(turn, dict)]
        )
        if PII.search(haystack):
            problems.append(f"{case_id}: possible phone/email/URL remains")

    ids = [case.get("id") for case in cases]
    if len(set(ids)) != len(ids):
        problems.append("duplicate id")
    if len(cases) > target:
        problems.append(f"{len(cases)} cases exceeds target {target}")
    return cases, problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", default=str(DEFAULT))
    parser.add_argument("--target", type=int, default=18)
    args = parser.parse_args()

    path = Path(args.file)
    cases, problems = validate(path, args.target)
    scene_counts = Counter(case.get("scene") for case in cases)
    print(f"{path}: {len(cases)}/{args.target} real candidates")
    print(f"scenes: {dict(scene_counts)}")
    if problems:
        print("FAILED:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    missing = sorted(SCENES - set(scene_counts))
    if missing and cases:
        print(f"note: scenes not covered yet: {missing}")
    print("ok: structure/PII check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
