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
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .io import now_iso, stable_hash, write_json
from .llm import LLMClient, LLMResult
from .schemas import SignalBundle


def signal_fingerprint(signals: SignalBundle) -> str:
    """Hash of exactly what the rewrite stage consumes."""
    return stable_hash(signals.rewrite_view())


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
        if prompt_id == "rewrite":
            view = payload.get("signals")
            if isinstance(view, dict):
                signal_hash = stable_hash(view)

        key = make_cache_key(
            stage=prompt_id,
            model=model,
            prompt_version=prompt_version,
            payload=payload,
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
