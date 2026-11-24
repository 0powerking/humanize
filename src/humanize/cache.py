"""Cache key derivation.

The rewrite stage is cached on ``(raw reply, signals, prompt version, model)``.
Keying it on the raw reply alone would be wrong: if the signal layer changes
(new prompt, new model, new lexicon) while the raw reply stays identical, the
cached rewrite is stale -- it was produced for different evidence.

``signal_fingerprint`` therefore hashes the semantic content of SignalBundle and
deliberately drops volatile fields (latency) that do not change meaning.

Day 2 adds the on-disk store; this module already owns the contract so Day 2
only has to wrap it. See docs/decisions.md ADR-004.
"""

from __future__ import annotations

from typing import Any

from .io import stable_hash
from .schemas import SignalBundle

#: Fields that vary between two semantically identical runs and must not
#: invalidate the cache.
_VOLATILE_SIGNAL_FIELDS = ("latency_ms",)


def signal_fingerprint(signals: SignalBundle) -> str:
    """Hash of the signal content that the rewrite stage actually consumes."""
    data: dict[str, Any] = signals.model_dump(mode="json")
    meta = data.get("meta", {})
    for field in _VOLATILE_SIGNAL_FIELDS:
        meta.pop(field, None)
    return stable_hash(data)


def make_cache_key(
    *,
    stage: str,
    model: str,
    prompt_version: str,
    payload: dict[str, Any],
    signal_hash: str | None = None,
) -> str:
    """Deterministic key for one LLM stage call.

    ``signal_hash`` is required for stages whose input includes upstream signals
    (i.e. rewrite) and must be omitted only for stages that consume raw input
    alone (i.e. each signal extractor).
    """
    material = {
        "stage": stage,
        "model": model,
        "prompt_version": prompt_version,
        "payload": payload,
        "signal": signal_hash,
    }
    return stable_hash(material)
