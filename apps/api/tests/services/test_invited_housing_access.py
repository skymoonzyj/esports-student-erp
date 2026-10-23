from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.core.errors import ProblemError
from app.rbac.models import UserPermissionOverride
from app.rbac.seed import PERMISSIONS
from tests.invited_housing_helpers import NOW, account, binding, code, manager, organization


def access(db):
    from app.referrals.access import InvitedHousingAccess

    return InvitedHousingAccess(db)


def args(actor):
    return {"user_id": actor.id, "actor_person_id": actor.person_id, "as_of": NOW}


def test_pending_registration_and_unbound_people_are_not_invitees(db: Session):
    from app.registration.models import RegistrationApplication
    from tests.invited_housing_helpers import person

    actor = account(db)
    invitation = code(db, actor)
    person(db, student=True)
    db.add(
        RegistrationApplication(
            primary_purpose="student",
            state="draft",
            referral_code_id=invitation.id,
            expires_at=NOW + timedelta(days=1),
        )
    )
    db.flush()
    assert access(db).has_sources(**args(actor))
    assert people(db, actor) == []


def people(db, actor, **kwargs):
    statement = access(db).authorized_people_statement(
        **args(actor),
        source_kind=kwargs.get("source_kind", "all"),
        organization_id=kwargs.get("organization_id"),
    )
    return list(db.execute(statement).mappings())


def override(db, actor, effect, scope_kind, scope_id=None, **kwargs):
    db.add(
        UserPermissionOverride(
            user_id=actor.id,
            permission_id=PERMISSIONS["referrals.read_invited_housing"].id,
            effect=effect,
            scope_kind=scope_kind,
            scope_id=scope_id,
            **kwargs,
        )
    )
    db.flush()


def test_relationship_intersection_ignores_global_and_person_allow(db: Session):
    actor, other = account(db), account(db)
    own, _, invitation = binding(db, actor)
    outsider, _, _ = binding(db, other)
    invitation.is_active = False
    override(db, actor, "allow", "global")
    override(db, actor, "allow", "person", outsider.id)
    assert [row["student_person_id"] for row in people(db, actor)] == [own.id]
    assert access(db).has_sources(**args(actor))


@pytest.mark.parametrize("scope", ["global", "self"])
def test_feature_deny_blocks_sources_and_query(db: Session, scope):
    actor = account(db)
    binding(db, actor)
    override(db, actor, "deny", scope)
    assert not access(db).has_sources(**args(actor))
    with pytest.raises(ProblemError) as error:
        people(db, actor)
    assert error.value.status == 403


def test_person_deny_and_deleted_people_are_removed_before_query(db: Session):
    actor = account(db)
    denied, _, _ = binding(db, actor)
    deleted, _, _ = binding(db, actor)
    visible, _, _ = binding(db, actor)
    deleted.deleted_at = NOW
    override(db, actor, "deny", "person", denied.id)
    assert [row["student_person_id"] for row in people(db, actor)] == [visible.id]


def test_two_orgs_two_managers_and_live_specific_revocation(db: Session):
    actor, other, creator = account(db, "student"), account(db), account(db, "super_admin")
    first, second = organization(db, creator), organization(db, creator)
    manager(db, first, actor)
    second_mapping = manager(db, second, actor)
    manager(db, second, other)
    a, _, _ = binding(db, creator, first)
    b, _, _ = binding(db, creator, second)
    assert {r["student_person_id"] for r in people(db, actor)} == {a.id, b.id}
    assert {r["student_person_id"] for r in people(db, other)} == {b.id}
    assert not access(db).has_sources(**args(creator))
    second_mapping.revoked_at = NOW
    second_mapping.revoked_by_user_id = creator.id
    db.flush()
    assert {r["student_person_id"] for r in people(db, actor)} == {a.id}
    for org_id in (second.id, uuid4()):
        with pytest.raises(ProblemError) as error:
            people(db, actor, source_kind="organization", organization_id=org_id)
        assert error.value.status == 404
    first.is_active = False
    db.flush()
    assert not access(db).has_sources(**args(actor))


@pytest.mark.parametrize("change", ["disabled", "deleted"])
def test_current_actor_state_is_revalidated(db: Session, change):
    from app.identity.models import Person

    actor = account(db)
    code(db, actor)
    assert access(db).has_sources(**args(actor))
    if change == "disabled":
        actor.status = "disabled"
    else:
        db.get(Person, actor.person_id).deleted_at = NOW
    db.flush()
    assert not access(db).has_sources(**args(actor))


def test_empty_sources_do_not_require_completed_invitees(db: Session):
    actor = account(db)
    assert not access(db).has_sources(**args(actor))
    code(db, actor)
    assert access(db).has_sources(**args(actor))
    assert people(db, actor) == []
    assert [s.source_kind for s in access(db).list_sources(**args(actor))] == ["person"]


def test_expired_person_deny_does_not_remove_bound_student(db: Session):
    actor = account(db)
    student, _, _ = binding(db, actor)
    override(
        db,
        actor,
        "deny",
        "person",
        student.id,
        valid_from=NOW - timedelta(days=1),
        valid_until=NOW - timedelta(seconds=1),
    )
    assert [r["student_person_id"] for r in people(db, actor)] == [student.id]
