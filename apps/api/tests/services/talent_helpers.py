from datetime import date, datetime
from uuid import UUID, uuid4

from pydantic import TypeAdapter
from sqlalchemy.orm import Session

from app.auth.models import AuthSession, UserAccount
from app.auth.service import AuthContext
from app.identity.models import Person
from app.rbac.models import UserRole
from app.rbac.seed import ROLES
from app.talent_tests.catalog import CURRENT_CATALOG_VERSION, TEST_IDS, get_rules
from app.talent_tests.models import TalentTestSession
from app.talent_tests.queries import TalentQueries
from app.talent_tests.schemas import (
    RawResultInput,
    SessionCreateRequest,
    SessionView,
)
from app.talent_tests.scoring import fingerprint
from tests.unit.talent_test_helpers import valid_payload

ADAPTER: TypeAdapter[RawResultInput] = TypeAdapter(RawResultInput)


def student_actor(db: Session) -> AuthContext:
    marker = uuid4().hex
    person = Person(
        full_name="测试学员",
        identity_ciphertext=marker,
        identity_digest=marker,
        sex="male",
        date_of_birth=date(2000, 1, 1),
    )
    db.add(person)
    db.flush()
    account = UserAccount(
        person_id=person.id,
        email_normalized=f"{marker}@example.test",
        password_hash="test",
        status="active",
        force_password_change=False,
        failed_login_count=0,
    )
    db.add(account)
    db.flush()
    db.add(UserRole(user_id=account.id, role_id=ROLES["student"].id))
    db.commit()
    return AuthContext(account=account, person=person, session=AuthSession())


def submission(test_id: str, submission_id: UUID | None = None) -> RawResultInput:
    payload = valid_payload(test_id)
    payload["submission_id"] = submission_id or uuid4()
    if test_id == "reactiontime":
        payload["duration_ms"] = 10000
    return ADAPTER.validate_python(payload)


def reaction_submission(submission_id: UUID) -> RawResultInput:
    return submission("reactiontime", submission_id)


def complete_battery(service, actor: AuthContext, now: datetime) -> SessionView:
    current, _ = service.start(
        actor=actor,
        request_id=uuid4(),
        catalog_version=CURRENT_CATALOG_VERSION,
        now=now,
    )
    for test_id in TEST_IDS:
        saved, _ = service.submit(
            actor=actor, session_id=current.id, value=submission(test_id), now=now
        )
        current = saved.session
    return current


def seed_legacy_session(
    db: Session,
    actor: AuthContext,
    request_id: UUID,
    now: datetime,
) -> SessionView:
    rules = get_rules("talent-training-v1")
    row = TalentTestSession(
        person_id=actor.person.id,
        created_by_user_id=actor.account.id,
        creation_key=request_id,
        creation_request_digest=fingerprint(
            SessionCreateRequest(
                request_id=request_id,
                catalog_version=rules.catalog_version,
            )
        ),
        catalog_version=rules.catalog_version,
        scoring_version=rules.scoring_version,
        report_version=rules.report_version,
        rules_snapshot=rules.model_dump(mode="json"),
        status="in_progress",
        created_at=now,
        updated_at=now,
        results=[],
        report=None,
    )
    db.add(row)
    db.commit()
    return TalentQueries.session_view(row)


def reload_actor(db: Session, account_id: UUID) -> AuthContext:
    account = db.get(UserAccount, account_id)
    return AuthContext(
        account=account, person=db.get(Person, account.person_id), session=AuthSession()
    )


def cleanup_actor(db: Session, account_id: UUID) -> None:
    from sqlalchemy import delete, select, text

    from app.audit.models import AuditEvent
    from app.talent_tests.models import TalentTestReport, TalentTestResult, TalentTestSession

    account = db.get(UserAccount, account_id)
    person_id = account.person_id
    sessions = select(TalentTestSession.id).where(TalentTestSession.person_id == person_id)
    # Committed UUID fixtures follow the established concurrency-test cleanup;
    # the trigger change is transactional and confined to the isolated test DB.
    db.execute(text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update"))
    db.execute(delete(AuditEvent).where(AuditEvent.actor_user_id == account_id))
    db.execute(text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update"))
    db.execute(delete(TalentTestReport).where(TalentTestReport.session_id.in_(sessions)))
    db.execute(delete(TalentTestResult).where(TalentTestResult.session_id.in_(sessions)))
    db.execute(delete(TalentTestSession).where(TalentTestSession.person_id == person_id))
    db.execute(delete(UserRole).where(UserRole.user_id == account_id))
    db.delete(account)
    db.flush()
    db.execute(delete(Person).where(Person.id == person_id))
    db.commit()
