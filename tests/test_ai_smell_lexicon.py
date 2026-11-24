from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from humanize.io import read_jsonl
from humanize.lexicon import Lexicon

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "ai_smell_cases.jsonl"


@pytest.fixture(scope="module")
def cases() -> list[dict[str, Any]]:
    return read_jsonl(FIXTURE)


def _types(lexicon: Lexicon, text: str) -> set[str]:
    return {finding.type.value for finding in lexicon.scan(text)}


def test_fixture_has_expected_shape(cases: list[dict[str, Any]]) -> None:
    assert len(cases) == 20
    assert {case["expect"] for case in cases} == {"hit", "clean", "gap"}
    assert len({case["id"] for case in cases}) == 20


def test_rule_cases_are_caught(lexicon: Lexicon, cases: list[dict[str, Any]]) -> None:
    hits = [case for case in cases if case["expect"] == "hit"]
    misses = [
        case["id"]
        for case in hits
        if not set(case["types"]).issubset(_types(lexicon, case["text"]))
    ]
    assert not misses, f"lexicon missed expected types: {misses}"
    assert len(hits) == 10


def test_clean_cases_have_no_false_positives(
    lexicon: Lexicon, cases: list[dict[str, Any]]
) -> None:
    clean = [case for case in cases if case["expect"] == "clean"]
    false_positives = [
        (case["id"], sorted(_types(lexicon, case["text"])))
        for case in clean
        if lexicon.scan(case["text"])
    ]
    assert not false_positives, f"false positives on clean cases: {false_positives}"
    assert len(clean) == 7


def test_known_gaps_are_still_missed(lexicon: Lexicon, cases: list[dict[str, Any]]) -> None:
    """These are exactly what the Day 3 LLM fallback must cover.

    If the lexicon is extended to catch them, update the fixture instead of
    deleting the case -- the case is documentation of the rule ceiling.
    """
    gaps = [case for case in cases if case["expect"] == "gap"]
    caught = [case["id"] for case in gaps if lexicon.scan(case["text"])]
    assert not caught, f"gap cases unexpectedly caught by rules: {caught}"
    assert len(gaps) == 3


def test_score_is_monotonic_and_capped(lexicon: Lexicon) -> None:
    one = lexicon.scan("其实，这件事没有那么复杂。")
    two = lexicon.scan("其实，这不是钱的问题，而是信任的问题。")
    many = lexicon.scan(
        "首先，你要冷静。其次，分析原因。最后，制定计划。总之，相信自己，你一定可以的。"
    )

    assert lexicon.score(one) < lexicon.score(two) < lexicon.score(many)
    assert lexicon.score(many) == 1.0
