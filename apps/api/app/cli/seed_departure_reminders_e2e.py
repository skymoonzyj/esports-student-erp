"""Synthetic departure fixtures for the controller-owned local database only."""

from __future__ import annotations

import argparse
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import delete, make_url, select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from app.admissions.models import AdmissionCase, Enrollment
from app.cli import seed_e2e as base
from app.cli.departure_e2e_runtime import DepartureRuntimeRefused, require_departure_database
from app.core.config import Settings, get_settings
from app.core.database import SessionLocal
from app.core.transactions import write_transaction
from app.housing.models import HousingBuilding, HousingFloor, HousingRoom, HousingStay
from app.identity.models import GuardianRelationship, Person, StudentProfile
from app.personnel_intake.models import CoachPersonResponsibility
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES
from app.registration.models import RegistrationApplication  # noqa: F401 - binding FK metadata
from app.revenue_share.models import ReferralCode, StudentReferralBinding
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService
from app.teaching.models import (
    CoachAssignment,
    Cohort,
    GameProject,
    ProfessionalTeam,
    TeachingClass,
)
from app.team_management.models import TeamManagerAssignment, TeamMembership

__all__ = ["DepartureRuntimeRefused", "seed_departure_reminders"]
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _require_seed_session(db: Session, validated_database_url: str) -> None:
    """Prove the actual reset target without opening a connection or issuing SQL."""
    try:
        # The CLI factory generates an otherwise stock Session subclass. Unknown
        # subclasses may route per mapper or statement even with a safe .bind.
        if type(db) not in (Session, SessionLocal.class_):
            raise ValueError
        if getattr(db.get_bind, "__func__", None) is not Session.get_bind:
            raise ValueError
        # SQLAlchemy exposes no public accessor for mapper/table bind routing.
        # Fail closed if its routing inventory is missing, unknown or nonempty.
        bindings = vars(db).get("_Session__binds")
        if type(bindings) is not dict or bindings:
            raise ValueError
        bind = db.bind
        if not isinstance(bind, (Engine, Connection)) or db.get_bind() is not bind:
            raise ValueError
        engine = bind.engine if isinstance(bind, Connection) else bind
        if not isinstance(engine, Engine) or engine.url != make_url(validated_database_url):
            raise ValueError
    except Exception:  # noqa: BLE001 - never stringify a bind/URL or its password.
        raise DepartureRuntimeRefused(
            "Departure seed Session requires the exact task-owned local database"
        ) from None


def seed_departure_reminders(db: Session, settings: Settings, now: datetime) -> None:
    validated_database_url = settings.database_url
    require_departure_database(settings)
    if settings.database_url != validated_database_url:
        raise DepartureRuntimeRefused("Departure seed requires the exact task-owned local database")
    _require_seed_session(db, validated_database_url)
    if now.tzinfo is None:
        raise ValueError("Departure seed requires an aware server time")
    settings.validate_runtime_secrets()
    cipher = EnvelopeCipher(base._secret_bytes(settings, "field_encryption_master_key"))
    lookup = LookupDigest(base._secret_bytes(settings, "field_lookup_key"))
    base.seed_e2e(db, settings=settings, cipher=cipher, lookup=lookup, passwords=PasswordService())
    today = now.astimezone(SHANGHAI).date()
    midnight = datetime.combine(today, time.min, tzinfo=SHANGHAI)
    admin = base.E2E_ADMIN_ACCOUNT_ID
    with write_transaction(db):
        roles = {
            admin: "super_admin",
            base.E2E_CUSTOMER_SERVICE_ACCOUNT_ID: "administration",
            base.E2E_COACH_ACCOUNT_ID: "coach",
            base.E2E_READY_MALE_STUDENT_ACCOUNT_ID: "team_manager",
            base.E2E_LIFE_TEACHER_ACCOUNT_ID: "registered_user",
            base.E2E_OTHER_GUARDIAN_ACCOUNT_ID: "registered_user",
        }
        for account_id, role in roles.items():
            db.execute(delete(UserRole).where(UserRole.user_id == account_id))
            db.add(UserRole(user_id=account_id, role_id=ROLES[role].id))
        db.execute(delete(GuardianRelationship).where(GuardianRelationship.guardian_person_id.in_(
            [base.E2E_COACH_PERSON_ID, base.E2E_OTHER_GUARDIAN_PERSON_ID]
        )))
        buildings = list(db.scalars(select(HousingBuilding)))
        fourth = next(building for building in buildings if building.code == "4")
        for permission, scope, scope_id in [
            ("personnel_intake.housing.read", "global", None),
            *[("housing.read", "building", row.id) for row in buildings if row.id != fourth.id],
        ]:
            db.add(UserPermissionOverride(
                user_id=base.E2E_CUSTOMER_SERVICE_ACCOUNT_ID,
                permission_id=PERMISSIONS[permission].id, effect="deny",
                scope_kind=scope, scope_id=scope_id,
                created_by_user_id=admin, grant_reason="Synthetic scoped administration",
            ))
        rooms = list(db.execute(
            select(HousingRoom, HousingFloor.name, HousingBuilding.name)
            .join(HousingFloor, HousingFloor.id == HousingRoom.floor_id)
            .join(HousingBuilding, HousingBuilding.id == HousingRoom.building_id)
            .where(HousingRoom.room_type != "functional", HousingRoom.capacity > 0)
            .order_by(HousingRoom.room_number)
        ))
        ordinary_rooms = iter(row for row in rooms if row[0].building_id == fourth.id)
        outside_rooms = iter(row for row in rooms if row[0].building_id != fourth.id)

        def person(index: int, name: str, *, pending: bool = False) -> Person:
            subject = base._seed_person(
                person_id=UUID(f"ed600000-0000-4000-8000-{index:012d}"), full_name=name,
                identity_birth="20000102", identity_sequence=500 + index,
                sex="female", date_of_birth=date(2000, 1, 2), cipher=cipher, lookup=lookup,
            )
            if pending:
                subject.document_type = "passport"
                subject.passport_number_digest = lookup.digest(f"DEPARTURE-SYNTHETIC-{index}")
                subject.intake_mode = "pending"
                subject.sex = subject.date_of_birth = None
            db.add(subject)
            db.flush()
            db.add(StudentProfile(person_id=subject.id, student_number=f"DEPARTURE-{index}", status="active"))
            db.flush()
            return subject

        def stay(subject: Person, offset: int | None = 0, *, outside: bool = False, checked_out: bool = False) -> None:
            room, floor_name, building_name = next(outside_rooms if outside else ordinary_rooms)
            db.add(HousingStay(
                person_id=subject.id, room_id=room.id, stay_purpose="permanent",
                duration_mode="long_term" if offset is None else "fixed_term",
                planned_check_in_at=midnight - timedelta(days=10),
                actual_check_in_at=midnight - timedelta(days=10),
                planned_check_out_at=midnight + timedelta(days=offset) if offset is not None else None,
                actual_check_out_at=midnight - timedelta(days=1) if checked_out else None,
                status="checked_out" if checked_out else "checked_in",
                created_by_user_id=admin, checked_in_by_user_id=admin,
                checked_out_by_user_id=admin if checked_out else None,
                room_name_snapshot=room.room_number, floor_name_snapshot=floor_name,
                building_name_snapshot=building_name, version=1,
            ))

        def enrollment(subject: Person, offset: int = 0, *, completed: bool = False, coached: bool = False) -> None:
            end = midnight + timedelta(days=offset + 1)
            cohort_end = midnight - timedelta(days=4) if completed else end
            cohort = Cohort(
                name="E2E独立日期公共已结束营期" if completed else "E2E当前提醒营期",
                starts_at=midnight - timedelta(days=30), ends_at=cohort_end,
                default_check_in_at=midnight - timedelta(days=30), default_check_out_at=cohort_end,
                status="completed" if completed else "active", created_by_user_id=admin,
            )
            db.add(cohort)
            db.flush()
            teaching_class = TeachingClass(
                cohort_id=cohort.id, project_id=base._LOL_PROJECT_ID, name="E2E提醒班级",
                class_type="standard", capacity=100, default_tuition_cents=0, created_by_user_id=admin,
            )
            case = AdmissionCase(student_person_id=subject.id, status="ready", agreed_tuition_cents=0, created_by_user_id=admin)
            db.add_all([teaching_class, case])
            db.flush()
            row = Enrollment(
                admission_case_id=case.id, student_person_id=subject.id, cohort_id=cohort.id,
                class_id=teaching_class.id, agreed_tuition_cents=0,
                status="completed" if completed else "active", confirmed_at=midnight - timedelta(days=25),
                created_by_user_id=admin, completed_at=midnight - timedelta(days=5) if completed else None,
                schedule_mode="custom" if completed else "cohort",
                custom_starts_at=midnight - timedelta(days=25) if completed else None,
                custom_ends_at=end if completed else None,
            )
            db.add(row)
            db.flush()
            case.status, case.current_enrollment_id = "enrolled", row.id
            if coached:
                db.add(CoachAssignment(
                    class_id=teaching_class.id, coach_person_id=base.E2E_COACH_PERSON_ID,
                    assignment_type="primary", valid_from=midnight - timedelta(days=10),
                    valid_until=midnight + timedelta(days=10), created_by_user_id=admin,
                ))

        pending = person(1, "E2E未激活提醒", pending=True)
        stay(pending)
        enrollment(pending, 1)
        permanent = person(2, "E2E常住结营提醒")
        stay(permanent, None)
        enrollment(permanent)
        for index, offset in ((3, 0), (4, 2)):
            stay(person(index, "E2E同名提醒"), offset)
        multiple = person(5, "E2E多来源提醒")
        stay(multiple, 1)
        enrollment(multiple)
        enrollment(multiple, 2)
        enrollment(person(6, "E2E走读结营提醒"), 1)
        enrollment(person(7, "E2E独立已完成提醒"), 1, completed=True)
        stay(person(8, "E2E已退房不提醒"), checked_out=True)
        coached = person(9, "E2E教练关联提醒")
        stay(coached, 1)
        enrollment(coached, 1, coached=True)
        db.add(CoachPersonResponsibility(coach_person_id=base.E2E_COACH_PERSON_ID, person_id=coached.id, created_by_user_id=admin))
        teammate = person(10, "E2E队伍关联提醒")
        stay(teammate, outside=True)
        enrollment(teammate)
        division = GameProject(name="E2E提醒虚构队伍项目", sort_order=100, is_active=True, is_team_division=True, created_by_user_id=admin)
        db.add(division)
        db.flush()
        team = ProfessionalTeam(project_id=division.id, name="E2E提醒专属队伍", is_active=True, created_by_user_id=admin)
        db.add(team)
        db.flush()
        db.add_all([
            TeamManagerAssignment(account_id=base.E2E_READY_MALE_STUDENT_ACCOUNT_ID, team_id=team.id, created_by_user_id=admin),
            TeamMembership(person_id=teammate.id, team_id=team.id, joined_on=today - timedelta(days=1), created_by_user_id=admin),
        ])
        invited = person(11, "E2E邀请关联提醒")
        stay(invited, outside=True)
        enrollment(invited)
        code = ReferralCode(code="DEPARTURE-SYNTHETIC", label="E2E提醒邀请", source_kind="person", referrer_person_id=base.E2E_LIFE_TEACHER_PERSON_ID, created_by_user_id=admin)
        db.add(code)
        db.flush()
        db.add(StudentReferralBinding(student_person_id=invited.id, referral_code_id=code.id, source_kind="person", referrer_person_id=base.E2E_LIFE_TEACHER_PERSON_ID, bound_at=now - timedelta(days=1)))
        for identifier in (base.E2E_SELF_STUDENT_PERSON_ID, base.E2E_BOUND_STUDENT_ID):
            subject = db.get(Person, identifier)
            assert subject is not None
            stay(subject)
            enrollment(subject)
        for index, width in enumerate((1440, 768, 360), 1):
            subject = person(100 + index, f"E2E撤权提醒{width}")
            stay(subject)
            enrollment(subject)
            db.add(GuardianRelationship(
                id=UUID(f"ed610000-0000-4000-8000-{index:012d}"), guardian_person_id=base.E2E_GUARDIAN_PERSON_ID,
                student_person_id=subject.id, relationship="father", is_primary=False,
                valid_from=today - timedelta(days=10), requires_verification=False,
            ))
        for index in range(1, 26):
            stay(person(200 + index, f"E2E分页{index:02d}"), 2)
        db.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-database", action="store_true")
    args = parser.parse_args(argv)
    settings = get_settings()
    require_departure_database(settings)
    if args.check_database:
        return 0
    settings.validate_runtime_secrets()
    with SessionLocal() as db:
        seed_departure_reminders(db, settings, datetime.now(UTC))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
