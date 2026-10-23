"""Typed, domain-separated document identity; never infer passport facts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from typing import Literal
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.identity.models import Person
from app.identity.normalization import IdentityValidationError, normalize_cn_id
from app.security.encryption import LookupDigest

DocumentType = Literal["cn_id", "passport"]
# ISO 3166-1 alpha-2 assigned codes, not arbitrary two-letter strings.
ISO_COUNTRIES = frozenset(
    [
        "AD",
        "AE",
        "AF",
        "AG",
        "AI",
        "AL",
        "AM",
        "AO",
        "AQ",
        "AR",
        "AS",
        "AT",
        "AU",
        "AW",
        "AX",
        "AZ",
        "BA",
        "BB",
        "BD",
        "BE",
        "BF",
        "BG",
        "BH",
        "BI",
        "BJ",
        "BL",
        "BM",
        "BN",
        "BO",
        "BQ",
        "BR",
        "BS",
        "BT",
        "BV",
        "BW",
        "BY",
        "BZ",
        "CA",
        "CC",
        "CD",
        "CF",
        "CG",
        "CH",
        "CI",
        "CK",
        "CL",
        "CM",
        "CN",
        "CO",
        "CR",
        "CU",
        "CV",
        "CW",
        "CX",
        "CY",
        "CZ",
        "DE",
        "DJ",
        "DK",
        "DM",
        "DO",
        "DZ",
        "EC",
        "EE",
        "EG",
        "EH",
        "ER",
        "ES",
        "ET",
        "FI",
        "FJ",
        "FK",
        "FM",
        "FO",
        "FR",
        "GA",
        "GB",
        "GD",
        "GE",
        "GF",
        "GG",
        "GH",
        "GI",
        "GL",
        "GM",
        "GN",
        "GP",
        "GQ",
        "GR",
        "GS",
        "GT",
        "GU",
        "GW",
        "GY",
        "HK",
        "HM",
        "HN",
        "HR",
        "HT",
        "HU",
        "ID",
        "IE",
        "IL",
        "IM",
        "IN",
        "IO",
        "IQ",
        "IR",
        "IS",
        "IT",
        "JE",
        "JM",
        "JO",
        "JP",
        "KE",
        "KG",
        "KH",
        "KI",
        "KM",
        "KN",
        "KP",
        "KR",
        "KW",
        "KY",
        "KZ",
        "LA",
        "LB",
        "LC",
        "LI",
        "LK",
        "LR",
        "LS",
        "LT",
        "LU",
        "LV",
        "LY",
        "MA",
        "MC",
        "MD",
        "ME",
        "MF",
        "MG",
        "MH",
        "MK",
        "ML",
        "MM",
        "MN",
        "MO",
        "MP",
        "MQ",
        "MR",
        "MS",
        "MT",
        "MU",
        "MV",
        "MW",
        "MX",
        "MY",
        "MZ",
        "NA",
        "NC",
        "NE",
        "NF",
        "NG",
        "NI",
        "NL",
        "NO",
        "NP",
        "NR",
        "NU",
        "NZ",
        "OM",
        "PA",
        "PE",
        "PF",
        "PG",
        "PH",
        "PK",
        "PL",
        "PM",
        "PN",
        "PR",
        "PS",
        "PT",
        "PW",
        "PY",
        "QA",
        "RE",
        "RO",
        "RS",
        "RU",
        "RW",
        "SA",
        "SB",
        "SC",
        "SD",
        "SE",
        "SG",
        "SH",
        "SI",
        "SJ",
        "SK",
        "SL",
        "SM",
        "SN",
        "SO",
        "SR",
        "SS",
        "ST",
        "SV",
        "SX",
        "SY",
        "SZ",
        "TC",
        "TD",
        "TF",
        "TG",
        "TH",
        "TJ",
        "TK",
        "TL",
        "TM",
        "TN",
        "TO",
        "TR",
        "TT",
        "TV",
        "TW",
        "TZ",
        "UA",
        "UG",
        "UM",
        "US",
        "UY",
        "UZ",
        "VA",
        "VC",
        "VE",
        "VG",
        "VI",
        "VN",
        "VU",
        "WF",
        "WS",
        "YE",
        "YT",
        "ZA",
        "ZM",
        "ZW",
    ]
)


@dataclass(frozen=True)
class NormalizedDocumentIdentity:
    document_type: DocumentType
    number: str
    issuing_country: str | None
    sex: str | None
    date_of_birth: date | None


@dataclass(frozen=True)
class DocumentIdentityKeys:
    identity_digest: str
    passport_number_digest: str | None
    issuing_country: str | None


class DocumentIdentityConflict(ValueError):
    """Same document or ambiguous country requires an authorized manual review."""


def normalize_document_identity(
    document_type: DocumentType,
    number: str,
    *,
    issuing_country: str | None,
    sex: str | None,
    date_of_birth: date | None,
    allow_pending: bool,
) -> NormalizedDocumentIdentity:
    def invalid(field: str) -> None:
        raise IdentityValidationError(
            field=field, code="invalid_document_identity", message="证件资料不完整或无效"
        )

    if document_type == "cn_id":
        document = normalize_cn_id(number, expected_birth_date=date_of_birth)
        if sex is None or date_of_birth is None:
            invalid("sex" if sex is None else "date_of_birth")
        if sex != document.sex:
            raise IdentityValidationError(
                field="sex", code="identity_sex_mismatch", message="性别与身份证不一致"
            )
        if issuing_country not in (None, "CN"):
            invalid("document_issuing_country")
        return NormalizedDocumentIdentity("cn_id", document.number, None, sex, date_of_birth)
    if document_type != "passport":
        invalid("document_type")
    normalized = number.strip().upper()
    if not normalized or len(normalized) > 80 or any(ord(char) < 32 for char in normalized):
        invalid("identity_number")
    country = issuing_country.strip().upper() if issuing_country is not None else None
    if country is not None and country not in ISO_COUNTRIES:
        invalid("document_issuing_country")
    if sex is not None and sex not in ("male", "female"):
        invalid("sex")
    if not allow_pending:
        for field, value in (
            ("document_issuing_country", country),
            ("sex", sex),
            ("date_of_birth", date_of_birth),
        ):
            if value is None:
                invalid(field)
    return NormalizedDocumentIdentity("passport", normalized, country, sex, date_of_birth)


def document_identity_keys(
    document: NormalizedDocumentIdentity, lookup: LookupDigest
) -> DocumentIdentityKeys:
    if document.document_type == "cn_id":
        return DocumentIdentityKeys(lookup.digest(document.number), None, None)
    encoded = json.dumps([document.issuing_country, document.number], separators=(",", ":"))
    return DocumentIdentityKeys(
        lookup.digest_domain("passport-identity", encoded),
        lookup.digest_domain("passport-number", document.number),
        document.issuing_country,
    )


def lock_document_identity_keys(db: Session, keys: list[DocumentIdentityKeys]) -> None:
    """Acquire bare passport locks before credential/person locks, in global order."""
    for digest in sorted(
        {key.passport_number_digest for key in keys if key.passport_number_digest}
    ):
        value = int.from_bytes(
            hashlib.sha256(("passport-number:" + digest).encode()).digest()[:8],
            byteorder="big",
            signed=True,
        )
        db.execute(select(func.pg_advisory_xact_lock(value)))


def require_document_available(
    db: Session, keys: DocumentIdentityKeys, *, exclude_person_id: UUID | None = None
) -> None:
    lock_document_identity_keys(db, [keys])
    conflict = Person.identity_digest == keys.identity_digest
    if keys.passport_number_digest is not None:
        country_conflict = (
            True
            if keys.issuing_country is None
            else or_(
                Person.document_issuing_country.is_(None),
                Person.document_issuing_country == keys.issuing_country,
            )
        )
        conflict = or_(
            conflict,
            (Person.passport_number_digest == keys.passport_number_digest) & country_conflict,
        )
    query = select(Person.id).where(conflict)
    if exclude_person_id is not None:
        query = query.where(Person.id != exclude_person_id)
    if db.scalar(query.limit(1)) is not None:
        raise DocumentIdentityConflict("document identity requires manual review")


def mask_document_identity(document_type: DocumentType, number: str) -> str:
    if document_type == "cn_id":
        return f"{number[:6]}********{number[-4:]}"
    return "*" * len(number) if len(number) <= 4 else "****" + number[-2:]
