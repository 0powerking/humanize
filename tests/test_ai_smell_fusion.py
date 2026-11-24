from __future__ import annotations

import json
from dataclasses import replace

from humanize.config import PersonaBook, PipelineConfig
from humanize.lexicon import Lexicon
from humanize.llm import LLMError, LLMResult, MockClient
from humanize.pipeline import HumanizePipeline
from humanize.schemas import RunInput


class ExtraClient:
    """Mock client that returns a canned `extra_hits` list for ai_smell."""

    name = "mock"

    def __init__(
        self,
        inner: MockClient,
        extra_hits: list[dict],
        fail_ai_smell: bool = False,
    ) -> None:
        self.inner = inner
        self.extra_hits = extra_hits
        self.fail_ai_smell = fail_ai_smell

    def complete_json(self, *, prompt_id, payload, model, temperature, seed=None) -> LLMResult:
        if prompt_id == "signal.ai_smell" and self.fail_ai_smell:
            raise LLMError("ai_smell provider down")
        result = self.inner.complete_json(
            prompt_id=prompt_id,
            payload=payload,
            model=model,
            temperature=temperature,
            seed=seed,
        )
        if prompt_id != "signal.ai_smell":
            return result
        body = json.dumps(
            {"extra_hits": self.extra_hits, "notes": "test"}, ensure_ascii=False
        )
        return replace(result, text=body)


def _input(raw: str) -> RunInput:
    return RunInput(
        sample_id="fusion_case",
        context={"scene": "casual", "turns": [{"role": "user", "content": "在吗"}]},
        raw_reply=raw,
        persona={"persona_id": "p_sunyou", "version": "1.0"},
    )


def _run(
    config: PipelineConfig,
    personas: PersonaBook,
    lexicon: Lexicon,
    raw: str,
    extra_hits: list[dict],
    fail: bool = False,
):
    client = ExtraClient(MockClient(lexicon=lexicon), extra_hits, fail_ai_smell=fail)
    pipeline = HumanizePipeline(
        config=config, personas=personas, lexicon=lexicon, client=client
    )
    return pipeline.run(_input(raw))


def test_extra_hit_is_added_and_score_is_recomputed(config, personas, lexicon) -> None:
    raw = "这个问题的关键在于，我们需要从多个维度去理解它。"
    record = _run(
        config,
        personas,
        lexicon,
        raw,
        [{"type": "over_explain", "span": "关键在于", "severity": 2}],
    )

    hits = record.signals.ai_smell.hits
    assert [(hit.type.value, hit.span) for hit in hits] == [("over_explain", "关键在于")]
    # 2 / 8, recomputed locally; the model never reports a score.
    assert record.signals.ai_smell.score == 0.25


def test_same_span_rule_wins_and_alt_type_is_recorded(config, personas, lexicon) -> None:
    raw = "确实，你说得有道理。"
    record = _run(
        config,
        personas,
        lexicon,
        raw,
        [{"type": "over_explain", "span": "确实", "severity": 3}],
    )

    hits = record.signals.ai_smell.hits
    assert len(hits) == 1
    assert hits[0].type.value == "hedge"
    assert hits[0].span == "确实"
    assert "alt_type" in record.signals.ai_smell.notes
    assert "over_explain" in record.signals.ai_smell.notes
    assert record.signals.ai_smell.score == 0.125


def test_overlapping_but_different_span_keeps_rule_hit(config, personas, lexicon) -> None:
    raw = "确实，你说得有道理。"
    record = _run(
        config,
        personas,
        lexicon,
        raw,
        [{"type": "over_explain", "span": "确实，你说得", "severity": 2}],
    )

    assert len(record.signals.ai_smell.hits) == 1
    assert "overlap_skipped" in record.signals.ai_smell.notes


def test_hallucinated_span_is_dropped(config, personas, lexicon) -> None:
    raw = "周六下午吧，上午要补觉。"
    record = _run(
        config,
        personas,
        lexicon,
        raw,
        [{"type": "golden_quote", "span": "不存在的句子", "severity": 2}],
    )

    assert record.signals.ai_smell.hits == ()
    assert record.signals.ai_smell.score == 0.0
    assert "dropped_invalid_spans=1" in record.signals.ai_smell.notes


def test_llm_failure_falls_back_to_rules(config, personas, lexicon) -> None:
    raw = "首先，你要冷静。其次，分析原因。"
    record = _run(config, personas, lexicon, raw, [], fail=True)

    types = {hit.type.value for hit in record.signals.ai_smell.hits}
    assert types == {"enumeration"}
    assert record.signals.ai_smell.score == 0.5  # 2 + 2, severity / 8
    assert "ai_smell_llm" in record.signals.meta.degraded
    assert record.govern.final_text  # the run still completed
