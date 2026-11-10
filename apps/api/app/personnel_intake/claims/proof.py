"""Immutable submitted facts and historical email proof; no settings or current-OTP dependency."""

from __future__ import annotations

import hmac
import json
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.identity.document_identity import (
    DocumentIdentityKeys,
    DocumentType,
    document_identity_keys,
    normalize_document_identity,
    normalize_hk_mo_permit,
)
from app.identity.normalization import normalize_cn_id
from app.personnel_intake.claims.models import ProfileClaim, ProfileClaimSession
from app.personnel_intake.claims.schemas import ClaimCompletionFields, ClaimStartInput
from app.registration.models import RegistrationVerificationChallenge
from app.registration.verification_service import VerificationInvalidOrExpired
from app.security.encryption import EncryptedFieldError, EnvelopeCipher, LookupDigest

SUBMISSION_VERSION = 1
SUBMISSION_FIELD = "profile_claim_submission"
SUBMISSION_DOMAIN = "profile-claim-submission-v1"


@dataclass(frozen=True, repr=False)
class ClaimSubmission:
    full_name: str
    identity_number: str
    email: str
    version: int = SUBMISSION_VERSION
    document_type: DocumentType = "cn_id"
    issuing_country: str | None = None
    completion: ClaimCompletionFields | None = None

    @property
    def domain(self) -> str:
        return SUBMISSION_DOMAIN if self.version == 1 else "profile-claim-submission-v2"

    def identity_keys(self, lookup: LookupDigest) -> DocumentIdentityKeys:
        cn = normalize_cn_id(self.identity_number) if self.document_type == "cn_id" else None
        return document_identity_keys(
            normalize_document_identity(
                self.document_type,
                self.identity_number,
                issuing_country=self.issuing_country,
                sex=cn.sex if cn else None,
                date_of_birth=cn.birth_date if cn else None,
                allow_pending=True,
            ),
            lookup,
        )

    def canonical(self) -> str:
        data = {
            "version": self.version,
            "full_name": self.full_name,
            "identity_number": self.identity_number,
            "email": self.email,
        }
        if self.version == 2:
            data.update(
                document_type=self.document_type,
                issuing_country=self.issuing_country,
                completion=completion_material(self.completion),
            )
        return json.dumps(
            data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


def completion_material(value: ClaimCompletionFields | None) -> dict[str, Any] | None:
    """Explicit plaintext mapping, used only inside authenticated encrypted proof."""
    if value is None:
        return None
    return {
        "phone": value.phone.get_secret_value() if value.phone else None,
        "sex": value.sex,
        "date_of_birth": value.date_of_birth.isoformat() if value.date_of_birth else None,
        "issuing_country": value.issuing_country,
        "guardians": [
            {
                "full_name": g.full_name,
                "identity_number": normalize_hk_mo_permit(g.identity_number.get_secret_value())
                if g.document_type == "hk_mo_permit"
                else g.identity_number.get_secret_value(),
                "phone": g.phone.get_secret_value(),
                "email": g.email.get_secret_value() if g.email else None,
                "sex": g.sex,
                "date_of_birth": g.date_of_birth.isoformat(),
                "relationship": g.relationship,
                "is_primary": g.is_primary,
                **(
                    {
                        "document_type": g.document_type,
                        "document_issuing_country": g.document_issuing_country,
                    }
                    if g.document_type != "cn_id" or g.document_issuing_country is not None
                    else {}
                ),
            }
            for g in value.guardians
        ],
    }


def submission_from_input(value: ClaimStartInput) -> ClaimSubmission:
    return ClaimSubmission(
        value.full_name.get_secret_value(),
        value.identity_number.get_secret_value(),
        value.email.get_secret_value(),
        version=2
        if value.document_type != "cn_id"
        or value.completion is not None
        or value.issuing_country is not None
        else 1,
        document_type=value.document_type,
        issuing_country=value.issuing_country
        or (value.completion.issuing_country if value.completion else None),
        completion=value.completion,
    )


def decode_claim_submission(
    *,
    ciphertext: str,
    payload_digest: str,
    identity_digest: str,
    email_digest: str,
    cipher: EnvelopeCipher,
    lookup: LookupDigest,
    submission_version: int = SUBMISSION_VERSION,
) -> ClaimSubmission:
    """Authenticate ciphertext, canonical normalized input, version and each digest domain."""
    try:
        raw = cipher.decrypt(ciphertext, field_name=SUBMISSION_FIELD)
        data = json.loads(raw)
        if not isinstance(data, dict) or data.pop("version", None) != submission_version:
            raise ValueError("invalid submission version")
        if submission_version not in (1, 2):
            raise ValueError("unsupported submission version")
        result = submission_from_input(ClaimStartInput.model_validate(data))
        if raw != result.canonical() or not all(
            (
                hmac.compare_digest(lookup.digest_domain(result.domain, raw), payload_digest),
                hmac.compare_digest(result.identity_keys(lookup).identity_digest, identity_digest),
                hmac.compare_digest(
                    lookup.digest_domain("registration-verification-email", result.email),
                    email_digest,
                ),
            )
        ):
            raise ValueError("invalid submission evidence")
        return result
    except (EncryptedFieldError, ValidationError, ValueError, TypeError) as error:
        raise VerificationInvalidOrExpired("claim evidence is invalid") from error


def validate_claim_proof(
    db: Session,
    claim: ProfileClaim,
    *,
    cipher: EnvelopeCipher,
    lookup: LookupDigest,
) -> ClaimSubmission:
    """Validate original consumed proof, NOT today's OTP validity or an access session.

    Review callers acquire their lifecycle locks first. A redacted original
    session input is legitimate; the claim's immutable encrypted input survives.
    """
    value = decode_claim_submission(
        ciphertext=claim.payload_ciphertext,
        payload_digest=claim.payload_digest,
        identity_digest=claim.identity_digest,
        email_digest=claim.email_digest,
        submission_version=claim.submission_version,
        cipher=cipher,
        lookup=lookup,
    )
    original = db.get(ProfileClaimSession, claim.session_id, populate_existing=True)
    challenge = db.get(
        RegistrationVerificationChallenge, claim.challenge_id, populate_existing=True
    )
    if (
        original is None
        or challenge is None
        or original.state != "verified"
        or original.submission_version != claim.submission_version
        or original.claim_id != claim.id
        or (
            original.challenge_id,
            original.payload_digest,
            original.identity_digest,
            original.email_digest,
        )
        != (claim.challenge_id, claim.payload_digest, claim.identity_digest, claim.email_digest)
        or original.verified_at != claim.verified_at
        or challenge.purpose != "profile_claim"
        or challenge.identifier_kind != "email"
        or challenge.application_id is not None
        or challenge.account_id is not None
        or challenge.identifier_digest != claim.email_digest
        or challenge.consumed_at != claim.challenge_consumed_at
        or claim.challenge_consumed_at != claim.verified_at
        or challenge.consumed_at is None
        or challenge.attempts >= 5
        or challenge.consumed_at >= challenge.expires_at
        or claim.verified_at >= original.expires_at
    ):
        raise VerificationInvalidOrExpired("claim evidence is invalid")
    return value
