from __future__ import annotations

import json
from dataclasses import replace

from humanize.config import PersonaBook, PipelineConfig
from humanize.lexicon import Lexicon
from humanize.llm import LLMError, LLMResult, MockClient
from humanize.pipeline import HumanizePipeline
from humanize.schemas import RunInput


class SelectiveFailClient:
    """Delegates to the mock, but raises for selected prompt ids."""

    name = "mock"

    def __init__(self, inner: MockClient, fail: set[str]) -> None:
        self.inner = inner
        self.fail = fail

    def complete_json(self, *, prompt_id, payload, model, temperature, seed=None) -> LLMResult:
        if prompt_id in self.fail:
            raise LLMError(f"boom: {prompt_id}")
        return self.inner.complete_json(
            prompt_id=prompt_id,
            payload=payload,
            model=model,
            temperature=temperature,
            seed=seed,
        )


class LyingRewriteClient:
    """Reports a rewrite_ratio that does not match the returned text."""

    name = "mock"

    def __init__(self, inner: MockClient) -> None:
        self.inner = inner

    def complete_json(self, *, prompt_id, payload, model, temperature, seed=None) -> LLMResult:
        result = self.inner.complete_json(
            prompt_id=prompt_id,
            payload=payload,
            model=model,
            temperature=temperature,
            seed=seed,
        )
        if prompt_id != "rewrite":
            return result
        data = json.loads(result.text)
        data["rewrite_ratio"] = 0.0
        return replace(result, text=json.dumps(data, ensure_ascii=False))


class SeedCapturingClient:
    """Records the seed forwarded for each stage."""

    name = "mock"

    def __init__(self, inner: MockClient) -> None:
        self.inner = inner
        self.seeds: dict[str, int | None] = {}

    def complete_json(self, *, prompt_id, payload, model, temperature, seed=None) -> LLMResult:
        self.seeds[prompt_id] = seed
        return self.inner.complete_json(
            prompt_id=prompt_id,
            payload=payload,
            model=model,
            temperature=temperature,
            seed=seed,
        )


class MultiMessageClient:
    """Returns two messages plus deliberately wrong counts."""

    name = "mock"

    def __init__(self, inner: MockClient) -> None:
        self.inner = inner

    def complete_json(self, *, prompt_id, payload, model, temperature, seed=None) -> LLMResult:
        result = self.inner.complete_json(
            prompt_id=prompt_id,
            payload=payload,
            model=model,
            temperature=temperature,
            seed=seed,
        )
        if prompt_id != "rewrite":
            return result
        body = json.dumps(
            {
                "messages": ["第一句", "第二句，稍微长一点"],
                "message_count": 9,
                "max_msg_chars": 999,
                "rewrite_ratio": 0.1,
                "change_notes": [],
                "kept_facts": [],
                "risk_flags": [],
                "noop": False,
            },
            ensure_ascii=False,
        )
        return replace(result, text=body)


class LegacyTextClient:
    """Returns the v1 prompt shape until prompt v2 lands."""

    name = "mock"

    def __init__(self, inner: MockClient) -> None:
        self.inner = inner

    def complete_json(self, *, prompt_id, payload, model, temperature, seed=None) -> LLMResult:
        result = self.inner.complete_json(
            prompt_id=prompt_id,
            payload=payload,
            model=model,
            temperature=temperature,
            seed=seed,
        )
        if prompt_id != "rewrite":
            return result
        body = json.dumps(
            {
                "text": "旧格式回复",
                "rewrite_ratio": 0.1,
                "change_notes": [],
                "kept_facts": [],
                "risk_flags": [],
                "noop": False,
            },
            ensure_ascii=False,
        )
        return replace(result, text=body)


class MessagesClient:
    """Arbitrary message list, for governance-boundary tests."""

    name = "mock"

    def __init__(
        self,
        inner: MockClient,
        messages: list[str],
        change_notes: list[dict] | None = None,
        kept_facts: list[str] | None = None,
    ) -> None:
        self.inner = inner
        self.messages = messages
        self.change_notes = change_notes or []
        self.kept_facts = kept_facts or []

    def complete_json(self, *, prompt_id, payload, model, temperature, seed=None) -> LLMResult:
        result = self.inner.complete_json(
            prompt_id=prompt_id,
            payload=payload,
            model=model,
            temperature=temperature,
            seed=seed,
        )
        if prompt_id != "rewrite":
            return result
        body = json.dumps(
            {
                "messages": self.messages,
                "rewrite_ratio": 0.2,
                "change_notes": self.change_notes,
                "kept_facts": self.kept_facts,
                "risk_flags": [],
                "noop": False,
            },
            ensure_ascii=False,
        )
        return replace(result, text=body)


def _pipeline(client, config: PipelineConfig, personas: PersonaBook, lexicon: Lexicon):
    return HumanizePipeline(config=config, personas=personas, lexicon=lexicon, client=client)


def test_route_seeds_are_forwarded(config, personas, lexicon, client, sample) -> None:
    capture = SeedCapturingClient(client)
    record = _pipeline(capture, config, personas, lexicon).run(
        sample("e001_overwork_advice")
    )

    assert capture.seeds["signal.sarcasm"] == 7
    assert capture.seeds["signal.emotion"] == 7
    assert capture.seeds["signal.ai_smell"] == 7
    assert capture.seeds["rewrite"] == 42
    assert record.meta["model_seeds"] == {"signal": 7, "rewrite": 42}


def test_multi_message_governance_merges(config, personas, lexicon, client, sample) -> None:
    record = _pipeline(MultiMessageClient(client), config, personas, lexicon).run(
        sample("e002_sarcasm_late")
    )

    assert record.rewrite.messages == ("第一句", "第二句，稍微长一点")
    assert record.rewrite.message_count == 2
    assert record.rewrite.max_msg_chars == 9
    assert record.govern.final_text == "第一句\n第二句，稍微长一点"
    assert any("merged into final_text" in d for d in record.govern.audit.decisions)


def test_legacy_text_shape_is_wrapped(config, personas, lexicon, client, sample) -> None:
    record = _pipeline(LegacyTextClient(client), config, personas, lexicon).run(
        sample("e002_sarcasm_late")
    )

    assert record.rewrite.messages == ("旧格式回复",)
    assert record.rewrite.message_count == 1
    assert record.rewrite.text == "旧格式回复"


def test_residue_does_not_cross_message_boundaries(
    config, personas, lexicon, client, sample
) -> None:
    record = _pipeline(
        MessagesClient(client, ["这不是钱的问题", "而是信任的问题"]),
        config,
        personas,
        lexicon,
    ).run(sample("e002_sarcasm_late"))

    assert record.govern.residue.hits == ()


def test_residue_scans_each_message_without_newline_spans(
    config, personas, lexicon, client, sample
) -> None:
    record = _pipeline(
        MessagesClient(client, ["首先，你要冷静。", "总之，别急。"]),
        config,
        personas,
        lexicon,
    ).run(sample("e002_sarcasm_late"))

    types = {hit.type.value for hit in record.govern.residue.hits}
    assert "enumeration" in types
    assert "hedge" in types
    assert all("\n" not in hit.span for hit in record.govern.residue.hits)


def test_non_split_scene_is_merged_to_one_message(
    config, personas, lexicon, client, sample
) -> None:
    base = sample("e002_sarcasm_late")
    boundary = RunInput.model_validate(
        {
            **base.model_dump(),
            "sample_id": "boundary_merge",
            "context": {
                "scene": "boundary",
                "turns": [{"role": "user", "content": "你能帮我改论文吗"}],
            },
        }
    )
    record = _pipeline(
        MessagesClient(client, ["这次时间太紧，帮不了。", "你找导师问问看？"]),
        config,
        personas,
        lexicon,
    ).run(boundary)

    assert record.rewrite.messages == ("这次时间太紧，帮不了。你找导师问问看？",)
    assert record.rewrite.message_count == 1
    assert "merged_outside_split_scene" in record.rewrite.risk_flags


def test_trivial_edit_on_gated_sample_is_forced_noop(
    config, personas, lexicon, client, sample
) -> None:
    record = _pipeline(
        MessagesClient(
            client,
            ["周六下午吧，上午得补觉"],
            change_notes=[{"op": "把要改成得", "reason": "tone", "signal_ref": None}],
        ),
        config,
        personas,
        lexicon,
    ).run(sample("e004_natural_noop"))

    assert record.rewrite.noop is True
    assert record.rewrite.messages == (record.input.raw_reply,)
    assert record.rewrite.rewrite_ratio == 0.0
    assert "forced_noop_trivial_edit" in record.rewrite.risk_flags
    assert record.rewrite.change_notes[0].suppressed is True
    assert record.rewrite.change_notes[0].suppressed_by == "forced_noop_trivial_edit"


def test_unsafe_omission_flows_to_risk_flags(config, personas, lexicon, client, sample) -> None:
    record = _pipeline(
        MessagesClient(client, ["改天吧"], kept_facts=["周六下午"]),
        config,
        personas,
        lexicon,
    ).run(sample("e004_natural_noop"))

    assert "unsafe_omission" in record.rewrite.risk_flags
    assert any(
        "unsafe_omission: 周六下午" in decision
        for decision in record.govern.audit.decisions
    )


def test_abstract_kept_facts_are_demoted(config, personas, lexicon, client, sample) -> None:
    record = _pipeline(
        MessagesClient(
            client,
            ["换个工作也不算啥坏事"],
            kept_facts=["评估核心诉求", "换个工作"],
        ),
        config,
        personas,
        lexicon,
    ).run(sample("e001_overwork_advice"))

    assert record.rewrite.kept_facts == ("换个工作",)
    assert record.rewrite.semantic_points == ("评估核心诉求",)
    assert "kept_facts_demoted" in record.rewrite.risk_flags
    assert any(
        "semantic_points" in decision for decision in record.govern.audit.decisions
    )


def test_boundary_does_not_run_omission_rule(config, personas, lexicon, client, sample) -> None:
    base = sample("e002_sarcasm_late")
    boundary = RunInput.model_validate(
        {
            **base.model_dump(),
            "sample_id": "boundary_omission",
            "context": {
                "scene": "boundary",
                "turns": [{"role": "user", "content": "周六下午能帮我改论文吗"}],
            },
        }
    )
    record = _pipeline(
        MessagesClient(client, ["改天吧"], kept_facts=["周六下午"]),
        config,
        personas,
        lexicon,
    ).run(boundary)

    # Prompt forbids omission in formal scenes; the rule is intentionally not
    # applied, and their omission rate is tracked separately in Step 4.
    assert "unsafe_omission" not in record.rewrite.risk_flags
    assert any("omission: clean" in d for d in record.govern.audit.decisions)


def test_run_signals_returns_usage_without_rewrite(pipeline, sample) -> None:
    signals, usage = pipeline.run_signals(sample("e001_overwork_advice"))

    assert signals.persona_state.persona_id == "p_sunyou"
    assert usage.calls == 3  # sarcasm + emotion + ai_smell, no rewrite
    assert usage.cache_hits == 0


def test_vote_runs_three_queries_each_signal(
    config, personas, lexicon, client, sample
) -> None:
    voted = config.model_copy(deep=True)
    voted.signals = voted.signals.model_copy(update={"vote_runs": 3})
    signals, usage = _pipeline(client, voted, personas, lexicon).run_signals(
        sample("e001_overwork_advice")
    )

    assert usage.calls == 9  # 3 modules x 3 samples
    assert usage.cache_hits == 0
    assert signals.meta.vote_runs == 3


def test_rewrite_removes_enumeration(pipeline, sample) -> None:
    record = pipeline.run(sample("e001_overwork_advice"))

    assert record.signals.ai_smell.score == 1.0
    assert record.rewrite.noop is False
    assert record.rewrite.text != record.input.raw_reply
    assert "首先" not in record.rewrite.text
    assert "总之" not in record.rewrite.text
    assert record.rewrite.rewrite_ratio > 0.3
    assert any(
        note.signal_ref and note.signal_ref.startswith("ai_smell.hits[")
        for note in record.rewrite.change_notes
    )


def test_gate_allows_noop_on_natural_reply(pipeline, sample) -> None:
    record = pipeline.run(sample("e004_natural_noop"))

    assert record.signals.ai_smell.score == 0.0
    assert record.rewrite.noop is True
    assert record.rewrite.rewrite_ratio == 0.0
    assert record.rewrite.text == record.input.raw_reply
    assert record.govern.final_text == record.input.raw_reply


def test_sarcasm_and_emotion_signals(pipeline, sample) -> None:
    record = pipeline.run(sample("e002_sarcasm_late"))

    assert record.signals.sarcasm.label.value == "sarcastic"
    assert record.signals.sarcasm.target.value == "assistant"
    assert record.signals.emotion.user_emotion.label == "不满"
    assert record.signals.emotion.assistant_tone_target.value == "banter"
    assert record.signals.persona_state.persona_id == "p_sunyou"


def test_govern_flags_forbidden_persona_word(pipeline, sample) -> None:
    base = sample("e004_natural_noop")
    inp = RunInput.model_validate(
        {
            **base.model_dump(),
            "sample_id": "e_vocab",
            "raw_reply": "宝子，周六下午吧。",
        }
    )
    record = pipeline.run(inp)

    rule_ids = {hit.rule_id for hit in record.govern.residue.hits}
    assert "vocab:宝子" in rule_ids
    # phase 1 never rewrites locally; the hit is recorded and the text is emitted.
    assert record.govern.final_text == record.rewrite.text


def test_sensitive_stub_flags_contact_info(pipeline, sample) -> None:
    base = sample("e004_natural_noop")
    inp = RunInput.model_validate(
        {
            **base.model_dump(),
            "sample_id": "e_phone",
            "raw_reply": "我手机13800138000，加微信聊。",
        }
    )
    record = pipeline.run(inp)

    assert "contact_info" in record.govern.sensitive.categories
    assert "keyword" in record.govern.sensitive.categories
    assert record.govern.sensitive.blocked is False
    assert record.govern.sensitive.action == "pass"


def test_signal_failure_degrades_without_crashing(
    config: PipelineConfig,
    personas: PersonaBook,
    lexicon: Lexicon,
    client: MockClient,
    sample,
) -> None:
    failing = SelectiveFailClient(client, {"signal.sarcasm"})
    record = _pipeline(failing, config, personas, lexicon).run(
        sample("e001_overwork_advice")
    )

    assert "sarcasm" in record.signals.meta.degraded
    assert record.signals.sarcasm.label.value == "uncertain"
    assert record.rewrite.text  # the run still completed


def test_rewrite_failure_falls_back_to_raw(
    config: PipelineConfig,
    personas: PersonaBook,
    lexicon: Lexicon,
    client: MockClient,
    sample,
) -> None:
    failing = SelectiveFailClient(client, {"rewrite"})
    record = _pipeline(failing, config, personas, lexicon).run(
        sample("e001_overwork_advice")
    )

    assert record.rewrite.noop is True
    assert "rewrite_failed" in record.rewrite.risk_flags
    assert record.rewrite.text == record.input.raw_reply
    assert "rewrite" in record.meta["degraded"]
    assert any("degraded: rewrite" in d for d in record.govern.audit.decisions)


def test_ratio_is_recomputed_not_trusted(
    config: PipelineConfig,
    personas: PersonaBook,
    lexicon: Lexicon,
    client: MockClient,
    sample,
) -> None:
    record = _pipeline(LyingRewriteClient(client), config, personas, lexicon).run(
        sample("e002_sarcasm_late")
    )

    assert record.rewrite.rewrite_ratio > 0.3
    assert "ratio_self_report_mismatch" in record.rewrite.risk_flags


def test_audit_is_consistent_with_record(pipeline, sample) -> None:
    record = pipeline.run(sample("e001_overwork_advice"))

    assert record.govern.audit.config_hash == record.versions.config_hash
    assert record.govern.audit.trace_id == record.run_id
    assert len(record.govern.audit.decisions) >= 4
    assert record.meta["signal_fingerprint"]


def test_mock_is_deterministic(pipeline, sample) -> None:
    inp = sample("e003_comfort_interview")
    first = pipeline.run(inp)
    second = pipeline.run(inp)

    assert first.rewrite.text == second.rewrite.text
    assert first.rewrite.rewrite_ratio == second.rewrite.rewrite_ratio
    assert first.signals.ai_smell == second.signals.ai_smell
    assert first.signals.emotion == second.signals.emotion


def test_comfort_reply_drops_cheerleading(pipeline, sample) -> None:
    record = pipeline.run(sample("e003_comfort_interview"))

    assert record.rewrite.noop is False
    assert "首先" not in record.rewrite.text
    assert "你一定可以" not in record.rewrite.text
    assert record.signals.emotion.assistant_tone_target.value == "soothe"
