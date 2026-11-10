"""A real email activation atomically realizes the original personnel roster."""

from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from app.auth.models import UserAccount
from app.personnel_intake.activation_models import AssistedActivationSession
from app.personnel_intake.identity_selection import ManagedIdentityInput, StaffTeamSelection
from app.registration.models import RegistrationVerificationChallenge
from app.team_management.models import TeamManagerAssignment, TeamStaffAssignment
from app.team_management.person_staff_models import PersonTeamStaffPosition
from app.team_management.person_staff_query import PersonStaffQuery
from tests.api.conftest import platform_users as make_users
from tests.services.test_assisted_activation import activation
from tests.services.test_intake_activation_roles import committed_db as _committed_db
from tests.services.test_personnel_approval import approvals
from tests.services.test_personnel_intake import actor, intake
from tests.services.test_team_access import teams


@pytest.fixture
def committed_db(postgres_engine):
    yield from _committed_db.__wrapped__(postgres_engine)


def prepared_staff(db):
    users = make_users.__wrapped__(db)
    first, second = teams(db, users["accounts"]["admin"].id)
    result = approvals(db).direct_create(
        actor(users, "admin"), intake(users), uuid4(),
        identity_selection=ManagedIdentityInput(
            primary_identity="student", concurrent_identities=["coach", "team_manager"],
            primary_staff_identity="coach"),
        staff_teams=[StaffTeamSelection(staff_role="coach", team_ids=[second.id, first.id]),
                     StaffTeamSelection(staff_role="manager", team_ids=[first.id])],
    )
    db.commit()
    service, sender = activation(db)
    token = service.start("intake@example.com", "127.0.9.92").session_token.get_secret_value()
    session = db.scalar(select(AssistedActivationSession).where(
        AssistedActivationSession.person_id == result.person_id))
    code = SecretStr(sender.code_for(session.challenge_id))
    service.verify(token, code)
    return users, result, first, second, service, token, code, session.challenge_id


def test_activation_preserves_roster_identity_without_manager_write_scope(committed_db):
    db = committed_db
    users, result, first, second, service, token, code, _ = prepared_staff(db)
    query = PersonStaffQuery(db, lookup=approvals(db).lookup)
    before = query.list(actor(users, "admin"), first.id, status="current", page=1, page_size=20)
    assert before.total == 2
    assert all(row.account_status == "not_activated" for row in before.items)
    service.finish(token, code, SecretStr("SecurePass1"))
    account = db.scalar(select(UserAccount).where(UserAccount.person_id == result.person_id))
    after = query.list(actor(users, "admin"), first.id, status="current", page=1, page_size=20)
    assert after.total == before.total
    assert after.items[0].row_key == before.items[0].row_key
    assert after.items[0].account_id == account.id
    assert after.items[0].account_assignment_id is not None
    assert all(row.account_assignment_id is not None for row in after.items)
    assert db.scalar(select(func.count()).select_from(TeamManagerAssignment)
                     .where(TeamManagerAssignment.account_id == account.id)) == 0
    assert db.scalar(select(func.count()).select_from(TeamStaffAssignment)
                     .where(TeamStaffAssignment.account_id == account.id)) == 3
    from app.auth.service import AuthContext
    from app.identity.models import Person
    from app.team_management.access import TeamAccessService
    from app.team_management.errors import TeamAccessDenied

    who = AuthContext(account=account, person=db.get(Person, result.person_id), session=None)
    TeamAccessService(db).require_team(who, second.id, "read")
    with pytest.raises(TeamAccessDenied):
        TeamAccessService(db).require_team(who, first.id, "manage_members")
    assert service.finish(token, code, SecretStr("SecurePass1")).completed
    assert db.scalar(select(func.count()).select_from(TeamStaffAssignment)) == 3


@pytest.mark.parametrize("invalidation", ["team_disabled", "team_grantor_denied", "grantor_disabled", "tampered_proof"])
def test_invalid_staff_authority_rolls_back_account_and_otp(committed_db, invalidation):
    db = committed_db
    users, result, first, _, service, token, code, challenge_id = prepared_staff(db)
    if invalidation == "team_disabled":
        first.is_active = False
    elif invalidation == "grantor_disabled":
        users["accounts"]["admin"].status = "disabled"
    elif invalidation == "tampered_proof":
        from app.team_management.person_staff_models import PersonTeamStaffAuthorization
        position = db.scalar(select(PersonTeamStaffPosition).order_by(PersonTeamStaffPosition.id))
        original = db.scalar(select(PersonTeamStaffAuthorization).where(
            PersonTeamStaffAuthorization.position_id == position.id))
        position.version = 2
        db.add(PersonTeamStaffAuthorization(position_id=position.id, version=2,
            authorizer_user_id=original.authorizer_user_id,
            authorized_role_key=original.authorized_role_key, role_plan_id=original.role_plan_id,
            operation_id=uuid4(), evidence_digest="0" * 64))
    else:
        from app.rbac.models import UserPermissionOverride
        from app.rbac.seed import PERMISSIONS
        db.add(UserPermissionOverride(user_id=users["accounts"]["admin"].id,
            permission_id=PERMISSIONS["team_manager_assignments.manage"].id,
            effect="deny", scope_kind="global"))
    db.commit()
    from app.core.errors import ProblemError
    with pytest.raises(ProblemError):
        service.finish(token, code, SecretStr("SecurePass1"))
    assert db.scalar(select(UserAccount.id).where(UserAccount.person_id == result.person_id)) is None
    assert db.scalar(select(func.count()).select_from(TeamStaffAssignment)) == 0
    assert db.get(RegistrationVerificationChallenge, challenge_id, populate_existing=True).consumed_at is None
    assert all(p.account_assignment_id is None for p in db.scalars(
        select(PersonTeamStaffPosition).where(PersonTeamStaffPosition.person_id == result.person_id)))


def test_unrelated_new_plan_does_not_resign_original_team_role_evidence(committed_db):
    from app.personnel_intake.errors import PersonnelIntakeError
    from app.personnel_intake.role_grants import ActivationRoleService, latest_plan
    from app.personnel_intake.schemas import ActivationRoleUpdate
    from app.rbac.models import UserRole
    from app.rbac.seed import ROLES
    from app.team_management.person_staff_models import PersonTeamStaffAuthorization

    db = committed_db
    users, result, _, _, service, token, code, challenge_id = prepared_staff(db)
    original_id = latest_plan(db, result.person_id).id
    db.add(UserRole(user_id=users["accounts"]["coach"].id, role_id=ROLES["super_admin"].id))
    db.commit()
    ActivationRoleService(db, lookup=approvals(db).lookup).update(actor(users, "coach"), result.person_id,
        ActivationRoleUpdate(role_keys=["coach", "team_manager", "guardian"],
                             expected_version=1, idempotency_key=uuid4()))
    assert {p.role_plan_id for p in db.scalars(select(PersonTeamStaffAuthorization))} == {original_id}
    users["accounts"]["admin"].status = "disabled"
    db.commit()
    with pytest.raises(PersonnelIntakeError):
        service.finish(token, code, SecretStr("SecurePass1"))
    assert db.scalar(select(UserAccount.id).where(UserAccount.person_id == result.person_id)) is None
    assert db.get(RegistrationVerificationChallenge, challenge_id, populate_existing=True).consumed_at is None


def test_explicit_reconfirmation_repairs_provenance_without_rewriting_history(committed_db):
    from app.personnel_intake.role_grants import ActivationRoleService, latest_plan
    from app.personnel_intake.schemas import ActivationRoleUpdate
    from app.rbac.models import UserRole
    from app.rbac.seed import ROLES
    from app.team_management.person_staff_models import PersonTeamStaffAuthorization
    from app.team_management.person_staff_schemas import PersonStaffReconfirmInput
    from app.team_management.person_staff_service import PersonStaffService

    db = committed_db
    users, result, _, _, service, token, code, _ = prepared_staff(db)
    original = list(db.scalars(select(PersonTeamStaffAuthorization)))
    snapshots = [(p.id, p.evidence_digest, p.role_plan_id, p.authorizer_user_id) for p in original]
    db.add(UserRole(user_id=users["accounts"]["coach"].id, role_id=ROLES["super_admin"].id))
    db.commit()
    ActivationRoleService(db, lookup=approvals(db).lookup).update(actor(users, "coach"), result.person_id,
        ActivationRoleUpdate(role_keys=["coach", "team_manager"], expected_version=1, idempotency_key=uuid4()))
    staff = PersonStaffService(db, lookup=approvals(db).lookup)
    for position in db.scalars(select(PersonTeamStaffPosition)).all():
        staff.reconfirm(actor(users, "coach"), position.id,
            PersonStaffReconfirmInput(expected_version=position.version, reason="重新核实队内岗位",
                                      idempotency_key=uuid4()))
    users["accounts"]["admin"].status = "disabled"
    db.commit()
    service.finish(token, code, SecretStr("SecurePass1"))
    assert db.scalar(select(func.count()).select_from(TeamStaffAssignment)) == 3
    assert [(p.id, p.evidence_digest, p.role_plan_id, p.authorizer_user_id) for p in original] == snapshots
    new = list(db.scalars(select(PersonTeamStaffAuthorization).where(PersonTeamStaffAuthorization.version == 2)))
    assert len(new) == 3
    assert {p.role_plan_id for p in new} == {latest_plan(db, result.person_id).id}


@pytest.mark.parametrize("revoked", ["admin", "coach"])
def test_distinct_role_and_team_authorizers_are_each_rechecked_and_otp_is_retryable(committed_db, revoked):
    from app.personnel_intake.errors import PersonnelIntakeError
    from app.personnel_intake.role_grants import ActivationRoleService
    from app.personnel_intake.schemas import ActivationRoleUpdate
    from app.rbac.models import UserRole
    from app.rbac.seed import ROLES
    from app.team_management.person_staff_schemas import PersonStaffReconfirmInput
    from app.team_management.person_staff_service import PersonStaffService
    db = committed_db
    users, result, _, _, service, token, code, challenge = prepared_staff(db)
    db.add(UserRole(user_id=users["accounts"]["coach"].id, role_id=ROLES["super_admin"].id))
    db.commit()
    ActivationRoleService(db, lookup=approvals(db).lookup).update(actor(users, "coach"), result.person_id,
        ActivationRoleUpdate(role_keys=["coach", "team_manager"], expected_version=1, idempotency_key=uuid4()))
    staff = PersonStaffService(db, lookup=approvals(db).lookup)
    for position in db.scalars(select(PersonTeamStaffPosition)).all():
        staff.reconfirm(actor(users, "admin"), position.id,
            PersonStaffReconfirmInput(expected_version=position.version, reason="分别核实岗位授权",
                                      idempotency_key=uuid4()))
    users["accounts"][revoked].status = "disabled"
    db.commit()
    with pytest.raises(PersonnelIntakeError):
        service.finish(token, code, SecretStr("SecurePass1"))
    assert db.scalar(select(UserAccount.id).where(UserAccount.person_id == result.person_id)) is None
    assert db.scalar(select(func.count()).select_from(TeamStaffAssignment)) == 0
    assert db.get(RegistrationVerificationChallenge, challenge, populate_existing=True).consumed_at is None
    users["accounts"][revoked].status = "active"
    db.commit()
    assert service.finish(token, code, SecretStr("SecurePass1")).completed
    assert db.scalar(select(func.count()).select_from(TeamStaffAssignment)) == 3
