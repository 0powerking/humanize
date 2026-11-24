from __future__ import annotations

from humanize.schemas import (
    AiSmellHit,
    AiSmellSignal,
    AiSmellType,
    EmotionLabel,
    EmotionSignal,
    SarcasmLabel,
    SarcasmSignal,
    SarcasmTarget,
    ToneTarget,
)
from humanize.voting import vote_ai_smell, vote_emotion, vote_sarcasm


def _sarcasm(
    label: SarcasmLabel, confidence: float = 0.8
) -> SarcasmSignal:
    return SarcasmSignal(
        label=label,
        confidence=confidence,
        target=SarcasmTarget.assistant,
        reason="sample",
    )


def _emotion(
    label: str, tone: ToneTarget, valence: float, arousal: float, intensity: float
) -> EmotionSignal:
    return EmotionSignal(
        user_emotion=EmotionLabel(label=label, intensity=intensity),
        valence=valence,
        arousal=arousal,
        assistant_tone_target=tone,
    )


def _smell(types: list[str], score: float = 0.5) -> AiSmellSignal:
    hits = tuple(
        AiSmellHit(type=AiSmellType(item), span="x", severity=1) for item in types
    )
    return AiSmellSignal(score=score, hits=hits, notes="sample")


def test_sarcasm_label_majority_and_median_confidence() -> None:
    result = vote_sarcasm(
        [
            _sarcasm(SarcasmLabel.literal, 0.6),
            _sarcasm(SarcasmLabel.literal, 0.7),
            _sarcasm(SarcasmLabel.sarcastic, 0.9),
        ]
    )

    assert result.label == SarcasmLabel.literal
    assert result.confidence == 0.65
    assert "vote 2/3" in result.reason


def test_sarcasm_full_tie_becomes_uncertain() -> None:
    result = vote_sarcasm(
        [
            _sarcasm(SarcasmLabel.literal),
            _sarcasm(SarcasmLabel.sarcastic),
            _sarcasm(SarcasmLabel.uncertain),
        ]
    )

    assert result.label == SarcasmLabel.uncertain
    assert result.confidence == 0.0
    assert "vote tie" in result.reason


def test_emotion_label_majority_and_median_values() -> None:
    result = vote_emotion(
        [
            _emotion("不满", ToneTarget.banter, -0.6, 0.6, 0.7),
            _emotion("不满", ToneTarget.banter, -0.5, 0.7, 0.8),
            _emotion("平静", ToneTarget.matter_of_fact, 0.0, 0.3, 0.3),
        ]
    )

    assert result.user_emotion.label == "不满"
    assert result.assistant_tone_target == ToneTarget.banter
    assert result.valence == -0.55
    assert result.arousal == 0.65
    assert result.user_emotion.intensity == 0.75


def test_emotion_full_tie_falls_back_to_unknown() -> None:
    result = vote_emotion(
        [
            _emotion("不满", ToneTarget.banter, -0.6, 0.6, 0.7),
            _emotion("平静", ToneTarget.matter_of_fact, 0.0, 0.3, 0.3),
            _emotion("失落", ToneTarget.soothe, -0.4, 0.4, 0.8),
        ]
    )

    assert result.user_emotion.label == "unknown"
    assert result.user_emotion.intensity == 0.0
    assert result.assistant_tone_target == ToneTarget.matter_of_fact
    assert result.valence == -0.4  # median over all samples


def test_ai_smell_majority_type_set() -> None:
    result = vote_ai_smell([_smell(["hedge"]), _smell(["hedge"]), _smell(["enumeration"])])

    assert {hit.type.value for hit in result.hits} == {"hedge"}
    assert "vote 2/3" in result.notes


def test_ai_smell_full_tie_takes_fewest_hits() -> None:
    result = vote_ai_smell(
        [_smell(["hedge"]), _smell(["enumeration"]), _smell(["hedge", "enumeration"])]
    )

    assert len(result.hits) == 1
    assert "vote tie" in result.notes
