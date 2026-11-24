"""LLM transport contracts.

* ``DeepSeekClient`` -- real provider over the OpenAI-compatible chat API.
* ``MockClient``     -- deterministic, offline stand-in used by tests and by
  ``--provider mock``; its rewrite is a crude de-AI dictionary, not the product.

``LLMClient`` is deliberately narrow: given a stage prompt id and a JSON-safe
payload, return raw text that must contain one JSON object. Prompt rendering,
retries and token accounting live behind this seam; the pipeline never changes
when a provider is swapped.
"""

from __future__ import annotations

import json
import random
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Protocol

from .lexicon import Lexicon
from .prompts import PromptStore


class LLMError(RuntimeError):
    """Transport / provider failure (timeout, HTTP error, ...)."""


class LLMFormatError(LLMError):
    """The model returned text that does not contain a usable JSON object."""


@dataclass(frozen=True)
class LLMResult:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    prompt_cache_hit_tokens: int = 0
    system_fingerprint: str | None = None
    latency_ms: int = 0
    cached: bool = False


class LLMClient(Protocol):
    name: str

    def complete_json(
        self,
        *,
        prompt_id: str,
        payload: Mapping[str, Any],
        model: str,
        temperature: float,
        seed: int | None = None,
    ) -> LLMResult:
        """Return the model's raw text; caller parses and validates it."""


_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)


def parse_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object out of model output.

    Tolerates fenced blocks and leading/trailing prose, because that is what
    real models do even when told "JSON only". Anything else fails loudly.
    """
    stripped = text.strip()
    fenced = _FENCE.match(stripped)
    if fenced:
        stripped = fenced.group(1).strip()

    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        start, end = stripped.find("{"), stripped.rfind("}")
        if start == -1 or end <= start:
            raise LLMFormatError(f"no JSON object in model output: {text[:200]!r}") from None
        try:
            data = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMFormatError(f"invalid JSON object: {exc}") from exc

    if not isinstance(data, dict):
        raise LLMFormatError(f"expected a JSON object, got {type(data).__name__}")
    return data


# --------------------------------------------------------------------------- #
# DeepSeek (OpenAI-compatible chat completions)
# --------------------------------------------------------------------------- #

_RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


class ProviderHTTPError(LLMError):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}: {body[:200]}")
        self.status = status
        self.body = body


class DeepSeekClient:
    """Real provider. Retries transient failures, never retries auth/validation."""

    name = "deepseek"

    def __init__(
        self,
        *,
        api_key: str,
        prompt_store: PromptStore,
        base_url: str = "https://api.deepseek.com",
        timeout_s: float = 60.0,
        max_retries: int = 2,
        backoff_base_s: float = 1.0,
        json_mode: bool = True,
        transport: Callable[[dict[str, Any]], Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key.strip():
            raise ValueError("DeepSeek api_key is empty")
        self._api_key = api_key.strip()
        self._prompts = prompt_store
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._timeout_s = timeout_s
        self._max_retries = max(0, max_retries)
        self._backoff_base_s = max(0.0, backoff_base_s)
        self._json_mode = json_mode
        self._transport = transport or self._http_post
        self._sleep = sleep

    def complete_json(
        self,
        *,
        prompt_id: str,
        payload: Mapping[str, Any],
        model: str,
        temperature: float,
        seed: int | None = None,
    ) -> LLMResult:
        started = time.perf_counter()
        rendered = self._prompts.render(prompt_id, payload)
        body: dict[str, Any] = {
            "model": model,
            "messages": rendered.messages,
            "temperature": temperature,
        }
        if seed is not None:
            body["seed"] = seed
        if self._json_mode:
            body["response_format"] = {"type": "json_object"}

        data = self._post_with_retry(body)
        latency_ms = int((time.perf_counter() - started) * 1000)

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected provider response: {str(data)[:300]}") from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMError("provider returned empty content")

        usage = data.get("usage") or {}
        return LLMResult(
            text=content,
            model=str(data.get("model") or model),
            prompt_tokens=_as_int(usage.get("prompt_tokens")),
            completion_tokens=_as_int(usage.get("completion_tokens")),
            prompt_cache_hit_tokens=_as_int(usage.get("prompt_cache_hit_tokens")),
            system_fingerprint=(
                str(data["system_fingerprint"]) if data.get("system_fingerprint") else None
            ),
            latency_ms=latency_ms,
        )

    def _post_with_retry(self, body: dict[str, Any]) -> Any:
        attempts = self._max_retries + 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                return self._transport(body)
            except ProviderHTTPError as exc:
                if exc.status not in _RETRYABLE_STATUS:
                    raise LLMError(
                        f"provider rejected request (HTTP {exc.status}): {exc.body[:300]}"
                    ) from exc
                last_error = exc
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                last_error = exc
            if attempt + 1 < attempts:
                delay = self._backoff_base_s * (2**attempt) + random.uniform(0.0, 0.2)
                self._sleep(delay)
        raise LLMError(f"provider call failed after {attempts} attempt(s): {last_error}")

    def _http_post(self, body: dict[str, Any]) -> Any:
        request = urllib.request.Request(
            self._url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as response:
                raw = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", "replace") if exc.fp else ""
            raise ProviderHTTPError(exc.code, body_text) from exc
        return json.loads(raw)


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


# --------------------------------------------------------------------------- #
# Mock client
# --------------------------------------------------------------------------- #

_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?；;])")
_DROP_PREFIX = re.compile(r"^(?:首先|其次|最后|第一|第二|第三)[，,、：:]\s*")
_LEADING_HEDGE = re.compile(r"^(?:其实|说实话|老实说|总的来说)[，,]\s*")
_FACT_RE = re.compile(r"\d{1,2}点|\d{1,3}家|\d{1,3}%|周[一二三四五六日末]|\d{1,2}月\d{1,2}日")

#: Crude "AI 味 -> 人话" substitutions. This is a placeholder for the Day 5
#: prompt; it is intentionally small and survives only until then.
_SUBSTITUTIONS: tuple[tuple[str, str], ...] = (
    ("我理解你的不满情绪", "换我我也烦"),
    ("从另一个角度来看", "不过"),
    ("确实存在不确定性", "说不准"),
    ("要不我们改天再约", "要不改天"),
    ("并不是一件坏事", "也不算啥坏事"),
    ("不要灰心", "先别急着否定自己"),
    ("你需要评估自己的核心诉求", "先想清楚你图啥"),
)

#: Sentences that carry no information a friend would actually say.
_DROP_SENTENCE_MARKERS: dict[str, tuple[str, ...]] = {
    "over_explain": ("从另一个角度", "换个角度", "客观来说", "不可否认", "让人感到"),
    "golden_quote": ("每一次", "任何选择", "相信你", "你一定可以", "未来可期"),
}

_SARCASM_MARKERS = ("可真", "真行", "呵呵", "您可", "编也", "说得好像", "行吧", "牛啊", "厉害了")

_EMOTION_RULES: tuple[tuple[tuple[str, ...], str, float, float, str], ...] = (
    (("挂", "失败", "不适合", "没用", "崩"), "失落", -0.65, 0.35, "soothe"),
    (("说好", "迟到", "凭什么", "等着", "又"), "不满", -0.55, 0.65, "banter"),
    (("累", "加班", "困", "烦"), "疲惫", -0.35, 0.30, "soothe"),
    (("换工作", "面试", "报错", "怎么办"), "焦虑", -0.25, 0.45, "matter_of_fact"),
)


class MockClient:
    """Deterministic, offline stand-in for the real model."""

    name = "mock"

    def __init__(self, lexicon: Lexicon) -> None:
        self._lexicon = lexicon

    def complete_json(
        self,
        *,
        prompt_id: str,
        payload: Mapping[str, Any],
        model: str,
        temperature: float,
        seed: int | None = None,
    ) -> LLMResult:
        started = time.perf_counter()
        if prompt_id == "signal.sarcasm":
            data: dict[str, Any] = self._sarcasm(payload)
        elif prompt_id == "signal.emotion":
            data = self._emotion(payload)
        elif prompt_id == "signal.ai_smell":
            data = self._ai_smell(payload)
        elif prompt_id == "rewrite":
            data = self._rewrite(payload)
        else:
            raise LLMError(f"mock client has no handler for prompt_id={prompt_id!r}")

        text = json.dumps(data, ensure_ascii=False)
        if prompt_id == "signal.ai_smell":
            # Exercise the fenced-JSON parsing path that real models hit.
            text = f"```json\n{text}\n```"
        latency_ms = int((time.perf_counter() - started) * 1000)
        return LLMResult(text=text, model=self.name, latency_ms=latency_ms)

    # -- signals ----------------------------------------------------------- #

    def _sarcasm(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        turns = payload["context"]["turns"]
        user_text = "\n".join(t["content"] for t in turns if t["role"] == "user")
        haystack = f"{user_text}\n{payload['raw_reply']}"
        matched = [marker for marker in _SARCASM_MARKERS if marker in haystack]
        if matched:
            return {
                "label": "sarcastic",
                "confidence": 0.75,
                "target": "assistant",
                "evidence": [_sentence_with(haystack, matched[0])],
                "reason": f"命中反讽标记：{matched[0]}（字面褒义 + 上下文抱怨）",
            }
        return {
            "label": "literal",
            "confidence": 0.6,
            "target": "none",
            "evidence": [],
            "reason": "未发现反讽标记，按字面理解",
        }

    def _emotion(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        turns = payload["context"]["turns"]
        user_text = "\n".join(t["content"] for t in turns if t["role"] == "user")
        haystack = f"{user_text}\n{payload['raw_reply']}"
        for markers, label, valence, arousal, tone in _EMOTION_RULES:
            hit = next((m for m in markers if m in haystack), None)
            if hit:
                return {
                    "user_emotion": {"label": label, "intensity": 0.7},
                    "valence": valence,
                    "arousal": arousal,
                    "assistant_tone_target": tone,
                    "cues": [_sentence_with(haystack, hit)],
                }
        return {
            "user_emotion": {"label": "平静", "intensity": 0.3},
            "valence": 0.0,
            "arousal": 0.3,
            "assistant_tone_target": "matter_of_fact",
            "cues": [],
        }

    def _ai_smell(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        # Rules are applied by the pipeline before the LLM; the mock adds nothing
        # semantic, so it returns an empty extras list (same contract as v2 prompt).
        return {
            "extra_hits": [],
            "notes": f"mock: rules-only baseline, lexicon={self._lexicon.version}",
        }

    # -- rewrite ----------------------------------------------------------- #

    def _rewrite(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        raw: str = payload["raw_reply"]
        signals: dict[str, Any] = payload["signals"]
        constraints: dict[str, Any] = payload["constraints"]
        min_chars: int = constraints.get("min_chars", 3)
        max_ratio: float = constraints.get("max_rewrite_ratio", 1.0)

        if constraints.get("allow_noop", False):
            return {
                "text": raw,
                "rewrite_ratio": 0.0,
                "change_notes": [],
                "kept_facts": _FACT_RE.findall(raw),
                "risk_flags": [],
                "noop": True,
            }

        notes: list[dict[str, Any]] = []
        kept: list[str] = []
        for sentence in _sentences(raw):
            core = sentence.strip()
            if _DROP_PREFIX.match(core):
                notes.append(_note("删掉列举句式", "enumeration", signals))
                continue
            dropped = next(
                (
                    category
                    for category, markers in _DROP_SENTENCE_MARKERS.items()
                    if any(marker in core for marker in markers)
                ),
                None,
            )
            if dropped:
                notes.append(_note("删掉解释/金句句", dropped, signals))
                continue
            kept.append(core)

        text = "".join(kept)
        for old, new in _SUBSTITUTIONS:
            if old in text:
                text = text.replace(old, new)
                notes.append(
                    {
                        "op": f"口语化替换「{old}」",
                        "reason": "tone: 书面表达改为口语",
                        "signal_ref": None,
                    }
                )

        text = _LEADING_HEDGE.sub("", text)
        text = re.sub(r"[！!]+", "。", text)
        text = re.sub(r"。{2,}", "。", text).strip()

        risk_flags: list[str] = []
        if len(text) < min_chars:
            risk_flags.append("mock_rewrite_too_short")
            text = raw
            notes.append({"op": "回退原文", "reason": "mock 改写过于激进", "signal_ref": None})

        ratio = round(1.0 - SequenceMatcher(None, raw, text).ratio(), 3)
        ratio = max(0.0, min(1.0, ratio))
        if ratio > max_ratio:
            risk_flags.append("rewrite_ratio_over_limit")

        return {
            "text": text,
            "rewrite_ratio": ratio,
            "change_notes": notes,
            "kept_facts": _FACT_RE.findall(raw),
            "risk_flags": risk_flags,
            "noop": False,
        }


def _sentences(text: str) -> list[str]:
    return [part for part in _SENTENCE_SPLIT.split(text) if part.strip()]


def _sentence_with(text: str, marker: str) -> str:
    for sentence in _sentences(text):
        if marker in sentence:
            return sentence.strip()[:60]
    return marker


def _note(op: str, smell_type: str, signals: Mapping[str, Any]) -> dict[str, Any]:
    hits = signals.get("ai_smell", {}).get("hits", [])
    ref = next(
        (
            f"ai_smell.hits[{index}]"
            for index, hit in enumerate(hits)
            if hit.get("type") == smell_type
        ),
        None,
    )
    return {"op": op, "reason": f"{smell_type}: 命中 AI 味词典", "signal_ref": ref}
