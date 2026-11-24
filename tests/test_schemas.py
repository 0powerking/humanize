from __future__ import annotations

import pytest
from pydantic import ValidationError

from humanize.schemas import RewriteOutput, RunInput, RunRecord


def test_seed_inputs_validate(seed_inputs: list[RunInput]) -> None:
    assert len(seed_inputs) == 4
    assert len({item.sample_id for item in seed_inputs}) == len(seed_inputs)
    assert all(item.source == "synthetic" for item in seed_inputs)


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RunInput.model_validate(
            {
                "sample_id": "x",
                "context": {"turns": [{"role": "user", "content": "hi"}]},
                "raw_reply": "hi",
                "persona": {"persona_id": "p_sunyou"},
                "typo_field": 1,
            }
        )


def test_noop_requires_zero_ratio() -> None:
    with pytest.raises(ValidationError):
        RewriteOutput(text="x", rewrite_ratio=0.3, noop=True)


def test_signal_bundle_is_immutable(pipeline, sample) -> None:
    record = pipeline.run(sample("e001_overwork_advice"))
    with pytest.raises(ValidationError):
        record.signals.ai_smell.score = 0.0  # type: ignore[misc]
    with pytest.raises(ValidationError):
        record.signals.sarcasm.label = "literal"  # type: ignore[misc]


def test_record_json_roundtrip(pipeline, sample) -> None:
    record = pipeline.run(sample("e002_sarcasm_late"))
    restored = RunRecord.model_validate_json(record.model_dump_json())
    assert restored.model_dump() == record.model_dump()


def test_final_text_matches_rewrite(pipeline, sample) -> None:
    record = pipeline.run(sample("e001_overwork_advice"))
    assert record.govern.final_text == record.rewrite.text
