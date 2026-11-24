"""Rule-level omission legality check for the governed rewrite (Day 5 step 3).

Precision over recall. The first version diffed every clause and produced 7 false
positives on e001 because a paraphrase ("评估核心诉求" -> "看看自己最想要啥")
looks like an omission to a string matcher. This version only judges what a rule
can judge reliably:

  * ``kept_facts`` that contain a concrete marker (time, date, number, money);
  * concrete spans removed from the reply (time/date/number/place-ish tokens).

Abstract nouns/verbs and paraphrase are explicitly out of scope; they belong to
the human eval and to ``kept_facts`` quality, not to a string heuristic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_CONCRETE_RE = re.compile(
    r"\d{1,2}[点:：]\d{0,2}"
    r"|\d{1,2}月\d{1,2}日"
    r"|周[一二三四五六日末]"
    r"|今天|明天|后天|昨天|上午|下午|晚上|早上|中午|周末|下周|这周|月底|月初"
    r"|\d+(?:块|元|%|个|家|岁|次|天|年)"
)

_COVERAGE = 0.8


@dataclass(frozen=True)
class OmissionCheck:
    flags: tuple[str, ...] = ()
    details: tuple[str, ...] = ()


def _coverage(phrase: str, text: str) -> float:
    chars = set(phrase)
    if not chars:
        return 0.0
    return sum(1 for char in chars if char in text) / len(chars)


def _preserved(phrase: str, text: str) -> bool:
    return phrase in text or _coverage(phrase, text) >= _COVERAGE


def fact_preserved(fact: str, text: str, threshold: float = 0.7) -> bool:
    """Retention metric helper: exact substring or high character coverage.

    Exact substring is too strict for word order/insertions ("换个工作" ->
    "换工作", "上午补觉" -> "上午要补觉"). Coverage catches those; synonyms still
    need an LLM/human judge, so both numbers are reported.
    """
    if fact in text:
        return True
    return _coverage(fact, text) >= threshold


def _is_concrete(text: str) -> bool:
    return bool(_CONCRETE_RE.search(text))


def has_concrete_anchor(text: str) -> bool:
    """True when a kept fact contains a time/date/number/money marker."""
    return _is_concrete(text)


def _concrete_spans(text: str) -> list[str]:
    return list(dict.fromkeys(match.group(0) for match in _CONCRETE_RE.finditer(text)))


def concrete_spans(text: str) -> list[str]:
    """Public accessor used by the Step 4 regression statistics."""
    return _concrete_spans(text)


def check_omissions(
    *,
    raw_reply: str,
    final_text: str,
    kept_facts: tuple[str, ...],
    context_texts: tuple[str, ...],
    lexicon,
) -> OmissionCheck:
    # ``lexicon`` is accepted for interface symmetry; concrete spans are not
    # AI-smell phrases, so no lexicon filtering is needed here.
    del lexicon

    context = "\n".join(context_texts)
    unsafe: list[str] = []
    partial: list[str] = []
    concrete_facts = [fact.strip() for fact in kept_facts if _is_concrete(fact)]

    for fact in concrete_facts:
        cleaned = fact
        if not cleaned:
            continue
        if cleaned in final_text:
            continue
        if any(cleaned[index : index + 2] in final_text for index in range(len(cleaned) - 1)):
            partial.append(cleaned)
            continue
        if _preserved(cleaned, context):
            continue
        unsafe.append(cleaned)

    for span in _concrete_spans(raw_reply):
        # A concrete fact is already judged above; do not report its sub-spans
        # twice (e.g. 周六/下午 inside kept_fact 周六下午).
        if any(span in fact for fact in concrete_facts):
            continue
        if span in final_text:
            continue
        if _preserved(span, context):
            continue
        unsafe.append(span)

    flags: list[str] = []
    if unsafe:
        flags.append("unsafe_omission")
    if partial:
        flags.append("partial_omission")

    details = tuple(
        [f"unsafe_omission: {phrase}" for phrase in unsafe]
        + [f"partial_omission: {phrase}" for phrase in partial]
    )
    return OmissionCheck(flags=tuple(flags), details=details)
