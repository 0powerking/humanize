"""Every rewrite_view field must be classified as key-sensitive or deliberately not.

The cache key no longer contains the ``signals`` payload, so a field that is
neither in ``fingerprint_view`` nor in the payload is silently invisible to the
cache. This test enumerates the fields and pins the intent: if someone adds a
field to ``rewrite_view`` without deciding its cache behaviour, the field list
here is the place that should fail review.

Plan A deliberately ignores continuous values that are unbucketed
(intensity, ai_smell score, hit severity) and uses 5/3-bin buckets for
valence/arousal/confidence, so those cases assert equality instead.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from humanize.cache import fingerprint_from_view, make_cache_key
from humanize.io import stable_hash


def _key(view: dict[str, Any]) -> str:
    # Mirrors CachedClient: exact signals are dropped from the payload, the
    # bucketed fingerprint is carried explicitly, persona stays exact.
    payload = {
        "raw_reply": "在吗",
        "scene": "casual",
        "constraints": {"allow_noop": True, "max_rewrite_ratio": 0.6},
        "persona_state": view["persona_state"],
    }
    return make_cache_key(
        stage="rewrite",
        model="deepseek-chat",
        prompt_version="rw.v1",
        payload=payload,
        signal_hash=stable_hash(fingerprint_from_view(view)),
        temperature=0.9,
        seed=42,
    )


def _mutate(view: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    changed = copy.deepcopy(view)
    parts = path.split(".")
    node: Any = changed
    for part in parts[:-1]:
        node = node[int(part)] if part.isdigit() else node[part]
    last = parts[-1]
    if last.isdigit():
        node[int(last)] = value
    else:
        node[last] = value
    return changed


@pytest.fixture()
def base_view(pipeline, sample) -> dict[str, Any]:
    return pipeline.run(sample("e002_sarcasm_late")).signals.rewrite_view()


@pytest.mark.parametrize(
    ("path", "value", "changes_key"),
    [
        ("sarcasm.label", "literal", True),
        ("sarcasm.target", "user", True),
        ("sarcasm.confidence", 0.5, True),  # 0.9 -> low confidence bucket
        ("emotion.user_emotion.label", "平静", True),
        ("emotion.user_emotion.intensity", 0.95, False),  # deliberately unbucketed
        ("emotion.valence", 0.9, True),  # crosses several buckets
        ("emotion.arousal", 0.3, True),
        ("emotion.assistant_tone_target", "soothe", True),
        ("ai_smell.score", 0.99, False),  # types only
        ("ai_smell.hits.0.type", "enumeration", True),
        ("ai_smell.hits.0.severity", 3, False),  # types only
        ("persona_state.persona_id", "p_kezhi", True),
        ("persona_state.display_name", "克制同事", True),
        ("persona_state.applied.warmth", 0.95, True),
        ("persona_state.constraints.0", "新的硬约束", True),
        ("persona_state.tone_hint", "新的语气提示", True),
        ("persona_state.vocabulary.forbid.0", "新禁词", True),
    ],
)
def test_cache_key_tracks_rewrite_view(
    base_view: dict[str, Any], path: str, value: Any, changes_key: bool
) -> None:
    changed = _mutate(base_view, path, value)

    if changes_key:
        assert _key(changed) != _key(base_view), f"{path} changed but key did not"
    else:
        assert _key(changed) == _key(base_view), f"{path} should not change the key"


def test_cache_key_is_stable_within_a_bucket(base_view: dict[str, Any]) -> None:
    low_valence_a = _mutate(base_view, "emotion.valence", -0.61)
    low_valence_b = _mutate(base_view, "emotion.valence", -0.64)
    high_conf_a = _mutate(base_view, "sarcasm.confidence", 0.85)
    high_conf_b = _mutate(base_view, "sarcasm.confidence", 0.95)

    assert _key(low_valence_a) == _key(low_valence_b)
    assert _key(high_conf_a) == _key(high_conf_b)
