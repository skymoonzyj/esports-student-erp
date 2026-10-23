"""Explicit encrypted payload mapping; input SecretStr serialization is forbidden."""

from datetime import date
from typing import Literal, TypedDict

from app.core.time import business_today
from app.identity.normalization import (
    IdentityValidationError,
    normalize_cn_id,
    normalize_email,
    normalize_phone,
)
from app.identity.readiness import is_minor
from app.personnel_intake.errors import PersonnelIntakeError
from app.personnel_intake.schemas import (
    AssistedGuardianInput,
    AssistedGuardianView,
    IntakeInput,
    IntakePayloadView,
)


class NormalizedFields(TypedDict):
    full_name: str
    identity_number: str
    phone: str
    email: str | None
    sex: Literal["male", "female"]
    date_of_birth: date


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
