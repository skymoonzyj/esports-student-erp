"""Pending evidence changes cannot bypass the versioned correction workflow."""

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.auth.models import UserAccount
from app.housing.models import HousingStay
from app.housing.occupancy_service import HousingOccupancyService
from app.housing.resident_readiness import HousingResidentReadinessPolicy
from app.identity.models import Person
from app.identity.schemas import PersonUpdate
from app.personnel_intake.claims.models import ProfileClaimBinding
from app.personnel_intake.errors import PersonnelIntakeError
from app.personnel_intake.models import AssistedApproval, AssistedPersonProfile
from app.personnel_intake.schemas import PendingIntakeInput
from tests.api.test_people import sign_in
from tests.db.test_housing_stay_lifecycle import _allow, _input, _room
from tests.services.test_personnel_approval import approvals
from tests.services.test_personnel_intake import actor


@pytest.fixture
def housed_pending(db, platform_users):
    who = actor(platform_users, "admin")
    source = PendingIntakeInput(
        kind="staff",
        full_name="待补住宿外籍人员",
        identity_number="PENDING-GUARD-851",
        document_type="passport",
        sex="male",
        date_of_birth=None,
        email="pending-guard@example.com",
    )
    result = approvals(db).direct_create_pending(who, source, uuid4())
    person = db.get(Person, result.person_id)
    profile = db.get(AssistedPersonProfile, person.id)
    now = datetime.now(UTC)
    service = approvals(db)
    stay = HousingOccupancyService(
        db,
        resident_readiness=HousingResidentReadinessPolicy(
            db, cipher=service.cipher, lookup=service.lookup
        ),
    ).create_stay(
        _room(db).id,
        _input(person.id, start=now - timedelta(days=1)),
        actor_user_id=who.account.id,
        occurred_at=now,
        authorize_room=_allow,
    )
    assert stay.status == "checked_in"
    assert profile.activation_requirement == "reviewed_claim"
    assert db.scalar(select(UserAccount.id).where(UserAccount.person_id == person.id)) is None
    assert (
        db.scalar(select(ProfileClaimBinding.id).where(ProfileClaimBinding.person_id == person.id))
        is None
    )
    before_person = {column.key: getattr(person, column.key) for column in Person.__table__.columns}
    before_stay = {
        column.key: getattr(stay, column.key) for column in HousingStay.__table__.columns
    }
    return (
        person.id,
        source,
        (profile.current_approval_id, profile.version),
        before_person,
        before_stay,
    )


def assert_approval_and_stay_unchanged(db, context):
    person_id, _, approval_state, _, before_stay = context
    profile = db.get(AssistedPersonProfile, person_id, populate_existing=True)
    assert (profile.current_approval_id, profile.version) == approval_state
    assert (
        db.scalar(
            select(func.count())
            .select_from(AssistedApproval)
            .where(AssistedApproval.person_id == person_id)
        )
        == 1
    )
    stay = db.get(HousingStay, before_stay["id"], populate_existing=True)
    assert {
        column.key: getattr(stay, column.key) for column in HousingStay.__table__.columns
    } == before_stay


@pytest.mark.parametrize(
    "changes",
    [
        {"sex": "female"},
        {"full_name": "未经审核的新名字"},
        {"date_of_birth": date(1990, 1, 1)},
        {"document_issuing_country": "BR"},
    ],
)
def test_pending_same_email_unbound_housed_facts_require_versioned_correction(
    db, platform_users, housed_pending, changes
):
    person_id, source, _, before_person, _ = housed_pending
    with pytest.raises(PersonnelIntakeError) as blocked:
        platform_users["identity_service"].update_person(
            person_id,
            PersonUpdate(email=source.email.get_secret_value(), **changes),
            actor_user_id=platform_users["accounts"]["admin"].id,
            request_id=str(uuid4()),
        )
    assert blocked.value.status == 409
    assert blocked.value.code == "pending_profile_correction_required"
    person = db.get(Person, person_id, populate_existing=True)
    assert {
        column.key: getattr(person, column.key) for column in Person.__table__.columns
    } == before_person
    assert_approval_and_stay_unchanged(db, housed_pending)


def test_pending_http_ordinary_update_cannot_change_housed_sex(
    db, db_client, platform_users, housed_pending
):
    person_id, source, _, before_person, _ = housed_pending
    csrf = sign_in(db_client, platform_users, "admin")
    response = db_client.patch(
        f"/api/v1/people/{person_id}",
        json={"sex": "female", "email": source.email.get_secret_value()},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "pending_profile_correction_required"
    assert response.json()["detail"] == "待补档案的实名与联系资料请通过待补资料更正流程修改"
    person = db.get(Person, person_id, populate_existing=True)
    assert {
        column.key: getattr(person, column.key) for column in Person.__table__.columns
    } == before_person
    assert_approval_and_stay_unchanged(db, housed_pending)


def test_pending_nickname_notes_and_unchanged_identity_facts_remain_editable(
    db, db_client, platform_users, housed_pending
):
    person_id, source, _, _, _ = housed_pending
    csrf = sign_in(db_client, platform_users, "admin")
    response = db_client.patch(
        f"/api/v1/people/{person_id}",
        json={
            "full_name": source.full_name,
            "identity_number": source.identity_number.get_secret_value(),
            "document_type": "passport",
            "document_issuing_country": None,
            "sex": "male",
            "date_of_birth": None,
            "email": source.email.get_secret_value(),
            "nickname": "已核对昵称",
            "profile_notes": "内部补充说明",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    assert response.json()["nickname"] == "已核对昵称"
    assert db.get(Person, person_id, populate_existing=True).profile_notes == "内部补充说明"
    assert_approval_and_stay_unchanged(db, housed_pending)
