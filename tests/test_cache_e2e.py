"""End-to-end cache acceptance: a changed signal must invalidate the rewrite.

The unit test ``test_signal_change_invalidates_key`` proves the key derivation in
isolation. This one proves the wiring: run the pipeline against a real cache
directory, change what the signal layer reports for the same input, and check
that the rewrite call is actually made again instead of being served from disk.

Signal calls are cached too, so a signal-content change in production arrives
via a changed signal layer (prompt version / model / lexicon). The test bumps the
sarcasm prompt version for the second run to let the new content through -- that
is exactly the invalidation path a real edit would take.
"""

from __future__ import annotations

import json
from dataclasses import replace

from humanize.cache import CachedClient, CacheStore
from humanize.config import PersonaBook, PipelineConfig
from humanize.io import stable_hash
from humanize.lexicon import Lexicon
from humanize.llm import LLMResult, MockClient
from humanize.pipeline import HumanizePipeline


class MutableSarcasmClient:
    """Delegates to the mock, but can flip the sarcasm label on demand."""

    name = "mock"

    def __init__(self, inner: MockClient, label: str = "literal") -> None:
        self.inner = inner
        self.label = label
        self.calls = {"signal.sarcasm": 0, "rewrite": 0}

    def complete_json(self, *, prompt_id, payload, model, temperature) -> LLMResult:
        if prompt_id in self.calls:
            self.calls[prompt_id] += 1
        result = self.inner.complete_json(
            prompt_id=prompt_id, payload=payload, model=model, temperature=temperature
        )
        if prompt_id != "signal.sarcasm":
            return result
        data = json.loads(result.text)
        data["label"] = self.label
        data["confidence"] = 0.9 if self.label == "sarcastic" else 0.6
        data["target"] = "assistant" if self.label == "sarcastic" else "none"
        return replace(result, text=json.dumps(data, ensure_ascii=False))


class CapturingClient:
    name = "mock"

    def __init__(self, inner: MockClient) -> None:
        self.inner = inner
        self.rewrite_payloads: list[dict] = []

    def complete_json(self, *, prompt_id, payload, model, temperature) -> LLMResult:
        if prompt_id == "rewrite":
            self.rewrite_payloads.append(payload)
        return self.inner.complete_json(
            prompt_id=prompt_id, payload=payload, model=model, temperature=temperature
        )


def _pipeline(
    config: PipelineConfig,
    personas: PersonaBook,
    lexicon: Lexicon,
    client,
) -> HumanizePipeline:
    return HumanizePipeline(config=config, personas=personas, lexicon=lexicon, client=client)


def test_rewrite_payload_has_no_meta(config, personas, lexicon, sample) -> None:
    capture = CapturingClient(MockClient(lexicon=lexicon))
    record = _pipeline(config, personas, lexicon, capture).run(sample("e001_overwork_advice"))

    payload = capture.rewrite_payloads[0]
    assert set(payload["signals"]) == {"sarcasm", "emotion", "ai_smell", "persona_state"}
    assert "meta" not in payload["signals"]
    assert stable_hash(payload["signals"]) == record.meta["signal_fingerprint"]


def test_signal_content_change_invalidates_rewrite_cache(
    tmp_path, config, personas, lexicon, sample
) -> None:
    store = CacheStore(tmp_path / "cache")
    inner = MutableSarcasmClient(MockClient(lexicon=lexicon), label="literal")
    client = CachedClient(inner, store, config.prompt_versions)
    pipeline = _pipeline(config, personas, lexicon, client)
    inp = sample("e001_overwork_advice")

    first = pipeline.run(inp)
    assert first.usage.calls == 4
    assert first.usage.cache_hits == 0
    assert first.meta["cache_source_models"] == []
    assert inner.calls["rewrite"] == 1
    first_fingerprint = first.meta["signal_fingerprint"]

    # Same input, different signal content. Signals are cached too, so this is
    # expressed as "the signal layer changed" (new prompt version).
    inner.label = "sarcastic"
    bumped = config.model_copy(deep=True)
    bumped.prompt_versions["signal.sarcasm"] = "sig.sarcasm.v2"
    client2 = CachedClient(inner, store, bumped.prompt_versions)
    pipeline2 = _pipeline(bumped, personas, lexicon, client2)

    second = pipeline2.run(inp)
    assert second.signals.sarcasm.label.value == "sarcastic"
    assert second.meta["signal_fingerprint"] != first_fingerprint
    assert inner.calls["rewrite"] == 2  # rewrite was re-invoked, not served from cache
    assert second.usage.cache_hits == 2  # emotion + ai_smell
    assert second.usage.calls == 2  # sarcasm + rewrite

    # Nothing changed for a third run: everything is served from cache.
    third = pipeline2.run(inp)
    assert inner.calls["rewrite"] == 2
    assert third.usage.cache_hits == 4
    assert third.usage.calls == 0
    assert third.meta["cache_source_models"] == ["mock"]
