"""Immutable applicant facts may fill gaps only, after an authorized review."""

from datetime import date

from app.identity.readiness import is_minor
from app.personnel_intake.claims.errors import ProfileClaimError
from app.personnel_intake.claims.proof import completion_material
from app.personnel_intake.claims.schemas import ClaimCompletionFields, ClaimCompletionView
from app.personnel_intake.codec import normalize_pending_payload
from app.personnel_intake.errors import PersonnelIntakeError
from app.personnel_intake.schemas import PendingIntakeInput, PendingIntakePayloadView


def merge_completion_payload(
    approved: PendingIntakePayloadView,
    completion: ClaimCompletionFields,
    *,
    verified_email: str,
    today: date,
) -> PendingIntakePayloadView:
    """Pure preparation: no lookup, writes, target discovery, grants or invented facts."""
    submitted = completion_material(completion)
    assert submitted is not None
    if approved.email is not None and approved.email != verified_email:
        raise ProfileClaimError(code="completion_conflicts")
    # Unknown-issuer passports must be corrected by an administrator first.
    if approved.document_type == "passport" and not approved.document_issuing_country:
        raise ProfileClaimError(code="completion_missing")
    values = approved.model_dump(exclude={"material_version"})
    for source, target in (
        ("phone", "phone"),
        ("sex", "sex"),
        ("date_of_birth", "date_of_birth"),
        ("issuing_country", "document_issuing_country"),
    ):
        incoming = getattr(completion, source)
        if source == "phone" and incoming is not None:
            incoming = incoming.get_secret_value()
        existing = values[target]
        if source == "issuing_country" and approved.document_type != "passport":
            allowed = {"cn_id": "CN", "hk_id": "HK"}.get(approved.document_type)
            if incoming not in (None, allowed):
                raise ProfileClaimError(code="completion_conflicts")
            continue
        if existing is not None and incoming is not None and existing != incoming:
            raise ProfileClaimError(code="completion_conflicts")
        if existing is None and incoming is not None:
            values[target] = incoming
    if completion.guardians:
        # Existing approved relationships are immutable to the claimant. Their
        # explicit correction remains the existing authenticated admin workflow.
        incoming_guardians = submitted["guardians"]
        from app.personnel_intake.schemas import AssistedGuardianView

        if (
            approved.guardians
            and [AssistedGuardianView.model_validate(g) for g in incoming_guardians]
            != approved.guardians
        ):
            raise ProfileClaimError(code="completion_conflicts")
        if not approved.guardians:
            values["guardians"] = incoming_guardians
    values["email"] = verified_email
    # assigned is a persisted outcome, not applicant authority or a create input.
    values["team_allocation_status"] = (
        "not_required" if approved.team_allocation_status == "not_required" else "pending"
    )
    try:
        normalized = normalize_pending_payload(
            PendingIntakeInput.model_validate(values), today=today
        )
        return normalized.model_copy(update={"identity_number": approved.identity_number})
    except (PersonnelIntakeError, ValueError) as error:
        raise ProfileClaimError(code="completion_invalid") from error


def completion_is_complete(payload: PendingIntakePayloadView, *, today: date) -> bool:
    return bool(
        payload.phone
        and payload.sex
        and payload.date_of_birth
        and (payload.document_type != "passport" or payload.document_issuing_country)
        and (not is_minor(payload.date_of_birth, today) or payload.guardians)
    )


def completion_view(value: ClaimCompletionFields) -> ClaimCompletionView:
    return ClaimCompletionView.model_validate(completion_material(value))


def approved_completion_view(value: PendingIntakePayloadView) -> ClaimCompletionView:
    return ClaimCompletionView(
        phone=value.phone,
        sex=value.sex,
        date_of_birth=value.date_of_birth,
        issuing_country=value.document_issuing_country,
        guardians=value.guardians,
    )
