from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from humanize.llm import DeepSeekClient, LLMError, ProviderHTTPError
from humanize.prompts import PromptStore

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def store() -> PromptStore:
    return PromptStore(ROOT / "prompts")


def _ok(content: str = '{"a": 1}', model: str = "deepseek-flash") -> dict[str, Any]:
    return {
        "model": model,
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def _client(store: PromptStore, transport, **overrides) -> DeepSeekClient:
    kwargs: dict[str, Any] = {
        "api_key": "sk-test",
        "prompt_store": store,
        "transport": transport,
        "sleep": lambda _seconds: None,
    }
    kwargs.update(overrides)
    return DeepSeekClient(**kwargs)


def _call(client: DeepSeekClient):
    return client.complete_json(
        prompt_id="rewrite",
        payload={"raw_reply": "在吗", "constraints": {"allow_noop": False}},
        model="deepseek-chat",
        temperature=0.9,
    )


def test_complete_json_returns_usage_and_model(store: PromptStore) -> None:
    captured: dict[str, Any] = {}

    def transport(body: dict[str, Any]) -> dict[str, Any]:
        captured.update(body)
        return _ok()

    result = _call(_client(store, transport))

    assert result.text == '{"a": 1}'
    assert result.model == "deepseek-flash"  # provider-reported, not requested
    assert result.prompt_tokens == 10
    assert result.completion_tokens == 5
    assert captured["response_format"] == {"type": "json_object"}
    assert captured["messages"][0]["role"] == "system"
    assert "在吗" in captured["messages"][1]["content"]


def test_json_mode_can_be_disabled(store: PromptStore) -> None:
    captured: dict[str, Any] = {}

    def transport(body: dict[str, Any]) -> dict[str, Any]:
        captured.update(body)
        return _ok()

    _call(_client(store, transport, json_mode=False))

    assert "response_format" not in captured


def test_seed_is_forwarded_when_set(store: PromptStore) -> None:
    captured: dict[str, Any] = {}

    def transport(body: dict[str, Any]) -> dict[str, Any]:
        captured.update(body)
        return _ok()

    client = _client(store, transport)
    client.complete_json(
        prompt_id="rewrite",
        payload={"raw_reply": "在吗"},
        model="deepseek-chat",
        temperature=0.9,
        seed=42,
    )

    assert captured["seed"] == 42


def test_seed_is_omitted_when_none(store: PromptStore) -> None:
    captured: dict[str, Any] = {}

    def transport(body: dict[str, Any]) -> dict[str, Any]:
        captured.update(body)
        return _ok()

    _call(_client(store, transport))

    assert "seed" not in captured


def test_retries_transient_5xx(store: PromptStore) -> None:
    calls: list[int] = []

    def transport(body: dict[str, Any]) -> dict[str, Any]:
        calls.append(1)
        if len(calls) == 1:
            raise ProviderHTTPError(503, "busy")
        return _ok()

    result = _call(_client(store, transport, max_retries=2))

    assert len(calls) == 2
    assert result.text == '{"a": 1}'


def test_does_not_retry_auth_error(store: PromptStore) -> None:
    calls: list[int] = []

    def transport(body: dict[str, Any]) -> dict[str, Any]:
        calls.append(1)
        raise ProviderHTTPError(401, "invalid api key")

    with pytest.raises(LLMError, match="HTTP 401"):
        _call(_client(store, transport, max_retries=3))

    assert len(calls) == 1


def test_gives_up_after_retries(store: PromptStore) -> None:
    calls: list[int] = []

    def transport(body: dict[str, Any]) -> dict[str, Any]:
        calls.append(1)
        raise ProviderHTTPError(500, "boom")

    with pytest.raises(LLMError, match="after 3 attempt"):
        _call(_client(store, transport, max_retries=2))

    assert len(calls) == 3


def test_empty_content_is_an_error(store: PromptStore) -> None:
    def transport(body: dict[str, Any]) -> dict[str, Any]:
        return _ok(content="   ")

    with pytest.raises(LLMError, match="empty content"):
        _call(_client(store, transport))


def test_unexpected_shape_is_an_error(store: PromptStore) -> None:
    def transport(body: dict[str, Any]) -> dict[str, Any]:
        return {"choices": []}

    with pytest.raises(LLMError):
        _call(_client(store, transport))
