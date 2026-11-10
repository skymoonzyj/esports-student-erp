"""Synthetic departure scenarios; never import operational records."""

from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.admissions.models import AdmissionCase, Enrollment
from app.auth.models import AuthSession, UserAccount
from app.auth.service import AuthContext
from app.housing.models import HousingBuilding, HousingFloor, HousingRoom, HousingStay
from app.identity.models import Person, StudentProfile
from app.portal.departure_service import PortalDepartureService
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.teaching.models import Cohort, TeachingClass
from tests.invited_housing_helpers import room, stay

NOW = datetime(2026, 10, 24, 8, tzinfo=UTC)
PROJECT_ID = UUID("30000000-0000-4000-8000-000000000001")


def departure_service(db: Session) -> PortalDepartureService:
    return PortalDepartureService(
        db,
        cipher=EnvelopeCipher(bytes(range(32))),
        lookup=LookupDigest(bytes(range(32))),
    )


def person(db: Session, name: str = "提醒测试人员") -> Person:
    marker = uuid4().hex
    result = Person(
        full_name=name,
        nickname="测试昵称",
        identity_ciphertext=marker,
        identity_digest=marker,
        sex="female",
        date_of_birth=date(2000, 1, 1),
        phone_ciphertext="secret-phone",
        email_ciphertext="secret-email",
    )
    db.add(result)
    db.flush()
    return result


def actor(db: Session, role: str = "super_admin") -> AuthContext:
    subject = person(db, "提醒测试访问人")
    account = UserAccount(
        person_id=subject.id,
        password_hash="test-only",
        status="active",
        force_password_change=False,
    )
    db.add(account)
    db.flush()
    db.add(UserRole(user_id=account.id, role_id=ROLES[role].id))
    session = AuthSession(
        user_id=account.id,
        token_digest=uuid4().hex,
        csrf_digest=uuid4().hex,
        expires_at=datetime(2099, 1, 1, tzinfo=UTC),
    )
    db.add(session)
    db.flush()
    return AuthContext(session=session, account=account, person=subject)


def housing(
    db: Session, context: AuthContext, subject: Person, *, end=NOW, status="checked_in", start=None
):
    return stay(
        db,
        context.account,
        subject,
        status=status,
        start=start or NOW - timedelta(days=10),
        end=end,
        actual_end=NOW - timedelta(days=1) if status == "checked_out" else None,
    )


def enrollment(
    db: Session,
    context: AuthContext,
    subject: Person,
    *,
    end=None,
    custom_end=None,
    status="active",
    cohort_status="active",
) -> Enrollment:
    end = end or datetime(2026, 10, 24, 16, tzinfo=UTC)
    start = min(end, custom_end or end, NOW) - timedelta(days=30)
    cohort = Cohort(
        name="虚拟营期",
        starts_at=start,
        ends_at=end,
        default_check_in_at=start,
        default_check_out_at=end,
        status=cohort_status,
        created_by_user_id=context.account.id,
    )
    db.add(cohort)
    db.flush()
    teaching_class = TeachingClass(
        cohort_id=cohort.id,
        project_id=PROJECT_ID,
        name="虚拟班级",
        class_type="standard",
        capacity=2000,
        default_tuition_cents=0,
        created_by_user_id=context.account.id,
    )
    case = AdmissionCase(
        student_person_id=subject.id,
        status="ready",
        agreed_tuition_cents=0,
        created_by_user_id=context.account.id,
    )
    db.add_all([teaching_class, case])
    db.flush()
    result = Enrollment(
        admission_case_id=case.id,
        student_person_id=subject.id,
        cohort_id=cohort.id,
        class_id=teaching_class.id,
        agreed_tuition_cents=0,
        status=status,
        confirmed_at=start,
        created_by_user_id=context.account.id,
        schedule_mode="custom" if custom_end else "cohort",
        custom_starts_at=start if custom_end else None,
        custom_ends_at=custom_end,
        completed_at=NOW - timedelta(days=5) if status == "completed" else None,
        cancelled_at=NOW - timedelta(days=5) if status == "cancelled" else None,
    )
    db.add(result)
    db.flush()
    return result


def deny(db: Session, context: AuthContext, permission: str, kind: str, value) -> None:
    db.add(
        UserPermissionOverride(
            user_id=context.account.id,
            permission_id=PERMISSIONS[permission].id,
            effect="deny",
            scope_kind=kind,
            scope_id=value,
        )
    )
    db.flush()


__all__ = ["NOW", "actor", "deny", "enrollment", "housing", "person", "room"]


@contextmanager
def committed_scene(engine):
    """Independent connections must see committed data; remove only our UUIDs."""
    with Session(engine, expire_on_commit=False) as db:
        context = actor(db)
        subject = person(db)
        source = housing(db, context, subject)
        room_row = db.get(HousingRoom, source.room_id)
        room_id, floor_id, building_id = room_row.id, room_row.floor_id, room_row.building_id
        db.commit()
        try:
            yield db, context, subject, source
        finally:
            db.rollback()
            db.execute(
                delete(UserPermissionOverride).where(
                    UserPermissionOverride.user_id == context.account.id
                )
            )
            db.execute(delete(HousingStay).where(HousingStay.id == source.id))
            db.execute(delete(HousingRoom).where(HousingRoom.id == room_id))
            db.execute(delete(HousingFloor).where(HousingFloor.id == floor_id))
            db.execute(delete(HousingBuilding).where(HousingBuilding.id == building_id))
            db.execute(delete(AuthSession).where(AuthSession.user_id == context.account.id))
            db.execute(delete(UserRole).where(UserRole.user_id == context.account.id))
            db.execute(delete(UserAccount).where(UserAccount.id == context.account.id))
            db.execute(delete(StudentProfile).where(StudentProfile.person_id == context.person.id))
            db.execute(delete(Person).where(Person.id.in_([context.person.id, subject.id])))
            db.commit()


def business_snapshot(db):
    tables = (Person, HousingStay, HousingRoom, Enrollment)
    return {row.__tablename__: list(db.execute(select(row.__table__)).all()) for row in tables}
