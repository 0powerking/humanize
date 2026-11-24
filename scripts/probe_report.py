"""Validate the stability probe and print its scene/intent distribution.

    python scripts/probe_report.py

Zero-cost: reads data/probe/stability_probe.jsonl + manifest.json only.
The probe is NOT the evaluation set; it is a 30-case label-stability check.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from humanize.io import load_run_inputs  # noqa: E402

PROBE = ROOT / "data/probe/stability_probe.jsonl"
MANIFEST = ROOT / "data/probe/manifest.json"
MIN_PER_SCENE = 5
MAX_SARCASM_IMBALANCE = 2


def main() -> int:
    cases = load_run_inputs(PROBE)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    intended = {item["id"]: item for item in manifest["cases"]}

    problems: list[str] = []
    ids = [case.sample_id for case in cases]
    if len(ids) != len(set(ids)):
        problems.append("duplicate sample_id in probe")
    if set(ids) != set(intended):
        problems.append("probe ids and manifest ids differ")
    if any(case.source != "synthetic" for case in cases):
        problems.append("all probe cases must be source=synthetic")
    if len(cases) != 30:
        problems.append(f"expected 30 cases, got {len(cases)}")

    scene_counts = Counter(intended[case.sample_id]["scene"] for case in cases)
    for scene, count in scene_counts.items():
        if count < MIN_PER_SCENE:
            problems.append(f"scene {scene} has {count} cases, need >= {MIN_PER_SCENE}")

    sarcasm = Counter(intended[case.sample_id]["intended_sarcasm"] for case in cases)
    imbalance = abs(sarcasm["sarcastic"] - sarcasm["literal"])
    if imbalance > MAX_SARCASM_IMBALANCE:
        problems.append(
            f"sarcasm imbalance {sarcasm['sarcastic']}/{sarcasm['literal']} "
            f"exceeds +/-{MAX_SARCASM_IMBALANCE}"
        )

    print(f"probe={PROBE.relative_to(ROOT)} cases={len(cases)}")
    print(f"threshold={manifest['threshold']:.0%} ({manifest['threshold_note']})")
    print()
    print(f"{'scene':10s} {'sarcastic':>9s} {'literal':>8s} {'total':>6s}")
    for scene in sorted(scene_counts):
        in_scene = [
            intended[case.sample_id]
            for case in cases
            if intended[case.sample_id]["scene"] == scene
        ]
        satiric = sum(1 for item in in_scene if item["intended_sarcasm"] == "sarcastic")
        literal = len(in_scene) - satiric
        print(f"{scene:10s} {satiric:9d} {literal:8d} {len(in_scene):6d}")
    print(
        f"{'total':10s} {sarcasm['sarcastic']:9d} {sarcasm['literal']:8d} "
        f"{len(cases):6d}"
    )
    print()
    emotions = Counter(item["intended_emotion"] for item in intended.values())
    print("emotion coverage:", dict(emotions))
    print("ids:", f"{ids[0]}..{ids[-1]}")
    print()

    if problems:
        print("FAILED:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("ok: within per-scene and balance constraints")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
