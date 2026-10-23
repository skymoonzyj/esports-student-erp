from __future__ import annotations

import re

_PHONE_VALUE = re.compile(
    r"(?<!\d)1[^\dA-Za-z]{0,3}[3-9](?:[^\dA-Za-z]{0,3}\d){9}(?!\d)"
)
_IDENTITY_VALUE = re.compile(
    r"(?<!\d)(?:\d[^\dA-Za-z]{0,3}){17}[0-9Xx](?!\d)"
)
_URLSAFE_SECRET = re.compile(
    r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])"
)
_CANONICAL_UUID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)
_REQUEST_ID_PREFIX = r"(?:e2e|[a-z]+)(?:[._-][a-z]+)*"
_HUMAN_READABLE_REQUEST_ID = re.compile(
    r"(?:e2e|[a-z]+)(?:[._-](?:[a-z]+|\d+))+\Z",
    re.IGNORECASE,
)
_PREFIXED_UUID_REQUEST_ID = re.compile(
    rf"{_REQUEST_ID_PREFIX}[._-][0-9a-f]{{8}}-[0-9a-f]{{4}}-"
    r"[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z",
    re.IGNORECASE,
)
_PREFIXED_HEX_REQUEST_ID = re.compile(
    rf"{_REQUEST_ID_PREFIX}[._-][0-9a-f]{{32}}\Z",
    re.IGNORECASE,
)
_OPAQUE_REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{32,}\Z")
_REQUEST_ID_PART = re.compile(r"[A-Za-z0-9]+")
_SENSITIVE_REQUEST_ID_LABELS = frozenset(
    {
        "bearer",
        "ciphertext",
        "credential",
        "csrf",
        "password",
        "secret",
        "state",
        "ticket",
        "token",
    }
)
_OPAQUE_REQUEST_ID_MATERIAL_MIN_LENGTH = 20
_CREDENTIAL_REQUEST_ID_MATERIAL_MIN_LENGTH = 32
_SENSITIVE_REQUEST_ID_LABEL_PAIRS = frozenset(
    {
        ("access", "token"),
        ("client", "secret"),
        ("refresh", "token"),
        ("service", "credential"),
        ("service", "token"),
    }
)


def contains_phone_or_identity(value: str) -> bool:
    if _CANONICAL_UUID.fullmatch(value):
        return False
    return bool(_PHONE_VALUE.search(value) or _IDENTITY_VALUE.search(value))


def looks_like_raw_secret(value: str) -> bool:
    for match in _URLSAFE_SECRET.finditer(value):
        candidate = match.group(0)
        classes = sum(
            (
                any(character.islower() for character in candidate),
                any(character.isupper() for character in candidate),
                any(character.isdigit() for character in candidate),
                "_" in candidate or "-" in candidate,
            )
        )
        if classes >= 3:
            return True
    return False


def _looks_like_opaque_parts(parts: list[str]) -> bool:
    if len("".join(parts)) < _OPAQUE_REQUEST_ID_MATERIAL_MIN_LENGTH:
        return False
    if any(
        len(part) >= _OPAQUE_REQUEST_ID_MATERIAL_MIN_LENGTH
        for part in parts
    ):
        return True
    return bool(
        len(parts) == 2
        and (
            (parts[0].isalpha() and parts[1].isdigit())
            or (parts[0].isdigit() and parts[1].isalpha())
        )
        and min(len(part) for part in parts) >= 8
    )


def _has_sensitive_opaque_material(value: str) -> bool:
    matches = list(_REQUEST_ID_PART.finditer(value))
    parts = [match.group(0) for match in matches]
    folded = [part.casefold() for part in parts]
    for index, part in enumerate(folded):
        if part not in _SENSITIVE_REQUEST_ID_LABELS:
            continue
        trailing_parts = parts[index + 1 :]
        leading_sensitive_label = index == 0 or (
            part == "state" and folded[index - 1] == "oauth"
        )
        trailing_value = value[matches[index].end() :].lstrip("._-")
        if _looks_like_opaque_parts(trailing_parts) or (
            leading_sensitive_label
            and len(trailing_value) >= _OPAQUE_REQUEST_ID_MATERIAL_MIN_LENGTH
        ):
            return True
    if len("".join(parts)) < _CREDENTIAL_REQUEST_ID_MATERIAL_MIN_LENGTH:
        return False
    return any(
        (folded[index], folded[index + 1])
        in _SENSITIVE_REQUEST_ID_LABEL_PAIRS
        for index in range(len(folded) - 1)
    )


def contains_sensitive_request_id(value: str) -> bool:
    if _CANONICAL_UUID.fullmatch(value):
        return False
    if _has_sensitive_opaque_material(value):
        return True
    if (
        _PREFIXED_UUID_REQUEST_ID.fullmatch(value)
        or _PREFIXED_HEX_REQUEST_ID.fullmatch(value)
    ):
        return False
    if contains_phone_or_identity(value):
        return True
    if _HUMAN_READABLE_REQUEST_ID.fullmatch(value):
        return False
    return bool(_OPAQUE_REQUEST_ID.fullmatch(value) or looks_like_raw_secret(value))
