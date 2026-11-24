from __future__ import annotations

from humanize.cache import make_cache_key, signal_fingerprint

PAYLOAD = {"raw_reply": "在吗", "scene": "casual"}


def _key(**overrides):
    kwargs = {
        "stage": "rewrite",
        "model": "deepseek-chat",
        "prompt_version": "rw.v1",
        "payload": PAYLOAD,
        "signal_hash": "abc123",
    }
    kwargs.update(overrides)
    return make_cache_key(**kwargs)


def test_key_is_deterministic_and_order_insensitive() -> None:
    assert _key() == _key()
    shuffled = {"scene": "casual", "raw_reply": "在吗"}
    assert _key(payload=shuffled) == _key()


def test_prompt_version_bump_invalidates_key() -> None:
    # Acceptance criterion requested on Day 1: bump prompt version -> cache key changes.
    assert _key() != _key(prompt_version="rw.v2")


def test_signal_change_invalidates_key() -> None:
    assert _key() != _key(signal_hash="def456")


def test_signal_fingerprint_ignores_latency(pipeline, sample) -> None:
    record = pipeline.run(sample("e001_overwork_advice"))
    signals = record.signals

    slower = signals.model_copy(
        update={"meta": signals.meta.model_copy(update={"latency_ms": 9999})}
    )
    assert signal_fingerprint(signals) == signal_fingerprint(slower)


def test_signal_fingerprint_ignores_provenance(pipeline, sample) -> None:
    record = pipeline.run(sample("e001_overwork_advice"))
    signals = record.signals

    reprovenanced = signals.model_copy(
        update={
            "meta": signals.meta.model_copy(
                update={
                    "model": "another-provider-model",
                    "prompt_versions": {"signal.sarcasm": "sig.sarcasm.v99"},
                    "degraded": ("sarcasm",),
                }
            )
        }
    )
    assert signal_fingerprint(signals) == signal_fingerprint(reprovenanced)


def test_rewrite_view_strips_diagnostic_prose(pipeline, sample) -> None:
    record = pipeline.run(sample("e002_sarcasm_late"))
    view = record.signals.rewrite_view()

    assert "reason" not in view["sarcasm"]
    assert "evidence" not in view["sarcasm"]
    assert "cues" not in view["emotion"]
    assert "notes" not in view["ai_smell"]
    # The fields the rewriter actually uses are still there.
    assert view["sarcasm"]["label"] == "sarcastic"
    assert view["emotion"]["assistant_tone_target"] == "banter"


def test_signal_fingerprint_ignores_diagnostic_prose(pipeline, sample) -> None:
    record = pipeline.run(sample("e002_sarcasm_late"))
    signals = record.signals

    rephrased = signals.model_copy(
        update={
            "sarcasm": signals.sarcasm.model_copy(update={"reason": "完全不同的解释文字"}),
            "ai_smell": signals.ai_smell.model_copy(update={"notes": "完全不同的备注"}),
        }
    )
    assert signal_fingerprint(signals) == signal_fingerprint(rephrased)


def test_signal_fingerprint_tracks_signal_content(pipeline, sample) -> None:
    record = pipeline.run(sample("e001_overwork_advice"))
    signals = record.signals

    different = signals.model_copy(
        update={
            "ai_smell": signals.ai_smell.model_copy(update={"score": 0.01}),
        }
    )
    assert signal_fingerprint(signals) != signal_fingerprint(different)
