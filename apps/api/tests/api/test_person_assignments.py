"""Profile assignment writes must not become account/intake/enrollment writes."""

from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.admissions.models import AdmissionCase, Enrollment, Payment
from app.audit.models import AuditEvent
from app.auth.models import UserAccount
from app.identity.models import StaffProfile, StudentProfile, VisitorProfile
from app.personnel_intake.models import (
    AssistedApproval,
    AssistedPersonProfile,
    CoachPersonResponsibility,
)
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS
from app.teaching.models import GameProject, ProjectCoachAssignment
from tests.api.conftest import make_cn_id
from tests.api.test_pending_personnel import pending
from tests.api.test_people import sign_in
from tests.services.test_personnel_approval import approvals
from tests.services.test_personnel_intake import actor


def setup_editor(db, client, users, *, assisted=False):
    if assisted:
        target = (
            approvals(db)
            .direct_create_pending(
                actor(users, "admin"),
                pending(
                    kind="staff",
                    identity_number=make_cn_id("20000102", 851),
                    date_of_birth="2000-01-02",
                    phone="13900000001",
                ),
                uuid4(),
            )
            .person_id
        )
    else:
        target = users["people"]["self_student"].id
    project = GameProject(
        name="档案项目", sort_order=99, created_by_user_id=users["accounts"]["admin"].id
    )
    db.add(project)
    db.flush()
    db.add(
        ProjectCoachAssignment(
            project_id=project.id,
            coach_person_id=users["people"]["coach"].id,
            created_by_user_id=users["accounts"]["admin"].id,
        )
    )
    db.commit()
    csrf = sign_in(client, users, "admin")
    return f"/api/v1/people/{target}/assignments", {"X-CSRF-Token": csrf}, target, project


def payload(view, **changes):
    return {
        "expected_version": view["version"],
        "idempotency_key": str(uuid4()),
        "reason": "调整培养归属",
        "business_kind": view["business_kind"],
        "student_project_ids": view["student_project_ids"],
        "coach_person_ids": view["coach_person_ids"],
        **changes,
    }


def test_assignments_authentication_errors_are_private(client):
    response = client.get(f"/api/v1/people/{uuid4()}/assignments")
    assert response.status_code == 401
    assert response.headers.get("Cache-Control") == "no-store, private"


@pytest.mark.parametrize("kind", ["student", "player", "both"])
def test_approved_staff_identity_is_additive_without_side_effects(
    db, db_client, platform_users, kind
):
    url, headers, target, project = setup_editor(db, db_client, platform_users, assisted=True)
    before = db_client.get(url)
    assert before.status_code == 200
    assert before.json()["business_kind"] is None
    assert before.json()["source_intake_kind"] == "staff"
    approval = db.get(AssistedApproval, db.get(AssistedPersonProfile, target).current_approval_id)
    signature = approval.snapshot_digest
    counts = [
        db.scalar(select(func.count()).select_from(model))
        for model in (UserAccount, UserRole, AdmissionCase, Payment, Enrollment)
    ]
    body = payload(
        before.json(),
        business_kind=kind,
        student_project_ids=[str(project.id)] if kind != "player" else [],
        coach_person_ids=[str(platform_users["people"]["coach"].id)] if kind != "player" else [],
    )
    saved = db_client.put(url, headers=headers, json=body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["business_kind"] == kind
    assert saved.json()["version"] != before.json()["version"]
    assert saved.json()["source_intake_kind"] == "staff"
    assert saved.json()["account_id"] is None
    assert db.get(StaffProfile, target).staff_type == "other"
    assert (db.get(StudentProfile, target) is not None) is (kind != "player")
    if kind != "player":
        assert db.get(StudentProfile, target).student_number.startswith("REG-")
    assert db.get(AssistedApproval, approval.id).snapshot_digest == signature
    assert [
        db.scalar(select(func.count()).select_from(model))
        for model in (UserAccount, UserRole, AdmissionCase, Payment, Enrollment)
    ] == counts
    from app.core.time import business_today
    from app.personnel_intake.approved_facts import load_approved_facts

    facts = load_approved_facts(
        db,
        [target],
        reference_date=business_today(),
        cipher=approvals(db).cipher,
        lookup=approvals(db).lookup,
    )[target]
    assert facts.approval_valid and facts.profile_matches and facts.guardian_ready


def test_registered_student_idempotency_stale_removal_and_history(db, db_client, platform_users):
    url, headers, target, project = setup_editor(db, db_client, platform_users)
    before = db_client.get(url)
    assert before.status_code == 200
    body = payload(
        before.json(),
        student_project_ids=[str(project.id)],
        coach_person_ids=[str(platform_users["people"]["coach"].id)],
    )
    saved = db_client.put(url, headers=headers, json=body)
    assert saved.status_code == 200, saved.text
    replay = db_client.put(url, headers=headers, json=body)
    assert replay.json() == saved.json()
    assert (
        db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == "person.assignments.update")
        )
        == 1
    )
    collision = db_client.put(url, headers=headers, json={**body, "reason": "另一个操作"})
    assert collision.status_code == 409 and collision.json()["code"] == "idempotency_conflict"
    stale = db_client.put(url, headers=headers, json={**body, "idempotency_key": str(uuid4())})
    assert stale.status_code == 409 and stale.json()["code"] == "assignments_stale"
    removal = db_client.put(url, headers=headers, json=payload(saved.json(), business_kind=None))
    assert removal.status_code == 409 and removal.json()["code"] == "identity_removal_not_supported"
    cleared = db_client.put(
        url,
        headers=headers,
        json=payload(saved.json(), student_project_ids=[], coach_person_ids=[]),
    )
    assert cleared.status_code == 200
    from app.identity.assignments.models import PersonProjectAssignment

    history = db.scalar(
        select(PersonProjectAssignment).where(PersonProjectAssignment.person_id == target)
    )
    assert history.ended_at is not None


@pytest.mark.parametrize(
    "invalid",
    ["unknown_project", "inactive_project", "wrong_coach", "inactive_coach", "without_project"],
)
def test_assignments_reject_invalid_project_coach_sets(db, db_client, platform_users, invalid):
    url, headers, _, project = setup_editor(db, db_client, platform_users)
    before = db_client.get(url)
    assert before.status_code == 200
    projects, coaches = [str(project.id)], [str(platform_users["people"]["coach"].id)]
    if invalid == "unknown_project":
        projects = [str(uuid4())]
    elif invalid == "inactive_project":
        project.is_active = False
    elif invalid == "wrong_coach":
        coaches = [str(platform_users["people"]["admin"].id)]
    elif invalid == "inactive_coach":
        platform_users["accounts"]["coach"].status = "disabled"
    else:
        projects = []
    db.commit()
    response = db_client.put(
        url,
        headers=headers,
        json=payload(before.json(), student_project_ids=projects, coach_person_ids=coaches),
    )
    assert response.status_code == 422


def test_legacy_coach_writer_invalidates_version_and_inactive_choices_stay_visible(
    db, db_client, platform_users
):
    url, headers, target, _project = setup_editor(db, db_client, platform_users)
    before = db_client.get(url)
    assert before.status_code == 200
    db.add(
        CoachPersonResponsibility(
            person_id=target,
            coach_person_id=platform_users["people"]["coach"].id,
            created_by_user_id=platform_users["accounts"]["admin"].id,
        )
    )
    db.commit()
    stale = db_client.put(url, headers=headers, json=payload(before.json()))
    assert stale.status_code == 409
    platform_users["accounts"]["coach"].status = "disabled"
    db.commit()
    view = db_client.get(url).json()
    assert view["coach_person_ids"] == [str(platform_users["people"]["coach"].id)]
    assert view["current_coaches"][0]["full_name"] == "平台教练"
    assert view["eligible_coaches"] == []


def test_permission_loss_rechecks_replay_and_read_scopes(db, db_client, platform_users):
    url, headers, target, _ = setup_editor(db, db_client, platform_users)
    before = db_client.get(url)
    assert before.status_code == 200
    body = payload(before.json())
    assert db_client.put(url, headers=headers, json=body).status_code == 200
    db.add(
        UserPermissionOverride(
            user_id=platform_users["accounts"]["admin"].id,
            permission_id=PERMISSIONS["people.update"].id,
            effect="deny",
            scope_kind="person",
            scope_id=target,
        )
    )
    db.commit()
    assert db_client.put(url, headers=headers, json=body).status_code == 403
    view = db_client.get(url).json()
    assert not view["capabilities"]["can_edit"] and not view["eligible_projects"]
    db.add(
        UserPermissionOverride(
            user_id=platform_users["accounts"]["admin"].id,
            permission_id=PERMISSIONS["people.read"].id,
            effect="deny",
            scope_kind="person",
            scope_id=target,
        )
    )
    db.commit()
    assert db_client.get(url).status_code == 403


def test_nonadmin_override_never_grants_assignment_write(db, db_client, platform_users):
    url, _, target, _ = setup_editor(db, db_client, platform_users)
    for key in ("people.read", "people.update"):
        db.add(
            UserPermissionOverride(
                user_id=platform_users["accounts"]["coach"].id,
                permission_id=PERMISSIONS[key].id,
                effect="allow",
                scope_kind="person",
                scope_id=target,
            )
        )
    db.commit()
    csrf = sign_in(db_client, platform_users, "coach")
    view = db_client.get(url)
    assert view.status_code == 200
    assert view.json()["account_id"] is None
    assert not view.json()["capabilities"]["can_edit"]
    assert (
        db_client.put(url, headers={"X-CSRF-Token": csrf}, json=payload(view.json())).status_code
        == 403
    )


def test_new_player_requires_live_trusted_approval_for_team_candidates(
    db, db_client, platform_users
):
    from app.identity.models import Person
    from tests.services.test_team_access import teams

    url, headers, target, _ = setup_editor(db, db_client, platform_users, assisted=True)
    view = db_client.get(url).json()
    assert (
        db_client.put(url, headers=headers, json=payload(view, business_kind="player")).status_code
        == 200
    )
    team, _ = teams(db, platform_users["accounts"]["admin"].id)
    db.commit()
    candidates_url = f"/api/v1/team-management/teams/{team.id}/candidates"
    candidates = db_client.get(candidates_url, params={"query": "待补"})
    assert candidates.status_code == 200
    assert [row["person_id"] for row in candidates.json()["items"]] == [str(target)]
    person = db.get(Person, target)
    person.full_name = "待补篡改资料"
    db.commit()
    assert db_client.get(candidates_url, params={"query": "待补"}).json()["items"] == []
    joined = db_client.post(
        f"/api/v1/team-management/teams/{team.id}/memberships",
        headers=headers,
        json={"person_id": str(target), "idempotency_key": str(uuid4())},
    )
    assert joined.status_code == 409
    person.full_name = "待补未成年"
    db.commit()
    joined = db_client.post(
        f"/api/v1/team-management/teams/{team.id}/memberships",
        headers=headers,
        json={"person_id": str(target), "idempotency_key": str(uuid4())},
    )
    assert joined.status_code == 200, joined.text


def test_existing_account_player_marker_never_substitutes_team_member_role(
    db, db_client, platform_users
):
    from tests.services.test_team_access import teams

    url, headers, target, _ = setup_editor(db, db_client, platform_users)
    assert (
        db_client.put(
            url, headers=headers, json=payload(db_client.get(url).json(), business_kind="both")
        ).status_code
        == 200
    )
    team, _ = teams(db, platform_users["accounts"]["admin"].id)
    db.commit()
    response = db_client.post(
        f"/api/v1/team-management/teams/{team.id}/memberships",
        headers=headers,
        json={"person_id": str(target), "idempotency_key": str(uuid4())},
    )
    assert response.status_code == 409


def test_old_coach_entry_cannot_bypass_managed_assignment_projects(db, db_client, platform_users):
    from app.personnel_intake.errors import PersonnelIntakeError
    from app.personnel_intake.responsibilities import CoachResponsibilityService

    url, headers, target, _ = setup_editor(db, db_client, platform_users, assisted=True)
    assert (
        db_client.put(
            url, headers=headers, json=payload(db_client.get(url).json(), business_kind="student")
        ).status_code
        == 200
    )
    profile = db.get(AssistedPersonProfile, target)
    with pytest.raises(PersonnelIntakeError) as denied:
        CoachResponsibilityService(db).replace(
            actor(platform_users, "admin"),
            target,
            [platform_users["people"]["coach"].id],
            profile.version,
            "调整旧负责人",
        )
    assert denied.value.status == 409


@pytest.mark.parametrize("legacy_kind", ["staff", "visitor"])
def test_existing_adult_staff_student_conversion_preserves_housing_and_account(
    db, db_client, platform_users, legacy_kind
):
    from datetime import UTC, datetime

    from app.auth.account_readiness import AccountAccessReadinessPolicy
    from app.auth.models import AccountLoginIdentifier
    from app.core.time import business_today
    from app.housing.resident_readiness import HousingResidentReadinessPolicy

    _, headers, _, _ = setup_editor(db, db_client, platform_users)
    target = platform_users["people"]["customer_service"].id
    account = platform_users["accounts"]["customer_service"]
    if legacy_kind == "visitor":
        db.delete(db.get(StaffProfile, target))
        db.add(VisitorProfile(person_id=target, visit_reason="虚拟访客住宿"))
    url = f"/api/v1/people/{target}/assignments"
    identity = platform_users["identity_service"]
    db.add(
        AccountLoginIdentifier(
            account_id=account.id,
            kind="email",
            identifier_digest=identity.lookup.digest("cs@example.com"),
            verified_at=datetime.now(UTC),
            verification_source="test",
            is_primary=True,
        )
    )
    db.commit()
    housing = HousingResidentReadinessPolicy(db, cipher=identity.cipher, lookup=identity.lookup)
    readiness = AccountAccessReadinessPolicy(db, cipher=identity.cipher, lookup=identity.lookup)
    before_housing = housing.evaluate_many([target], reference_date=business_today())[target]
    before_account = readiness.evaluate_one(account, reference_date=business_today())
    before_status = (account.status, account.force_password_change, account.account_access_origin)
    before_roles = set(db.scalars(select(UserRole.role_id).where(UserRole.user_id == account.id)))
    assert before_housing.housing_ready
    response = db_client.put(
        url, headers=headers, json=payload(db_client.get(url).json(), business_kind="student")
    )
    assert response.status_code == 200, response.text
    assert db.get(StudentProfile, target).status == "active"
    assert (
        housing.evaluate_many([target], reference_date=business_today())[target] == before_housing
    )
    assert readiness.evaluate_one(account, reference_date=business_today()) == before_account
    assert (
        account.status,
        account.force_password_change,
        account.account_access_origin,
    ) == before_status
    assert (
        set(db.scalars(select(UserRole.role_id).where(UserRole.user_id == account.id)))
        == before_roles
    )


def test_legacy_nonstudent_coaches_are_preserved_when_only_adding_player(
    db, db_client, platform_users
):
    url, headers, target, _ = setup_editor(db, db_client, platform_users, assisted=True)
    db.add(
        CoachPersonResponsibility(
            person_id=target,
            coach_person_id=platform_users["people"]["coach"].id,
            created_by_user_id=platform_users["accounts"]["admin"].id,
        )
    )

    platform_users["accounts"]["coach"].status = "disabled"
    db.commit()
    before = db_client.get(url).json()
    response = db_client.put(url, headers=headers, json=payload(before, business_kind="player"))
    assert response.status_code == 200
    assert response.json()["coach_person_ids"] == before["coach_person_ids"]
    assert (
        db.scalar(
            select(func.count())
            .select_from(CoachPersonResponsibility)
            .where(
                CoachPersonResponsibility.person_id == target,
                CoachPersonResponsibility.ended_at.is_(None),
            )
        )
        == 1
    )


def test_assignment_write_rechecks_actual_role_after_participant_locks(
    db, db_client, platform_users, monkeypatch
):
    import app.identity.assignments.service as module
    from app.core.errors import ProblemError
    from app.identity.assignments.schemas import AssignmentInput

    url, _, target, _ = setup_editor(db, db_client, platform_users)
    view = db_client.get(url).json()
    original = module.lock_participants

    def revoked_after_lock(session, people, accounts):
        original(session, people, accounts)
        session.execute(
            UserRole.__table__.delete().where(
                UserRole.user_id == platform_users["accounts"]["admin"].id
            )
        )

    monkeypatch.setattr(module, "lock_participants", revoked_after_lock)
    identity = platform_users["identity_service"]
    with pytest.raises(ProblemError) as denied:
        module.PersonAssignmentService(db, cipher=identity.cipher, lookup=identity.lookup).replace(
            actor(platform_users, "admin"), target, AssignmentInput.model_validate(payload(view))
        )
    assert denied.value.status == 403
    assert (
        db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == "person.assignments.update")
        )
        == 0
    )


def test_assignment_replay_redacts_account_after_permissions_loss(db, db_client, platform_users):
    url, headers, target, _ = setup_editor(db, db_client, platform_users)
    body = payload(db_client.get(url).json())
    saved = db_client.put(url, headers=headers, json=body)
    assert saved.status_code == 200 and saved.json()["account_id"] is not None
    db.add(
        UserPermissionOverride(
            user_id=platform_users["accounts"]["admin"].id,
            permission_id=PERMISSIONS["permissions.manage"].id,
            effect="deny",
            scope_kind="person",
            scope_id=target,
        )
    )
    db.commit()
    replay = db_client.put(url, headers=headers, json=body)
    assert replay.status_code == 200
    assert replay.json()["account_id"] is None
    assert not replay.json()["capabilities"]["can_manage_account_roles"]


@pytest.mark.parametrize("legacy_kind", ["staff", "visitor"])
def test_legacy_staff_incomplete_student_conversion_is_explicitly_blocked(
    db, db_client, platform_users, legacy_kind
):
    from app.identity.models import Person

    _, headers, _, _ = setup_editor(db, db_client, platform_users)
    target = platform_users["people"]["customer_service"].id
    if legacy_kind == "visitor":
        db.delete(db.get(StaffProfile, target))
        db.add(VisitorProfile(person_id=target, visit_reason="虚拟访客住宿"))
    db.get(Person, target).email_ciphertext = None
    db.commit()
    url = f"/api/v1/people/{target}/assignments"
    response = db_client.put(
        url, headers=headers, json=payload(db_client.get(url).json(), business_kind="student")
    )
    assert response.status_code == 409
    assert response.json()["code"] == "student_identity_requires_completion"
    assert db.get(StudentProfile, target) is None


def test_assignments_rejects_extra_fields_and_sensitive_reason_without_audit(
    db, db_client, platform_users
):
    url, headers, _, _ = setup_editor(db, db_client, platform_users)
    body = payload(db_client.get(url).json())
    assert (
        db_client.put(url, headers=headers, json={**body, "team_id": str(uuid4())}).status_code
        == 422
    )
    assert (
        db_client.put(url, headers=headers, json={**body, "reason": "联系19990000001"}).status_code
        == 422
    )
    assert (
        db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == "person.assignments.update")
        )
        == 0
    )


@pytest.mark.parametrize("age_case", ["minor", "unknown_date_of_birth"])
@pytest.mark.parametrize("legacy_kind", ["staff", "visitor"])
def test_legacy_staff_first_student_conversion_rejects_unready_age(
    db, db_client, platform_users, age_case, legacy_kind
):
    from datetime import date

    from app.core.time import business_today
    from app.identity.assignments.models import PersonAssignmentOperation, PersonAssignmentState
    from app.identity.document_identity import document_identity_keys, normalize_document_identity
    from app.identity.models import Person

    # Retain a real legacy staff account and complete contact fields. A typed
    # pending passport permits NULL birth date without violating DB constraints;
    # the known minor remains a strict, otherwise complete passport profile.
    _, headers, _, _ = setup_editor(db, db_client, platform_users)
    target = platform_users["people"]["customer_service"].id
    if legacy_kind == "visitor":
        db.delete(db.get(StaffProfile, target))
        db.add(VisitorProfile(person_id=target, visit_reason="虚拟访客住宿"))
    identity = platform_users["identity_service"]
    birth_date = date(business_today().year - 10, 1, 2) if age_case == "minor" else None
    document = normalize_document_identity(
        "passport",
        "LEGACY-AGE-851",
        issuing_country="BR",
        sex="female",
        date_of_birth=birth_date,
        allow_pending=age_case == "unknown_date_of_birth",
    )
    keys = document_identity_keys(document, identity.lookup)
    person = db.get(Person, target)
    person.document_type = document.document_type
    person.document_issuing_country = keys.issuing_country
    person.identity_ciphertext = identity.cipher.encrypt(
        document.number, field_name="identity_number"
    )
    person.identity_digest = keys.identity_digest
    person.passport_number_digest = keys.passport_number_digest
    person.intake_mode = "strict" if age_case == "minor" else "pending"
    person.date_of_birth = birth_date
    db.commit()
    assert person.email_ciphertext and person.email_digest and person.phone_ciphertext
    assert db.get(AssistedPersonProfile, target) is None
    assert db.get(StudentProfile, target) is None
    account_id = platform_users["accounts"]["customer_service"].id
    account = db.get(UserAccount, account_id)
    assert account.account_access_origin == "legacy"
    before_account = (account.status, account.force_password_change, account.account_access_origin)
    before_roles = set(db.scalars(select(UserRole.role_id).where(UserRole.user_id == account_id)))
    url = f"/api/v1/people/{target}/assignments"
    before = db_client.get(url)
    assert before.status_code == 200
    assert before.json()["business_kind"] is None
    assert before.json()["source_intake_kind"] is None

    response = db_client.put(
        url, headers=headers, json=payload(before.json(), business_kind="student")
    )

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "student_identity_requires_completion"
    db.expire_all()
    assert db.get(StudentProfile, target) is None
    assert db.get(PersonAssignmentState, target) is None
    if legacy_kind == "staff":
        assert db.get(StaffProfile, target).staff_type == "customer_service"
    else:
        assert db.get(VisitorProfile, target).visit_reason == "虚拟访客住宿"
    assert (
        db.scalar(
            select(PersonAssignmentOperation.id).where(
                PersonAssignmentOperation.person_id == target
            )
        )
        is None
    )
    assert (
        db.scalar(
            select(AuditEvent.id).where(
                AuditEvent.resource_id == target, AuditEvent.action == "person.assignments.update"
            )
        )
        is None
    )
    account = db.get(UserAccount, account_id)
    assert (
        account.status, account.force_password_change, account.account_access_origin
    ) == before_account
    assert (
        set(db.scalars(select(UserRole.role_id).where(UserRole.user_id == account_id)))
        == before_roles
    )
    after = db_client.get(url)
    assert after.status_code == 200
    assert after.json()["version"] == before.json()["version"]
    assert after.json()["business_kind"] is None
