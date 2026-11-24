from __future__ import annotations

from humanize.sensitive import SensitiveDetector


def test_phone_is_contact_info() -> None:
    detector = SensitiveDetector(keywords=())

    result = detector.result("我手机13800138000，回聊")

    assert result.categories == ("contact_info",)
    assert result.blocked is False


def test_url_is_contact_info() -> None:
    detector = SensitiveDetector(keywords=())

    result = detector.result("看这个 https://example.com/x")

    assert result.categories == ("contact_info",)


def test_keyword_stub() -> None:
    detector = SensitiveDetector(keywords=("加微信", "代开发票"))

    result = detector.result("加微信聊，代开发票也行")

    assert result.categories == ("keyword",)


def test_block_on_hit() -> None:
    detector = SensitiveDetector(keywords=("加微信",), block_on_hit=True)

    result = detector.result("加微信")

    assert result.blocked is True
    assert result.action == "block"


def test_clean_text_passes() -> None:
    detector = SensitiveDetector(keywords=("加微信",))

    result = detector.result("周六下午见")

    assert result.categories == ()
    assert result.action == "pass"
