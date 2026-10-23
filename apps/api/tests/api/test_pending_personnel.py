"""Pending intake preserves housing facts without inventing activation authority."""

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.auth.models import AccountLoginIdentifier, UserAccount
from app.identity.models import GuardianRelationship, Person, StaffProfile
from app.personnel_intake.errors import PersonnelIntakeError
from app.personnel_intake.models import AssistedApproval, AssistedPersonProfile
from app.rbac.models import UserRole
from tests.api.conftest import make_cn_id
from tests.services.test_personnel_approval import approvals
from tests.services.test_personnel_approval import platform_users as _platform_users
from tests.services.test_personnel_intake import actor


@pytest.fixture
def platform_users(db):
    return _platform_users.__wrapped__(db)


def pending(**changes):
    from app.personnel_intake.schemas import PendingIntakeInput

    return PendingIntakeInput.model_validate(
        {
            "kind": "student",
            "full_name": "待补未成年",
            "identity_number": make_cn_id("20150102", 851),
            "sex": "male",
            "date_of_birth": "2015-01-02",
            **changes,
        }
    )


def test_pending_minor_can_reside_but_cannot_activate(db, platform_users):
    from app.personnel_intake.readiness import AssistedReadinessPolicy

    counts = [
        db.scalar(select(func.count()).select_from(t))
        for t in (UserAccount, AccountLoginIdentifier, UserRole)
    ]
    result = approvals(db).direct_create_pending(actor(platform_users, "admin"), pending(), uuid4())
    readiness = AssistedReadinessPolicy(
        db, cipher=approvals(db).cipher, lookup=approvals(db).lookup
    ).evaluate_many([result.person_id], reference_date=date(2026, 10, 21))[result.person_id]
    assert readiness.housing_ready
    assert not readiness.activation_profile_ready
    assert readiness.completion_allowed
    after = [
        db.scalar(select(func.count()).select_from(t))
        for t in (UserAccount, AccountLoginIdentifier, UserRole)
    ]
    new_accounts, new_login_identifiers, new_roles = [b - a for a, b in zip(counts, after)]
    assert new_accounts == new_login_identifiers == new_roles == 0
    assert (
        db.scalar(
            select(GuardianRelationship).where(
                GuardianRelationship.student_person_id == result.person_id
            )
        )
        is None
    )
    assert db.get(Person, result.person_id).intake_mode == "pending"
    assert (
        db.get(AssistedPersonProfile, result.person_id).activation_requirement == "reviewed_claim"
    )


@pytest.mark.parametrize("who", ["coach", "self_student"])
def test_pending_creation_requires_global_admin_boundary(db, platform_users, who):
    # Coach fixture also has an admin role, so remove only that extra review role.
    if who == "coach":
        from app.rbac.seed import ROLES

        role = db.scalar(
            select(UserRole).where(
                UserRole.user_id == platform_users["accounts"][who].id,
                UserRole.role_id == ROLES["admin"].id,
            )
        )
        db.delete(role)
        db.flush()
    with pytest.raises(PersonnelIntakeError) as denied:
        approvals(db).direct_create_pending(actor(platform_users, who), pending(), uuid4())
    assert denied.value.status == 403


@pytest.mark.parametrize("country", [None, "BR"])
def test_pending_passport_correction_enables_housing_without_new_person(
    db, platform_users, country
):
    from app.personnel_intake.readiness import AssistedReadinessPolicy
    from app.personnel_intake.schemas import PendingProfileCorrection

    data = pending(
        kind="staff",
        identity_number="Foreign-851",
        document_type="passport",
        sex=None,
        date_of_birth=None,
        phone="+55 11 99999 0000",
    )
    who = actor(platform_users, "admin")
    key = uuid4()
    result = approvals(db).direct_create_pending(who, data, key)
    assert approvals(db).direct_create_pending(who, data, key) == result
    policy = AssistedReadinessPolicy(db, cipher=approvals(db).cipher, lookup=approvals(db).lookup)
    before = policy.evaluate_many([result.person_id], reference_date=date(2026, 10, 21))[
        result.person_id
    ]
    assert not before.housing_ready and "sex" in before.housing_missing
    from app.housing.occupancy_service import HousingOccupancyService
    from tests.db.test_housing_stay_lifecycle import _allow, _room

    room = _room(db)
    candidates = HousingOccupancyService(db).list_resident_options(
        room.id,
        data.full_name,
        cipher=approvals(db).cipher,
        lookup=approvals(db).lookup,
        authorize_room=_allow,
        reference_date=date(2026, 10, 21),
    )
    assert len(candidates) == 1 and candidates[0].sex is None
    assert not candidates[0].housing_ready
    correction = PendingProfileCorrection(
        **data.model_dump(
            exclude={"kind", "coach_person_ids", "team_id", "team_allocation_status"}
        ),
        expected_version=1,
        reason="本人提供真实性别",
        idempotency_key=uuid4(),
    )
    correction.sex = "male"
    correction.document_issuing_country = country
    changed = approvals(db).correct_pending_profile(who, result.person_id, correction)
    assert changed.person_id == result.person_id
    assert db.get(Person, result.person_id).document_issuing_country == country
    assert changed.approval_id != result.approval_id
    assert approvals(db).correct_pending_profile(who, result.person_id, correction) == changed
    after = policy.evaluate_many([result.person_id], reference_date=date(2026, 10, 21))[
        result.person_id
    ]
    assert after.housing_ready and not after.activation_profile_ready
    from app.housing.resident_readiness import HousingResidentReadinessPolicy
    from tests.db.test_housing_stay_lifecycle import _input

    when = datetime.now(UTC)
    stay = HousingOccupancyService(
        db,
        resident_readiness=HousingResidentReadinessPolicy(
            db, cipher=approvals(db).cipher, lookup=approvals(db).lookup
        ),
    ).create_stay(
        room.id,
        _input(result.person_id, start=when),
        actor_user_id=who.account.id,
        occurred_at=when,
        authorize_room=_allow,
    )
    assert stay.person_id == result.person_id and stay.status == "checked_in"
    assert db.get(StaffProfile, result.person_id).staff_type == "other"
    assert db.scalar(select(UserAccount).where(UserAccount.person_id == result.person_id)) is None
    assert (
        db.scalar(
            select(func.count())
            .select_from(AssistedApproval)
            .where(AssistedApproval.person_id == result.person_id)
        )
        == 2
    )


def test_pending_input_does_not_relax_strict_intake():
    from pydantic import ValidationError

    from app.personnel_intake.schemas import (
        IntakeInput,
        ManagedPeopleFilters,
        PendingProfileCorrection,
    )

    with pytest.raises(ValidationError):
        IntakeInput.model_validate(pending().model_dump())
    with pytest.raises(ValidationError):
        ManagedPeopleFilters(kind="staff")
    with pytest.raises(ValidationError):
        PendingProfileCorrection.model_validate(
            {
                **pending().model_dump(),
                "expected_version": 1,
                "reason": "核对",
                "idempotency_key": uuid4(),
            }
        )


@pytest.mark.parametrize("raw", ["[]", '{"material_version":2}', "not-json"])
def test_intake_payload_malformed_authenticated_shape_is_controlled(raw):
    from app.personnel_intake.codec import decode_payload
    from app.personnel_intake.guardian_records import ApprovalEvidenceError

    with pytest.raises(ApprovalEvidenceError):
        decode_payload(raw)


def test_scoped_admin_cannot_create_unowned_pending_person(db, platform_users):
    from app.rbac.models import RolePermission, UserPermissionOverride
    from app.rbac.seed import PERMISSIONS, ROLES
    from tests.services.test_team_access import teams

    who = actor(platform_users, "super_admin")
    team, _ = teams(db, who.account.id)
    db.execute(
        RolePermission.__table__.delete().where(
            RolePermission.role_id == ROLES["admin"].id,
            RolePermission.permission_id == PERMISSIONS["personnel_intake.review"].id,
        )
    )
    db.add(
        UserPermissionOverride(
            user_id=who.account.id,
            permission_id=PERMISSIONS["personnel_intake.review"].id,
            effect="allow",
            scope_kind="assigned_team",
            scope_id=team.id,
        )
    )
    db.commit()
    with pytest.raises(PersonnelIntakeError) as denied:
        approvals(db).direct_create_pending(who, pending(), uuid4())
    assert denied.value.status == 403


@pytest.mark.parametrize("email", [None, "reserved-pending@example.com"])
def test_v2_pending_always_requires_reviewed_claim(db, platform_users, email):
    from app.personnel_intake.guardian_records import authenticated_approval_facts

    result = approvals(db).direct_create_pending(
        actor(platform_users, "admin"), pending(email=email), uuid4()
    )
    assert (
        db.get(AssistedPersonProfile, result.person_id).activation_requirement == "reviewed_claim"
    )
    own, guardians = authenticated_approval_facts(
        db.get(AssistedApproval, result.approval_id),
        cipher=approvals(db).cipher,
        lookup=approvals(db).lookup,
    )
    assert own["material_version"] == 2 and own["email"] == email and guardians == []


def test_pending_failed_approval_does_not_leave_person_or_v2_draft(db, platform_users, monkeypatch):
    from app.personnel_intake.models import PersonnelIntakeRequest, PersonnelIntakeRevision

    service = approvals(db)
    db.commit()
    before = [
        db.scalar(select(func.count()).select_from(model))
        for model in (Person, PersonnelIntakeRequest, PersonnelIntakeRevision, AssistedApproval)
    ]

    def fail(*args, **kwargs):
        raise RuntimeError("injected storage failure")

    monkeypatch.setattr(service, "_new_approval", fail)
    with pytest.raises(RuntimeError):
        service.direct_create_pending(actor(platform_users, "admin"), pending(), uuid4())
    after = [
        db.scalar(select(func.count()).select_from(model))
        for model in (Person, PersonnelIntakeRequest, PersonnelIntakeRevision, AssistedApproval)
    ]
    assert after == before


@pytest.mark.parametrize("kind", ["student", "staff"])
def test_pending_http_routes_are_private_and_return_independent_readiness(
    db, db_client, platform_users, kind
):
    from app.personnel_intake.dependencies import get_approval_service
    from tests.api.test_people import sign_in

    db_client.app.dependency_overrides[get_approval_service] = lambda: approvals(db)
    csrf = sign_in(db_client, platform_users, "admin")
    data = pending(kind=kind)
    body = data.model_dump(mode="json")
    body["identity_number"] = data.identity_number.get_secret_value()
    body["idempotency_key"] = str(uuid4())
    response = db_client.post(
        "/api/v1/personnel-intake/direct-create-pending", json=body, headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 201, response.text
    person_id = response.json()["person_id"]
    approval = db.get(AssistedApproval, response.json()["approval_id"])
    detail = db_client.get(f"/api/v1/personnel-intake/requests/{approval.request_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["scoped_payload"]["material_version"] == 2
    assert detail.json()["kind"] == kind and detail.json()["allowed_actions"] == []
    listed = db_client.get("/api/v1/personnel-intake/requests", params={"view": "mine"})
    assert listed.status_code == 200, listed.text
    assert any(
        item["id"] == str(approval.request_id) and item["kind"] == kind
        for item in listed.json()["items"]
    )
    ready = db_client.get(f"/api/v1/personnel-intake/people/{person_id}/readiness")
    assert ready.status_code == 200, ready.text
    assert ready.json()["housing_ready"] is True
    assert ready.json()["activation_profile_ready"] is False
    assert ready.headers["Cache-Control"] == "no-store, private"
    editor = db_client.get(f"/api/v1/personnel-intake/people/{person_id}/editor-context")
    assert editor.json()["intake_mode"] == "pending"
    assert "correct_pending_profile" in editor.json()["allowed_actions"]
    assert "correct_profile" not in editor.json()["allowed_actions"]
    assert editor.json()["can_edit_activation_email"] is True


@pytest.mark.parametrize("route", ["create", "correction"])
@pytest.mark.parametrize("denial", ["anonymous", "nonadmin", "csrf"])
def test_pending_http_write_boundaries_reject_without_materializing(
    db, db_client, platform_users, route, denial
):
    from app.personnel_intake.dependencies import get_approval_service
    from app.rbac.seed import ROLES
    from tests.api.test_people import sign_in

    db_client.app.dependency_overrides[get_approval_service] = lambda: approvals(db)
    data = pending()
    root = "/api/v1/personnel-intake"
    body = data.model_dump(mode="json")
    if route == "correction":
        result = approvals(db).direct_create_pending(actor(platform_users, "admin"), data, uuid4())
        url = f"{root}/people/{result.person_id}/pending-corrections"
        for key in ("kind", "coach_person_ids", "team_id", "team_allocation_status"):
            body.pop(key)
        body.update(expected_version=1, reason="核对待补事实")
    else:
        url = root + "/direct-create-pending"
    body.update(
        identity_number=data.identity_number.get_secret_value(), idempotency_key=str(uuid4())
    )
    headers = {}
    if denial != "anonymous":
        who = "coach" if denial == "nonadmin" else "admin"
        if who == "coach":
            db.execute(
                UserRole.__table__.delete().where(
                    UserRole.user_id == platform_users["accounts"][who].id,
                    UserRole.role_id == ROLES["admin"].id,
                )
            )
            db.commit()
        csrf = sign_in(db_client, platform_users, who)
        if denial != "csrf":
            headers["X-CSRF-Token"] = csrf
    response = db_client.post(url, json=body, headers=headers)
    assert response.status_code == (401 if denial == "anonymous" else 403), response.text
    assert response.headers["Cache-Control"] == "no-store, private"
    assert data.identity_number.get_secret_value() not in response.text
    people = list(db.scalars(select(Person).where(Person.full_name == data.full_name)))
    assert len(people) == (1 if route == "correction" else 0)
    if route == "correction":
        assert (
            db.scalar(
                select(func.count())
                .select_from(AssistedApproval)
                .where(AssistedApproval.person_id == result.person_id)
            )
            == 1
        )


def test_pending_team_assignment_is_real_and_tracked_after_join(db, platform_users):
    from app.team_management.models import TeamMembership
    from tests.services.test_team_access import teams

    team, _ = teams(db, platform_users["accounts"]["admin"].id)
    result = approvals(db).direct_create_pending(
        actor(platform_users, "admin"), pending(kind="player", team_id=team.id), uuid4()
    )
    person = db.get(Person, result.person_id)
    assert person.team_allocation_status == "assigned"
    assert person.team_allocation_version == 2
    assert (
        db.scalar(select(TeamMembership.team_id).where(TeamMembership.person_id == person.id))
        == team.id
    )


def test_pending_not_required_cannot_smuggle_real_team(db, platform_users):
    from tests.services.test_team_access import teams

    team, _ = teams(db, platform_users["accounts"]["admin"].id)
    with pytest.raises(PersonnelIntakeError):
        approvals(db).direct_create_pending(
            actor(platform_users, "admin"),
            pending(kind="player", team_id=team.id, team_allocation_status="not_required"),
            uuid4(),
        )


def test_pending_readiness_batch_does_not_grow_per_person(db, platform_users):
    from sqlalchemy import event

    from app.personnel_intake.readiness import AssistedReadinessPolicy

    ids = [
        approvals(db)
        .direct_create_pending(
            actor(platform_users, "admin"),
            pending(identity_number=make_cn_id("20150102", 861 + i * 2)),
            uuid4(),
        )
        .person_id
        for i in range(3)
    ]
    statements = []

    def record(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(db.bind, "before_cursor_execute", record)
    try:
        result = AssistedReadinessPolicy(
            db, cipher=approvals(db).cipher, lookup=approvals(db).lookup
        ).evaluate_many(ids, reference_date=date(2026, 10, 21))
    finally:
        event.remove(db.bind, "before_cursor_execute", record)
    assert all(v.housing_ready and not v.activation_profile_ready for v in result.values())
    assert len(statements) <= 8


def test_pending_completion_not_allowed_after_any_account_exists(db, platform_users):
    from app.personnel_intake.readiness import AssistedReadinessPolicy

    result = approvals(db).direct_create_pending(
        actor(platform_users, "admin"),
        pending(
            identity_number=make_cn_id("20000102", 871),
            date_of_birth=date(2000, 1, 2),
            phone="13900000971",
        ),
        uuid4(),
    )
    db.add(
        UserAccount(
            person_id=result.person_id,
            password_hash="test-only",
            status="disabled",
            force_password_change=False,
            email_normalized="existing-pending@example.com",
        )
    )
    db.flush()
    ready = AssistedReadinessPolicy(
        db, cipher=approvals(db).cipher, lookup=approvals(db).lookup
    ).evaluate_many([result.person_id], reference_date=date(2026, 10, 21))[result.person_id]
    assert not ready.completion_allowed


def test_pending_adult_can_be_real_guardian_without_granting_family_account(
    db, platform_users, monkeypatch
):
    from app.core.time import business_date_at
    from app.personnel_intake import approval_service
    from app.personnel_intake.readiness import AssistedReadinessPolicy

    frozen_now = datetime(2026, 10, 22, 4, tzinfo=UTC)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen_now.astimezone(tz) if tz is not None else frozen_now.replace(tzinfo=None)

    monkeypatch.setattr(approval_service, "datetime", FrozenDateTime)
    reference_date = business_date_at(frozen_now)
    assert reference_date == date(2026, 10, 22)

    adult = pending(
        full_name="已建档监护人",
        identity_number=make_cn_id("19800102", 875),
        date_of_birth=date(1980, 1, 2),
        phone="13900000975",
        kind="staff",
    )
    guardian = approvals(db).direct_create_pending(actor(platform_users, "admin"), adult, uuid4())
    child = pending(
        guardians=[
            {
                "full_name": adult.full_name,
                "identity_number": adult.identity_number,
                "sex": adult.sex,
                "date_of_birth": adult.date_of_birth,
                "phone": adult.phone,
                "relationship": "父亲",
                "is_primary": True,
            }
        ]
    )
    result = approvals(db).direct_create_pending(actor(platform_users, "admin"), child, uuid4())
    policy = AssistedReadinessPolicy(db, cipher=approvals(db).cipher, lookup=approvals(db).lookup)
    before = policy.evaluate_many(
        [result.person_id], reference_date=reference_date - timedelta(days=1)
    )[result.person_id]
    assert before.housing_ready
    assert "guardian" in before.profile_missing
    ready = policy.evaluate_many([result.person_id], reference_date=reference_date)[
        result.person_id
    ]
    assert ready.housing_ready
    assert "guardian" not in ready.profile_missing
    edge = db.scalar(
        select(GuardianRelationship).where(
            GuardianRelationship.student_person_id == result.person_id
        )
    )
    assert edge.valid_from == reference_date
    assert edge.guardian_person_id == guardian.person_id and edge.requires_verification
    assert db.scalar(select(UserAccount).where(UserAccount.person_id == guardian.person_id)) is None


def test_pending_nonobject_snapshot_is_controlled_evidence_failure(db, platform_users):
    from app.personnel_intake.guardian_records import (
        PERSON_SNAPSHOT_AAD,
        PERSON_SNAPSHOT_DOMAIN,
        ApprovalEvidenceError,
        authenticated_approval_facts,
    )

    service = approvals(db)
    result = service.direct_create_pending(actor(platform_users, "admin"), pending(), uuid4())
    original = db.get(AssistedApproval, result.approval_id)
    evidence = AssistedApproval(
        **{col.key: getattr(original, col.key) for col in AssistedApproval.__table__.columns}
    )
    raw = "[]"
    evidence.snapshot_ciphertext = service.cipher.encrypt(raw, field_name=PERSON_SNAPSHOT_AAD)
    evidence.snapshot_digest = service.lookup.digest_domain(PERSON_SNAPSHOT_DOMAIN, raw)
    with pytest.raises(ApprovalEvidenceError):
        authenticated_approval_facts(evidence, cipher=service.cipher, lookup=service.lookup)
