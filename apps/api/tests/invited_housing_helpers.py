from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.housing.models import HousingBuilding, HousingFloor, HousingRoom, HousingStay
from app.identity.models import Person, StudentProfile
from app.rbac.models import UserRole
from app.rbac.seed import ROLES
from app.revenue_share.models import (
    ReferralCode,
    RevenueShareOrganization,
    RevenueShareOrganizationManagerAssignment,
    StudentReferralBinding,
)

NOW = datetime(2026, 10, 11, 16, 1, tzinfo=UTC)


def person(db: Session, *, student=False):
    marker = uuid4().hex
    row = Person(
        full_name=f"测试-{marker}",
        nickname="昵称",
        identity_ciphertext=marker,
        identity_digest=marker,
        sex="female",
        date_of_birth=date(1990, 1, 1),
    )
    db.add(row)
    db.flush()
    if student:
        db.add(StudentProfile(person_id=row.id, student_number=marker, status="active"))
        db.flush()
    return row


def account(db: Session, role="registered_user"):
    row = UserAccount(person_id=person(db).id, password_hash="test", status="active")
    db.add(row)
    db.flush()
    db.add(UserRole(user_id=row.id, role_id=ROLES[role].id))
    db.flush()
    return row


def organization(db: Session, actor, name=None):
    row = RevenueShareOrganization(name=name or uuid4().hex, created_by_user_id=actor.id)
    db.add(row)
    db.flush()
    return row


def manager(db: Session, org, actor):
    row = RevenueShareOrganizationManagerAssignment(
        organization_id=org.id,
        account_id=actor.id,
        granted_by_user_id=actor.id,
        granted_at=NOW - timedelta(days=1),
    )
    db.add(row)
    db.flush()
    return row


def code(db: Session, actor, org=None):
    row = ReferralCode(
        code=uuid4().hex[:24],
        label="邀请码",
        source_kind="organization" if org else "person",
        referrer_person_id=None if org else actor.person_id,
        referrer_organization_id=org.id if org else None,
        created_by_user_id=actor.id,
    )
    db.add(row)
    db.flush()
    return row


def binding(db: Session, actor, org=None, student=None, bound_at=NOW):
    student = student or person(db, student=True)
    invitation = code(db, actor, org)
    row = StudentReferralBinding(
        student_person_id=student.id,
        referral_code_id=invitation.id,
        source_kind=invitation.source_kind,
        referrer_person_id=invitation.referrer_person_id,
        referrer_organization_id=invitation.referrer_organization_id,
        bound_at=bound_at,
    )
    db.add(row)
    db.flush()
    return student, row, invitation


def room(db: Session):
    marker = uuid4().hex
    building = HousingBuilding(code=marker[:16], name=marker)
    db.add(building)
    db.flush()
    floor = HousingFloor(building_id=building.id, number=1, name="1层")
    db.add(floor)
    db.flush()
    row = HousingRoom(
        building_id=building.id,
        floor_id=floor.id,
        room_number="101",
        normalized_key=marker,
        room_type="single",
        capacity=1,
    )
    db.add(row)
    db.flush()
    return row


def stay(
    db: Session,
    actor,
    student,
    *,
    status="planned",
    start=None,
    end=None,
    actual_end=None,
    stay_id=None,
):
    start = start or NOW + timedelta(days=2)
    row = HousingStay(
        person_id=student.id,
        room_id=room(db).id,
        stay_purpose="permanent",
        duration_mode="long_term" if end is None else "fixed_term",
        planned_check_in_at=start,
        planned_check_out_at=end,
        actual_check_in_at=start
        if status in {"checked_in", "checkout_pending", "checked_out"}
        else None,
        actual_check_out_at=actual_end,
        status=status,
        room_name_snapshot="101",
        floor_name_snapshot="1层",
        building_name_snapshot="测试楼",
        notes="私密住宿备注",
        created_by_user_id=actor.id,
        checked_in_by_user_id=actor.id
        if status in {"checked_in", "checkout_pending", "checked_out"}
        else None,
        checked_out_by_user_id=actor.id if status == "checked_out" else None,
        cancelled_by_user_id=actor.id if status == "cancelled" else None,
        cancellation_reason="取消" if status == "cancelled" else None,
    )
    if stay_id:
        row.id = stay_id
    db.add(row)
    db.flush()
    return row
