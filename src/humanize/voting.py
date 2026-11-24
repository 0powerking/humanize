"""Aggregate repeated signal samples: labels by majority, numbers by median.

Used only when ``signals.vote_runs > 1`` (evaluation runs). Runtime defaults to a
single sample; the rewrite layer is expected to tolerate a little signal noise,
and the risk is recorded in ADR-008 rather than hidden behind 3x cost.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Hashable, Sequence
from statistics import median
from typing import TypeVar

from .schemas import (
    AiSmellSignal,
    EmotionLabel,
    EmotionSignal,
    SarcasmLabel,
    SarcasmSignal,
    SarcasmTarget,
    ToneTarget,
)

T = TypeVar("T")


def _median(values: Sequence[float]) -> float:
    return round(float(median(values)), 3) if values else 0.0


def _majority(items: Sequence[T], key: Callable[[T], Hashable]) -> T | None:
    counts = Counter(key(item) for item in items)
    top, count = counts.most_common(1)[0]
    if count == 1 and len(items) > 1:
        return None  # no majority at all
    return next(item for item in items if key(item) == top)


def vote_sarcasm(items: Sequence[SarcasmSignal]) -> SarcasmSignal:
    chosen = _majority(items, lambda signal: signal.label)
    if chosen is None:
        return SarcasmSignal(
            label=SarcasmLabel.uncertain,
            confidence=0.0,
            target=SarcasmTarget.none,
            reason=f"vote tie across {len(items)} samples",
        )

    same = [signal for signal in items if signal.label == chosen.label]
    target = _majority(same, lambda signal: signal.target) or chosen
    return chosen.model_copy(
        update={
            "confidence": _median([signal.confidence for signal in same]),
            "target": target.target,
            "reason": f"{chosen.reason} (vote {len(same)}/{len(items)})",
        }
    )


def vote_emotion(items: Sequence[EmotionSignal]) -> EmotionSignal:
    labels = Counter(item.user_emotion.label for item in items)
    label, count = labels.most_common(1)[0]
    if count == 1 and len(items) > 1:
        label = "unknown"
        same = list(items)
    else:
        same = [item for item in items if item.user_emotion.label == label]

    tone = _majority(same, lambda item: item.assistant_tone_target)
    return EmotionSignal(
        user_emotion=EmotionLabel(
            label=label,
            intensity=0.0 if label == "unknown" else _median(
                [item.user_emotion.intensity for item in same]
            ),
        ),
        valence=_median([item.valence for item in same]),
        arousal=_median([item.arousal for item in same]),
        assistant_tone_target=(
            tone.assistant_tone_target if tone else ToneTarget.matter_of_fact
        ),
        cues=same[0].cues,
    )


def vote_ai_smell(items: Sequence[AiSmellSignal]) -> AiSmellSignal:
    def type_key(signal: AiSmellSignal) -> tuple[str, ...]:
        return tuple(sorted({hit.type.value for hit in signal.hits}))

    counts = Counter(type_key(signal) for signal in items)
    top, count = counts.most_common(1)[0]
    if count == 1 and len(items) > 1:
        # No majority: take the most conservative sample (fewest hits) so a rare
        # extra type cannot inflate the score.
        chosen = min(items, key=lambda signal: (len(signal.hits), signal.score))
        suffix = "vote tie; chose fewest hits"
    else:
        chosen = next(signal for signal in items if type_key(signal) == top)
        suffix = f"vote {count}/{len(items)}"

    notes = " | ".join(part for part in (chosen.notes, suffix) if part)
    return chosen.model_copy(update={"notes": notes})
