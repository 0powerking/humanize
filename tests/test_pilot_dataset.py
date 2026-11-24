from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / "data/eval/pilot_10.jsonl"
TEMPLATE = ROOT / "data/eval/annotations/pilot_annotations_template.csv"
TEMPLATE_B = ROOT / "data/eval/annotations/pilot_annotations_template_B.csv"
TIMING = ROOT / "data/eval/annotations/pilot_timing_template.csv"


def _cases() -> list[dict]:
    return [json.loads(line) for line in PILOT.read_text(encoding="utf-8").splitlines() if line]


def test_pilot_shape_and_scenes() -> None:
    cases = _cases()
    assert len(cases) == 10
    assert len({case["id"] for case in cases}) == 10
    assert Counter(case["scene"] for case in cases) == {
        "casual": 2,
        "comfort": 2,
        "banter": 2,
        "boundary": 2,
        "work": 2,
    }
    assert all(case["source"] == "synthetic" for case in cases)


def test_pilot_sarcasm_balance() -> None:
    cases = _cases()
    counts = Counter(case["gold"]["user_sarcasm"] for case in cases)
    assert counts["sarcastic"] == 5
    assert counts["literal"] == 5


def test_pilot_rhythm_profiles() -> None:
    cases = _cases()
    assert {case["rhythm_profile"] for case in cases} <= {
        "short_burst",
        "single_msg",
        "long_explain",
    }
    for case in cases:
        assert case["gold"]["user_emotion"]
        assert 0.0 <= case["gold"]["user_emotion_intensity"] <= 1.0


def test_annotation_template_covers_pilot_with_five_overlaps() -> None:
    with TEMPLATE.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert [row["sample_id"] for row in rows] == [case["id"] for case in _cases()]
    assert sum(1 for row in rows if row["is_overlap"] == "1") == 5
    required = {
        "user_sarcasm",
        "user_emotion_label",
        "user_emotion_intensity",
        "persona_consistency",
        "rhythm_profile",
        "omission_naturalness",
        "ab_preference",
    }
    assert required <= set(rows[0])


def test_b_template_has_five_overlap_rows_and_blank_annotator_b() -> None:
    with TEMPLATE_B.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 5
    assert all(row["is_overlap"] == "1" for row in rows)
    assert all(row["annotator_B"] == "" for row in rows)
    assert [row["sample_id"] for row in rows] == [
        case["id"] for case in _cases()[:5]
    ]


def test_timing_template_segments() -> None:
    with TIMING.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert [row["sample_id"] for row in rows] == [case["id"] for case in _cases()]
    segments = [row["segment"] for row in rows]
    assert segments[0] == "warmup"
    assert segments[-1] == "fatigue"
    assert segments.count("core") == 8
