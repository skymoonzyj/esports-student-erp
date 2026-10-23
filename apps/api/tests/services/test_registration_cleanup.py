from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import Engine, delete, event, select, text
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.identity.models import Person
from app.registration.cleanup_service import (
    RegistrationCleanupService,
    touch_registration_application,
)
from app.registration.finalization_service import clear_registration_application_secrets
from app.registration.invitation_retention import (
    REDACTED_GUARDIAN_INVITATION_CIPHERTEXT,
    REDACTED_GUARDIAN_INVITATION_DIGEST,
)
from app.registration.models import (
    GuardianRegistrationInvitation,
    RegistrationApplication,
    RegistrationSession,
)
from app.registration.session_service import RegistrationSessionService

NOW = datetime(2026, 9, 14, 8, tzinfo=UTC)
SESSION_PEPPER = bytes(range(2, 34))


def _person(db: Session, *, person_id: UUID) -> Person:
    person = Person(
        id=person_id,
        full_name="清理测试人员",
        identity_ciphertext="encrypted-person-identity",
        identity_digest=f"{person_id.int:064x}"[-64:],
        sex="female",
        date_of_birth=date(1990, 1, 2),
    )
    db.add(person)
    db.flush()
    return person


def _application(
    db: Session,
    *,
    application_id: UUID,
    expires_at: datetime,
    state: str = "identity_completed",
) -> RegistrationApplication:
    application = RegistrationApplication(
        id=application_id,
        primary_purpose="student",
        state=state,
        verified_identifier_kind="phone",
        verified_identifier_digest=f"{application_id.int:064x}"[-64:],
        verified_identifier_ciphertext="encrypted-registration-phone",
        password_hash="argon2-registration-password",
        full_name="待清理注册人",
        identity_ciphertext="encrypted-registration-identity",
        identity_digest=f"{application_id.int + 100:064x}"[-64:],
        sex="female",
        date_of_birth=date(2008, 1, 2),
        conflict_code="identity_profile_mismatch",
        expires_at=expires_at,
    )
    db.add(application)
    db.flush()
    return application


def test_thirty_day_cleanup_clears_sensitive_fields_and_revokes_sessions(
    db: Session,
) -> None:
    application = _application(
        db,
        application_id=UUID("71000000-0000-4000-8000-000000000001"),
        expires_at=NOW,
    )
    registration_session = RegistrationSession(
        application_id=application.id,
        token_digest="a" * 64,
        csrf_digest="b" * 64,
        expires_at=NOW + timedelta(hours=1),
    )
    db.add(registration_session)
    db.flush()

    changed = RegistrationCleanupService(db, clock=lambda: NOW).prune(limit=100)

    db.expire_all()
    row = db.get(RegistrationApplication, application.id)
    stored_session = db.get(RegistrationSession, registration_session.id)
    assert changed == 1
    assert row is not None
    assert row.state == "expired"
    assert row.primary_purpose == "student"
    assert row.conflict_code == "identity_profile_mismatch"
    assert row.verified_identifier_kind is None
    assert row.verified_identifier_digest is None
    assert row.verified_identifier_ciphertext is None
    assert row.password_hash is None
    assert row.full_name is None
    assert row.identity_ciphertext is None
    assert row.identity_digest is None
    assert row.sex is None
    assert row.date_of_birth is None
    assert stored_session is not None
    assert stored_session.revoked_at == NOW


def test_touching_a_historical_draft_caps_it_at_ten_minutes_from_creation() -> None:
    created_at = NOW - timedelta(minutes=2)
    application = RegistrationApplication(
        primary_purpose="student",
        state="draft",
        verified_identifier_kind="email",
        verified_identifier_digest="d" * 64,
        verified_identifier_ciphertext="encrypted-registration-email",
        expires_at=NOW + timedelta(days=30),
    )
    application.created_at = created_at

    touch_registration_application(application, now=NOW)

    assert application.expires_at == created_at + timedelta(minutes=10)


def test_cleanup_expires_historical_draft_at_created_at_plus_ten_minutes(
    db: Session,
) -> None:
    application = _application(
        db,
        application_id=UUID("71000000-0000-4000-8000-000000000009"),
        expires_at=NOW + timedelta(days=30),
        state="draft",
    )
    application.created_at = NOW - timedelta(minutes=10)
    db.flush()

    changed = RegistrationCleanupService(db, clock=lambda: NOW).prune(limit=100)

    db.refresh(application)
    assert changed == 1
    assert application.state == "expired"
    assert application.verified_identifier_digest is None


def test_day_twenty_nine_activity_restarts_thirty_day_cleanup_boundary(
    db: Session,
) -> None:
    application = _application(
        db,
        application_id=UUID("71000000-0000-4000-8000-000000000008"),
        expires_at=NOW + timedelta(days=1),
    )
    RegistrationSessionService(
        db,
        token_pepper=SESSION_PEPPER,
        session_ttl_minutes=480,
        clock=lambda: NOW,
    ).issue(application.id)

    before_boundary = RegistrationCleanupService(
        db,
        clock=lambda: NOW + timedelta(days=30) - timedelta(microseconds=1),
    ).prune(limit=100)
    db.refresh(application)

    assert before_boundary == 0
    assert application.state == "identity_completed"

    at_boundary = RegistrationCleanupService(
        db,
        clock=lambda: NOW + timedelta(days=30),
    ).prune(limit=100)
    db.refresh(application)

    assert at_boundary == 1
    assert application.state == "expired"


def test_cleanup_clears_review_metadata_before_expiring_reviewed_conflict(
    db: Session,
) -> None:
    reviewer = _person(
        db,
        person_id=UUID("71000000-0000-4000-8000-000000000201"),
    )
    reviewer_account = UserAccount(
        person_id=reviewer.id,
        password_hash="argon2-reviewer-password",
        status="active",
        force_password_change=False,
    )
    db.add(reviewer_account)
    db.flush()
    application = _application(
        db,
        application_id=UUID("71000000-0000-4000-8000-000000000002"),
        expires_at=NOW,
        state="conflict",
    )
    application.review_decision = "approved"
    application.reviewed_by_user_id = reviewer_account.id
    application.review_reason = "实名资料已人工复核"
    application.reviewed_at = NOW - timedelta(days=1)
    db.flush()

    RegistrationCleanupService(db, clock=lambda: NOW).prune(limit=100)

    db.expire_all()
    row = db.get(RegistrationApplication, application.id)
    assert row is not None
    assert row.state == "expired"
    assert row.review_decision is None
    assert row.reviewed_by_user_id is None
    assert row.review_reason is None
    assert row.reviewed_at is None


def test_expired_reviewed_pending_guardian_clears_candidate_binding(
    db: Session,
) -> None:
    candidate = _person(
        db,
        person_id=UUID("71000000-0000-4000-8000-000000000205"),
    )
    reviewer = _person(
        db,
        person_id=UUID("71000000-0000-4000-8000-000000000206"),
    )
    reviewer_account = UserAccount(
        person_id=reviewer.id,
        password_hash="argon2-reviewer-password",
        status="active",
        force_password_change=False,
    )
    db.add(reviewer_account)
    db.flush()
    application = _application(
        db,
        application_id=UUID("71000000-0000-4000-8000-000000000006"),
        expires_at=NOW,
        state="pending_guardian",
    )
    application.review_decision = "approved"
    application.reviewed_by_user_id = reviewer_account.id
    application.reviewed_candidate_person_id = candidate.id
    application.review_reason = "实名资料已核验等待监护确认"
    application.reviewed_at = NOW - timedelta(days=1)
    db.flush()

    RegistrationCleanupService(db, clock=lambda: NOW).prune(limit=100)

    db.expire_all()
    row = db.get(RegistrationApplication, application.id)
    assert row is not None
    assert row.state == "expired"
    assert row.review_decision is None
    assert row.reviewed_by_user_id is None
    assert row.reviewed_candidate_person_id is None
    assert row.review_reason is None
    assert row.reviewed_at is None


def test_cleanup_backstops_finalized_temporary_secrets_without_changing_state(
    db: Session,
) -> None:
    application = _application(
        db,
        application_id=UUID("71000000-0000-4000-8000-000000000003"),
        expires_at=NOW + timedelta(days=20),
    )
    application.state = "finalized"
    application.finalization_match_kind = "new_person"
    application.finalized_at = NOW - timedelta(minutes=1)
    db.flush()

    changed = RegistrationCleanupService(db, clock=lambda: NOW).prune(limit=100)

    db.expire_all()
    row = db.get(RegistrationApplication, application.id)
    assert changed == 1
    assert row is not None
    assert row.state == "finalized"
    assert row.finalization_match_kind == "new_person"
    assert row.finalized_at == NOW - timedelta(minutes=1)
    assert row.verified_identifier_ciphertext is None
    assert row.password_hash is None
    assert row.identity_ciphertext is None


@pytest.mark.parametrize(
    ("state", "review_decision"),
    [("finalized", "approved"), ("rejected", "rejected")],
)
def test_cleanup_clears_terminal_review_metadata_when_no_other_payload_remains(
    db: Session,
    state: str,
    review_decision: str,
) -> None:
    reviewer = _person(
        db,
        person_id=UUID(
            "71000000-0000-4000-8000-000000000203"
            if state == "finalized"
            else "71000000-0000-4000-8000-000000000204"
        ),
    )
    reviewer_account = UserAccount(
        person_id=reviewer.id,
        password_hash="argon2-reviewer-password",
        status="active",
        force_password_change=False,
    )
    db.add(reviewer_account)
    db.flush()
    application = _application(
        db,
        application_id=UUID(
            "71000000-0000-4000-8000-000000000004"
            if state == "finalized"
            else "71000000-0000-4000-8000-000000000005"
        ),
        expires_at=NOW + timedelta(days=20),
        state="conflict",
    )
    application.state = state
    if state == "finalized":
        application.finalization_match_kind = "new_person"
        application.finalized_at = NOW - timedelta(minutes=1)
    else:
        application.rejected_at = NOW - timedelta(minutes=1)
    application.review_decision = review_decision
    application.reviewed_by_user_id = reviewer_account.id
    application.review_reason = "终态审核备注应按保留规则清除"
    application.reviewed_at = NOW - timedelta(minutes=1)
    clear_registration_application_secrets(application)
    db.flush()

    changed = RegistrationCleanupService(db, clock=lambda: NOW).prune(limit=100)

    db.expire_all()
    stored = db.get(RegistrationApplication, application.id)
    assert changed == 1
    assert stored is not None
    assert stored.state == state
    assert stored.review_decision is None
    assert stored.reviewed_by_user_id is None
    assert stored.review_reason is None
    assert stored.reviewed_at is None


def test_prune_is_bounded_ordered_and_uses_skip_locked(db: Session) -> None:
    applications = [
        _application(
            db,
            application_id=UUID(f"71000000-0000-4000-8000-{index:012d}"),
            expires_at=NOW - timedelta(days=days_ago),
        )
        for index, days_ago in ((10, 3), (11, 2), (12, 1))
    ]
    statements: list[str] = []

    def capture_cleanup_select(
        _connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        if "registration_applications" in statement:
            statements.append(statement.upper())

    bind = db.get_bind()
    event.listen(bind, "before_cursor_execute", capture_cleanup_select)
    try:
        changed = RegistrationCleanupService(db, clock=lambda: NOW).prune(limit=2)
    finally:
        event.remove(bind, "before_cursor_execute", capture_cleanup_select)

    db.expire_all()
    assert changed == 2
    assert [db.get(RegistrationApplication, row.id).state for row in applications] == [
        "expired",
        "expired",
        "identity_completed",
    ]
    locking_selects = [statement for statement in statements if "FOR UPDATE" in statement]
    assert locking_selects
    assert "SKIP LOCKED" in locking_selects[0]
    assert "ORDER BY REGISTRATION_APPLICATIONS.EXPIRES_AT" in locking_selects[0]
    assert "REGISTRATION_APPLICATIONS.ID" in locking_selects[0]


def test_pending_guardian_invitation_expires_at_seven_day_boundary(
    db: Session,
) -> None:
    student = _person(
        db,
        person_id=UUID("71000000-0000-4000-8000-000000000202"),
    )
    invitation = GuardianRegistrationInvitation(
        student_person_id=student.id,
        invited_identifier_kind="email",
        invited_identifier_digest="c" * 64,
        invited_identifier_ciphertext="encrypted-guardian-email",
        handoff_token_digest="1" * 64,
        relationship="母亲",
        is_primary=True,
        status="pending",
        expires_at=NOW,
    )
    db.add(invitation)
    db.flush()

    statements: list[str] = []

    def capture_invitation_select(
        _connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        normalized = statement.upper()
        if "GUARDIAN_REGISTRATION_INVITATIONS" in normalized:
            statements.append(normalized)

    bind = db.get_bind()
    event.listen(bind, "before_cursor_execute", capture_invitation_select)
    try:
        changed = RegistrationCleanupService(db, clock=lambda: NOW).prune(limit=100)
    finally:
        event.remove(bind, "before_cursor_execute", capture_invitation_select)

    db.expire_all()
    stored = db.get(GuardianRegistrationInvitation, invitation.id)
    assert changed == 1
    assert stored is not None
    assert stored.status == "expired"
    assert stored.handoff_token_digest is None
    assert stored.invited_identifier_digest == REDACTED_GUARDIAN_INVITATION_DIGEST
    assert (
        stored.invited_identifier_ciphertext
        == REDACTED_GUARDIAN_INVITATION_CIPHERTEXT
    )
    locking_selects = [statement for statement in statements if "FOR UPDATE" in statement]
    assert locking_selects
    assert "SKIP LOCKED" in locking_selects[0]
    assert "ORDER BY GUARDIAN_REGISTRATION_INVITATIONS.EXPIRES_AT" in locking_selects[0]
    assert "GUARDIAN_REGISTRATION_INVITATIONS.ID" in locking_selects[0]


def test_pending_guardian_invitation_is_retained_before_seven_day_boundary(
    db: Session,
) -> None:
    student = _person(
        db,
        person_id=UUID("71000000-0000-4000-8000-000000000206"),
    )
    invitation = GuardianRegistrationInvitation(
        student_person_id=student.id,
        invited_identifier_kind="email",
        invited_identifier_digest="8" * 64,
        invited_identifier_ciphertext="encrypted-future-guardian-email",
        handoff_token_digest="2" * 64,
        relationship="母亲",
        is_primary=True,
        status="pending",
        expires_at=NOW + timedelta(microseconds=1),
    )
    db.add(invitation)
    db.flush()

    changed = RegistrationCleanupService(db, clock=lambda: NOW).prune(limit=100)

    db.expire_all()
    stored = db.get(GuardianRegistrationInvitation, invitation.id)
    assert changed == 0
    assert stored is not None
    assert stored.status == "pending"
    assert stored.handoff_token_digest == "2" * 64
    assert stored.invited_identifier_digest == "8" * 64
    assert stored.invited_identifier_ciphertext == "encrypted-future-guardian-email"


def test_cleanup_expires_future_invitation_after_parent_application_expires(
    db: Session,
) -> None:
    application = _application(
        db,
        application_id=UUID("71000000-0000-4000-8000-000000000007"),
        expires_at=NOW,
    )
    invitation = GuardianRegistrationInvitation(
        application_id=application.id,
        invited_identifier_kind="email",
        invited_identifier_digest="9" * 64,
        invited_identifier_ciphertext="encrypted-late-guardian-email",
        handoff_token_digest="3" * 64,
        relationship="监护人",
        is_primary=True,
        status="pending",
        expires_at=NOW + timedelta(days=6),
    )
    db.add(invitation)
    db.flush()

    changed = RegistrationCleanupService(db, clock=lambda: NOW).prune(limit=2)

    db.expire_all()
    stored = db.get(GuardianRegistrationInvitation, invitation.id)
    assert changed == 2
    assert db.get(RegistrationApplication, application.id).state == "expired"
    assert stored is not None
    assert stored.status == "expired"
    assert stored.handoff_token_digest is None
    assert stored.invited_identifier_digest == REDACTED_GUARDIAN_INVITATION_DIGEST
    assert (
        stored.invited_identifier_ciphertext
        == REDACTED_GUARDIAN_INVITATION_CIPHERTEXT
    )


def test_cleanup_limit_does_not_bulk_expire_application_invitations(
    db: Session,
) -> None:
    application = _application(
        db,
        application_id=UUID("71000000-0000-4000-8000-000000000006"),
        expires_at=NOW,
    )
    invitations = [
        GuardianRegistrationInvitation(
            application_id=application.id,
            invited_identifier_kind="email",
            invited_identifier_digest=character * 64,
            invited_identifier_ciphertext=f"encrypted-family-{index}",
            handoff_token_digest=str(index + 4) * 64,
            relationship="监护人",
            is_primary=index == 0,
            status="pending",
            expires_at=NOW,
        )
        for index, character in enumerate(("d", "e"))
    ]
    db.add_all(invitations)
    db.flush()

    changed = RegistrationCleanupService(db, clock=lambda: NOW).prune(limit=1)

    db.expire_all()
    assert changed == 1
    assert db.get(RegistrationApplication, application.id).state == "expired"
    assert [
        db.get(GuardianRegistrationInvitation, invitation.id).status
        for invitation in invitations
    ] == ["pending", "pending"]


@pytest.mark.parametrize("status", ["accepted", "revoked", "expired"])
def test_cleanup_backstops_terminal_invitation_identifier_payload(
    db: Session,
    status: str,
) -> None:
    student = _person(
        db,
        person_id=UUID("71000000-0000-4000-8000-000000000205"),
    )
    invitation = GuardianRegistrationInvitation(
        student_person_id=student.id,
        invited_identifier_kind="phone",
        invited_identifier_digest="f" * 64,
        invited_identifier_ciphertext="encrypted-revoked-phone",
        relationship="监护人",
        is_primary=False,
        status=status,
        expires_at=NOW + timedelta(days=7),
        accepted_by_person_id=student.id if status == "accepted" else None,
        accepted_at=NOW if status == "accepted" else None,
        revoked_at=NOW if status == "revoked" else None,
    )
    db.add(invitation)
    db.flush()

    changed = RegistrationCleanupService(db, clock=lambda: NOW).prune(limit=1)

    db.expire_all()
    stored = db.get(GuardianRegistrationInvitation, invitation.id)
    assert changed == 1
    assert stored is not None
    assert stored.status == status
    assert stored.invited_identifier_digest == REDACTED_GUARDIAN_INVITATION_DIGEST
    assert (
        stored.invited_identifier_ciphertext
        == REDACTED_GUARDIAN_INVITATION_CIPHERTEXT
    )


def test_cleanup_skips_an_application_locked_by_another_postgresql_worker(
    postgres_engine: Engine,
) -> None:
    application_ids = [
        UUID("71000000-0000-4000-8000-000000000301"),
        UUID("71000000-0000-4000-8000-000000000302"),
    ]
    with Session(postgres_engine) as seed, seed.begin():
        for index, application_id in enumerate(application_ids):
            seed.add(
                RegistrationApplication(
                    id=application_id,
                    primary_purpose="student",
                    state="draft",
                    verified_identifier_kind="phone",
                    verified_identifier_digest=f"{application_id.int:064x}"[-64:],
                    verified_identifier_ciphertext="encrypted-registration-phone",
                    expires_at=NOW - timedelta(days=2 - index),
                )
            )

    locker = Session(postgres_engine)
    try:
        locked = locker.scalar(
            select(RegistrationApplication)
            .where(RegistrationApplication.id == application_ids[0])
            .with_for_update()
        )
        assert locked is not None

        with Session(postgres_engine) as worker:
            assert RegistrationCleanupService(
                worker,
                clock=lambda: NOW,
            ).prune(limit=1) == 1

        with Session(postgres_engine) as verification:
            states = list(
                verification.execute(
                    select(
                        RegistrationApplication.id,
                        RegistrationApplication.state,
                    )
                    .where(RegistrationApplication.id.in_(application_ids))
                    .order_by(RegistrationApplication.id)
                )
            )
            assert states == [
                (application_ids[0], "draft"),
                (application_ids[1], "expired"),
            ]
    finally:
        locker.rollback()
        locker.close()
        with Session(postgres_engine) as cleanup, cleanup.begin():
            cleanup.execute(
                delete(RegistrationApplication).where(
                    RegistrationApplication.id.in_(application_ids)
                )
            )


def test_cleanup_skips_an_invitation_locked_by_another_postgresql_worker(
    postgres_engine: Engine,
) -> None:
    person_ids = [
        UUID("71000000-0000-4000-8000-000000000401"),
        UUID("71000000-0000-4000-8000-000000000402"),
    ]
    invitation_ids = [
        UUID("71000000-0000-4000-8000-000000000411"),
        UUID("71000000-0000-4000-8000-000000000412"),
    ]
    with Session(postgres_engine) as seed, seed.begin():
        for index, person_id in enumerate(person_ids):
            seed.add(
                Person(
                    id=person_id,
                    full_name=f"邀请清理并发人员{index}",
                    identity_ciphertext=f"encrypted-invitation-person-{index}",
                    identity_digest=f"{person_id.int:064x}"[-64:],
                    sex="female",
                    date_of_birth=date(1990, 1, 2),
                )
            )
        seed.flush()
        for index, invitation_id in enumerate(invitation_ids):
            seed.add(
                GuardianRegistrationInvitation(
                    id=invitation_id,
                    student_person_id=person_ids[index],
                    invited_identifier_kind="email",
                    invited_identifier_digest=str(index + 1) * 64,
                    invited_identifier_ciphertext=(
                        f"encrypted-concurrent-invitation-{index}"
                    ),
                    handoff_token_digest=str(index + 6) * 64,
                    relationship="监护人",
                    is_primary=False,
                    status="pending",
                    expires_at=NOW - timedelta(days=2 - index),
                )
            )

    locker = Session(postgres_engine)
    try:
        locked = locker.scalar(
            select(GuardianRegistrationInvitation)
            .where(GuardianRegistrationInvitation.id == invitation_ids[0])
            .with_for_update()
        )
        assert locked is not None

        with Session(postgres_engine) as worker:
            worker.execute(text("SET LOCAL lock_timeout = '500ms'"))
            assert RegistrationCleanupService(
                worker,
                clock=lambda: NOW,
            ).prune(limit=1) == 1

        with Session(postgres_engine) as verification:
            rows = list(
                verification.execute(
                    select(
                        GuardianRegistrationInvitation.id,
                        GuardianRegistrationInvitation.status,
                        GuardianRegistrationInvitation.invited_identifier_digest,
                    )
                    .where(GuardianRegistrationInvitation.id.in_(invitation_ids))
                    .order_by(GuardianRegistrationInvitation.id)
                )
            )
            assert rows == [
                (invitation_ids[0], "pending", "1" * 64),
                (
                    invitation_ids[1],
                    "expired",
                    REDACTED_GUARDIAN_INVITATION_DIGEST,
                ),
            ]
    finally:
        locker.rollback()
        locker.close()
        with Session(postgres_engine) as cleanup, cleanup.begin():
            cleanup.execute(
                delete(GuardianRegistrationInvitation).where(
                    GuardianRegistrationInvitation.id.in_(invitation_ids)
                )
            )
            cleanup.execute(delete(Person).where(Person.id.in_(person_ids)))


@pytest.mark.parametrize("limit", [0, -1])
def test_cleanup_rejects_nonpositive_limits_without_writes(
    db: Session,
    limit: int,
) -> None:
    application = _application(
        db,
        application_id=UUID("71000000-0000-4000-8000-000000000099"),
        expires_at=NOW,
    )

    with pytest.raises(ValueError, match="limit"):
        RegistrationCleanupService(db, clock=lambda: NOW).prune(limit=limit)

    assert db.scalar(
        select(RegistrationApplication.state).where(
            RegistrationApplication.id == application.id
        )
    ) == "identity_completed"
