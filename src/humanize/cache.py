"""Cache key derivation and the on-disk response cache.

The rewrite stage is keyed on ``(rewrite_view(signals), raw reply, prompt
version, model, temperature)``. Keying on the raw reply alone would silently
reuse a rewrite produced for different evidence; keying on provenance as well
would miss whenever a model is swapped even though the signals say the same
thing. ``rewrite_view`` is therefore the single source of truth for both the
rewrite payload and the fingerprint (ADR-004).
"""

from __future__ import annotations

import json
from bisect import bisect_right
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .io import now_iso, stable_hash, write_json
from .llm import LLMClient, LLMResult
from .schemas import SignalBundle

#: ADR-008 plan A: 5 bins for valence/arousal, 3 bins for confidence.
_VALENCE_EDGES = (-0.6, -0.2, 0.2, 0.6)
_CONFIDENCE_EDGES = (0.6, 0.8)


def _bucket(value: Any, edges: tuple[float, ...]) -> int | None:
    try:
        return bisect_right(edges, float(value))
    except (TypeError, ValueError):
        return None


def _trait_bucket(value: Any) -> str | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0.34:
        return "low"
    if number < 0.67:
        return "mid"
    return "high"


def fingerprint_from_view(view: Mapping[str, Any]) -> dict[str, Any]:
    """Coarse, semantic view used for the cache key (ADR-008 plan A).

    Labels and type sets are exact; continuous values are bucketed so a ±0.1
    sampling wobble does not invalidate an otherwise identical rewrite. The
    rewrite prompt still receives the exact values from ``rewrite_view``.
    """
    sarcasm = view.get("sarcasm") or {}
    emotion = view.get("emotion") or {}
    user_emotion = emotion.get("user_emotion") or {}
    ai_smell = view.get("ai_smell") or {}
    persona = view.get("persona_state") or {}
    traits = persona.get("applied") or {}

    return {
        "sarcasm": {
            "label": sarcasm.get("label"),
            "target": sarcasm.get("target"),
            "confidence_bucket": _bucket(sarcasm.get("confidence"), _CONFIDENCE_EDGES),
        },
        "emotion": {
            "label": user_emotion.get("label"),
            "tone": emotion.get("assistant_tone_target"),
            "valence_bucket": _bucket(emotion.get("valence"), _VALENCE_EDGES),
            "arousal_bucket": _bucket(emotion.get("arousal"), _VALENCE_EDGES),
        },
        "ai_smell": {
            "types": sorted(
                {
                    hit.get("type")
                    for hit in ai_smell.get("hits", [])
                    if hit.get("type")
                }
            ),
        },
        "persona_state": {
            "persona_id": persona.get("persona_id"),
            "mood": traits.get("mood"),
            "traits": {
                key: _trait_bucket(value)
                for key, value in traits.items()
                if key != "mood"
            },
        },
    }


def fingerprint_view(signals: SignalBundle) -> dict[str, Any]:
    return fingerprint_from_view(signals.rewrite_view())


def signal_fingerprint(signals: SignalBundle) -> str:
    """Hash of the rewrite stage's *semantic* input (labels + bucketed values)."""
    return stable_hash(fingerprint_view(signals))


def make_cache_key(
    *,
    stage: str,
    model: str,
    prompt_version: str,
    payload: Mapping[str, Any],
    signal_hash: str | None = None,
    temperature: float | None = None,
    seed: int | None = None,
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
        "temperature": temperature,
        "seed": seed,
        "payload": dict(payload),
        "signal": signal_hash,
    }
    return stable_hash(material)


class CacheStore:
    """One JSON file per key, sharded by the first two hex chars."""

    def __init__(self, root: str | Path, enabled: bool = True) -> None:
        self.root = Path(root)
        self.enabled = enabled

    def path_for(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        path = self.path_for(key)
        if not path.exists():
            return None
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None  # a corrupt entry is a miss, never a crash
        if not isinstance(entry, dict) or not isinstance(entry.get("text"), str):
            return None
        return entry

    def put(self, key: str, entry: Mapping[str, Any]) -> Path | None:
        if not self.enabled:
            return None
        payload = {**entry, "stored_at": now_iso()}
        return write_json(self.path_for(key), payload)


class CachedClient:
    """LLMClient decorator: read-through disk cache, keyed as described above."""

    def __init__(
        self,
        inner: LLMClient,
        store: CacheStore,
        prompt_versions: Mapping[str, str],
    ) -> None:
        self._inner = inner
        self._store = store
        self._versions = dict(prompt_versions)
        self.hits = 0
        self.misses = 0

    @property
    def name(self) -> str:
        return self._inner.name

    def complete_json(
        self,
        *,
        prompt_id: str,
        payload: Mapping[str, Any],
        model: str,
        temperature: float,
        seed: int | None = None,
    ) -> LLMResult:
        prompt_version = self._versions.get(prompt_id)
        if not self._store.enabled or not prompt_version:
            return self._inner.complete_json(
                prompt_id=prompt_id,
                payload=payload,
                model=model,
                temperature=temperature,
                seed=seed,
            )

        signal_hash: str | None = None
        key_payload: Mapping[str, Any] = payload
        if prompt_id == "rewrite":
            view = payload.get("signals")
            if isinstance(view, dict):
                signal_hash = stable_hash(fingerprint_from_view(view))
                # Exact values stay in the prompt payload, but not in the key:
                # otherwise bucketing the fingerprint would be pointless.
                key_payload = {key: value for key, value in payload.items() if key != "signals"}

        key = make_cache_key(
            stage=prompt_id,
            model=model,
            prompt_version=prompt_version,
            payload=key_payload,
            signal_hash=signal_hash,
            temperature=temperature,
            seed=seed,
        )
        entry = self._store.get(key)
        if entry is not None:
            self.hits += 1
            return LLMResult(
                text=str(entry["text"]),
                model=str(entry.get("model") or model),
                prompt_tokens=int(entry.get("prompt_tokens") or 0),
                completion_tokens=int(entry.get("completion_tokens") or 0),
                system_fingerprint=entry.get("system_fingerprint") or None,
                cached=True,
            )

        self.misses += 1
        result = self._inner.complete_json(
            prompt_id=prompt_id,
            payload=payload,
            model=model,
            temperature=temperature,
            seed=seed,
        )
        self._store.put(
            key,
            {
                "stage": prompt_id,
                "prompt_version": prompt_version,
                "model": result.model,
                "text": result.text,
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "system_fingerprint": result.system_fingerprint,
            },
        )
        return result
