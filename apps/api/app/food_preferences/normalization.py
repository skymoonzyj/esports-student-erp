from __future__ import annotations

import re
import unicodedata

_WHITESPACE = re.compile(r"\s+")


class FoodTextValidationError(ValueError):
    """Raised when a cuisine or dish name is outside the accepted text range."""


def normalize_food_display(value: str, *, max_length: int = 40) -> str:
    """Return the canonical display form of a cuisine or dish name.

    Unicode compatibility characters are normalized first, then leading/trailing
    and repeated Unicode whitespace is represented by one ASCII space.  The
    resulting display text keeps its original case; only the comparison helper
    below applies case folding.
    """

    if not isinstance(max_length, int) or isinstance(max_length, bool) or max_length < 1:
        raise ValueError("max_length must be a positive integer")
    display = _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", value).strip())
    if not 1 <= len(display) <= max_length:
        raise FoodTextValidationError(
            f"food text must contain 1 to {max_length} characters"
        )
    return display


def normalize_food_key(value: str, *, max_length: int = 40) -> str:
    """Return the canonical exact-comparison key for food text."""

    return normalize_food_display(value, max_length=max_length).casefold()
