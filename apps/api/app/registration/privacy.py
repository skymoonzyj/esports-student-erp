from __future__ import annotations

from app.core.privacy import (
    contains_email,
    contains_labeled_sensitive_value,
    contains_phone_or_identity,
    looks_like_raw_secret,
)


def contains_sensitive_registration_text(value: str) -> bool:
    return bool(
        contains_email(value)
        or contains_labeled_sensitive_value(value)
        or contains_phone_or_identity(value)
        or looks_like_raw_secret(value)
    )
