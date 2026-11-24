from __future__ import annotations

from pathlib import Path

import pytest

from humanize.prompts import PromptError, PromptStore

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_IDS = ["rewrite", "signal.ai_smell", "signal.emotion", "signal.sarcasm"]


@pytest.fixture(scope="module")
def store() -> PromptStore:
    return PromptStore(ROOT / "prompts")


def test_all_prompts_load(store: PromptStore) -> None:
    assert store.ids() == EXPECTED_IDS


def test_config_and_prompt_versions_match(store: PromptStore, config) -> None:
    store.check_versions(config.prompt_versions)


def test_render_injects_payload(store: PromptStore) -> None:
    rendered = store.render(
        "signal.sarcasm",
        {"raw_reply": "你可真行啊", "context": {"turns": []}},
    )

    assert "你可真行啊" in rendered.user
    assert "{{PAYLOAD_JSON}}" not in rendered.user
    assert rendered.messages[0]["role"] == "system"
    assert rendered.messages[1]["role"] == "user"
    assert rendered.version == "sig.sarcasm.v1"


def test_unknown_prompt_raises(store: PromptStore) -> None:
    with pytest.raises(PromptError):
        store.render("signal.nope", {})


def test_version_drift_is_detected(store: PromptStore) -> None:
    with pytest.raises(PromptError, match="version drift"):
        store.check_versions({"rewrite": "rw.v999"})


def test_missing_prompt_is_detected(store: PromptStore) -> None:
    with pytest.raises(PromptError, match="missing prompt"):
        store.check_versions({"signal.unknown": "v1"})
