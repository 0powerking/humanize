"""Structured contracts between pipeline stages.

Every boundary in the pipeline is a pydantic model: nothing crosses a module
boundary as free-form text. Signal models are frozen, so the rewrite stage
receives a read-only snapshot of the evidence it must act on -- a bug there can
never corrupt the attribution trail (docs/decisions.md ADR-002).

Bump ``SCHEMA_VERSION`` whenever a field is removed or its meaning changes.
Adding an optional field is backward compatible and does not require a bump.
"""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "1.0"


class StrictModel(BaseModel):
    """Reject unknown fields: a typo in YAML/JSON must fail loudly, not silently."""

    model_config = ConfigDict(extra="forbid")


class FrozenModel(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #


class Scene(str, Enum):
    casual = "casual"
    comfort = "comfort"
    banter = "banter"
    boundary = "boundary"
    work = "work"


class Role(str, Enum):
    user = "user"
    assistant = "assistant"


class SarcasmLabel(str, Enum):
    sarcastic = "sarcastic"
    literal = "literal"
    uncertain = "uncertain"


class SarcasmTarget(str, Enum):
    user = "user"
    assistant = "assistant"
    third_party = "third_party"
    none = "none"


class ToneTarget(str, Enum):
    soothe = "soothe"
    banter = "banter"
    matter_of_fact = "matter_of_fact"
    set_boundary = "set_boundary"


class AiSmellType(str, Enum):
    not_x_but_y = "not_x_but_y"
    enumeration = "enumeration"
    golden_quote = "golden_quote"
    service_tone = "service_tone"
    hedge = "hedge"
    over_explain = "over_explain"
    parallelism = "parallelism"
    other = "other"


# --------------------------------------------------------------------------- #
# Stage 1 input
# --------------------------------------------------------------------------- #


class Turn(StrictModel):
    role: Role
    content: str = Field(min_length=1, max_length=4000)
    ts: str | None = None


class Context(StrictModel):
    scene: Scene = Scene.casual
    turns: tuple[Turn, ...] = Field(min_length=1, max_length=40)


class Options(StrictModel):
    enable_memes: bool = False
    max_rewrite_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    dry_run: bool = False


class PersonaRef(StrictModel):
    persona_id: str = Field(min_length=1, max_length=64)
    version: str = "1.0"


class RunInput(StrictModel):
    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    sample_id: str = Field(min_length=1, max_length=64)
    # Provenance for the eval set: never mix synthetic and real without a label.
    source: Literal["synthetic", "real", "unknown"] = "unknown"
    context: Context
    raw_reply: str = Field(min_length=1, max_length=4000)
    persona: PersonaRef
    options: Options = Field(default_factory=Options)


# --------------------------------------------------------------------------- #
# Stage 2 signals (frozen)
# --------------------------------------------------------------------------- #


class SarcasmSignal(FrozenModel):
    label: SarcasmLabel
    confidence: float = Field(ge=0.0, le=1.0)
    target: SarcasmTarget = SarcasmTarget.none
    evidence: tuple[str, ...] = ()
    reason: str = ""


class EmotionLabel(FrozenModel):
    label: str = Field(min_length=1, max_length=32)
    intensity: float = Field(default=0.5, ge=0.0, le=1.0)


class EmotionSignal(FrozenModel):
    user_emotion: EmotionLabel
    valence: float = Field(ge=-1.0, le=1.0)
    arousal: float = Field(ge=0.0, le=1.0)
    assistant_tone_target: ToneTarget = ToneTarget.matter_of_fact
    cues: tuple[str, ...] = ()


class AiSmellHit(FrozenModel):
    type: AiSmellType
    span: str = Field(min_length=1, max_length=80)
    severity: int = Field(default=1, ge=1, le=3)


class AiSmellSignal(FrozenModel):
    score: float = Field(ge=0.0, le=1.0)
    hits: tuple[AiSmellHit, ...] = ()
    notes: str = ""


class AiSmellExtra(StrictModel):
    """Internal LLM response for ai-smell fusion: only hits the rules missed.

    Not part of ``RunRecord``; the pipeline merges it into ``AiSmellSignal``.
    ``extra="ignore"`` because models tend to add an unrequested ``score`` field.
    """

    model_config = ConfigDict(extra="ignore")

    extra_hits: tuple[AiSmellHit, ...] = ()
    notes: str = ""


class PersonaTraits(FrozenModel):
    warmth: float = Field(default=0.5, ge=0.0, le=1.0)
    playfulness: float = Field(default=0.5, ge=0.0, le=1.0)
    formality: float = Field(default=0.5, ge=0.0, le=1.0)
    energy: float = Field(default=0.5, ge=0.0, le=1.0)
    familiarity: float = Field(default=0.5, ge=0.0, le=1.0)
    verbosity: float = Field(default=0.5, ge=0.0, le=1.0)
    mood: str = "neutral"


class Vocabulary(FrozenModel):
    allow: tuple[str, ...] = ()
    forbid: tuple[str, ...] = ()


class PersonaState(FrozenModel):
    persona_id: str
    display_name: str
    version: str = "1.0"
    applied: PersonaTraits = Field(default_factory=PersonaTraits)
    constraints: tuple[str, ...] = ()
    tone_hint: str = ""
    vocabulary: Vocabulary = Field(default_factory=Vocabulary)


class SignalMeta(FrozenModel):
    model: str = "unknown"
    latency_ms: int = 0
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    degraded: tuple[str, ...] = ()


class SignalBundle(FrozenModel):
    sarcasm: SarcasmSignal
    emotion: EmotionSignal
    ai_smell: AiSmellSignal
    persona_state: PersonaState
    meta: SignalMeta

    def rewrite_view(self) -> dict[str, Any]:
        """The signal content the rewrite stage consumes.

        Diagnostic prose (evidence/reason/cues/notes) is stripped: it is useful
        for audit but is model-generated free text that varies between runs, so
        keeping it in the rewrite payload would churn the cache without changing
        what the rewriter needs (ADR-004). The full signals stay in RunRecord.
        """
        sarcasm = self.sarcasm.model_dump(mode="json")
        sarcasm.pop("reason", None)
        sarcasm.pop("evidence", None)
        emotion = self.emotion.model_dump(mode="json")
        emotion.pop("cues", None)
        ai_smell = self.ai_smell.model_dump(mode="json")
        ai_smell.pop("notes", None)
        return {
            "sarcasm": sarcasm,
            "emotion": emotion,
            "ai_smell": ai_smell,
            "persona_state": self.persona_state.model_dump(mode="json"),
        }


# --------------------------------------------------------------------------- #
# Stage 3 rewrite
# --------------------------------------------------------------------------- #


class ChangeNote(StrictModel):
    op: str = Field(min_length=1, max_length=60)
    reason: str = Field(min_length=1, max_length=200)
    signal_ref: str | None = None


class RewriteOutput(StrictModel):
    text: str = Field(min_length=1, max_length=4000)
    rewrite_ratio: float = Field(ge=0.0, le=1.0)
    change_notes: tuple[ChangeNote, ...] = ()
    kept_facts: tuple[str, ...] = ()
    risk_flags: tuple[str, ...] = ()
    noop: bool = False

    @model_validator(mode="after")
    def _noop_keeps_ratio_zero(self) -> RewriteOutput:
        if self.noop and self.rewrite_ratio > 1e-9:
            raise ValueError("noop rewrite must have rewrite_ratio == 0")
        return self


# --------------------------------------------------------------------------- #
# Stage 4 governance
# --------------------------------------------------------------------------- #


class ResidueHit(StrictModel):
    type: AiSmellType
    span: str
    rule_id: str = ""


class ResidueResult(StrictModel):
    hits: tuple[ResidueHit, ...] = ()
    local_rewrite_rounds: int = Field(default=0, ge=0, le=2)
    note: str = ""


class SensitiveResult(StrictModel):
    blocked: bool = False
    categories: tuple[str, ...] = ()
    action: Literal["pass", "rewrite", "block"] = "pass"


class AuditLog(StrictModel):
    trace_id: str
    rule_hits: tuple[str, ...] = ()
    decisions: tuple[str, ...] = ()
    elapsed_ms: int = 0
    config_hash: str = ""


class GovernOutput(StrictModel):
    final_text: str = Field(min_length=1, max_length=4000)
    residue: ResidueResult = Field(default_factory=ResidueResult)
    sensitive: SensitiveResult = Field(default_factory=SensitiveResult)
    audit: AuditLog


# --------------------------------------------------------------------------- #
# Stage 5 run record
# --------------------------------------------------------------------------- #


class Usage(StrictModel):
    calls: int = 0
    cache_hits: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_yuan: float = 0.0


class Timing(StrictModel):
    signals_ms: int = 0
    rewrite_ms: int = 0
    govern_ms: int = 0
    total_ms: int = 0


class Versions(StrictModel):
    code_commit: str = "unknown"
    config_hash: str = ""
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    models: dict[str, str] = Field(default_factory=dict)


class RunRecord(StrictModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    run_id: str
    sample_id: str
    created_at: str
    input: RunInput
    signals: SignalBundle
    rewrite: RewriteOutput
    govern: GovernOutput
    usage: Usage = Field(default_factory=Usage)
    timing: Timing = Field(default_factory=Timing)
    versions: Versions
    meta: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _identity_matches_input(self) -> RunRecord:
        if self.input.run_id != self.run_id:
            raise ValueError("run_id does not match input.run_id")
        if self.input.sample_id != self.sample_id:
            raise ValueError("sample_id does not match input.sample_id")
        if self.govern.final_text != self.rewrite.text and not self.govern.sensitive.blocked:
            raise ValueError("final_text must equal rewrite.text unless blocked (v1 invariant)")
        return self
