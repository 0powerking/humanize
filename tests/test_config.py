from __future__ import annotations

from pathlib import Path

import pytest

from humanize.config import ConfigError, load_config, load_personas
from humanize.io import stable_hash
from humanize.lexicon import LexiconError, load_lexicon

ROOT = Path(__file__).resolve().parents[1]


def test_config_loads_with_expected_defaults(config) -> None:
    assert config.provider == "mock"
    assert set(config.prompt_versions) == {
        "signal.sarcasm",
        "signal.emotion",
        "signal.ai_smell",
        "rewrite",
    }
    assert config.thresholds.rewrite_gate_ai_smell == 0.25
    assert config.cache.include_signal_hash is True


def test_config_hash_changes_when_prompt_version_bumps(config) -> None:
    # Day 2 acceptance: editing a prompt version must invalidate derived caches.
    bumped = config.model_copy(deep=True)
    bumped.prompt_versions["rewrite"] = "rw.v2"
    assert stable_hash(config) != stable_hash(bumped)


def test_persona_book_resolves_default(personas) -> None:
    state = personas.resolve(None)
    assert state.persona_id == "p_sunyou"
    assert state.display_name == "损友"
    assert "不是……而是……" in " ".join(state.constraints)


def test_unknown_persona_raises(personas) -> None:
    with pytest.raises(ConfigError):
        personas.resolve("p_nobody")


def test_lexicon_loads_and_scores(lexicon) -> None:
    assert lexicon.version == "lex.v1"
    assert len(lexicon.entries) == 14
    findings = lexicon.scan("首先，你要冷静。其次，别急。")
    assert [f.type.value for f in findings] == ["enumeration", "enumeration"]
    assert lexicon.score(findings) == 0.5


def test_missing_files_raise_clear_errors() -> None:
    with pytest.raises(ConfigError):
        load_config(ROOT / "config/does_not_exist.yaml")
    with pytest.raises(ConfigError):
        load_personas(ROOT / "config/does_not_exist.yaml")
    with pytest.raises(LexiconError):
        load_lexicon(ROOT / "config/does_not_exist.yaml")
