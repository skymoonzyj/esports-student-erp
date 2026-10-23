from __future__ import annotations

import pytest

from app.food_preferences.normalization import (
    FoodTextValidationError,
    normalize_food_display,
    normalize_food_key,
)


@pytest.mark.parametrize(
    ("raw", "display", "key"),
    [
        ("  宫保  鸡丁  ", "宫保 鸡丁", "宫保 鸡丁"),
        ("ＳｉＣｈｕａｎ", "SiChuan", "sichuan"),
        ("粤\u3000菜", "粤 菜", "粤 菜"),
    ],
)
def test_normalize_food_text(raw: str, display: str, key: str) -> None:
    assert normalize_food_display(raw) == display
    assert normalize_food_key(raw) == key


@pytest.mark.parametrize("raw", ["", "   ", "x" * 41])
def test_normalize_food_text_rejects_invalid_length(raw: str) -> None:
    with pytest.raises(FoodTextValidationError):
        normalize_food_display(raw)


def test_normalize_food_text_applies_nfkc_before_whitespace_folding() -> None:
    assert normalize_food_display("  ①　ＳｉＣｈｕａｎ  ") == "1 SiChuan"


@pytest.mark.parametrize("whitespace", ["\t", "\n", "\r", "\v", "\f", "\u00a0", "\u2003"])
def test_normalize_food_text_folds_unicode_whitespace(whitespace: str) -> None:
    assert normalize_food_display(f"川{whitespace}{whitespace}菜") == "川 菜"


def test_normalize_food_text_preserves_display_case_but_casefolds_only_key() -> None:
    assert normalize_food_display("  Straẞe  ") == "Straẞe"
    assert normalize_food_key("  Straẞe  ") == "strasse"


def test_normalize_food_text_does_not_fuzzy_match_similar_names() -> None:
    assert normalize_food_key("宫保鸡丁") != normalize_food_key("宫爆鸡丁")


def test_normalize_food_text_accepts_a_configured_max_length() -> None:
    assert normalize_food_display("abcd", max_length=4) == "abcd"
    with pytest.raises(FoodTextValidationError, match="1 to 4"):
        normalize_food_display("abcde", max_length=4)
