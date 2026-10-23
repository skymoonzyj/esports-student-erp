from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID, uuid4

from sqlalchemy import Engine, delete, func, or_, select, text
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.auth.models import AuthSession, UserAccount
from app.auth.schemas import AccountCreate
from app.auth.service import AccountService, AuthContext
from app.esports_assessments.catalog import get_task
from app.esports_assessments.models import (
    AssessmentAttempt,
    AssessmentPracticeRun,
    AssessmentReport,
    AssessmentResult,
    AssessmentSession,
)
from app.esports_assessments.schemas import (
    AttemptCreate,
    ControlSettings,
    EnvironmentSnapshot,
    QualityInput,
    ResultSubmit,
    SessionCreate,
)
from app.identity.models import Person, StudentProfile
from app.identity.schemas import StudentCreate
from app.identity.service import IdentityService
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import ROLES
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService
from tests.unit.esports_samples import sample_raw
from tests.unit.esports_v3_samples import sample_raw_v3


@dataclass(frozen=True, slots=True)
class CommittedAssessmentActor:
    account_id: UUID
    person_id: UUID


def _cn_id(birth: str, sequence: int) -> str:
    body = f"110105{birth}{sequence:03d}"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    checks = ("1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2")
    checksum = sum(int(digit) * weight for digit, weight in zip(body, weights))
    return body + checks[checksum % 11]


def committed_assessment_actor(engine: Engine) -> CommittedAssessmentActor:
    marker = uuid4()
    value = marker.int
    birth = date(1980 + value % 20, 1 + (value // 20) % 12, 1 + (value // 240) % 28)
    sequence = 1 + (value // 6720) % 999
    phone = f"13{value % 1_000_000_000:09d}"
    email = f"assessment-{marker.hex}@example.com"
    with Session(engine) as db:
        identity = IdentityService(
            db,
            cipher=EnvelopeCipher(bytes(range(32))),
            lookup=LookupDigest(bytes(range(32))),
        )
        person = identity.create_student(
            StudentCreate(
                full_name="电竞评测并发学员",
                identity_number=_cn_id(birth.strftime("%Y%m%d"), sequence),
                sex="male" if sequence % 2 else "female",
                date_of_birth=birth,
                phone=phone,
                email=email,
                student_number=f"EA-{marker.hex[:20]}",
            )
        )
        created = AccountService(
            db,
            identity_service=identity,
            passwords=PasswordService(),
            reference_date_provider=lambda: date(2026, 9, 22),
        ).create_account(AccountCreate(person_id=person.id, login_phone=phone, email=email))
        db.add(UserRole(user_id=created.account.id, role_id=ROLES["student"].id))
        db.commit()
        return CommittedAssessmentActor(
            account_id=created.account.id,
            person_id=person.id,
        )


def student_actor(db: Session) -> AuthContext:
    marker = uuid4().hex
    person = Person(
        full_name="电竞评测学员",
        nickname="评测昵称",
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


def reload_actor(db: Session, account_id: UUID) -> AuthContext:
    account = db.get(UserAccount, account_id)
    assert account is not None
    person = db.get(Person, account.person_id)
    assert person is not None
    return AuthContext(account=account, person=person, session=AuthSession())


def settings(suite_id: str) -> ControlSettings:
    if suite_id == "pc-fps":
        return ControlSettings(sensitivity=1, input_kind="mouse", mouse_mode="raw")
    if suite_id == "pc-moba":
        return ControlSettings(sensitivity=1, input_kind="mouse")
    controls = [
        {
            "id": "move",
            "kind": "joystick",
            "x": 0.15,
            "y": 0.75,
            "width": 120,
            "height": 120,
        }
    ]
    if suite_id == "mobile-fps":
        controls.append(
            {
                "id": "fire",
                "kind": "button",
                "x": 0.85,
                "y": 0.75,
                "width": 64,
                "height": 64,
            }
        )
    else:
        controls.extend(
            [
                {
                    "id": "attack",
                    "kind": "button",
                    "x": 0.82,
                    "y": 0.78,
                    "width": 64,
                    "height": 64,
                },
                {
                    "id": "skill",
                    "kind": "button",
                    "x": 0.70,
                    "y": 0.60,
                    "width": 64,
                    "height": 64,
                },
            ]
        )
    return ControlSettings.model_validate(
        {"sensitivity": 1, "input_kind": "touch", "controls": controls}
    )


def environment(suite_id: str) -> EnvironmentSnapshot:
    mobile = suite_id.startswith("mobile")
    pc_fps = suite_id == "pc-fps"
    return EnvironmentSnapshot(
        input_kind="touch" if mobile else "mouse",
        is_phone=mobile,
        touch_capable=mobile,
        width_css_px=844 if mobile else 1280,
        height_css_px=390 if mobile else 720,
        device_pixel_ratio=2 if mobile else 1,
        pointer_lock_supported=pc_fps,
        pointer_lock_active=pc_fps,
        raw_mouse_requested=pc_fps,
        raw_mouse_confirmed=pc_fps,
        webgl2_supported="fps" in suite_id,
    )


def quality(suite_id: str, **updates: object) -> QualityInput:
    payload: dict[str, object] = {
        "duration_ms": 60000,
        "max_frame_gap_ms": 16,
        "long_frame_gap_total_ms": 0,
        "input_kind": "touch" if suite_id.startswith("mobile") else "mouse",
        "hidden": False,
        "interrupted": False,
    }
    payload.update(updates)
    return QualityInput.model_validate(payload)


def session_request(suite_id: str = "pc-fps", key: UUID | None = None) -> SessionCreate:
    return SessionCreate(suite_id=suite_id, operation_key=key or uuid4())


def attempt_request(
    suite_id: str,
    test_id: str,
    key: UUID | None = None,
) -> AttemptCreate:
    return AttemptCreate.model_validate(
        {
            "test_id": test_id,
            "operation_key": key or uuid4(),
            "settings": settings(suite_id),
            "environment": environment(suite_id),
        }
    )


def result_request(
    suite_id: str,
    test_id: str,
    *,
    seed: int,
    key: UUID | None = None,
    perfect: bool = True,
    quality_updates: dict[str, object] | None = None,
    catalog_version: str | None = None,
) -> ResultSubmit:
    config = (
        get_task(suite_id, test_id, catalog_version=catalog_version)
        if catalog_version is not None
        else None
    )
    raw = sample_raw(suite_id, test_id, perfect=perfect, seed=seed)
    if config is not None and config.catalog_version == f"{suite_id}-v3":
        raw = sample_raw_v3(config, perfect=perfect, seed=seed)
    return ResultSubmit.model_validate(
        {
            "operation_key": key or uuid4(),
            "raw": raw,
            "quality": quality(suite_id, **(quality_updates or {})),
        }
    )


def counts(db: Session, person_id: UUID, account_id: UUID) -> tuple[int, int, int, int, int]:
    sessions = select(AssessmentSession.id).where(AssessmentSession.person_id == person_id)
    return (
        db.scalar(
            select(func.count())
            .select_from(AssessmentSession)
            .where(AssessmentSession.person_id == person_id)
        )
        or 0,
        db.scalar(
            select(func.count())
            .select_from(AssessmentAttempt)
            .where(AssessmentAttempt.session_id.in_(sessions))
        )
        or 0,
        db.scalar(
            select(func.count())
            .select_from(AssessmentResult)
            .where(AssessmentResult.session_id.in_(sessions))
        )
        or 0,
        db.scalar(
            select(func.count())
            .select_from(AssessmentReport)
            .where(AssessmentReport.session_id.in_(sessions))
        )
        or 0,
        db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.actor_user_id == account_id,
                AuditEvent.action.like("esports_assessments.%"),
            )
        )
        or 0,
    )


def assessment_counts(db: Session, person_id: UUID) -> dict[str, int]:
    formal_sessions = select(AssessmentSession.id).where(AssessmentSession.person_id == person_id)
    return {
        "formal_results": db.scalar(
            select(func.count())
            .select_from(AssessmentResult)
            .where(AssessmentResult.session_id.in_(formal_sessions))
        )
        or 0,
        "formal_reports": db.scalar(
            select(func.count())
            .select_from(AssessmentReport)
            .where(AssessmentReport.session_id.in_(formal_sessions))
        )
        or 0,
        "practice_results": db.scalar(
            select(func.count())
            .select_from(AssessmentPracticeRun)
            .where(
                AssessmentPracticeRun.person_id == person_id,
                AssessmentPracticeRun.state == "submitted",
            )
        )
        or 0,
    }


def cleanup_assessment_actor(engine: Engine, person_id: UUID) -> None:
    with Session(engine) as db:
        account_ids = set(
            db.scalars(select(UserAccount.id).where(UserAccount.person_id == person_id))
        )
        session_ids = select(AssessmentSession.id).where(AssessmentSession.person_id == person_id)
        if account_ids:
            db.execute(text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update"))
            try:
                db.execute(delete(AuditEvent).where(AuditEvent.actor_user_id.in_(account_ids)))
            finally:
                db.execute(text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update"))
        db.execute(delete(AssessmentReport).where(AssessmentReport.session_id.in_(session_ids)))
        db.execute(delete(AssessmentResult).where(AssessmentResult.session_id.in_(session_ids)))
        db.execute(delete(AssessmentAttempt).where(AssessmentAttempt.session_id.in_(session_ids)))
        db.execute(delete(AssessmentSession).where(AssessmentSession.person_id == person_id))
        db.execute(
            delete(AssessmentPracticeRun).where(AssessmentPracticeRun.person_id == person_id)
        )
        if account_ids:
            db.execute(
                delete(UserPermissionOverride).where(
                    or_(
                        UserPermissionOverride.user_id.in_(account_ids),
                        UserPermissionOverride.created_by_user_id.in_(account_ids),
                    )
                )
            )
            db.execute(delete(UserRole).where(UserRole.user_id.in_(account_ids)))
            db.execute(delete(UserAccount).where(UserAccount.id.in_(account_ids)))
        db.execute(delete(StudentProfile).where(StudentProfile.person_id == person_id))
        db.execute(delete(Person).where(Person.id == person_id))
        db.commit()


def cleanup_actor(db: Session, account_id: UUID) -> None:
    account = db.get(UserAccount, account_id)
    assert account is not None
    person_id = account.person_id
    session_ids = select(AssessmentSession.id).where(AssessmentSession.person_id == person_id)
    db.execute(text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update"))
    db.execute(delete(AuditEvent).where(AuditEvent.actor_user_id == account_id))
    db.execute(text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update"))
    db.execute(delete(AssessmentReport).where(AssessmentReport.session_id.in_(session_ids)))
    db.execute(delete(AssessmentResult).where(AssessmentResult.session_id.in_(session_ids)))
    db.execute(delete(AssessmentAttempt).where(AssessmentAttempt.session_id.in_(session_ids)))
    db.execute(delete(AssessmentSession).where(AssessmentSession.person_id == person_id))
    db.execute(delete(AssessmentPracticeRun).where(AssessmentPracticeRun.person_id == person_id))
    db.execute(delete(UserRole).where(UserRole.user_id == account_id))
    db.delete(account)
    db.flush()
    db.execute(delete(Person).where(Person.id == person_id))
    db.commit()
