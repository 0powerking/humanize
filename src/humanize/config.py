"""Configuration loading: pipeline config and the persona book.

Both are validated pydantic models so a malformed YAML fails at startup with a
field-level error instead of producing a half-configured run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field

from .schemas import PersonaState, PersonaTraits, StrictModel, Vocabulary


class ConfigError(RuntimeError):
    """Raised when a config file is missing or fails validation."""


class ModelRoute(StrictModel):
    model: str = "deepseek-chat"
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)


class ModelsConfig(StrictModel):
    signal: ModelRoute = Field(default_factory=lambda: ModelRoute(temperature=0.0))
    rewrite: ModelRoute = Field(default_factory=lambda: ModelRoute(temperature=0.9))


class Thresholds(StrictModel):
    rewrite_gate_ai_smell: float = Field(default=0.25, ge=0.0, le=1.0)
    max_rewrite_ratio: float = Field(default=0.6, ge=0.0, le=1.0)
    min_rewrite_chars: int = Field(default=4, ge=1)
    signal_failure_default_ai_smell: float = Field(default=0.5, ge=0.0, le=1.0)


class GovernConfig(StrictModel):
    local_rewrite_enabled: bool = False
    block_on_hit: bool = False
    sensitive_stub: tuple[str, ...] = ()


class CacheConfig(StrictModel):
    enabled: bool = True
    dir: str = "data/cache"
    # 重写阶段必须为 True；False 仅保留给纯文本级实验。
    include_signal_hash: bool = True


class CostConfig(StrictModel):
    budget_yuan: float = Field(default=50.0, ge=0.0)
    price_per_1m_prompt_yuan: float = Field(default=0.0, ge=0.0)
    price_per_1m_completion_yuan: float = Field(default=0.0, ge=0.0)


class ApiConfig(StrictModel):
    timeout_s: float = Field(default=60.0, gt=0.0)
    max_retries: int = Field(default=2, ge=0, le=5)
    backoff_base_s: float = Field(default=1.0, ge=0.0)
    json_mode: bool = True


class PathsConfig(StrictModel):
    prompts_dir: str = "prompts"
    runs_dir: str = "data/runs"


class PipelineConfig(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    provider: Literal["mock", "deepseek"] = "mock"
    base_url: str = "https://api.deepseek.com"
    api_key_env: str = "DEEPSEEK_API_KEY"
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    govern: GovernConfig = Field(default_factory=GovernConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    cost: CostConfig = Field(default_factory=CostConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)


class PersonaDef(StrictModel):
    version: str = "1.0"
    display_name: str = Field(min_length=1)
    description: str = ""
    traits: PersonaTraits = Field(default_factory=PersonaTraits)
    constraints: tuple[str, ...] = ()
    tone_hint: str = ""
    vocabulary: Vocabulary = Field(default_factory=Vocabulary)


class PersonaBook(StrictModel):
    default_persona: str
    personas: dict[str, PersonaDef] = Field(min_length=1)

    def resolve(self, persona_id: str | None = None) -> PersonaState:
        pid = persona_id or self.default_persona
        try:
            persona = self.personas[pid]
        except KeyError:
            available = ", ".join(sorted(self.personas))
            raise ConfigError(f"unknown persona_id={pid!r}; available: {available}") from None
        return PersonaState(
            persona_id=pid,
            display_name=persona.display_name,
            version=persona.version,
            applied=persona.traits,
            constraints=persona.constraints,
            tone_hint=persona.tone_hint,
            vocabulary=persona.vocabulary,
        )


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ConfigError(f"config file must contain a mapping: {path}")
    return raw


def load_config(path: str | Path) -> PipelineConfig:
    p = Path(path)
    try:
        return PipelineConfig.model_validate(_load_yaml(p))
    except Exception as exc:  # pydantic wraps into ValidationError, keep path context
        raise ConfigError(f"invalid pipeline config {p}: {exc}") from exc


def load_personas(path: str | Path) -> PersonaBook:
    p = Path(path)
    try:
        book = PersonaBook.model_validate(_load_yaml(p))
    except Exception as exc:
        raise ConfigError(f"invalid persona config {p}: {exc}") from exc
    if book.default_persona not in book.personas:
        raise ConfigError(
            f"default_persona={book.default_persona!r} not defined in personas: "
            f"{sorted(book.personas)}"
        )
    return book
