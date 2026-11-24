from __future__ import annotations

import pytest

from humanize.lexicon import Lexicon
from humanize.llm import LLMError, LLMFormatError, MockClient, parse_json_object

BASE_PAYLOAD = {
    "context": {"scene": "casual", "turns": [{"role": "user", "content": "你可真行啊"}]},
    "raw_reply": "我理解你的不满情绪。首先，你要冷静。",
    "persona_ref": {"persona_id": "p_sunyou", "version": "1.0"},
}


def _call(client: MockClient, prompt_id: str, payload=None):
    return client.complete_json(
        prompt_id=prompt_id,
        payload=payload or BASE_PAYLOAD,
        model="mock",
        temperature=0.0,
    )


def test_parse_plain_json() -> None:
    assert parse_json_object('{"a": 1}') == {"a": 1}


def test_parse_fenced_json() -> None:
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}


def test_parse_json_with_surrounding_prose() -> None:
    assert parse_json_object('好的，结果如下：{"a": 1} 以上。') == {"a": 1}


def test_parse_rejects_non_json() -> None:
    with pytest.raises(LLMFormatError):
        parse_json_object("我觉得这个问题很复杂")


def test_mock_signal_payloads_are_parseable(lexicon: Lexicon) -> None:
    client = MockClient(lexicon=lexicon)
    for prompt_id in ("signal.sarcasm", "signal.emotion", "signal.ai_smell"):
        data = parse_json_object(_call(client, prompt_id).text)
        assert isinstance(data, dict)


def test_mock_ai_smell_returns_no_extras(lexicon: Lexicon) -> None:
    # v2 contract: rules are applied by the pipeline, the model only adds extras.
    data = parse_json_object(_call(MockClient(lexicon=lexicon), "signal.ai_smell").text)

    assert data["extra_hits"] == []
    assert "lexicon=lex.v1" in data["notes"]


def test_mock_rejects_unknown_prompt(lexicon: Lexicon) -> None:
    with pytest.raises(LLMError):
        _call(MockClient(lexicon=lexicon), "signal.unknown")
