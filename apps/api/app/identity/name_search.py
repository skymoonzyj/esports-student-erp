from __future__ import annotations

import re
import unicodedata


# GB2312 ranges provide a dependency-free initial lookup for the commonly
# used Chinese characters in names. The full name remains the display value.
_PINYIN_INITIAL_RANGES: tuple[tuple[int, str], ...] = (
    (-20319, "a"),
    (-20283, "b"),
    (-19775, "c"),
    (-19218, "d"),
    (-18710, "e"),
    (-18526, "f"),
    (-18239, "g"),
    (-17922, "h"),
    (-17417, "j"),
    (-16474, "k"),
    (-16212, "l"),
    (-15640, "m"),
    (-15165, "n"),
    (-14922, "o"),
    (-14914, "p"),
    (-14630, "q"),
    (-14149, "r"),
    (-14090, "s"),
    (-13318, "t"),
    (-12838, "w"),
    (-12556, "x"),
    (-11847, "y"),
    (-11056, "z"),
)
_INITIAL_QUERY_PATTERN = re.compile(r"^[a-z]+$")


def normalize_name_query(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def _han_initial(character: str) -> str:
    if not "\u4e00" <= character <= "\u9fff":
        return ""
    try:
        encoded = character.encode("gb2312")
    except UnicodeEncodeError:
        return ""
    if len(encoded) != 2:
        return ""
    code = encoded[0] * 256 + encoded[1] - 65536
    for boundary, initial in reversed(_PINYIN_INITIAL_RANGES):
        if code >= boundary:
            return initial
    return ""


def name_initials(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    initials: list[str] = []
    for character in normalized:
        if character.isspace():
            continue
        if "a" <= character <= "z" or character.isdigit():
            initials.append(character)
            continue
        initial = _han_initial(character)
        if initial:
            initials.append(initial)
    return "".join(initials)


def is_name_initial_query(value: str) -> bool:
    return _INITIAL_QUERY_PATTERN.fullmatch(normalize_name_query(value)) is not None


def name_matches_query(name: str, query: str) -> bool:
    normalized_query = normalize_name_query(query)
    if not normalized_query:
        return False
    if is_name_initial_query(normalized_query):
        return name_initials(name).startswith(normalized_query)
    return normalized_query in normalize_name_query(name)
