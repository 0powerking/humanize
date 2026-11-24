"""Evaluation-layer aggregation over RunRecord objects.

Reads rhythm metrics from ``rewrite.messages`` and sensitive/residue data from
``govern``; never derives message lengths from ``final_text`` (a joined view would
turn "2 x 10 chars" into "1 x 21 chars").
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from math import ceil, floor

from .omission import fact_preserved
from .schemas import RunRecord

OVER_LONG_CHARS = 35


def percentile(values: list[int], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower, upper = floor(position), ceil(position)
    if lower == upper:
        return float(ordered[int(position)])
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def summarize(records: Iterable[RunRecord]) -> dict:
    records = list(records)
    message_lengths: list[int] = []
    message_counts: list[int] = []
    split_scene_replies = 0
    split_scene_multi = 0
    facts_total = facts_exact = facts_coverage = 0
    unsafe = partial = commitment_runs = commitment_items = 0
    residue_runs = residue_hits = sensitive_runs = blocked_runs = 0
    sensitive_categories: Counter[str] = Counter()
    scenes: Counter[str] = Counter()
    work_expansion = 0
    boundary_compressions: list[float] = []
    split_allowed = {"casual", "banter", "comfort"}

    degraded = cache_hits = 0
    cost = 0.0
    for record in records:
        scene = record.input.context.scene.value
        scenes[scene] += 1
        degraded += bool(record.meta.get("degraded"))
        cache_hits += record.usage.cache_hits
        cost += record.usage.cost_yuan

        message_counts.append(record.rewrite.message_count)
        message_lengths.extend(len(message) for message in record.rewrite.messages)
        if scene in split_allowed:
            split_scene_replies += 1
            split_scene_multi += int(record.rewrite.message_count > 1)

        final_text = record.govern.final_text
        kept = record.rewrite.kept_facts
        facts_total += len(kept)
        facts_exact += sum(1 for fact in kept if fact in final_text)
        facts_coverage += sum(1 for fact in kept if fact_preserved(fact, final_text))

        flags = set(record.rewrite.risk_flags)
        unsafe += int("unsafe_omission" in flags)
        partial += int("partial_omission" in flags)
        commitment_flags = [
            flag
            for flag in record.rewrite.risk_flags
            if flag.startswith("commitment_weakened:")
        ]
        commitment_runs += int(bool(commitment_flags))
        commitment_items += len(commitment_flags)

        if record.govern.residue.hits:
            residue_runs += 1
            residue_hits += len(record.govern.residue.hits)
        if record.govern.sensitive.categories:
            sensitive_runs += 1
            sensitive_categories.update(record.govern.sensitive.categories)
        blocked_runs += int(record.govern.sensitive.blocked)

        raw_length = len(record.input.raw_reply)
        compression = 1 - len(final_text) / raw_length if raw_length else 0.0
        if scene == "work" and compression < -0.3:
            work_expansion += 1
        if scene == "boundary":
            boundary_compressions.append(round(compression, 3))

    return {
        "runs": len(records),
        "degraded_runs": degraded,
        "cache_hits": cache_hits,
        "cost_yuan": round(cost, 6),
        "rhythm": {
            "messages": len(message_lengths),
            "message_count_p50": percentile(message_counts, 0.5),
            "message_count_p95": percentile(message_counts, 0.95),
            "length_p50": percentile(message_lengths, 0.5),
            "length_p95": percentile(message_lengths, 0.95),
            "over_35_ratio": (
                round(sum(1 for length in message_lengths if length > OVER_LONG_CHARS)
                      / len(message_lengths), 4)
                if message_lengths
                else None
            ),
            "split_rate": (
                round(split_scene_multi / split_scene_replies, 4)
                if split_scene_replies
                else None
            ),
        },
        "facts": {
            "total": facts_total,
            "exact": facts_exact,
            "coverage": facts_coverage,
            "exact_rate": round(facts_exact / facts_total, 4) if facts_total else None,
            "coverage_rate": (
                round(facts_coverage / facts_total, 4) if facts_total else None
            ),
        },
        "omission": {
            "unsafe_samples": unsafe,
            "partial_samples": partial,
            "commitment_weakened_runs": commitment_runs,
            "commitment_weakened_items": commitment_items,
        },
        "governance": {
            "residue_runs": residue_runs,
            "residue_hits": residue_hits,
            "sensitive_runs": sensitive_runs,
            "sensitive_categories": dict(sensitive_categories),
            "blocked_runs": blocked_runs,
        },
        "formal": {
            "work_expansion_runs": work_expansion,
            "boundary_compressions": boundary_compressions,
        },
        "scenes": dict(scenes),
    }


def format_markdown(summary: dict) -> str:
    rhythm = summary["rhythm"]
    facts = summary["facts"]
    govern = summary["governance"]
    formal = summary["formal"]
    lines = [
        "## Runs",
        "",
        f"- runs: {summary['runs']}",
        f"- degraded: {summary['degraded_runs']}",
        f"- cache_hits: {summary['cache_hits']}",
        f"- cost: {summary['cost_yuan']} CNY",
        "",
        "## Rhythm (read from rewrite.messages)",
        "",
        f"- messages: {rhythm['messages']}",
        f"- message_count p50/p95: {rhythm['message_count_p50']:.1f}/"
        f"{rhythm['message_count_p95']:.1f}",
        f"- length p50/p95: {rhythm['length_p50']:.1f}/{rhythm['length_p95']:.1f}",
        f"- over 35 chars: {rhythm['over_35_ratio']}",
        f"- split rate (allowed scenes): {rhythm['split_rate']}",
        "",
        "## Facts",
        "",
        f"- exact retention: {facts['exact']}/{facts['total']} = {facts['exact_rate']}",
        f"- coverage retention: {facts['coverage']}/{facts['total']} = {facts['coverage_rate']}",
        "",
        "## Omission / Commitment",
        "",
        f"- unsafe samples: {summary['omission']['unsafe_samples']}",
        f"- partial samples: {summary['omission']['partial_samples']}",
        f"- commitment weakened runs/items: "
        f"{summary['omission']['commitment_weakened_runs']}/"
        f"{summary['omission']['commitment_weakened_items']}",
        "",
        "## Governance",
        "",
        f"- residue runs/hits: {govern['residue_runs']}/{govern['residue_hits']}",
        f"- sensitive runs: {govern['sensitive_runs']}",
        f"- sensitive categories: {govern['sensitive_categories']}",
        f"- blocked runs: {govern['blocked_runs']}",
        "",
        "## Formal scenes",
        "",
        f"- work expansion (< -0.3): {formal['work_expansion_runs']}",
        f"- boundary compressions: {formal['boundary_compressions']}",
        "",
    ]
    return "\n".join(lines)
