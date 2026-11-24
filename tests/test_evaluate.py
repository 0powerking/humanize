from __future__ import annotations

from humanize.evaluate import format_markdown, percentile, summarize
from humanize.schemas import RunInput


def test_percentile() -> None:
    assert percentile([], 0.5) == 0.0
    assert percentile([10], 0.5) == 10.0
    assert percentile([10, 20, 30, 40], 0.5) == 25.0


def test_summary_reads_messages_for_rhythm(pipeline, seed_inputs) -> None:
    records = [pipeline.run(item) for item in seed_inputs]
    summary = summarize(records)

    assert summary["runs"] == 4
    expected_messages = sum(record.rewrite.message_count for record in records)
    assert summary["rhythm"]["messages"] == expected_messages
    assert summary["rhythm"]["length_p50"] > 0
    assert summary["governance"]["sensitive_runs"] == 0


def test_summary_counts_sensitive_from_govern(pipeline, sample) -> None:
    base = sample("e004_natural_noop")
    with_phone = RunInput.model_validate(
        {
            **base.model_dump(),
            "sample_id": "sensitive_case",
            "raw_reply": "我手机13800138000，加微信聊",
        }
    )
    summary = summarize([pipeline.run(with_phone)])

    assert summary["governance"]["sensitive_runs"] == 1
    assert summary["governance"]["sensitive_categories"] == {"contact_info": 1, "keyword": 1}


def test_markdown_contains_sections(pipeline, seed_inputs) -> None:
    summary = summarize([pipeline.run(seed_inputs[0])])
    text = format_markdown(summary)

    assert "## Rhythm (read from rewrite.messages)" in text
    assert "## Governance" in text
