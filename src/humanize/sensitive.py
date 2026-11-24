"""Sensitive-content stub for phase 1.

Deliberately small and replaceable: phone numbers, URLs and a config keyword list.
It scans the merged **final_text** (cross-message concatenation can be sensitive),
while rhythm metrics read `messages`; those two views must not be mixed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .schemas import SensitiveResult

_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_URL = re.compile(r"(?:https?://|www\.)\S+")


@dataclass(frozen=True)
class SensitiveFinding:
    category: str
    rule: str
    span: str


class SensitiveDetector:
    def __init__(self, keywords: tuple[str, ...], block_on_hit: bool = False) -> None:
        self.keywords = tuple(word for word in keywords if word)
        self.block_on_hit = block_on_hit

    def scan(self, text: str) -> list[SensitiveFinding]:
        findings: list[SensitiveFinding] = []
        for match in _PHONE.finditer(text):
            findings.append(
                SensitiveFinding(category="contact_info", rule="phone", span=match.group(0))
            )
        for match in _URL.finditer(text):
            findings.append(
                SensitiveFinding(category="contact_info", rule="url", span=match.group(0))
            )
        for keyword in self.keywords:
            if keyword in text:
                findings.append(
                    SensitiveFinding(category="keyword", rule=f"kw:{keyword}", span=keyword)
                )
        return findings

    def result(self, text: str) -> SensitiveResult:
        findings = self.scan(text)
        categories = tuple(dict.fromkeys(finding.category for finding in findings))
        blocked = bool(findings) and self.block_on_hit
        return SensitiveResult(
            blocked=blocked,
            categories=categories,
            action="block" if blocked else "pass",
        )
