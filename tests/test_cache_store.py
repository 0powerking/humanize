from __future__ import annotations

from pathlib import Path
from typing import Any

from humanize.cache import CachedClient, CacheStore, make_cache_key, signal_fingerprint
from humanize.io import stable_hash
from humanize.llm import LLMResult

VERSIONS = {"rewrite": "rw.v1", "signal.sarcasm": "sig.sarcasm.v1"}
PAYLOAD: dict[str, Any] = {
    "raw_reply": "在吗",
    "signals": {"sarcasm": {"label": "literal"}, "emotion": {"valence": 0.0}},
}


class CountingClient:
    name = "counting"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, float]] = []

    def complete_json(self, *, prompt_id, payload, model, temperature) -> LLMResult:
        self.calls.append((prompt_id, model, temperature))
        return LLMResult(
            text='{"ok": true}',
            model="reported-model",
            prompt_tokens=3,
            completion_tokens=2,
        )


def _store(tmp_path: Path, enabled: bool = True) -> CacheStore:
    return CacheStore(tmp_path / "cache", enabled=enabled)


def _client(store: CacheStore, inner: CountingClient, versions=None) -> CachedClient:
    return CachedClient(inner, store, versions or VERSIONS)


def _call(
    client: CachedClient,
    payload: dict[str, Any] | None = None,
    *,
    model: str = "m",
    temperature: float = 0.9,
) -> LLMResult:
    return client.complete_json(
        prompt_id="rewrite",
        payload=payload or PAYLOAD,
        model=model,
        temperature=temperature,
    )


def test_second_call_is_a_hit(tmp_path: Path) -> None:
    inner = CountingClient()
    client = _client(_store(tmp_path), inner)

    first = _call(client)
    second = _call(client)

    assert len(inner.calls) == 1
    assert first.cached is False
    assert second.cached is True
    assert second.text == '{"ok": true}'
    assert second.model == "reported-model"
    assert (second.prompt_tokens, second.completion_tokens) == (3, 2)
    assert (client.hits, client.misses) == (1, 1)


def test_signal_content_change_misses(tmp_path: Path) -> None:
    inner = CountingClient()
    client = _client(_store(tmp_path), inner)

    _call(client)
    changed = {
        **PAYLOAD,
        "signals": {"sarcasm": {"label": "sarcastic"}, "emotion": {"valence": 0.0}},
    }
    result = _call(client, changed)

    assert len(inner.calls) == 2
    assert result.cached is False


def test_temperature_change_misses(tmp_path: Path) -> None:
    inner = CountingClient()
    client = _client(_store(tmp_path), inner)

    _call(client, temperature=0.9)
    _call(client, temperature=0.2)

    assert len(inner.calls) == 2


def test_prompt_version_bump_misses(tmp_path: Path) -> None:
    inner = CountingClient()
    store = _store(tmp_path)

    _call(_client(store, inner))
    _call(_client(store, inner, versions={"rewrite": "rw.v2"}))

    assert len(inner.calls) == 2


def test_signal_stage_without_signal_hash_is_cached(tmp_path: Path) -> None:
    inner = CountingClient()
    client = _client(_store(tmp_path), inner)
    payload = {"raw_reply": "在吗"}

    for _ in range(2):
        client.complete_json(
            prompt_id="signal.sarcasm", payload=payload, model="m", temperature=0.0
        )

    assert len(inner.calls) == 1


def test_corrupt_entry_is_a_miss(tmp_path: Path) -> None:
    inner = CountingClient()
    store = _store(tmp_path)
    client = _client(store, inner)
    _call(client)

    key = make_cache_key(
        stage="rewrite",
        model="m",
        prompt_version="rw.v1",
        payload=PAYLOAD,
        signal_hash=stable_hash(PAYLOAD["signals"]),
        temperature=0.9,
    )
    store.path_for(key).write_text("{not json", encoding="utf-8")

    _call(client)

    assert len(inner.calls) == 2


def test_disabled_store_never_reads_or_writes(tmp_path: Path) -> None:
    inner = CountingClient()
    store = _store(tmp_path, enabled=False)
    client = _client(store, inner)

    _call(client)
    _call(client)

    assert len(inner.calls) == 2
    assert not store.root.exists()


def test_signal_content_is_part_of_the_key(tmp_path: Path) -> None:
    # Signals live inside the rewrite payload, so a content change must miss even
    # without relying on the explicit signal hash. Guard against someone dropping
    # "signals" from the payload later.
    inner = CountingClient()
    client = _client(_store(tmp_path), inner)
    _call(client)
    changed = {
        **PAYLOAD,
        "signals": {"sarcasm": {"label": "sarcastic"}, "emotion": {"valence": 0.0}},
    }
    _call(client, changed)

    assert len(inner.calls) == 2


def test_cache_files_are_sharded(tmp_path: Path) -> None:
    store = _store(tmp_path)
    client = _client(store, CountingClient())
    _call(client)

    files = list((tmp_path / "cache").rglob("*.json"))
    assert len(files) == 1
    assert files[0].parent.name == files[0].stem[:2]


def test_fingerprint_matches_payload_view_hash(pipeline, sample) -> None:
    record = pipeline.run(sample("e001_overwork_advice"))
    assert signal_fingerprint(record.signals) == stable_hash(record.signals.rewrite_view())
