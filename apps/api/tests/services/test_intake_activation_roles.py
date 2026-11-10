from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.auth.models import UserAccount
from app.identity.models import Person
from app.personnel_intake.errors import PersonnelIntakeError
from app.personnel_intake.schemas import IntakeInput
from app.rbac.models import Role, UserRole
from app.rbac.seed import ROLES
from tests.api.conftest import platform_users as _platform_users
from tests.services.test_personnel_approval import approvals
from tests.services.test_personnel_intake import actor, intake


@pytest.fixture
def users(db):
    users = _platform_users.__wrapped__(db)
    db.execute(UserRole.__table__.delete().where(UserRole.user_id == users["accounts"]["admin"].id))
    db.add(UserRole(user_id=users["accounts"]["admin"].id, role_id=ROLES["admin"].id))
    db.add(UserRole(user_id=users["accounts"]["coach"].id, role_id=ROLES["super_admin"].id))
    db.flush()
    return users


def property_delegation(db, account_id):
    from app.rbac.models import UserPermissionOverride
    from app.rbac.seed import PERMISSIONS

    for key in [
        "permissions.manage",
        "housing.read",
        "housing.changes.submit",
        "housing.keycards.manage",
        "housing.exports.basic",
    ]:
        db.add(
            UserPermissionOverride(
                user_id=account_id,
                permission_id=PERMISSIONS[key].id,
                effect="allow",
                scope_kind="global",
            )
        )
    db.flush()


def test_root_can_plan_every_non_admin_role_without_creating_account(db, users):
    keys = sorted(set(ROLES) - {"admin", "super_admin"})
    result = approvals(db).direct_create(
        actor(users, "coach"), intake(users), uuid4(), activation_role_keys=keys
    )
    from app.personnel_intake.role_grants import ActivationRoleService

    view = ActivationRoleService(db, lookup=approvals(db).lookup).get(
        actor(users, "coach"), result.person_id
    )
    assert view.role_keys == keys
    assert view.version == 1 and view.can_edit and not view.account_activated
    assert (
        db.scalar(select(UserAccount.id).where(UserAccount.person_id == result.person_id)) is None
    )


@pytest.mark.parametrize("keys", [["admin"], ["super_admin"], ["missing"], ["coach", "coach"]])
def test_invalid_roles_roll_back_all_materialized_personnel(db, users, keys):
    before = db.scalar(select(func.count()).select_from(Person))
    with pytest.raises(PersonnelIntakeError) as exc:
        approvals(db).direct_create(
            actor(users, "coach"), intake(users), uuid4(), activation_role_keys=keys
        )
    assert exc.value.status == 422
    assert db.scalar(select(func.count()).select_from(Person)) == before


def test_admin_cannot_plan_finance(db, users):
    property_delegation(db, users["accounts"]["admin"].id)
    with pytest.raises(PersonnelIntakeError) as exc:
        approvals(db).direct_create(
            actor(users, "admin"), intake(users), uuid4(), activation_role_keys=["finance"]
        )
    assert exc.value.status == 403


def test_admin_directory_is_safe_and_returns_allowed_and_forbidden_choices(db, users):
    from app.personnel_intake.role_grants import ActivationRoleService

    property_delegation(db, users["accounts"]["admin"].id)
    options = ActivationRoleService(db, lookup=approvals(db).lookup).options(actor(users, "admin"))
    values = {item.key: item.assignable for item in options.items}
    assert values["finance"] is False
    assert values["property"] is True
    assert values["staff"] is False
    assert len(values) == 13


def test_ordinary_intake_rejects_activation_roles_and_staff(users):
    values = {k: getattr(intake(users), k) for k in IntakeInput.model_fields}
    with pytest.raises(ValidationError):
        IntakeInput(**values, activation_role_keys=["coach"])
    with pytest.raises(ValidationError):
        IntakeInput(**dict(values, kind="staff"))


def test_plan_reconfirmation_is_versioned_and_idempotent(db, users):
    from app.personnel_intake.role_grants import ActivationRoleService
    from app.personnel_intake.schemas import ActivationRoleUpdate

    who = actor(users, "coach")
    result = approvals(db).direct_create(
        who, intake(users), uuid4(), activation_role_keys=["coach"]
    )
    plans = ActivationRoleService(db, lookup=approvals(db).lookup)
    command = ActivationRoleUpdate(
        role_keys=["guardian"], expected_version=1, idempotency_key=uuid4()
    )
    view = plans.update(who, result.person_id, command)
    assert view.version == 2 and view.role_keys == ["guardian"]
    assert plans.update(who, result.person_id, command) == view
    with pytest.raises(PersonnelIntakeError) as exc:
        plans.update(who, result.person_id, command.model_copy(update={"idempotency_key": uuid4()}))
    assert exc.value.status == 409


@pytest.mark.parametrize("replacement", [[], ["property"], ["finance"]])
def test_pending_finance_plan_remains_protected_from_nonroot_edits(db, users, replacement):
    from app.personnel_intake.role_grants import ActivationRoleService, latest_plan
    from app.personnel_intake.schemas import ActivationRoleUpdate
    from app.rbac.models import UserPermissionOverride
    from app.rbac.seed import PERMISSIONS

    manager = actor(users, "admin")
    db.add(
        UserPermissionOverride(
            user_id=manager.account.id,
            permission_id=PERMISSIONS["permissions.manage"].id,
            effect="allow",
            scope_kind="global",
        )
    )
    db.flush()
    result = approvals(db).direct_create(
        actor(users, "coach"), intake(users), uuid4(), activation_role_keys=["finance"]
    )
    plans = ActivationRoleService(db, lookup=approvals(db).lookup)
    assert not plans.get(manager, result.person_id).can_edit
    command = ActivationRoleUpdate(
        role_keys=replacement, expected_version=1, idempotency_key=uuid4()
    )
    with pytest.raises(PersonnelIntakeError) as exc:
        plans.update(manager, result.person_id, command)
    assert exc.value.status == 403
    assert latest_plan(db, result.person_id).version == 1
    assert plans.update(actor(users, "coach"), result.person_id, command).version == 2


def test_role_set_participates_in_direct_create_replay(db, users):
    key = uuid4()
    who = actor(users, "coach")
    result = approvals(db).direct_create(who, intake(users), key, activation_role_keys=["coach"])
    assert (
        approvals(db).direct_create(who, intake(users), key, activation_role_keys=["coach"])
        == result
    )
    with pytest.raises(PersonnelIntakeError):
        approvals(db).direct_create(who, intake(users), key, activation_role_keys=["guardian"])


def test_pending_mode_saves_plan_without_creating_account(db, users):
    from app.personnel_intake.role_grants import ActivationRoleService
    from app.personnel_intake.schemas import PendingIntakeInput

    data = intake(users)
    pending = PendingIntakeInput(**{k: getattr(data, k) for k in IntakeInput.model_fields})
    result = approvals(db).direct_create_pending(
        actor(users, "coach"), pending, uuid4(), activation_role_keys=["guardian"]
    )
    view = ActivationRoleService(db, lookup=approvals(db).lookup).get(
        actor(users, "coach"), result.person_id
    )
    assert view.version == 1 and view.role_keys == ["guardian"] and not view.account_activated


@pytest.mark.parametrize("kind", ["student", "staff"])
def test_identity_correction_does_not_replace_role_authorizer_or_origin(db, users, kind):
    from app.personnel_intake.role_grants import latest_plan
    from app.personnel_intake.schemas import AssistedProfileCorrection, DirectPersonInput

    data = intake(users)
    if kind == "staff":
        data = DirectPersonInput(
            **{
                k: getattr(data, k)
                for k in IntakeInput.model_fields
                if k not in {"kind", "coach_person_ids"}
            },
            kind="staff",
            coach_person_ids=[],
        )
    result = approvals(db).direct_create(
        actor(users, "coach"), data, uuid4(), activation_role_keys=["coach"]
    )
    plan = latest_plan(db, result.person_id)
    before = (plan.id, plan.origin_approval_id, plan.authorizer_user_id, plan.plan_digest)
    values = {
        k: getattr(data, k)
        for k in AssistedProfileCorrection.model_fields
        if k in type(data).model_fields
    }
    changed = approvals(db).correct_profile(
        actor(users, "admin"),
        result.person_id,
        AssistedProfileCorrection(
            **dict(values, full_name="更正后的合成人员"),
            expected_version=1,
            reason="核实姓名",
            idempotency_key=uuid4(),
        ),
    )
    assert changed.approval_id != result.approval_id
    after = latest_plan(db, result.person_id)
    assert (
        after.id,
        after.origin_approval_id,
        after.authorizer_user_id,
        after.plan_digest,
    ) == before


def test_delegated_admin_can_plan_allowed_role_but_not_expired_or_denied_ceiling(db, users):
    from datetime import UTC, datetime, timedelta

    from app.personnel_intake.role_grants import ActivationRoleService
    from app.rbac.models import UserPermissionOverride
    from app.rbac.seed import PERMISSIONS

    who = actor(users, "admin")
    property_delegation(db, who.account.id)
    result = approvals(db).direct_create(
        who, intake(users), uuid4(), activation_role_keys=["property"]
    )
    assert ActivationRoleService(db, lookup=approvals(db).lookup).get(
        who, result.person_id
    ).role_keys == ["property"]
    db.add(
        UserPermissionOverride(
            user_id=who.account.id,
            permission_id=PERMISSIONS["housing.read"].id,
            effect="deny",
            scope_kind="global",
            valid_from=datetime.now(UTC) + timedelta(days=1),
        )
    )
    db.flush()
    with pytest.raises(PersonnelIntakeError) as exc:
        ActivationRoleService(db, lookup=approvals(db).lookup).validate(
            who.account.id, result.person_id, ["property"]
        )
    assert exc.value.status == 403


def test_expanded_role_bundle_cannot_be_activated_above_original_grantor_ceiling(committed_db):
    from pydantic import SecretStr

    from app.personnel_intake.role_grants import ActivationRoleService
    from app.personnel_intake.schemas import ActivationRoleUpdate
    from app.rbac.models import RolePermission
    from app.rbac.seed import PERMISSIONS
    from app.registration.models import RegistrationVerificationChallenge

    db = committed_db
    users, result, service, token, code, challenge = prepared_activation(db, ["property"])
    who = actor(users, "admin")
    db.execute(UserRole.__table__.delete().where(UserRole.user_id == who.account.id))
    db.add(UserRole(user_id=who.account.id, role_id=ROLES["admin"].id))
    property_delegation(db, who.account.id)
    db.commit()
    ActivationRoleService(db, lookup=approvals(db).lookup).update(
        who,
        result.person_id,
        ActivationRoleUpdate(role_keys=["property"], expected_version=1, idempotency_key=uuid4()),
    )
    db.add(
        RolePermission(
            role_id=ROLES["property"].id,
            permission_id=PERMISSIONS["audit.read"].id,
            scope_kind="global",
        )
    )
    db.commit()
    with pytest.raises(PersonnelIntakeError) as exc:
        service.finish(token, code, SecretStr("SecurePass1"))
    assert exc.value.code == "activation_roles_invalidated"
    assert (
        db.scalar(select(UserAccount.id).where(UserAccount.person_id == result.person_id)) is None
    )
    assert (
        db.get(RegistrationVerificationChallenge, challenge, populate_existing=True).consumed_at
        is None
    )


@pytest.fixture
def committed_db(postgres_engine):
    from tests.services.test_profile_claims import claim_db

    yield from claim_db.__wrapped__(postgres_engine)


def prepared_activation(db, keys):
    from pydantic import SecretStr

    from app.personnel_intake.activation_models import AssistedActivationSession
    from tests.services.test_assisted_activation import activation

    users = _platform_users.__wrapped__(db)
    result = approvals(db).direct_create(
        actor(users, "admin"), intake(users), uuid4(), activation_role_keys=keys
    )
    db.commit()
    service, sender = activation(db)
    token = service.start("intake@example.com", "127.0.9.91").session_token.get_secret_value()
    session = db.scalar(
        select(AssistedActivationSession).where(
            AssistedActivationSession.person_id == result.person_id
        )
    )
    code = SecretStr(sender.code_for(session.challenge_id))
    service.verify(token, code)
    return users, result, service, token, code, session.challenge_id


@pytest.mark.parametrize(
    "keys",
    [
        ["coach", "guardian", "finance"],
        [
            "coach",
            "customer_service",
            "life_teacher",
            "staff",
            "guardian",
            "student",
            "registered_user",
            "administration",
            "finance",
            "team_member",
            "team_manager",
            "property",
            "assistant_coach",
        ],
    ],
)
def test_activation_applies_approved_roles_with_minimum_identity(committed_db, keys):
    from pydantic import SecretStr

    from app.identity.models import GuardianRelationship, StaffProfile
    from app.teaching.models import CoachAssignment
    from app.team_management.models import TeamMembership

    db = committed_db
    _, result, service, token, code, _ = prepared_activation(db, keys)
    business_models = (GuardianRelationship, StaffProfile, CoachAssignment, TeamMembership)
    before = [db.scalar(select(func.count()).select_from(model)) for model in business_models]
    assert service.finish(token, code, SecretStr("SecurePass1")).completed
    account = db.scalar(select(UserAccount).where(UserAccount.person_id == result.person_id))
    assert set(
        db.scalars(select(Role.key).join(UserRole).where(UserRole.user_id == account.id))
    ) == set(keys) | {"student"}
    assert [
        db.scalar(select(func.count()).select_from(model)) for model in business_models
    ] == before


def test_cleared_plan_keeps_baseline_when_previous_grantor_is_disabled(committed_db):
    from pydantic import SecretStr

    from app.personnel_intake.role_grants import ActivationRoleService
    from app.personnel_intake.schemas import ActivationRoleUpdate

    db = committed_db
    users, result, service, token, code, _ = prepared_activation(db, ["finance"])
    plans = ActivationRoleService(db, lookup=approvals(db).lookup)
    plans.update(
        actor(users, "admin"),
        result.person_id,
        ActivationRoleUpdate(role_keys=[], expected_version=1, idempotency_key=uuid4()),
    )
    users["accounts"]["admin"].status = "disabled"
    db.commit()
    assert service.finish(token, code, SecretStr("SecurePass1")).completed
    account = db.scalar(select(UserAccount).where(UserAccount.person_id == result.person_id))
    assert set(
        db.scalars(select(Role.key).join(UserRole).where(UserRole.user_id == account.id))
    ) == {"student"}


def test_activated_plan_is_read_only_and_no_new_version_is_written(committed_db):
    from pydantic import SecretStr

    from app.personnel_intake.role_grants import ActivationRoleService, latest_plan
    from app.personnel_intake.schemas import ActivationRoleUpdate

    db = committed_db
    users, result, service, token, code, _ = prepared_activation(db, ["coach"])
    assert service.finish(token, code, SecretStr("SecurePass1")).completed
    plans = ActivationRoleService(db, lookup=approvals(db).lookup)
    view = plans.get(actor(users, "admin"), result.person_id)
    assert view.account_activated and not view.can_edit
    with pytest.raises(PersonnelIntakeError) as exc:
        plans.update(
            actor(users, "admin"),
            result.person_id,
            ActivationRoleUpdate(role_keys=[], expected_version=1, idempotency_key=uuid4()),
        )
    assert exc.value.code == "activation_roles_activated"
    assert latest_plan(db, result.person_id).version == 1


@pytest.mark.parametrize(
    "change", ["disabled", "force_password_change", "revoke", "tamper", "tamper_empty"]
)
def test_activation_rechecks_authority_and_rolls_back_otp_and_account(committed_db, change):
    from pydantic import SecretStr

    from app.personnel_intake.role_grants import latest_plan
    from app.registration.models import RegistrationVerificationChallenge

    db = committed_db
    users, result, service, token, code, challenge = prepared_activation(db, ["finance"])
    grantor = users["accounts"]["admin"]
    if change == "disabled":
        grantor.status = "disabled"
    elif change == "force_password_change":
        grantor.force_password_change = True
    elif change == "revoke":
        db.execute(UserRole.__table__.delete().where(UserRole.user_id == grantor.id))
    else:
        latest_plan(db, result.person_id).role_keys = [] if change == "tamper_empty" else ["coach"]
    db.commit()
    with pytest.raises(PersonnelIntakeError) as exc:
        service.finish(token, code, SecretStr("SecurePass1"))
    assert exc.value.code == "activation_roles_invalidated"
    assert (
        db.scalar(select(UserAccount.id).where(UserAccount.person_id == result.person_id)) is None
    )
    assert (
        db.get(RegistrationVerificationChallenge, challenge, populate_existing=True).consumed_at
        is None
    )


@pytest.mark.parametrize("change", ["revoke", "reconfirm"])
def test_activation_rechecks_after_real_concurrent_lock_wait(committed_db, postgres_engine, change):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from time import monotonic, sleep

    from pydantic import SecretStr
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from app.personnel_intake.role_grants import ActivationRoleService
    from app.personnel_intake.schemas import ActivationRoleUpdate
    from app.registration.models import RegistrationVerificationChallenge
    from app.team_management.write_support import lock_participants
    from tests.services.test_assisted_activation import activation

    db = committed_db
    users, result, _, token, code, challenge = prepared_activation(db, ["finance"])
    who = actor(users, "admin")
    actor_id, actor_person = who.account.id, who.person.id
    db.rollback()
    started, pids = Event(), []

    def finish():
        with Session(postgres_engine) as other:
            other.execute(text("SET lock_timeout = '8s'"))
            pids.append(other.scalar(text("SELECT pg_backend_pid()")))
            started.set()
            try:
                activation(other)[0].finish(token, code, SecretStr("SecurePass1"))
                return "completed"
            except PersonnelIntakeError as error:
                return error.code

    with Session(postgres_engine) as blocker:
        lock_participants(blocker, sorted([result.person_id, actor_person]), [actor_id])
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(finish)
            try:
                assert started.wait(5)
                waiting, deadline = False, monotonic() + 5
                with postgres_engine.connect() as observer:
                    while monotonic() < deadline:
                        waiting = observer.scalar(
                            text(
                                "SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid=:pid"
                            ),
                            {"pid": pids[0]},
                        )
                        observer.commit()
                        if waiting:
                            break
                        sleep(0.01)
                assert waiting, "activation did not reach real participant lock wait"
                if change == "revoke":
                    blocker.execute(UserRole.__table__.delete().where(UserRole.user_id == actor_id))
                else:
                    from app.auth.service import AuthContext

                    current = AuthContext(
                        account=blocker.get(UserAccount, actor_id),
                        person=blocker.get(Person, actor_person),
                        session=None,
                    )
                    ActivationRoleService(blocker, lookup=approvals(blocker).lookup).update(
                        current,
                        result.person_id,
                        ActivationRoleUpdate(
                            role_keys=["guardian"], expected_version=1, idempotency_key=uuid4()
                        ),
                    )
                blocker.commit()
            finally:
                blocker.rollback()
            assert future.result(timeout=10) == (
                "activation_roles_invalidated" if change == "revoke" else "activation_roles_retry"
            )
    assert (
        db.scalar(select(UserAccount.id).where(UserAccount.person_id == result.person_id)) is None
    )
    assert (
        db.get(RegistrationVerificationChallenge, challenge, populate_existing=True).consumed_at
        is None
    )


def test_late_failure_rolls_back_assigned_roles_and_otp(committed_db, monkeypatch):
    from pydantic import SecretStr

    from app.audit.service import AuditService
    from app.registration.models import RegistrationVerificationChallenge

    db = committed_db
    _, result, service, token, code, challenge = prepared_activation(db, ["coach"])
    append = AuditService.append

    def fail_completion(self, **kwargs):
        if kwargs["action"] == "profile_activation.completed":
            raise RuntimeError("synthetic audit failure")
        return append(self, **kwargs)

    monkeypatch.setattr(AuditService, "append", fail_completion)
    with pytest.raises(RuntimeError, match="synthetic audit failure"):
        service.finish(token, code, SecretStr("SecurePass1"))
    assert (
        db.scalar(select(UserAccount.id).where(UserAccount.person_id == result.person_id)) is None
    )
    assert (
        db.get(RegistrationVerificationChallenge, challenge, populate_existing=True).consumed_at
        is None
    )
