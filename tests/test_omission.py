from __future__ import annotations

from humanize.omission import check_omissions


def _check(
    lexicon,
    *,
    raw: str,
    final: str,
    facts: tuple[str, ...] = (),
    context: tuple[str, ...] = (),
):
    return check_omissions(
        raw_reply=raw,
        final_text=final,
        kept_facts=facts,
        context_texts=context,
        lexicon=lexicon,
    )


def test_kept_fact_preserved(lexicon) -> None:
    result = _check(
        lexicon, raw="周六下午三点见", final="周六下午三点见", facts=("周六下午",)
    )

    assert result.flags == ()


def test_kept_fact_partial_marker(lexicon) -> None:
    result = _check(lexicon, raw="周六下午见", final="周六见", facts=("周六下午",))

    assert result.flags == ("partial_omission",)
    assert "partial_omission: 周六下午" in result.details


def test_kept_fact_omitted_but_present_in_context_is_safe(lexicon) -> None:
    result = _check(
        lexicon,
        raw="周六下午见",
        final="好，到时候见",
        facts=("周六下午",),
        context=("那周六下午三点？",),
    )

    assert result.flags == ()


def test_kept_fact_omitted_and_absent_from_context_is_unsafe(lexicon) -> None:
    result = _check(
        lexicon,
        raw="周六下午见",
        final="好，到时候见",
        facts=("周六下午",),
        context=("你到了吗",),
    )

    assert "unsafe_omission" in result.flags
    assert "unsafe_omission: 周六下午" in result.details


def test_content_phrase_omitted_without_context_is_unsafe(lexicon) -> None:
    result = _check(
        lexicon,
        raw="我周六下午要去医院",
        final="改天吧",
        context=(),
    )

    assert "unsafe_omission" in result.flags
    assert "unsafe_omission: 周六" in result.details
    assert "unsafe_omission: 下午" in result.details


def test_content_phrase_omitted_but_in_context_is_safe(lexicon) -> None:
    result = _check(
        lexicon,
        raw="我周六下午要去医院",
        final="改天吧",
        context=("我周六下午要去医院，可能赶不过来",),
    )

    assert result.flags == ()


def test_abstract_paraphrase_is_not_flagged(lexicon) -> None:
    # The first implementation flagged this as 7 omissions; string rules cannot
    # distinguish paraphrase from omission, so abstract content is out of scope.
    result = _check(
        lexicon,
        raw="你需要评估自己的核心诉求，然后做好财务缓冲",
        final="看看自己最想要啥，钱也得留点底",
        context=(),
    )

    assert result.flags == ()


def test_particles_and_connectives_are_not_flagged(lexicon) -> None:
    result = _check(lexicon, raw="行吧，那明天见", final="明天见")

    assert result.flags == ()


def test_deleting_ai_smell_clause_is_not_flagged(lexicon) -> None:
    result = _check(lexicon, raw="首先，你要冷静", final="冷静点")

    assert result.flags == ()


def test_commitment_weakening_is_its_own_flag(lexicon) -> None:
    result = _check(
        lexicon,
        raw="改天再约",
        final="改天呗",
        facts=("改天再约",),
    )

    assert len(result.flags) == 1
    assert result.flags[0].startswith("commitment_weakened:改天再约:")
    assert "coverage=" in result.details[0]


def test_commitment_items_are_counted_per_promise(lexicon) -> None:
    result = _check(
        lexicon,
        raw="改天再约，下次一定",
        final="改天再约",
        facts=("改天再约", "下次一定"),
    )

    assert len(result.flags) == 1
    assert result.flags[0].startswith("commitment_weakened:下次一定:")
    assert len(result.details) == 1


def test_commitment_preserved_is_clean(lexicon) -> None:
    result = _check(
        lexicon,
        raw="改天再约",
        final="改天再约",
        facts=("改天再约",),
    )

    assert result.flags == ()
