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
        RewriteOutput(messages=("x",), rewrite_ratio=0.3, noop=True)


def test_legacy_text_is_wrapped_into_messages() -> None:
    output = RewriteOutput.model_validate({"text": "旧格式回复", "rewrite_ratio": 0.2})

    assert output.messages == ("旧格式回复",)
    assert output.message_count == 1
    assert output.max_msg_chars == len("旧格式回复")


def test_message_counts_are_recomputed_not_trusted() -> None:
    output = RewriteOutput(
        messages=("短", "稍微长一点"),
        message_count=9,
        max_msg_chars=999,
        rewrite_ratio=0.2,
    )

    assert output.message_count == 2
    assert output.max_msg_chars == len("稍微长一点")


def test_messages_are_limited_to_four() -> None:
    with pytest.raises(ValidationError):
        RewriteOutput(
            messages=("一", "二", "三", "四", "五"),
            rewrite_ratio=0.1,
        )


def test_noop_must_be_a_single_message() -> None:
    with pytest.raises(ValidationError):
        RewriteOutput(messages=("一", "二"), rewrite_ratio=0.0, noop=True)


def test_text_property_joins_messages() -> None:
    output = RewriteOutput(messages=("第一句", "第二句"), rewrite_ratio=0.2)

    assert output.text == "第一句\n第二句"


def test_text_property_survives_replay_without_text_field() -> None:
    # Replaying a run record has no `text` key; the property must still work.
    output = RewriteOutput.model_validate(
        {"messages": ["加载", "后仍可读"], "rewrite_ratio": 0.1}
    )

    assert "text" not in output.model_dump()
    assert output.text == "加载\n后仍可读"
    assert output.semantic_points == ()


@pytest.mark.skip(reason="compat shim stays until Step 4; unskip when it is removed")
def test_legacy_text_shape_is_rejected_after_shim_removal() -> None:
    with pytest.raises(ValidationError):
        RewriteOutput.model_validate({"text": "旧格式", "rewrite_ratio": 0.1})


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
