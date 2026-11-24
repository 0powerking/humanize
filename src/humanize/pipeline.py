"""Five-stage pipeline orchestration.

data flow (one direction only):

    RunInput
      -> [1] signals   (3 LLM calls in parallel + persona resolved from config)
      -> [2] rewrite   (raw reply + frozen signals + persona -> new text)
      -> [3] govern    (residue scan + sensitive stub + audit)
      -> RunRecord     (everything, persisted as one JSON file)

Failure policy: a failing signal call degrades to a neutral value and is listed
in ``meta.degraded``; the run continues. A failing rewrite falls back to the raw
reply (noop) and is flagged. Nothing silently disappears -- see ADR-003.
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, TypeVar

from pydantic import BaseModel

from .ai_smell import merge_ai_smell, rule_hits_payload
from .cache import signal_fingerprint
from .config import ModelRoute, PersonaBook, PipelineConfig
from .cost import estimate_cost
from .io import git_commit, now_iso, stable_hash
from .lexicon import Lexicon
from .llm import LLMClient, LLMResult, parse_json_object
from .schemas import (
    SCHEMA_VERSION,
    AiSmellExtra,
    AiSmellSignal,
    AiSmellType,
    AuditLog,
    EmotionLabel,
    EmotionSignal,
    GovernOutput,
    PersonaState,
    ResidueHit,
    ResidueResult,
    RewriteOutput,
    RunInput,
    RunRecord,
    SarcasmLabel,
    SarcasmSignal,
    SarcasmTarget,
    SensitiveResult,
    SignalBundle,
    SignalMeta,
    Timing,
    ToneTarget,
    Usage,
    Versions,
)

_PHONE = r"(?<!\d)1[3-9]\d{9}(?!\d)"
_URL = r"(?:https?://|www\.)\S+"

_ModelT = TypeVar("_ModelT", bound=BaseModel)


@dataclass
class _Outcome:
    name: str
    data: dict[str, Any] | None = None
    result: LLMResult | None = None
    error: Exception | None = None


class HumanizePipeline:
    def __init__(
        self,
        *,
        config: PipelineConfig,
        personas: PersonaBook,
        lexicon: Lexicon,
        client: LLMClient,
    ) -> None:
        self.cfg = config
        self.personas = personas
        self.lexicon = lexicon
        self.client = client
        self._config_hash = stable_hash(config)

    # -- public ------------------------------------------------------------ #

    def run(self, inp: RunInput) -> RunRecord:
        started = time.perf_counter()

        signals, signals_ms, signal_results = self._signals(inp)

        rewrite_started = time.perf_counter()
        rewrite, rewrite_degraded, rewrite_result = self._rewrite(inp, signals)
        rewrite_ms = _elapsed_ms(rewrite_started)

        govern_started = time.perf_counter()
        govern = self._govern(inp, signals, rewrite, rewrite_degraded)
        govern_ms = _elapsed_ms(govern_started)

        degraded = tuple(sorted(set(signals.meta.degraded) | set(rewrite_degraded)))
        usage = self._usage(signal_results, rewrite_result)
        all_results = [*signal_results, *([rewrite_result] if rewrite_result else [])]
        cache_source_models = sorted({r.model for r in all_results if r.cached})

        return RunRecord(
            schema_version=SCHEMA_VERSION,
            run_id=inp.run_id,
            sample_id=inp.sample_id,
            created_at=now_iso(),
            input=inp,
            signals=signals,
            rewrite=rewrite,
            govern=govern,
            usage=usage,
            timing=Timing(
                signals_ms=signals_ms,
                rewrite_ms=rewrite_ms,
                govern_ms=govern_ms,
                total_ms=_elapsed_ms(started),
            ),
            versions=Versions(
                code_commit=git_commit(),
                config_hash=self._config_hash,
                prompt_versions=dict(self.cfg.prompt_versions),
                models={
                    "signal": self.cfg.models.signal.model,
                    "rewrite": self.cfg.models.rewrite.model,
                },
            ),
            meta={
                "provider": self.client.name,
                "signal_fingerprint": signal_fingerprint(signals),
                "lexicon_version": self.lexicon.version,
                "degraded": list(degraded),
                # Provenance of cached answers, so a hit produced by a different
                # model is visible in the record instead of being invisible.
                "cache_source_models": cache_source_models,
                "system_fingerprints": sorted(
                    {r.system_fingerprint for r in all_results if r.system_fingerprint}
                ),
                "model_seeds": {
                    "signal": self.cfg.models.signal.seed,
                    "rewrite": self.cfg.models.rewrite.seed,
                },
            },
        )

    # -- stage 1: signals -------------------------------------------------- #

    def run_signals(self, inp: RunInput) -> tuple[SignalBundle, Usage]:
        """Signal layer only, with usage. Used by the stability probe.

        No cache is involved unless the caller injected a CachedClient; the probe
        asserts ``usage.cache_hits == 0`` so three samples are three real calls.
        """
        signals, _, results = self._signals(inp)
        return signals, self._usage(results, None)

    def _signals(self, inp: RunInput) -> tuple[SignalBundle, int, list[LLMResult]]:
        started = time.perf_counter()
        try:
            rule_findings = self.lexicon.scan(inp.raw_reply)
        except Exception:
            rule_findings = None

        base: dict[str, Any] = {
            "context": inp.context.model_dump(mode="json"),
            "raw_reply": inp.raw_reply,
            "persona_ref": inp.persona.model_dump(mode="json"),
        }
        jobs = (
            ("sarcasm", "signal.sarcasm", base),
            ("emotion", "signal.emotion", base),
            (
                "ai_smell",
                "signal.ai_smell",
                {
                    **base,
                    "lexicon": self.lexicon.to_payload(),
                    # Rules run first; the LLM is told what is already covered.
                    "rule_hits": (
                        rule_hits_payload(rule_findings) if rule_findings is not None else []
                    ),
                },
            ),
        )

        route = self.cfg.models.signal
        with ThreadPoolExecutor(max_workers=len(jobs), thread_name_prefix="signal") as pool:
            futures = {
                pool.submit(self._call, name, prompt_id, payload, route): name
                for name, prompt_id, payload in jobs
            }
            outcomes = {futures[future]: future.result() for future in futures}

        degraded: set[str] = set()

        sarcasm = self._as(SarcasmSignal, outcomes["sarcasm"])
        if sarcasm is None:
            degraded.add("sarcasm")
            sarcasm = SarcasmSignal(
                label=SarcasmLabel.uncertain,
                confidence=0.0,
                target=SarcasmTarget.none,
                reason="sarcasm signal failed; neutral fallback",
            )

        emotion = self._as(EmotionSignal, outcomes["emotion"])
        if emotion is None:
            degraded.add("emotion")
            emotion = EmotionSignal(
                user_emotion=EmotionLabel(label="unknown", intensity=0.0),
                valence=0.0,
                arousal=0.5,
                assistant_tone_target=ToneTarget.matter_of_fact,
            )

        if rule_findings is None:
            degraded.add("ai_smell")
            # Deliberately above the rewrite gate: when we cannot tell whether the
            # reply is AI-flavoured, prefer rewriting over letting it through.
            ai_smell = AiSmellSignal(
                score=self.cfg.thresholds.signal_failure_default_ai_smell,
                hits=(),
                notes="lexicon scan failed; conservative default",
            )
        else:
            extra = self._as(AiSmellExtra, outcomes["ai_smell"])
            if extra is None:
                degraded.add("ai_smell_llm")
                ai_smell = merge_ai_smell(
                    inp.raw_reply,
                    rule_findings,
                    (),
                    model_notes="llm unavailable; rules only",
                )
            else:
                ai_smell = merge_ai_smell(
                    inp.raw_reply,
                    rule_findings,
                    extra.extra_hits,
                    extra.notes,
                )

        persona_state: PersonaState = self.personas.resolve(inp.persona.persona_id)

        latency_ms = _elapsed_ms(started)
        prompt_versions = {
            key: value
            for key, value in self.cfg.prompt_versions.items()
            if key.startswith("signal.")
        }
        results = [outcome.result for outcome in outcomes.values() if outcome.result is not None]
        reported_model = next(
            (result.model for result in results if result.model), self.client.name
        )

        return (
            SignalBundle(
                sarcasm=sarcasm,
                emotion=emotion,
                ai_smell=ai_smell,
                persona_state=persona_state,
                meta=SignalMeta(
                    model=reported_model,
                    latency_ms=latency_ms,
                    prompt_versions=prompt_versions,
                    degraded=tuple(sorted(degraded)),
                ),
            ),
            latency_ms,
            results,
        )

    # -- stage 2: rewrite -------------------------------------------------- #

    def _rewrite(
        self, inp: RunInput, signals: SignalBundle
    ) -> tuple[RewriteOutput, tuple[str, ...], LLMResult | None]:
        max_ratio = (
            inp.options.max_rewrite_ratio
            if inp.options.max_rewrite_ratio is not None
            else self.cfg.thresholds.max_rewrite_ratio
        )
        allow_noop = signals.ai_smell.score < self.cfg.thresholds.rewrite_gate_ai_smell

        payload: dict[str, Any] = {
            "raw_reply": inp.raw_reply,
            "scene": inp.context.scene.value,
            # Provenance (signals.meta) is intentionally not sent to the rewriter;
            # the same projection is what the cache hashes (ADR-004).
            "signals": signals.rewrite_view(),
            "persona_state": signals.persona_state.model_dump(mode="json"),
            "constraints": {
                "allow_noop": allow_noop,
                "max_rewrite_ratio": max_ratio,
                "min_chars": self.cfg.thresholds.min_rewrite_chars,
            },
            "lexicon": self.lexicon.to_payload(),
        }

        degraded: set[str] = set()
        outcome = self._call("rewrite", "rewrite", payload, self.cfg.models.rewrite)
        output = self._as(RewriteOutput, outcome)

        if output is None:
            degraded.add("rewrite")
            output = RewriteOutput(
                text=inp.raw_reply,
                rewrite_ratio=0.0,
                risk_flags=("rewrite_failed",),
                noop=True,
            )

        output = self._audit_rewrite(inp, output, allow_noop, max_ratio)
        return output, tuple(sorted(degraded)), outcome.result

    @staticmethod
    def _audit_rewrite(
        inp: RunInput,
        output: RewriteOutput,
        allow_noop: bool,
        max_ratio: float,
    ) -> RewriteOutput:
        """Recompute the ratio from the actual texts instead of trusting the model."""
        actual = 1.0 - SequenceMatcher(None, inp.raw_reply, output.text).ratio()
        actual = round(max(0.0, min(1.0, actual)), 3)

        flags = list(output.risk_flags)
        if abs(actual - output.rewrite_ratio) > 0.2:
            flags.append("ratio_self_report_mismatch")
        if not output.noop and actual > max_ratio + 0.05:
            flags.append("rewrite_ratio_over_limit")
        if output.noop and not allow_noop:
            flags.append("noop_without_gate_permission")
        if not output.noop and output.text.strip() == inp.raw_reply.strip():
            flags.append("no_change_marked_as_rewrite")

        deduped = tuple(dict.fromkeys(flags))
        return RewriteOutput.model_validate(
            {
                **output.model_dump(),
                "rewrite_ratio": actual,
                "risk_flags": deduped,
            }
        )

    # -- stage 3: governance ----------------------------------------------- #

    def _govern(
        self,
        inp: RunInput,
        signals: SignalBundle,
        rewrite: RewriteOutput,
        rewrite_degraded: tuple[str, ...],
    ) -> GovernOutput:
        started = time.perf_counter()
        policy = self.cfg.govern

        findings = self.lexicon.scan(rewrite.text)
        residue_hits: list[ResidueHit] = [
            ResidueHit(type=finding.type, span=finding.span, rule_id=finding.rule_id)
            for finding in findings
        ]
        forbidden = [
            word for word in signals.persona_state.vocabulary.forbid if word in rewrite.text
        ]
        for word in forbidden:
            residue_hits.append(
                ResidueHit(type=AiSmellType.service_tone, span=word, rule_id=f"vocab:{word}")
            )

        residue_note = (
            "local rewrite disabled in phase 1: hits are recorded, text is emitted as-is"
            if residue_hits and not policy.local_rewrite_enabled
            else ""
        )
        residue = ResidueResult(
            hits=tuple(residue_hits),
            local_rewrite_rounds=0,
            note=residue_note,
        )

        categories: list[str] = []
        if _contains(rewrite.text, _PHONE) or _contains(rewrite.text, _URL):
            categories.append("contact_info")
        if any(word in rewrite.text for word in policy.sensitive_stub):
            categories.append("keyword")
        blocked = bool(categories) and policy.block_on_hit
        sensitive = SensitiveResult(
            blocked=blocked,
            categories=tuple(dict.fromkeys(categories)),
            action="block" if blocked else "pass",
        )

        gate = self.cfg.thresholds.rewrite_gate_ai_smell
        gate_passed = signals.ai_smell.score < gate
        decisions = [
            f"gate: ai_smell={signals.ai_smell.score:.2f} "
            f"{'<' if gate_passed else '>='} {gate:.2f} -> "
            f"{'allow_noop' if gate_passed else 'rewrite'}",
            f"rewrite: noop={rewrite.noop} ratio={rewrite.rewrite_ratio:.2f} "
            f"notes={len(rewrite.change_notes)} risk={len(rewrite.risk_flags)}",
            (
                f"residue: {len(residue_hits)} hit(s); local rewrite deferred (phase 1.5)"
                if residue_hits
                else "residue: clean"
            ),
            f"sensitive: {sensitive.action}; categories={list(sensitive.categories) or 'none'}",
        ]
        if blocked:
            decisions.append("blocked: output withheld by governance")
        if rewrite_degraded:
            decisions.append(f"degraded: {', '.join(rewrite_degraded)}")
        if inp.persona.version != signals.persona_state.version:
            decisions.append(
                f"persona: requested v{inp.persona.version}, "
                f"applied v{signals.persona_state.version}"
            )

        rule_hits = tuple(finding.rule_id for finding in findings) + tuple(
            f"vocab:{word}" for word in forbidden
        )

        return GovernOutput(
            final_text=rewrite.text if not blocked else "（内容被治理层拦截）",
            residue=residue,
            sensitive=sensitive,
            audit=AuditLog(
                trace_id=inp.run_id,
                rule_hits=rule_hits,
                decisions=tuple(decisions),
                elapsed_ms=_elapsed_ms(started),
                config_hash=self._config_hash,
            ),
        )

    # -- helpers ----------------------------------------------------------- #

    def _call(
        self,
        name: str,
        prompt_id: str,
        payload: Mapping[str, Any],
        route: ModelRoute,
    ) -> _Outcome:
        try:
            result = self.client.complete_json(
                prompt_id=prompt_id,
                payload=payload,
                model=route.model,
                temperature=route.temperature,
                seed=route.seed,
            )
            return _Outcome(name=name, data=parse_json_object(result.text), result=result)
        except Exception as exc:  # provider/format failures must degrade, not crash
            return _Outcome(name=name, error=exc)

    @staticmethod
    def _as(model_cls: type[_ModelT], outcome: _Outcome) -> _ModelT | None:
        if outcome.data is None:
            return None
        try:
            return model_cls.model_validate(outcome.data)
        except Exception:
            return None

    def _usage(
        self,
        signal_results: list[LLMResult],
        rewrite_result: LLMResult | None,
    ) -> Usage:
        results = [*signal_results, *([rewrite_result] if rewrite_result else [])]
        billable = [result for result in results if not result.cached]
        prompt_tokens = sum(result.prompt_tokens for result in billable)
        completion_tokens = sum(result.completion_tokens for result in billable)
        return Usage(
            calls=len(billable),
            cache_hits=len(results) - len(billable),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_yuan=estimate_cost(results, self.cfg.cost),
        )


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _contains(text: str, pattern: str) -> bool:
    return re.search(pattern, text) is not None
