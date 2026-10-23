from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.auth.service import AuthContext
from app.personnel_intake.models import (
    PersonnelCredentialReservation,
    PersonnelIntakeRequest,
    PersonnelIntakeRevision,
)
from app.personnel_intake.schemas import IntakeInput
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES
from app.security.encryption import EnvelopeCipher, LookupDigest
from tests.api.conftest import make_cn_id
from tests.api.conftest import platform_users as _platform_users


@pytest.fixture
def platform_users(db):
    return _platform_users.__wrapped__(db)


def actor(users, key="coach"):
    return AuthContext(account=users["accounts"][key], person=users["people"][key], session=None)


def intake(users, **changes):
    values = {
        "kind": "student",
        "full_name": "隔离建档人员",
        "identity_number": make_cn_id("20000102", 321),
        "sex": "male",
        "date_of_birth": date(2000, 1, 2),
        "phone": "13900000001",
        "email": "intake@example.com",
        "coach_person_ids": [users["people"]["coach"].id],
    }
    values.update(changes)
    return IntakeInput(**values)


def service(db):
    from app.personnel_intake.service import PersonnelIntakeService

    return PersonnelIntakeService(
        db, cipher=EnvelopeCipher(bytes(range(32))), lookup=LookupDigest(bytes(range(32)))
    )


def test_versioned_submission_and_private_conflicts(db, platform_users):
    from app.personnel_intake.errors import IntakeError

    s = service(db)
    who = actor(platform_users)
    pending = s.submit(who, intake(platform_users))
    assert (pending.version, pending.status, pending.allowed_actions) == (
        1,
        "pending",
        ["withdraw"],
    )
    assert pending.scoped_payload.email == "intake@example.com"
    revision = db.scalar(
        select(PersonnelIntakeRevision).where(PersonnelIntakeRevision.request_id == pending.id)
    )
    original = revision.payload_ciphertext
    assert "intake@example.com" not in original and "隔离建档人员" not in original
    with pytest.raises(IntakeError) as conflict:
        s.resubmit(who, pending.id, 1, intake(platform_users))
    assert conflict.value.status == 409
    row = db.get(PersonnelIntakeRequest, pending.id)
    row.state, row.review_reason = "returned", "资料需要补充"
    db.flush()
    resubmitted = s.resubmit(who, pending.id, 1, intake(platform_users, full_name="补充资料人员"))
    assert (resubmitted.version, resubmitted.status) == (2, "pending")
    assert db.get(PersonnelIntakeRevision, revision.id).payload_ciphertext == original
    with pytest.raises(IntakeError):
        s.withdraw(who, pending.id, 1)
    withdrawn = s.withdraw(who, pending.id, 2)
    assert withdrawn.status == "withdrawn"
    assert (
        db.scalar(
            select(func.count())
            .select_from(PersonnelCredentialReservation)
            .where(
                PersonnelCredentialReservation.request_id == pending.id,
                PersonnelCredentialReservation.ended_at.is_(None),
            )
        )
        == 0
    )
    assert (
        db.scalar(
            select(func.count())
            .select_from(PersonnelIntakeRevision)
            .where(PersonnelIntakeRevision.request_id == pending.id)
        )
        == 2
    )


@pytest.mark.parametrize(
    "change",
    [
        {"sex": "female"},
        {"phone": "bad"},
        {"email": "bad"},
        {"coach_person_ids": []},
        {"identity_number": make_cn_id("20150102", 321), "date_of_birth": date(2015, 1, 2)},
    ],
)
def test_complete_input_rejects_invalid_or_unscoped_fields(db, platform_users, change):
    from app.personnel_intake.errors import IntakeError

    with pytest.raises(IntakeError):
        service(db).submit(actor(platform_users), intake(platform_users, **change))
    assert db.scalar(select(func.count()).select_from(PersonnelIntakeRequest)) == 0


def test_scope_revocation_hides_detail_and_count_even_from_creator(db, platform_users):
    from app.personnel_intake.errors import IntakeError

    s = service(db)
    who = actor(platform_users)
    pending = s.submit(who, intake(platform_users))
    db.execute(UserRole.__table__.delete().where(UserRole.user_id == who.account.id))
    db.flush()
    assert s.list_requests(who).total == 0
    with pytest.raises(IntakeError) as hidden:
        s.detail(who, pending.id)
    with pytest.raises(IntakeError) as absent:
        s.detail(who, uuid4())
    assert (hidden.value.status, hidden.value.detail) == (absent.value.status, absent.value.detail)


def test_same_phone_never_merges_and_conflict_is_private(db, platform_users):
    from app.personnel_intake.errors import IntakeError

    s = service(db)
    who = actor(platform_users)
    first = s.submit(who, intake(platform_users))
    second = s.submit(
        who,
        intake(
            platform_users,
            identity_number=make_cn_id("20000102", 323),
            email="other-intake@example.com",
        ),
    )
    assert first.id != second.id
    assert first.approved_person_id is None and second.approved_person_id is None
    with pytest.raises(IntakeError) as error:
        s.submit(who, intake(platform_users, email="third@example.com"))
    assert error.value.detail == "资料存在冲突，请联系管理员核实"


def test_explicit_deny_and_fake_review_role_never_grant_access(db, platform_users):
    from app.personnel_intake.access import PersonnelIntakeAccess

    s = service(db)
    who = actor(platform_users)
    db.add(
        UserPermissionOverride(
            user_id=who.account.id,
            permission_id=PERMISSIONS["personnel_intake.review"].id,
            effect="allow",
            scope_kind="global",
        )
    )
    db.flush()
    assert PersonnelIntakeAccess(db).context(who).can_review is False
    s.submit(who, intake(platform_users))
    db.add(
        UserPermissionOverride(
            user_id=who.account.id,
            permission_id=PERMISSIONS["personnel_intake.submit"].id,
            effect="deny",
            scope_kind="global",
        )
    )
    db.flush()
    assert s.list_requests(who).total == 0


def test_revoked_scope_never_decrypts_for_detail_or_counts(db, platform_users, monkeypatch):
    from app.personnel_intake.errors import IntakeError

    s = service(db)
    who = actor(platform_users)
    pending = s.submit(who, intake(platform_users))
    db.execute(UserRole.__table__.delete().where(UserRole.user_id == who.account.id))
    db.flush()

    def never_decrypt(*args, **kwargs):
        pytest.fail("out-of-scope payload was decrypted")

    monkeypatch.setattr(s.cipher, "decrypt", never_decrypt)
    assert s.list_requests(who).total == 0
    with pytest.raises(IntakeError) as denied:
        s.detail(who, pending.id)
    assert denied.value.status == 404


def test_both_requires_live_coach_and_team_scope_without_auto_approval(db, platform_users):
    from app.personnel_intake.errors import IntakeError
    from app.team_management.models import TeamManagerAssignment
    from tests.services.test_team_access import teams

    who = actor(platform_users)
    first, second = teams(db, actor(platform_users, "admin").account.id)
    db.commit()
    s = service(db)
    data = intake(platform_users, kind="both", team_id=first.id)
    with pytest.raises(IntakeError):
        s.submit(who, data)
    db.add(UserRole(user_id=who.account.id, role_id=ROLES["team_manager"].id))
    assignment = TeamManagerAssignment(
        account_id=who.account.id,
        team_id=first.id,
        created_by_user_id=actor(platform_users, "admin").account.id,
    )
    db.add(assignment)
    db.flush()
    pending = s.submit(who, data)
    assert pending.status == "pending"
    assert pending.kind == "both"
    assert s.access.context(who).allowed_team_ids == [first.id]
    assert s.access.covers(who, kind="player", coaches=[], team_id=second.id) is False
    assignment.ended_at, assignment.end_kind = datetime.now(UTC), "unassigned"
    db.flush()
    assert s.list_requests(who).total == 0
    with pytest.raises(IntakeError) as hidden:
        s.detail(who, pending.id)
    assert hidden.value.status == 404


@pytest.mark.parametrize(
    "guardian_change",
    [
        {"email": "intake@example.com"},
        {"identity_number": make_cn_id("20150102", 333), "date_of_birth": date(2015, 1, 2)},
    ],
)
def test_minor_guardian_must_be_adult_with_independent_email(db, platform_users, guardian_change):
    from app.personnel_intake.errors import IntakeError

    guardian = {
        "full_name": "隔离监护人",
        "identity_number": make_cn_id("19800102", 333),
        "phone": "13900000001",
        "sex": "male",
        "date_of_birth": date(1980, 1, 2),
        "relationship": "father",
    }
    guardian.update(guardian_change)
    with pytest.raises(IntakeError) as rejected:
        service(db).submit(
            actor(platform_users),
            intake(
                platform_users,
                identity_number=make_cn_id("20150102", 321),
                date_of_birth=date(2015, 1, 2),
                guardians=[guardian],
            ),
        )
    assert rejected.value.status == 422


def test_valid_minor_guardian_needs_no_email_or_account(db, platform_users):
    guardian = {
        "full_name": "隔离监护人",
        "identity_number": make_cn_id("19800102", 333),
        "phone": "13900000001",
        "sex": "male",
        "date_of_birth": date(1980, 1, 2),
        "relationship": "father",
    }
    result = service(db).submit(
        actor(platform_users),
        intake(
            platform_users,
            identity_number=make_cn_id("20150102", 321),
            date_of_birth=date(2015, 1, 2),
            guardians=[guardian],
        ),
    )
    assert result.status == "pending"
    assert result.scoped_payload.guardians[0].email is None


def test_no_longer_coach_staff_cannot_keep_original_target_detail(db, platform_users):
    from app.identity.models import StaffProfile
    from app.personnel_intake.errors import IntakeError

    s = service(db)
    who = actor(platform_users)
    pending = s.submit(who, intake(platform_users))
    db.get(StaffProfile, who.person.id).staff_type = "other"
    db.flush()
    assert s.list_requests(who).total == 0
    with pytest.raises(IntakeError):
        s.detail(who, pending.id)


def test_safe_reuse_does_not_overwrite_existing_person_at_submission(db, platform_users):
    from app.identity.models import Person

    existing = platform_users["people"]["adult_without_account"]
    before = db.get(Person, existing.id)
    original = (before.identity_ciphertext, before.phone_ciphertext, before.email_ciphertext)
    result = service(db).submit(
        actor(platform_users, "admin"),
        intake(
            platform_users,
            full_name=existing.full_name,
            identity_number=make_cn_id("19900102", 110),
            date_of_birth=date(1990, 1, 2),
            sex="female",
        ),
    )
    assert result.status == "pending"
    assert result.approved_person_id is None
    assert db.get(PersonnelIntakeRequest, result.id).person_id == existing.id
    current = db.get(Person, existing.id)
    assert (
        current.identity_ciphertext,
        current.phone_ciphertext,
        current.email_ciphertext,
    ) == original


def test_existing_person_outside_scope_is_generic_conflict(db, platform_users):
    from app.personnel_intake.errors import IntakeError

    existing = platform_users["people"]["adult_without_account"]
    with pytest.raises(IntakeError) as error:
        service(db).submit(
            actor(platform_users),
            intake(
                platform_users,
                full_name=existing.full_name,
                identity_number=make_cn_id("19900102", 110),
                date_of_birth=date(1990, 1, 2),
                sex="female",
            ),
        )
    assert error.value.status == 409
    assert error.value.detail == "资料存在冲突，请联系管理员核实"
    assert existing.full_name not in error.value.detail


@pytest.mark.parametrize("ended", [False, True])
def test_legacy_teaching_reuse_requires_current_assignment(db, ended):
    from app.admissions.models import Enrollment
    from app.personnel_intake.access import PersonnelIntakeAccess
    from app.personnel_intake.codec import normalize_payload
    from app.teaching.models import CoachAssignment
    from tests.services.test_portal_coach import portal_coach_scenario

    scenario = portal_coach_scenario.__wrapped__(db)
    who = AuthContext(account=scenario.coach_account, person=scenario.coach, session=None)
    target = db.scalar(
        select(Enrollment.student_person_id).where(
            Enrollment.class_id == scenario.primary_class.id, Enrollment.status == "active"
        )
    )
    assignment = db.scalar(
        select(CoachAssignment).where(
            CoachAssignment.class_id == scenario.primary_class.id,
            CoachAssignment.coach_person_id == who.person.id,
        )
    )
    if ended:
        assignment.ended_at = datetime.now(UTC)
        assignment.ended_by_user_id = who.account.id
        assignment.end_reason = "隔离测试结束责任"
        db.flush()
    payload = normalize_payload(intake({"people": {"coach": scenario.coach}}))
    assert PersonnelIntakeAccess(db).can_reuse(who, target, payload) is (not ended)


@pytest.mark.parametrize("mode", ["no_role", "revoked_coach", "deny"])
def test_submit_checks_eligibility_before_existing_identity_or_decryption(
    db, platform_users, monkeypatch, mode
):
    from app.identity.models import StaffProfile
    from app.personnel_intake.errors import IntakeError

    s = service(db)
    who = actor(
        platform_users,
        "customer_service"
        if mode == "no_role"
        else "coach"
        if mode == "revoked_coach"
        else "admin",
    )
    if mode == "revoked_coach":
        db.get(StaffProfile, who.person.id).staff_type = "other"
    if mode == "deny":
        db.add(
            UserPermissionOverride(
                user_id=who.account.id,
                permission_id=PERMISSIONS["personnel_intake.submit"].id,
                effect="deny",
                scope_kind="global",
            )
        )
    db.commit()

    def never_decrypt(*args, **kwargs):
        pytest.fail("ineligible actor decrypted stored personal facts")

    monkeypatch.setattr(s.cipher, "decrypt", never_decrypt)
    outcomes = []
    for existing in (False, True):
        person = platform_users["people"]["self_student"]
        changes = (
            {
                "full_name": person.full_name,
                "identity_number": make_cn_id("19950102", 112),
                "sex": "female",
                "date_of_birth": date(1995, 1, 2),
                "phone": "13800000004",
                "email": "student@example.com",
            }
            if existing
            else {}
        )
        with pytest.raises(IntakeError) as error:
            s.submit(who, intake(platform_users, **changes))
        outcomes.append((error.value.status, error.value.detail))
    assert outcomes[0] == outcomes[1]
    assert outcomes[0][0] == 403


def test_person_denies_prevent_reuse_matching_and_existing_request_read(
    db, platform_users, monkeypatch
):
    from app.personnel_intake.errors import IntakeError

    s = service(db)
    who = actor(platform_users, "admin")
    existing = platform_users["people"]["self_student"]
    value = intake(
        platform_users,
        full_name=existing.full_name,
        identity_number=make_cn_id("19950102", 112),
        date_of_birth=date(1995, 1, 2),
        sex="female",
        phone="13800000004",
        email="student@example.com",
    )
    result = s.submit(who, value)
    unrelated = s.submit(who, intake(platform_users))
    db.add(
        UserPermissionOverride(
            user_id=who.account.id,
            permission_id=PERMISSIONS["personnel_intake.submit"].id,
            effect="deny",
            scope_kind="person",
            scope_id=existing.id,
        )
    )
    # Review is an independent actual-admin read authority after Task3.
    db.add(
        UserPermissionOverride(
            user_id=who.account.id,
            permission_id=PERMISSIONS["personnel_intake.review"].id,
            effect="deny",
            scope_kind="person",
            scope_id=existing.id,
        )
    )
    db.commit()

    def never_decrypt(*args, **kwargs):
        pytest.fail("target-person deny decrypted stored personal facts")

    monkeypatch.setattr(s.cipher, "decrypt", never_decrypt)
    allowed_list = s.list_requests(who)
    assert allowed_list.total == 1
    assert [item.id for item in allowed_list.items] == [unrelated.id]
    with pytest.raises(IntakeError) as hidden:
        s.detail(who, result.id)
    assert hidden.value.status == 404
    with pytest.raises(IntakeError):
        s.submit(who, value)


@pytest.mark.parametrize(
    "permission",
    ["personnel_intake.submit", "personnel_intake.review", "personnel_intake.housing.read"],
)
def test_supported_override_service_accepts_intake_team_denies(db, platform_users, permission):
    from app.rbac.schemas import PermissionOverrideCreate
    from app.rbac.service import InvalidPermissionOverride, PermissionDelegationDenied, RbacService
    from tests.services.test_team_access import teams

    admin = actor(platform_users, "admin")
    first, _ = teams(db, admin.account.id)
    db.commit()
    policy = RbacService(db)
    rule, _ = policy.add_override(
        actor_user_id=admin.account.id,
        account_id=platform_users["accounts"]["coach"].id,
        input=PermissionOverrideCreate(
            permission_id=PERMISSIONS[permission].id,
            effect="deny",
            scope_kind="assigned_team",
            scope_id=first.id,
        ),
    )
    assert (rule.scope_kind, rule.scope_id) == ("assigned_team", first.id)
    preview = policy.preview_override(
        actor_user_id=admin.account.id,
        account_id=platform_users["accounts"]["coach"].id,
        input=PermissionOverrideCreate(
            permission_id=PERMISSIONS[permission].id,
            effect="deny",
            scope_kind="assigned_team",
            scope_id=first.id,
        ),
    )
    assert preview.allowed is False and preview.reason == "explicit_deny"
    policy.add_override(
        actor_user_id=admin.account.id,
        account_id=admin.account.id,
        input=PermissionOverrideCreate(
            permission_id=PERMISSIONS[permission].id,
            effect="deny",
            scope_kind="assigned_team",
            scope_id=first.id,
        ),
    )
    with pytest.raises(PermissionDelegationDenied):
        policy.add_override(
            actor_user_id=admin.account.id,
            account_id=platform_users["accounts"]["coach"].id,
            input=PermissionOverrideCreate(
                permission_id=PERMISSIONS[permission].id,
                effect="allow",
                scope_kind="assigned_team",
                scope_id=first.id,
            ),
        )
    with pytest.raises(InvalidPermissionOverride):
        policy.add_override(
            actor_user_id=admin.account.id,
            account_id=platform_users["accounts"]["coach"].id,
            input=PermissionOverrideCreate(
                permission_id=PERMISSIONS[permission].id, effect="deny", scope_kind="family"
            ),
        )


@pytest.mark.parametrize(
    "actor_key,expected_total", [("admin", 35), ("coach", 35), ("customer_service", 0)]
)
def test_intake_list_count_and_page_query_cost_is_bounded(
    db, platform_users, actor_key, expected_total
):
    from sqlalchemy import event

    from app.auth.models import UserAccount
    from app.identity.models import Person, StaffProfile

    s = service(db)
    who = actor(platform_users, actor_key)
    for i in range(4):
        person = Person(
            full_name=f"隔离选项教练{i}",
            identity_ciphertext="synthetic-not-read",
            identity_digest=uuid4().hex + uuid4().hex,
            sex="male",
            date_of_birth=date(1990, 1, 2),
        )
        db.add(person)
        db.flush()
        db.add(StaffProfile(person_id=person.id, staff_type="coach"))
        account = UserAccount(
            person_id=person.id,
            password_hash="unused",
            status="active",
            force_password_change=False,
        )
        db.add(account)
        db.flush()
        db.add(UserRole(user_id=account.id, role_id=ROLES["coach"].id))
    rows = [
        PersonnelIntakeRequest(
            submitted_by_user_id=who.account.id,
            kind="student",
            version=1,
            state="pending",
            target_coach_person_ids=[platform_users["people"]["coach"].id],
        )
        for _ in range(35)
    ]
    db.add_all(rows)
    db.commit()
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.bind, "before_cursor_execute", capture)
    try:
        result = s.list_requests(who, page=2, page_size=10)
    finally:
        event.remove(db.bind, "before_cursor_execute", capture)
    assert (result.total, len(result.items), result.page) == (
        expected_total,
        10 if expected_total else 0,
        2,
    )
    assert len(statements) <= 10
    request_queries = [
        q.lower() for q in statements if "from personnel_intake_requests" in q.lower()
    ]
    assert any("count(" in q for q in request_queries)
    if expected_total:
        assert any("limit" in q and "offset" in q for q in request_queries)
    else:
        assert any("where false" in q for q in request_queries)


@pytest.mark.parametrize("role", ["admin", "administration", "team_manager"])
def test_pure_player_scope_and_administration_never_reviews(db, platform_users, role):
    from app.identity.models import StudentProfile
    from app.personnel_intake.errors import IntakeError
    from app.rbac.schemas import PermissionOverrideCreate
    from app.rbac.service import RbacService
    from app.team_management.models import TeamManagerAssignment
    from tests.services.test_team_access import teams

    root = actor(platform_users, "admin")
    first, second = teams(db, root.account.id)
    who = actor(platform_users)
    db.execute(UserRole.__table__.delete().where(UserRole.user_id == who.account.id))
    db.add(UserRole(user_id=who.account.id, role_id=ROLES[role].id))
    if role == "team_manager":
        db.add(
            TeamManagerAssignment(
                account_id=who.account.id, team_id=first.id, created_by_user_id=root.account.id
            )
        )
    db.commit()
    s = service(db)
    before = db.scalar(select(func.count()).select_from(StudentProfile))
    pending = s.submit(
        who, intake(platform_users, kind="player", coach_person_ids=[], team_id=first.id)
    )
    assert pending.status == "pending" and pending.kind == "player"
    assert db.scalar(select(func.count()).select_from(StudentProfile)) == before
    assert s.access.context(who).can_review is (role == "admin")
    if role == "team_manager":
        assert s.access.covers(who, kind="player", coaches=[], team_id=second.id) is False
    # Use the supported override service, not a manually inserted override row.
    RbacService(db).add_override(
        actor_user_id=root.account.id,
        account_id=who.account.id,
        input=PermissionOverrideCreate(
            permission_id=PERMISSIONS["personnel_intake.submit"].id,
            effect="deny",
            scope_kind="assigned_team",
            scope_id=first.id,
        ),
    )
    if role == "admin":
        assert s.list_requests(who).total == 1
        assert s.detail(who, pending.id).allowed_actions == []
        with pytest.raises(IntakeError) as no_withdraw:
            s.withdraw(who, pending.id, 1)
        assert no_withdraw.value.status == 403
    else:
        assert s.list_requests(who).total == 0
        with pytest.raises(IntakeError) as hidden:
            s.detail(who, pending.id)
        assert hidden.value.status == 404
    with pytest.raises(IntakeError) as denied:
        s.submit(
            who,
            intake(
                platform_users,
                kind="player",
                coach_person_ids=[],
                team_id=first.id,
                email="denied-player@example.com",
            ),
        )
    assert denied.value.status == 403


def test_second_active_same_person_request_is_atomic_generic_conflict(db, platform_users):
    from app.personnel_intake.errors import IntakeError

    s = service(db)
    existing = platform_users["people"]["adult_without_account"]
    value = intake(
        platform_users,
        full_name=existing.full_name,
        identity_number=make_cn_id("19900102", 110),
        date_of_birth=date(1990, 1, 2),
        sex="female",
    )
    first = s.submit(actor(platform_users, "admin"), value)
    before = tuple(
        db.scalar(select(func.count()).select_from(model))
        for model in (
            PersonnelIntakeRequest,
            PersonnelIntakeRevision,
            PersonnelCredentialReservation,
        )
    )
    reserved = set(
        db.scalars(
            select(PersonnelCredentialReservation.id).where(
                PersonnelCredentialReservation.request_id == first.id,
                PersonnelCredentialReservation.ended_at.is_(None),
            )
        )
    )
    with pytest.raises(IntakeError) as conflict:
        s.submit(actor(platform_users, "admin"), value)
    assert conflict.value.status == 409
    assert conflict.value.detail == "资料存在冲突，请联系管理员核实"
    assert (
        tuple(
            db.scalar(select(func.count()).select_from(model))
            for model in (
                PersonnelIntakeRequest,
                PersonnelIntakeRevision,
                PersonnelCredentialReservation,
            )
        )
        == before
    )
    assert (
        set(
            db.scalars(
                select(PersonnelCredentialReservation.id).where(
                    PersonnelCredentialReservation.request_id == first.id,
                    PersonnelCredentialReservation.ended_at.is_(None),
                )
            )
        )
        == reserved
    )
    assert s.detail(actor(platform_users, "admin"), first.id).version == 1


def test_scoped_intake_allow_is_visible_without_global_role_grant(db, platform_users):
    from app.rbac.models import RolePermission
    from app.rbac.schemas import PermissionOverrideCreate
    from app.rbac.service import RbacService
    from app.team_management.models import TeamManagerAssignment
    from tests.services.test_team_access import teams

    root, who = actor(platform_users, "admin"), actor(platform_users)
    first, _ = teams(db, root.account.id)
    db.add(UserRole(user_id=who.account.id, role_id=ROLES["team_manager"].id))
    db.add(
        TeamManagerAssignment(
            account_id=who.account.id, team_id=first.id, created_by_user_id=root.account.id
        )
    )
    db.execute(
        RolePermission.__table__.delete().where(
            RolePermission.role_id.in_([ROLES["coach"].id, ROLES["team_manager"].id]),
            RolePermission.permission_id == PERMISSIONS["personnel_intake.submit"].id,
        )
    )
    db.commit()
    policy = RbacService(db)
    policy.add_override(
        actor_user_id=root.account.id,
        account_id=who.account.id,
        input=PermissionOverrideCreate(
            permission_id=PERMISSIONS["personnel_intake.submit"].id,
            effect="allow",
            scope_kind="assigned_team",
            scope_id=first.id,
        ),
    )
    assert "personnel_intake.submit" in policy.available_permissions(user_id=who.account.id)
    assert service(db).access.context(who).can_submit is True
