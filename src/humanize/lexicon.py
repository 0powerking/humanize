"""Shared rule lexicon for AI-smell detection.

One dictionary, three consumers, so the pipeline never disagrees with itself:
  * mock signal layer  -> produces ai_smell.hits (placeholder until Day 3)
  * rewrite prompt     -> injects "expressions to remove"
  * governance layer    -> detects residue in the rewritten text

Scoring: ``score = min(1.0, sum(severity) / 8)``. Severity 1 means "also common
in human speech", 3 means "strong tell". This is v1 and expected to over-fire;
tune it against eval-set hits, not by intuition.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .schemas import AiSmellType

_SEVERITY_TOTAL = 8


class LexiconError(RuntimeError):
    pass


@dataclass(frozen=True)
class LexiconEntry:
    id: str
    type: AiSmellType
    severity: int
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class Finding:
    rule_id: str
    type: AiSmellType
    severity: int
    span: str
    start: int
    end: int


@dataclass(frozen=True)
class Lexicon:
    version: str
    entries: tuple[LexiconEntry, ...]

    def scan(self, text: str) -> list[Finding]:
        """Return non-overlapping findings, earliest match first.

        Overlaps are resolved by taking the leftmost-longest match, so a sentence
        that starts with a hedge is not double-counted as hedge + enumeration.
        """
        candidates: list[tuple[int, int, LexiconEntry]] = []
        for entry in self.entries:
            for match in entry.pattern.finditer(text):
                if match.end() > match.start():
                    candidates.append((match.start(), match.end(), entry))
        candidates.sort(key=lambda item: (item[0], -(item[1] - item[0])))

        findings: list[Finding] = []
        cursor = 0
        for start, end, entry in candidates:
            if start < cursor:
                continue
            span = text[start:end].strip()
            if not span:
                continue
            findings.append(
                Finding(
                    rule_id=entry.id,
                    type=entry.type,
                    severity=entry.severity,
                    span=span,
                    start=start,
                    end=end,
                )
            )
            cursor = end
        return findings

    def score(self, findings: list[Finding]) -> float:
        return score_severities(finding.severity for finding in findings)

    def to_payload(self) -> dict[str, Any]:
        """JSON-safe form, injected into prompts and used by the mock client."""
        return {
            "version": self.version,
            "patterns": [
                {
                    "id": entry.id,
                    "type": entry.type.value,
                    "severity": entry.severity,
                    "regex": entry.pattern.pattern,
                }
                for entry in self.entries
            ],
        }


def score_severities(severities: Iterable[int]) -> float:
    """Single scoring function for rule hits and LLM extras alike."""
    return min(1.0, sum(severities) / _SEVERITY_TOTAL)


def load_lexicon(path: str | Path) -> Lexicon:
    p = Path(path)
    if not p.exists():
        raise LexiconError(f"lexicon file not found: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("patterns"), list):
        raise LexiconError(f"lexicon must have a 'patterns' list: {p}")

    entries: list[LexiconEntry] = []
    seen: set[str] = set()
    for item in raw["patterns"]:
        try:
            rule_id = str(item["id"])
            entry_type = AiSmellType(item["type"])
            severity = int(item["severity"])
            regex = str(item["regex"])
        except (KeyError, TypeError, ValueError) as exc:
            raise LexiconError(f"bad lexicon entry {item!r}: {exc}") from exc
        if rule_id in seen:
            raise LexiconError(f"duplicate lexicon rule id: {rule_id}")
        if not 1 <= severity <= 3:
            raise LexiconError(f"{rule_id}: severity must be 1..3, got {severity}")
        try:
            compiled = re.compile(regex)
        except re.error as exc:
            raise LexiconError(f"{rule_id}: invalid regex {regex!r}: {exc}") from exc
        seen.add(rule_id)
        entries.append(
            LexiconEntry(id=rule_id, type=entry_type, severity=severity, pattern=compiled)
        )

    return Lexicon(version=str(raw.get("version", "unknown")), entries=tuple(entries))


def lexicon_from_payload(payload: dict[str, Any]) -> Lexicon:
    """Rebuild a Lexicon from ``Lexicon.to_payload()`` (used by the mock client)."""
    entries = tuple(
        LexiconEntry(
            id=str(item["id"]),
            type=AiSmellType(item["type"]),
            severity=int(item["severity"]),
            pattern=re.compile(str(item["regex"])),
        )
        for item in payload.get("patterns", [])
    )
    return Lexicon(version=str(payload.get("version", "unknown")), entries=entries)
