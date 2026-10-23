from __future__ import annotations

from app.security.encryption import LookupDigest

_STUDENT_GAME_ID_DOMAIN = "student_game_id"


def student_game_id_digest(lookup: LookupDigest, normalized_game_id: str) -> str:
    return lookup.digest_domain(_STUDENT_GAME_ID_DOMAIN, normalized_game_id)
