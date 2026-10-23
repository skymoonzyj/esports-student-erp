from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.registration.invitation_flow import (
    guardian_invitation_handoff_token_digest,
    invitation_id_from_flow_token,
    issue_guardian_invitation_handoff_token,
    issue_invitation_flow_token,
)
from app.security.encryption import LookupDigest

NOW = datetime(2026, 8, 16, 3, tzinfo=UTC)
INVITATION_ID = UUID("f7180000-0000-4000-8000-000000000001")
PEPPER = bytes(range(32))
BASE64URL_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def test_handoff_secret_is_canonical_high_entropy_and_domain_separated() -> None:
    lookup = LookupDigest(PEPPER)

    token = issue_guardian_invitation_handoff_token()

    assert len(token) == 43
    assert set(token) <= set(BASE64URL_ALPHABET)
    assert guardian_invitation_handoff_token_digest(lookup, token) == (
        lookup.digest_domain("guardian-invitation-handoff", token)
    )
    for invalid in ("", INVITATION_ID.hex, f"{token}=", "x" * 256):
        assert guardian_invitation_handoff_token_digest(lookup, invalid) is None


def test_invitation_flow_token_round_trips_without_exposing_a_bearer_url() -> None:
    token = issue_invitation_flow_token(
        INVITATION_ID,
        pepper=PEPPER,
        now=NOW,
        ttl=timedelta(days=7),
    )

    assert invitation_id_from_flow_token(token, pepper=PEPPER, now=NOW) == INVITATION_ID
    assert INVITATION_ID.hex in token


def test_invitation_flow_token_rejects_tampering_expiry_and_wrong_pepper() -> None:
    token = issue_invitation_flow_token(
        INVITATION_ID,
        pepper=PEPPER,
        now=NOW,
        ttl=timedelta(minutes=5),
    )
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")

    assert invitation_id_from_flow_token(tampered, pepper=PEPPER, now=NOW) is None
    assert (
        invitation_id_from_flow_token(
            token,
            pepper=bytes(range(1, 33)),
            now=NOW,
        )
        is None
    )
    assert (
        invitation_id_from_flow_token(
            token,
            pepper=PEPPER,
            now=NOW + timedelta(minutes=5, microseconds=1),
        )
        is None
    )


def test_invitation_flow_token_rejects_a_noncanonical_signature_alias() -> None:
    token = issue_invitation_flow_token(
        INVITATION_ID,
        pepper=PEPPER,
        now=NOW,
        ttl=timedelta(minutes=5),
    )
    signature = token.rsplit(".", 1)[1]
    last_index = BASE64URL_ALPHABET.index(signature[-1])
    alias_index = (last_index & 0b111100) | ((last_index + 1) & 0b000011)
    alias = f"{token[:-1]}{BASE64URL_ALPHABET[alias_index]}"
    assert alias != token

    assert invitation_id_from_flow_token(alias, pepper=PEPPER, now=NOW) is None


def test_invitation_flow_token_rejects_malformed_values() -> None:
    for value in ("", "not-a-token", "a.b.c", "..", "x" * 2048):
        assert invitation_id_from_flow_token(value, pepper=PEPPER, now=NOW) is None
