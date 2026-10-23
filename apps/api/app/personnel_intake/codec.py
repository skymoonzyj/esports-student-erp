"""Explicit encrypted payload mapping; input SecretStr serialization is forbidden."""

import json
from datetime import date
from typing import Literal, TypedDict

from pydantic import ValidationError

from app.core.time import business_today
from app.identity.document_identity import normalize_document_identity
from app.identity.normalization import (
    IdentityValidationError,
    normalize_cn_id,
    normalize_email,
    normalize_phone,
)
from app.identity.readiness import is_minor
from app.personnel_intake.errors import PersonnelIntakeError
from app.personnel_intake.guardian_records import ApprovalEvidenceError
from app.personnel_intake.schemas import (
    AssistedGuardianInput,
    AssistedGuardianView,
    IntakeInput,
    IntakePayloadView,
    PendingIntakeInput,
    PendingIntakePayloadView,
)


class NormalizedFields(TypedDict):
    full_name: str
    identity_number: str
    phone: str
    email: str | None
    sex: Literal["male", "female"]
    date_of_birth: date


def decode_payload(raw: str) -> IntakePayloadView | PendingIntakePayloadView:
    """Structural decoding follows envelope authentication in both callers."""
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ApprovalEvidenceError()
        model = (
            PendingIntakePayloadView if value.get("material_version") == 2 else IntakePayloadView
        )
        return model.model_validate_json(raw)
    except (ValidationError, ValueError, TypeError) as exc:
        raise ApprovalEvidenceError() from exc


def normalize_payload(value: IntakeInput, *, today: date | None = None) -> IntakePayloadView:
    today = today or business_today()
    try:

        def fields(person: IntakeInput | AssistedGuardianInput) -> NormalizedFields:
            identity = normalize_cn_id(
                person.identity_number.get_secret_value(), expected_birth_date=person.date_of_birth
            )
            if identity.sex != person.sex or identity.birth_date > today:
                raise PersonnelIntakeError(
                    "personnel_intake_validation", status=422, detail="身份资料不一致"
                )
            return {
                "full_name": person.full_name,
                "identity_number": identity.number,
                "phone": normalize_phone(person.phone.get_secret_value()),
                "email": normalize_email(person.email.get_secret_value()) if person.email else None,
                "sex": identity.sex,
                "date_of_birth": identity.birth_date,
            }

        own = fields(value)
        guardians = []
        identities = {own["identity_number"]}
        for guardian in value.guardians:
            item = fields(guardian)
            if (
                is_minor(guardian.date_of_birth, today)
                or item["identity_number"] in identities
                or (item["email"] and item["email"] == own["email"])
            ):
                raise PersonnelIntakeError(
                    "personnel_intake_validation", status=422, detail="监护资料不符合要求"
                )
            identities.add(item["identity_number"])
            guardians.append(
                AssistedGuardianView(
                    **item, relationship=guardian.relationship, is_primary=guardian.is_primary
                )
            )
        if is_minor(value.date_of_birth, today) and not guardians:
            raise PersonnelIntakeError(
                "personnel_intake_validation", status=422, detail="未成年人至少需要一名成年监护人"
            )
        return IntakePayloadView.model_validate(
            {
                **own,
                "kind": value.kind,
                "guardians": guardians,
                "coach_person_ids": sorted(set(value.coach_person_ids)),
                "team_id": value.team_id,
            }
        )
    except IdentityValidationError as exc:
        raise PersonnelIntakeError(
            "personnel_intake_validation", status=422, detail="人员资料格式或内容无效"
        ) from exc


def normalize_pending_payload(
    value: PendingIntakeInput, *, today: date | None = None
) -> PendingIntakePayloadView:
    today = today or business_today()
    if value.team_id and value.team_allocation_status == "not_required":
        raise PersonnelIntakeError(
            "personnel_intake_validation", status=422, detail="无需分队与实际队伍不能同时选择"
        )
    try:
        document = normalize_document_identity(
            value.document_type,
            value.identity_number.get_secret_value(),
            issuing_country=value.document_issuing_country,
            sex=value.sex,
            date_of_birth=value.date_of_birth,
            allow_pending=True,
        )
        if document.date_of_birth is not None and document.date_of_birth > today:
            raise PersonnelIntakeError(
                "personnel_intake_validation", status=422, detail="出生日期无效"
            )
        phone = value.phone.get_secret_value().strip() if value.phone else None
        if phone and (len(phone) > 80 or any(ord(c) < 32 for c in phone)):
            raise PersonnelIntakeError(
                "personnel_intake_validation", status=422, detail="联系方式无效"
            )
        # CN phone facts retain their strict format; foreign contacts are not login identifiers.
        if phone and value.document_type == "cn_id":
            phone = normalize_phone(phone)
        email = normalize_email(value.email.get_secret_value()) if value.email else None
        guardians: list[AssistedGuardianView] = []
        seen = {document.number} if document.document_type == "cn_id" else set()
        for guardian in value.guardians:
            identity = normalize_cn_id(
                guardian.identity_number.get_secret_value(),
                expected_birth_date=guardian.date_of_birth,
            )
            guardian_email = (
                normalize_email(guardian.email.get_secret_value()) if guardian.email else None
            )
            if (
                identity.sex != guardian.sex
                or is_minor(guardian.date_of_birth, today)
                or guardian.date_of_birth > today
                or identity.number in seen
                or (guardian_email and guardian_email == email)
            ):
                raise PersonnelIntakeError(
                    "personnel_intake_validation", status=422, detail="监护资料不符合要求"
                )
            seen.add(identity.number)
            guardians.append(
                AssistedGuardianView(
                    full_name=guardian.full_name,
                    identity_number=identity.number,
                    sex=guardian.sex,
                    date_of_birth=guardian.date_of_birth,
                    phone=normalize_phone(guardian.phone.get_secret_value()),
                    email=guardian_email,
                    relationship=guardian.relationship,
                    is_primary=guardian.is_primary,
                )
            )
        return PendingIntakePayloadView(
            kind=value.kind,
            full_name=value.full_name,
            identity_number=document.number
            if document.document_type == "cn_id"
            else value.identity_number.get_secret_value(),
            document_type=document.document_type,
            document_issuing_country=document.issuing_country,
            sex=value.sex,
            date_of_birth=value.date_of_birth,
            phone=phone,
            email=email,
            guardians=guardians,
            coach_person_ids=sorted(set(value.coach_person_ids)),
            team_id=value.team_id,
            team_allocation_status="assigned" if value.team_id else value.team_allocation_status,
        )
    except IdentityValidationError as exc:
        raise PersonnelIntakeError(
            "personnel_intake_validation", status=422, detail="人员资料格式或内容无效"
        ) from exc
