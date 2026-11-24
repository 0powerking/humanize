"""Validate the stability probe and print its scene/intent distribution.

    python scripts/probe_report.py

Zero-cost: reads data/probe/stability_probe.jsonl + manifest.json only.
The probe is NOT the evaluation set. The 95% decision line uses only the 30-case
base cohort; probe_031/032 are supplemental observations.
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
BASE_SIZE = 30
SUPPLEMENTAL_IDS = {"probe_031", "probe_032"}
MIN_PER_SCENE = 5
MAX_SARCASM_IMBALANCE = 2


def main() -> int:
    cases = load_run_inputs(PROBE)
    by_id = {case.sample_id: case for case in cases}
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    items = manifest["cases"]
    intended = {item["id"]: item for item in items}

    base = [item for item in items if item["cohort"] == "base"]
    supplemental = [item for item in items if item["cohort"] == "supplemental"]

    problems: list[str] = []
    ids = [case.sample_id for case in cases]
    if len(ids) != len(set(ids)):
        problems.append("duplicate sample_id in probe")
    if set(ids) != set(intended):
        problems.append("probe ids and manifest ids differ")
    if any(case.source != "synthetic" for case in cases):
        problems.append("all probe cases must be source=synthetic")
    if len(cases) != BASE_SIZE + len(SUPPLEMENTAL_IDS):
        problems.append(f"expected 32 cases, got {len(cases)}")
    if len(base) != BASE_SIZE:
        problems.append(f"base cohort must be {BASE_SIZE}, got {len(base)}")
    if {item["id"] for item in supplemental} != SUPPLEMENTAL_IDS:
        problems.append("supplemental cohort must be probe_031/032")

    scene_counts = Counter(item["scene"] for item in items)
    for scene, count in scene_counts.items():
        if count < MIN_PER_SCENE:
            problems.append(f"scene {scene} has {count} cases, need >= {MIN_PER_SCENE}")

    base_sarcasm = Counter(item["intended_sarcasm"] for item in base)
    imbalance = abs(base_sarcasm["sarcastic"] - base_sarcasm["literal"])
    if imbalance > MAX_SARCASM_IMBALANCE:
        problems.append(
            f"base sarcasm imbalance {base_sarcasm['sarcastic']}/{base_sarcasm['literal']} "
            f"exceeds +/-{MAX_SARCASM_IMBALANCE}"
        )

    for item in items:
        if item["intended_sarcasm"] == "sarcastic" and item["sarcasm_strength"] not in {
            "strong",
            "weak",
        }:
            problems.append(f"{item['id']}: sarcastic case needs strength strong/weak")
        if item["intended_sarcasm"] == "literal" and item["sarcasm_strength"] != "none":
            problems.append(f"{item['id']}: literal case must have strength none")

    comfort_sarcastic = [
        item
        for item in base
        if item["scene"] == "comfort" and item["intended_sarcasm"] == "sarcastic"
    ]
    if len(comfort_sarcastic) != 2:
        problems.append(f"comfort sarcastic subgroup must be 2, got {len(comfort_sarcastic)}")

    print(f"probe={PROBE.relative_to(ROOT)} cases={len(cases)}")
    print(f"base={len(base)} supplemental={len(supplemental)}")
    print(f"decision line: {manifest['threshold']:.0%} on base only")
    print(f"  {manifest['decision_cohort']['note']}")
    print()
    print(f"{'scene':10s} {'base_sc':>8s} {'base_lit':>9s} {'supp':>5s} {'total':>6s}")
    for scene in sorted(scene_counts):
        in_scene = [item for item in items if item["scene"] == scene]
        sc = sum(
            1
            for item in in_scene
            if item["cohort"] == "base" and item["intended_sarcasm"] == "sarcastic"
        )
        lit = sum(
            1
            for item in in_scene
            if item["cohort"] == "base" and item["intended_sarcasm"] == "literal"
        )
        supp = sum(1 for item in in_scene if item["cohort"] == "supplemental")
        print(f"{scene:10s} {sc:8d} {lit:9d} {supp:5d} {len(in_scene):6d}")
    print(
        f"{'total':10s} {base_sarcasm['sarcastic']:8d} {base_sarcasm['literal']:9d} "
        f"{len(supplemental):5d} {len(cases):6d}"
    )
    print()

    emotions = Counter(item["intended_emotion"] for item in items)
    print("emotion coverage:", dict(emotions))
    print()
    print("comfort sarcastic subgroup (report separately in the stability run):")
    for item in comfort_sarcastic:
        print(
            f"  {item['id']} strength={item['sarcasm_strength']}: "
            f"{by_id[item['id']].raw_reply}"
        )
    print()
    print("supplemental cases (observation only, outside the 95% decision):")
    for item in supplemental:
        print(
            f"  {item['id']} scene={item['scene']} emotion={item['intended_emotion']} "
            f"sarcasm={item['intended_sarcasm']}/{item['sarcasm_strength']}: "
            f"{by_id[item['id']].raw_reply}"
        )
    print()

    if problems:
        print("FAILED:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("ok: within per-scene, cohort and balance constraints")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
