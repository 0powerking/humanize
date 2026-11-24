"""Rule + LLM fusion for AI-smell detection (ADR-009).

Rules are the anchor: they are reproducible, explainable, zero-cost, and already
cover every expression with a surface marker. The LLM is asked only for semantic
hits the rules missed (``extra_hits``), so a model regression cannot silently
change the rule-calibrated score.

Conflict policy:
  * same span, different type -> rule wins; the LLM type is recorded in ``notes``
    as ``alt_type`` so no information is lost.
  * overlapping but different span -> rule wins; recorded as ``overlap_skipped``.
  * span not present in the reply -> dropped (hallucination guard).

The score is always recomputed from the merged hits; the model's self-reported
score is never trusted (same principle as rewrite ratio, ADR-005).
"""

from __future__ import annotations

from typing import Any

from .lexicon import Finding, score_severities
from .schemas import AiSmellHit, AiSmellSignal


def rule_hits_payload(findings: list[Finding]) -> list[dict[str, Any]]:
    """JSON-safe rule findings, given to the LLM so it does not repeat them."""
    return [
        {"type": finding.type.value, "span": finding.span, "severity": finding.severity}
        for finding in findings
    ]


def merge_ai_smell(
    raw_reply: str,
    rule_findings: list[Finding],
    extra_hits: tuple[AiSmellHit, ...],
    model_notes: str = "",
) -> AiSmellSignal:
    merged: list[AiSmellHit] = []
    occupied: list[tuple[int, int, AiSmellHit]] = []

    for finding in rule_findings:
        hit = AiSmellHit(type=finding.type, span=finding.span, severity=finding.severity)
        merged.append(hit)
        occupied.append((finding.start, finding.end, hit))

    notes: list[str] = [model_notes.strip()] if model_notes.strip() else []
    dropped = 0
    conflicts = 0
    overlaps = 0

    for extra in extra_hits:
        span = extra.span.strip()
        if not span or span not in raw_reply:
            dropped += 1
            continue

        start = raw_reply.find(span)
        end = start + len(span)
        clash = next(
            ((s, e, hit) for s, e, hit in occupied if start < e and s < end),
            None,
        )
        if clash is not None:
            clash_start, clash_end, existing = clash
            if (start, end) == (clash_start, clash_end):
                if extra.type != existing.type:
                    notes.append(
                        f"alt_type: span={span!r} rule={existing.type.value} "
                        f"llm={extra.type.value}"
                    )
                    conflicts += 1
                # same span + same type: plain duplicate, nothing to record
            else:
                notes.append(f"overlap_skipped: span={span!r} rule={existing.type.value}")
                overlaps += 1
            continue

        hit = AiSmellHit(type=extra.type, span=span, severity=extra.severity)
        merged.append(hit)
        occupied.append((start, end, hit))

    if dropped:
        notes.append(f"dropped_invalid_spans={dropped}")
    if conflicts:
        notes.append(f"alt_type_count={conflicts}")
    if overlaps:
        notes.append(f"overlap_skipped_count={overlaps}")

    score = round(score_severities(hit.severity for hit in merged), 3)
    return AiSmellSignal(score=score, hits=tuple(merged), notes=" | ".join(notes))
