from __future__ import annotations

from typing import get_args

from sqlalchemy import CheckConstraint, UniqueConstraint


def _check_texts(table: object) -> dict[str | None, str]:
    return {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints  # type: ignore[attr-defined]
        if isinstance(constraint, CheckConstraint)
    }


def test_registration_models_expose_fixed_literals_and_constraints() -> None:
    from app.registration.models import (
        ChallengePurpose,
        IdentifierKind,
        RegistrationApplication,
        RegistrationFinalizationMatchKind,
        RegistrationPurpose,
        RegistrationReviewDecision,
        RegistrationState,
        RegistrationVerificationChallenge,
    )

    assert get_args(RegistrationPurpose) == (
        "student",
        "guardian",
        "staff_applicant",
        "visitor",
    )
    assert get_args(RegistrationState) == (
        "draft",
        "contact_verified",
        "identity_completed",
        "pending_guardian",
        "ready_to_finalize",
        "finalized",
        "conflict",
        "rejected",
        "expired",
    )
    assert get_args(IdentifierKind) == ("phone", "email")
    assert get_args(RegistrationFinalizationMatchKind) == (
        "new_person",
        "claim_existing",
        "existing_account",
    )
    assert get_args(RegistrationReviewDecision) == ("approved", "rejected")
    assert get_args(ChallengePurpose) == (
        "registration",
        "password_reset",
        "guardian_invitation",
        "add_login_identifier",
        "assisted_activation",
    )

    application_checks = _check_texts(RegistrationApplication.__table__)
    assert "ready_to_finalize" in application_checks[
        "ck_registration_applications_state"
    ]
    finalization_kind_check = application_checks[
        "ck_registration_applications_finalization_match_kind"
    ]
    assert "state = 'finalized'" in finalization_kind_check
    assert all(
        kind in finalization_kind_check
        for kind in ("new_person", "claim_existing", "existing_account")
    )
    challenge_checks = _check_texts(RegistrationVerificationChallenge.__table__)
    assert "BETWEEN 0 AND 5" in challenge_checks[
        "ck_registration_verification_challenges_attempts"
    ]
    assert RegistrationVerificationChallenge.__table__.c.code_digest.nullable is False


def test_registration_application_freezes_purpose_specific_fields() -> None:
    from app.registration.models import RegistrationApplication

    columns = RegistrationApplication.__table__.c
    assert "visitor_reason" not in columns
    assert {
        "visit_reason",
        "requested_staff_type",
        "staff_application_reason",
    } <= set(columns.keys())
    checks = _check_texts(RegistrationApplication.__table__)
    staff_type_check = checks["ck_registration_applications_requested_staff_type"]
    assert all(
        staff_type in staff_type_check
        for staff_type in (
            "coach",
            "life_teacher",
            "customer_service",
            "administration",
            "other",
        )
    )
    staff_reason_check = checks[
        "ck_registration_applications_staff_application_reason"
    ]
    assert "regexp_replace(staff_application_reason" in staff_reason_check
    assert "BETWEEN 2 AND 500" in staff_reason_check
    visit_reason_check = checks["ck_registration_applications_visit_reason"]
    assert "regexp_replace(visit_reason" in visit_reason_check
    assert "BETWEEN 2 AND 200" in visit_reason_check
    purpose_fields_check = checks["ck_registration_applications_purpose_fields"]
    assert (
        "requested_staff_type IS NULL AND staff_application_reason IS NULL"
        in purpose_fields_check
    )
    assert "visit_reason IS NULL" in purpose_fields_check
    assert "primary_purpose IN ('student', 'guardian')" in purpose_fields_check
    assert (
        "state IN ('draft', 'contact_verified', 'finalized', 'rejected', 'expired')"
        in purpose_fields_check
    )
    assert all(
        field in purpose_fields_check
        for field in (
            "requested_staff_type",
            "staff_application_reason",
            "visit_reason",
        )
    )


def test_registration_application_review_metadata_is_constrained() -> None:
    from app.registration.models import RegistrationApplication

    columns = RegistrationApplication.__table__.c
    assert {
        "review_decision",
        "reviewed_by_user_id",
        "review_reason",
        "reviewed_at",
    } <= set(columns.keys())
    assert columns.review_decision.type.length == 16
    assert columns.reviewed_at.type.timezone is True
    assert {
        foreign_key.target_fullname
        for foreign_key in columns.reviewed_by_user_id.foreign_keys
    } == {"user_accounts.id"}

    checks = _check_texts(RegistrationApplication.__table__)
    reason_check = checks["ck_registration_applications_review_reason"]
    assert "regexp_replace(review_reason" in reason_check
    assert "BETWEEN 2 AND 500" in reason_check

    state_check = checks["ck_registration_applications_review_state"]
    assert "review_decision IS NULL" in state_check
    assert "review_decision IS NOT NULL" in state_check
    assert "review_decision = 'approved'" in state_check
    assert (
        "state IN ('pending_guardian', 'ready_to_finalize', 'finalized', "
        "'conflict')"
    ) in state_check
    assert "review_decision = 'rejected'" in state_check
    assert "state = 'rejected'" in state_check


def test_registration_application_review_candidate_binding_is_constrained() -> None:
    from app.registration.models import RegistrationApplication

    columns = RegistrationApplication.__table__.c
    candidate_column = columns.reviewed_candidate_person_id
    assert candidate_column.nullable is True
    assert {
        foreign_key.target_fullname
        for foreign_key in candidate_column.foreign_keys
    } == {"people.id"}
    candidate_check = _check_texts(RegistrationApplication.__table__)[
        "ck_registration_applications_review_candidate"
    ]
    assert "reviewed_candidate_person_id IS NULL" in candidate_check
    assert "reviewed_candidate_person_id IS NOT NULL" in candidate_check
    assert "review_decision IS DISTINCT FROM 'approved'" in candidate_check
    assert "review_decision IS NOT DISTINCT FROM 'approved'" in candidate_check
    assert "state IN ('pending_guardian', 'ready_to_finalize')" in candidate_check


def test_staff_identity_application_reason_has_frozen_bounds() -> None:
    from app.registration.models import StaffIdentityApplication

    checks = _check_texts(StaffIdentityApplication.__table__)
    reason_check = checks["ck_staff_identity_applications_reason"]
    assert "BETWEEN 2 AND 500" in reason_check
    assert "regexp_replace" in reason_check


def test_auth_account_outputs_allow_unavailable_email() -> None:
    from types import UnionType
    from typing import get_args

    from app.auth.schemas import AccountSummary, AuthUser

    for model, field_name in (
        (AuthUser, "email"),
        (AccountSummary, "masked_email"),
    ):
        annotation = model.model_fields[field_name].annotation
        assert isinstance(annotation, UnionType)
        assert type(None) in get_args(annotation)


def test_account_login_identifier_is_unique_and_legacy_columns_are_nullable() -> None:
    from app.auth.models import AccountLoginIdentifier, UserAccount

    identifier_uniques = {
        tuple(column.name for column in constraint.columns)
        for constraint in AccountLoginIdentifier.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("identifier_digest",) in identifier_uniques
    assert UserAccount.__table__.c.login_phone_digest.nullable is True
    assert UserAccount.__table__.c.email_normalized.nullable is True
    purpose_check = _check_texts(UserAccount.__table__)[
        "ck_user_accounts_self_service_primary_purpose"
    ]
    assert all(
        purpose in purpose_check
        for purpose in ("student", "guardian", "staff_applicant", "visitor")
    )


def test_guardian_profile_and_encrypted_email_columns_are_explicit() -> None:
    from app.identity.models import GuardianProfile, Person

    assert GuardianProfile.__table__.c.person_id.primary_key is True
    legacy_backfilled = GuardianProfile.__table__.c.legacy_backfilled
    assert legacy_backfilled.nullable is False
    assert legacy_backfilled.default.arg is False
    assert legacy_backfilled.server_default.arg == "false"
    assert Person.__table__.c.email.nullable is True
    assert Person.__table__.c.email_ciphertext.nullable is True
    assert Person.__table__.c.email_digest.nullable is True
    assert _check_texts(Person.__table__)["ck_people_legacy_email_cleared"] == (
        "email IS NULL"
    )


def test_registration_expiry_columns_are_timezone_aware() -> None:
    from app.registration.models import (
        GuardianRegistrationInvitation,
        RegistrationApplication,
        RegistrationSession,
        RegistrationVerificationChallenge,
    )

    for column in (
        RegistrationApplication.__table__.c.expires_at,
        RegistrationVerificationChallenge.__table__.c.expires_at,
        RegistrationSession.__table__.c.expires_at,
        GuardianRegistrationInvitation.__table__.c.expires_at,
    ):
        assert column.type.timezone is True


def test_registration_partial_unique_indexes_freeze_concurrency_guards() -> None:
    from app.auth.models import AccountLoginIdentifier
    from app.registration.models import (
        GuardianRegistrationInvitation,
        RegistrationApplication,
        StaffIdentityApplication,
    )

    application_indexes = {index.name: index for index in RegistrationApplication.__table__.indexes}
    active_identifier_where = str(
        application_indexes[
            "uq_registration_applications_active_identifier"
        ].dialect_options["postgresql"]["where"]
    )
    assert "contact_verified" in active_identifier_where
    assert "draft" not in active_identifier_where
    assert application_indexes[
        "uq_registration_applications_active_identity"
    ].dialect_options["postgresql"]["where"] is not None

    identifier_indexes = {index.name: index for index in AccountLoginIdentifier.__table__.indexes}
    assert "uq_account_login_identifiers_primary_account" not in identifier_indexes

    invitation_indexes = {
        index.name: index for index in GuardianRegistrationInvitation.__table__.indexes
    }
    assert any(
        name.startswith("uq_guardian_registration_invitations_pending_")
        and index.dialect_options["postgresql"]["where"] is not None
        for name, index in invitation_indexes.items()
    )
    assert any(
        name.startswith("uq_guardian_registration_invitations_accepted_")
        and index.dialect_options["postgresql"]["where"] is not None
        for name, index in invitation_indexes.items()
    )
    assert invitation_indexes[
        "uq_guardian_registration_invitations_handoff_token"
    ].dialect_options["postgresql"]["where"] is not None
    invitation_checks = {
        constraint.name
        for constraint in GuardianRegistrationInvitation.__table__.constraints
    }
    assert "ck_guardian_registration_invitations_handoff_token" in invitation_checks

    staff_indexes = {index.name: index for index in StaffIdentityApplication.__table__.indexes}
    assert staff_indexes[
        "uq_staff_identity_applications_open_person_type"
    ].dialect_options["postgresql"]["where"] is not None
