from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.audit.service import AuditService
from app.auth.models import AuthSession, UserAccount
from app.auth.service import AuthContext
from app.housing.access import HousingAccessService
from app.housing.api_support import LockedHousingAuthorizationDenied
from app.housing.errors import HousingResourceNotFound
from app.housing.history_void_models import HousingStayVoidOperation
from app.housing.history_void_schemas import StayHistoryVoidRequest
from app.identity.models import Person
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES, seed_roles_and_permissions
from app.security.encryption import EnvelopeCipher, LookupDigest
from tests.db.test_housing_transfer_checkout import NOW, _account_person, _room, _stay

CIPHER = EnvelopeCipher(bytes(range(32)))
LOOKUP = LookupDigest(bytes(range(32)))


def setup_history(db: Session):
    seed_roles_and_permissions(db)
    person, account_id = _account_person(db, name="历史修正超管")
    db.add(UserRole(user_id=account_id, role_id=ROLES["super_admin"].id))
    resident, _ = _account_person(db, name="历史修正住户")
    room = _room(db)
    stay = _stay(db, person_id=resident.id, room=room, actor_id=account_id, status="checked_in")
    stay.status = "checked_out"
    stay.actual_check_out_at = NOW - timedelta(days=1)
    stay.checked_out_by_user_id = account_id
    db.flush()
    actor = AuthContext(
        person=person, account=db.get(UserAccount, account_id), session=AuthSession()
    )
    return actor, room, stay


def command(**changes):
    values = {
        "expected_version": 1,
        "reason": "历史导入重复记录",
        "confirmed": True,
        "idempotency_key": uuid4(),
    }
    values.update(changes)
    return StayHistoryVoidRequest(**values)


def service(db):
    from app.housing.history_void_service import HousingHistoryVoidService

    return HousingHistoryVoidService(
        db, CIPHER, LOOKUP, occurred_at=NOW, request_id="history-void-test"
    )


def counts(db, stay_id):
    return (
        db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.resource_id == stay_id,
                AuditEvent.outcome == "allowed",
                AuditEvent.action == "housing.stay.void_history",
            )
        ),
        db.scalar(
            select(func.count())
            .select_from(HousingStayVoidOperation)
            .where(HousingStayVoidOperation.stay_id == stay_id)
        ),
    )


def test_void_once_or_rolls_back(db):
    actor, room, stay = setup_history(db)
    other = _stay(db, person_id=stay.person_id, room=room, actor_id=actor.account.id)
    db.commit()

    def protected_facts():
        return tuple(
            db.execute(text(sql), {"person_id": stay.person_id, "other_stay_id": other.id})
            .scalars()
            .all()
            for sql in (
                "SELECT row_to_json(p)::text FROM people p WHERE id=:person_id",
                "SELECT row_to_json(a)::text FROM user_accounts a WHERE person_id=:person_id",
                "SELECT row_to_json(s)::text FROM housing_stays s WHERE id=:other_stay_id",
            )
        )

    protected_before = protected_facts()
    original = (stay.person_id, stay.room_id, stay.status, stay.actual_check_out_at)
    cmd = command()
    first = service(db).void(actor, stay.id, cmd)
    replay = service(db).void(
        actor, stay.id, command(idempotency_key=cmd.idempotency_key, reason="  历史导入重复记录 \n")
    )
    assert first == replay
    assert first.stay_id == stay.id and first.room_id == room.id and first.version == 2
    allowed_audits, operations = counts(db, stay.id)
    assert allowed_audits == operations == 1
    db.refresh(stay)
    assert (stay.person_id, stay.room_id, stay.status, stay.actual_check_out_at) == original
    assert stay.version == 2 and stay.voided_by_user_id == actor.account.id
    assert (
        CIPHER.decrypt(stay.void_reason_ciphertext, field_name="housing_stay_void_reason")
        == "历史导入重复记录"
    )
    audit = db.scalar(select(AuditEvent).where(AuditEvent.resource_id == stay.id))
    assert "历史导入" not in str(audit.event_metadata)
    operation = db.scalar(
        select(HousingStayVoidOperation).where(HousingStayVoidOperation.stay_id == stay.id)
    )
    assert "历史导入" not in str(operation.__dict__)
    assert protected_facts() == protected_before


@pytest.mark.parametrize("failure", ["audit", "credential"])
def test_void_failure_rolls_back_every_fact(db, monkeypatch, failure):
    from sqlalchemy import event

    actor, _, stay = setup_history(db)
    db.commit()
    state_before = (
        stay.voided_at,
        stay.voided_by_user_id,
        stay.void_reason_ciphertext,
        stay.version,
        counts(db, stay.id),
    )

    def fail(*args, **kwargs):
        raise RuntimeError("injected persistence failure")

    if failure == "audit":
        monkeypatch.setattr(AuditService, "append", fail)
    else:
        event.listen(HousingStayVoidOperation, "before_insert", fail)
    try:
        with pytest.raises(RuntimeError, match="injected persistence failure"):
            service(db).void(actor, stay.id, command())
    finally:
        if failure == "credential":
            event.remove(HousingStayVoidOperation, "before_insert", fail)
    db.refresh(stay)
    state_after = (
        stay.voided_at,
        stay.voided_by_user_id,
        stay.void_reason_ciphertext,
        stay.version,
        counts(db, stay.id),
    )
    assert state_after == state_before


@pytest.mark.parametrize("role", ["admin", "administration", "coach", "team_manager", None])
def test_void_requires_live_superadmin_even_with_explicit_grant(db, role):
    actor, _, stay = setup_history(db)
    db.execute(delete(UserRole).where(UserRole.user_id == actor.account.id))
    if role:
        db.add(UserRole(user_id=actor.account.id, role_id=ROLES[role].id))
    # Deliberately grant both visibility and the sensitive permission: role is mandatory.
    for permission in ("housing.read", "housing.history.void"):
        db.add(
            UserPermissionOverride(
                user_id=actor.account.id,
                permission_id=PERMISSIONS[permission].id,
                effect="allow",
                scope_kind="global",
            )
        )
    db.commit()
    with pytest.raises((LockedHousingAuthorizationDenied, HousingResourceNotFound)):
        service(db).void(actor, stay.id, command())
    assert counts(db, stay.id) == (0, 0)


@pytest.mark.parametrize("revoke", ["role", "disabled", "hidden", "permission", "password"])
def test_void_requires_live_superadmin_even_on_replay(db, revoke):
    actor, room, stay = setup_history(db)
    db.commit()
    cmd = command()
    service(db).void(actor, stay.id, cmd)
    if revoke == "role":
        db.execute(delete(UserRole).where(UserRole.user_id == actor.account.id))
    elif revoke == "disabled":
        actor.account.status = "disabled"
    elif revoke == "password":
        actor.account.force_password_change = True
    else:
        db.add(
            UserPermissionOverride(
                user_id=actor.account.id,
                permission_id=PERMISSIONS[
                    "housing.read" if revoke == "hidden" else "housing.history.void"
                ].id,
                effect="deny",
                scope_kind="floor",
                scope_id=room.floor_id,
            )
        )
    db.commit()
    with pytest.raises((LockedHousingAuthorizationDenied, HousingResourceNotFound)):
        service(db).void(actor, stay.id, cmd)
    assert counts(db, stay.id) == (1, 1)


@pytest.mark.parametrize("status", ["planned", "checked_in", "checkout_pending", "cancelled"])
def test_nonhistorical_lifecycle_cannot_be_voided(db, status):
    from app.housing.errors import HousingInvalidStayTransition

    actor, _, stay = setup_history(db)
    stay.status = status
    stay.actual_check_out_at = None
    stay.checked_out_by_user_id = None
    if status in {"planned", "cancelled"}:
        stay.actual_check_in_at = None
        stay.checked_in_by_user_id = None
    if status == "cancelled":
        stay.cancelled_by_user_id = actor.account.id
        stay.cancellation_reason = "取消测试"
    db.commit()
    with pytest.raises(HousingInvalidStayTransition):
        service(db).void(actor, stay.id, command())
    assert counts(db, stay.id) == (0, 0)


def test_stale_version_and_new_key_after_void_are_conflicts_without_audits(db):
    from app.housing.errors import HousingServiceError, HousingStaleVersion

    actor, _, stay = setup_history(db)
    db.commit()
    with pytest.raises(HousingStaleVersion):
        service(db).void(actor, stay.id, command(expected_version=2))
    service(db).void(actor, stay.id, command())
    before = db.scalar(select(func.count()).select_from(AuditEvent))
    with pytest.raises(HousingServiceError) as caught:
        service(db).void(actor, stay.id, command(expected_version=2))
    assert caught.value.code == "housing_stay_already_voided"
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == before
    assert counts(db, stay.id) == (1, 1)


@pytest.mark.parametrize("changed", ["target", "version", "reason"])
def test_same_key_changed_content_conflicts(db, changed):
    from app.housing.errors import HousingServiceError

    actor, room, stay = setup_history(db)
    other = _stay(
        db, person_id=stay.person_id, room=room, actor_id=actor.account.id, status="checked_in"
    )
    other.status = "checked_out"
    other.actual_check_out_at = NOW - timedelta(days=1)
    other.checked_out_by_user_id = actor.account.id
    db.commit()
    cmd = command()
    service(db).void(actor, stay.id, cmd)
    target = other.id if changed == "target" else stay.id
    update = {"expected_version": 2} if changed == "version" else {}
    if changed == "reason":
        update["reason"] = SecretStr("不同修正理由")
    with pytest.raises(HousingServiceError) as caught:
        service(db).void(actor, target, cmd.model_copy(update=update))
    assert caught.value.code == "housing_void_operation_conflict"
    assert counts(db, stay.id) == (1, 1)


def test_reason_rejects_target_exact_passport_without_echo(db):
    from app.housing.errors import HousingServiceError

    actor, _, stay = setup_history(db)
    resident = db.get(Person, stay.person_id)
    resident.document_type = "passport"
    resident.document_issuing_country = "US"
    resident.passport_number_digest = LOOKUP.digest("PZ84Q72K")
    resident.identity_ciphertext = CIPHER.encrypt("PZ84Q72K", field_name="identity_number")
    db.commit()
    with pytest.raises(HousingServiceError) as caught:
        service(db).void(actor, stay.id, command(reason="错误导入 PZ84Q72K 已核对"))
    assert caught.value.code == "housing_void_reason_invalid"
    assert "PZ84Q72K" not in str(caught.value)
    assert counts(db, stay.id) == (0, 0)


@pytest.mark.parametrize("number", ["A1234563", "a123456(3)", "A 123456 （3）"])
def test_reason_rejects_target_hkid_variants_without_echo(db, number):
    from app.housing.errors import HousingServiceError

    actor, _, stay = setup_history(db)
    resident = db.get(Person, stay.person_id)
    resident.document_type = "hk_id"
    resident.identity_digest = LOOKUP.digest_domain("hk-identity", "A1234563")
    resident.identity_ciphertext = CIPHER.encrypt("A1234563", field_name="identity_number")
    db.commit()
    with pytest.raises(HousingServiceError) as caught:
        service(db).void(actor, stay.id, command(reason=f"错误导入 {number} 已核对"))
    assert caught.value.code == "housing_void_reason_invalid"
    assert number not in str(caught.value)
    assert counts(db, stay.id) == (0, 0)


def test_history_void_permission_changes_access_revision(db):
    from datetime import UTC, datetime

    from app.housing.history_void_access import can_void_history

    actor, room, stay = setup_history(db)
    db.commit()
    before = HousingAccessService(db, actor.account.id, datetime.now(UTC)).access_revision
    assert can_void_history(db, actor, room, stay)
    restriction = UserPermissionOverride(
        user_id=actor.account.id,
        permission_id=PERMISSIONS["housing.history.void"].id,
        effect="deny",
        scope_kind="floor",
        scope_id=room.floor_id,
    )
    db.add(restriction)
    db.commit()
    after = HousingAccessService(db, actor.account.id, datetime.now(UTC)).access_revision
    assert after != before
    assert not can_void_history(db, actor, room, stay)
    db.delete(restriction)
    db.commit()
    assert HousingAccessService(db, actor.account.id, datetime.now(UTC)).access_revision == before
    assert can_void_history(db, actor, room, stay)
