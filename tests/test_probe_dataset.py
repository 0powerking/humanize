from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from humanize.io import load_run_inputs

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "data/probe/stability_probe.jsonl"
MANIFEST = ROOT / "data/probe/manifest.json"


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_probe_ids_match_manifest_and_are_synthetic() -> None:
    cases = load_run_inputs(PROBE)
    manifest = _manifest()

    assert len(cases) == 30
    assert len({case.sample_id for case in cases}) == 30
    assert {case.sample_id for case in cases} == {item["id"] for item in manifest["cases"]}
    assert all(case.source == "synthetic" for case in cases)
    assert "data/eval" not in str(PROBE)


def test_every_scene_has_at_least_five_cases() -> None:
    manifest = _manifest()
    counts = Counter(item["scene"] for item in manifest["cases"])

    assert set(counts) == {"casual", "comfort", "banter", "boundary", "work"}
    assert all(count >= 5 for count in counts.values())


def test_intended_sarcasm_is_balanced() -> None:
    manifest = _manifest()
    counts = Counter(item["intended_sarcasm"] for item in manifest["cases"])

    assert abs(counts["sarcastic"] - counts["literal"]) <= 2
