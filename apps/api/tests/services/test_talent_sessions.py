from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, update

from app.audit.models import AuditEvent
from app.auth.models import UserAccount
from app.rbac.models import Permission, UserPermissionOverride, UserRole
from app.rbac.seed import ROLES
from app.talent_tests.catalog import CURRENT_CATALOG_VERSION, LEGACY_TEST_IDS, TEST_IDS
from app.talent_tests.errors import (
    TalentConflict,
    TalentForbidden,
    TalentNotFound,
    TalentTestError,
)
from app.talent_tests.models import TalentTestReport, TalentTestResult, TalentTestSession
from tests.services.talent_helpers import (
    seed_legacy_session,
    student_actor,
    submission,
)

NOW = datetime(2026, 9, 20, 8, tzinfo=UTC)


def service(db):
    from app.talent_tests.service import TalentTestService

    return TalentTestService(db)


def start(svc, actor, key=None, catalog=CURRENT_CATALOG_VERSION):
    return svc.start(actor=actor, request_id=key or uuid4(), catalog_version=catalog, now=NOW)


@pytest.mark.parametrize("terminal", ["abandoned", "completed"])
def test_legacy_creation_key_replays_after_upgrade(db, terminal):
    actor, key = student_actor(db), uuid4()
    svc = service(db)
    first = seed_legacy_session(db, actor, key, NOW)
    if terminal == "abandoned":
        svc.abandon(actor=actor, session_id=first.id, request_id=uuid4(), now=NOW)
    else:
        for test_id in LEGACY_TEST_IDS:
            svc.submit(actor=actor, session_id=first.id, value=submission(test_id), now=NOW)
    newer, _ = start(svc, actor)
    replay, created = start(svc, actor, key, catalog="talent-training-v1")
    assert replay.id == first.id and replay.status == terminal
    assert newer.id != replay.id and created is False
    assert (replay.report_id is not None) == (terminal == "completed")


@pytest.mark.parametrize("catalog_version", ["talent-training-v1", "talent-training-v2"])
def test_fresh_legacy_catalog_rejected_without_writes(db, catalog_version):
    actor, svc = student_actor(db), service(db)
    baseline = {
        "sessions": db.scalar(select(func.count()).select_from(TalentTestSession)),
        "results": db.scalar(select(func.count()).select_from(TalentTestResult)),
        "reports": db.scalar(select(func.count()).select_from(TalentTestReport)),
        "audits": db.scalar(select(func.count()).select_from(AuditEvent)),
    }

    with pytest.raises(TalentConflict) as error:
        start(svc, actor, catalog=catalog_version)

    assert error.value.code == "talent_catalog_updated"
    assert {
        "sessions": db.scalar(select(func.count()).select_from(TalentTestSession)),
        "results": db.scalar(select(func.count()).select_from(TalentTestResult)),
        "reports": db.scalar(select(func.count()).select_from(TalentTestReport)),
        "audits": db.scalar(select(func.count()).select_from(AuditEvent)),
    } == baseline


def test_legacy_session_rejects_dynamic_without_consuming_submission_key(db):
    actor, svc = student_actor(db), service(db)
    current = seed_legacy_session(db, actor, uuid4(), NOW)
    dynamic = submission("dynamic-vision")
    baseline = {
        "results": db.scalar(select(func.count()).select_from(TalentTestResult)),
        "reports": db.scalar(select(func.count()).select_from(TalentTestReport)),
        "audits": db.scalar(select(func.count()).select_from(AuditEvent)),
    }

    with pytest.raises(TalentTestError):
        svc.submit(
            actor=actor,
            session_id=current.id,
            value=dynamic,
            now=NOW,
        )

    assert {
        "results": db.scalar(select(func.count()).select_from(TalentTestResult)),
        "reports": db.scalar(select(func.count()).select_from(TalentTestReport)),
        "audits": db.scalar(select(func.count()).select_from(AuditEvent)),
    } == baseline
    saved, created = svc.submit(
        actor=actor,
        session_id=current.id,
        value=submission("reactiontime", dynamic.submission_id),
        now=NOW,
    )
    assert created is True
    assert saved.result.submission_id == dynamic.submission_id


def test_active_and_changed_creation_key_conflicts_do_not_poison_session(db):
    from app.talent_tests.errors import TalentConflict

    actor, key = student_actor(db), uuid4()
    svc = service(db)
    first, _ = start(svc, actor, key)
    with pytest.raises(TalentConflict) as error:
        start(svc, actor)
    assert error.value.active_session_id == first.id
    with pytest.raises(TalentConflict):
        svc.start(actor=actor, request_id=key, catalog_version="changed", now=NOW)
    assert db.scalar(select(1)) == 1


@pytest.mark.parametrize("terminal", ["abandoned", "completed"])
def test_successful_submission_replay_survives_terminal(db, terminal):
    from app.talent_tests.errors import TalentConflict

    actor, svc = student_actor(db), service(db)
    first, _ = start(svc, actor)
    value = submission("reactiontime")
    saved, created = svc.submit(actor=actor, session_id=first.id, value=value, now=NOW)
    assert created and saved.result.score == 60
    if terminal == "abandoned":
        svc.abandon(actor=actor, session_id=first.id, request_id=uuid4(), now=NOW)
    else:
        for test_id in TEST_IDS[1:]:
            svc.submit(actor=actor, session_id=first.id, value=submission(test_id), now=NOW)
    replay, created = svc.submit(actor=actor, session_id=first.id, value=value, now=NOW)
    assert not created and replay.result.id == saved.result.id
    assert replay.session.status == terminal
    with pytest.raises(TalentConflict):
        svc.submit(actor=actor, session_id=first.id, value=submission("reactiontime"), now=NOW)


def test_abandon_replay_has_one_audit_and_new_key_conflicts(db):
    from app.talent_tests.errors import TalentConflict

    actor, svc, key = student_actor(db), service(db), uuid4()
    first, _ = start(svc, actor)
    for _ in range(2):
        assert (
            svc.abandon(actor=actor, session_id=first.id, request_id=key, now=NOW).status
            == "abandoned"
        )
    with pytest.raises(TalentConflict):
        svc.abandon(actor=actor, session_id=first.id, request_id=uuid4(), now=NOW)
    assert (
        db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.resource_id == first.id, AuditEvent.action == "talent_tests.abandoned"
            )
        )
        == 1
    )


@pytest.mark.parametrize("revocation", ["role", "account", "read_deny", "take_deny"])
def test_replay_rechecks_current_authorization(db, revocation):
    actor, svc, key = student_actor(db), service(db), uuid4()
    first, _ = start(svc, actor, key)
    value = submission("reactiontime")
    svc.submit(actor=actor, session_id=first.id, value=value, now=NOW)
    if revocation == "role":
        db.execute(delete(UserRole).where(UserRole.user_id == actor.account.id))
    elif revocation == "account":
        db.execute(
            update(UserAccount).where(UserAccount.id == actor.account.id).values(status="disabled")
        )
    else:
        permission = revocation.removesuffix("_deny")
        permission_id = db.scalar(
            select(Permission.id).where(Permission.key == f"talent_tests.{permission}")
        )
        db.add(
            UserPermissionOverride(
                user_id=actor.account.id,
                permission_id=permission_id,
                effect="deny",
                scope_kind="self",
            )
        )
    db.commit()
    with pytest.raises(TalentForbidden):
        start(svc, actor, key)
    with pytest.raises(TalentForbidden):
        svc.submit(actor=actor, session_id=first.id, value=value, now=NOW)
    with pytest.raises(TalentForbidden):
        svc.abandon(actor=actor, session_id=first.id, request_id=uuid4(), now=NOW)


def test_other_actor_cannot_write_even_if_can_read(db):
    from app.talent_tests.queries import TalentQueries

    actor, other, svc = student_actor(db), student_actor(db), service(db)
    first, _ = start(svc, actor)
    db.add(UserRole(user_id=other.account.id, role_id=ROLES["super_admin"].id))
    db.commit()
    assert TalentQueries(db, other, NOW).session(first.id).id == first.id
    with pytest.raises(TalentNotFound):
        svc.submit(actor=other, session_id=first.id, value=submission("reactiontime"), now=NOW)
    with pytest.raises(TalentNotFound):
        svc.abandon(actor=other, session_id=first.id, request_id=uuid4(), now=NOW)


@pytest.mark.parametrize(
    ("version", "required"),
    [("talent-training-v1", 7), ("talent-training-v2", 8), ("talent-training-v3", 8)],
)
@pytest.mark.parametrize("dynamic_position", ["first", "last"])
def test_completion_uses_its_frozen_catalog(db, version, required, dynamic_position):
    actor, svc = student_actor(db), service(db)
    if version != CURRENT_CATALOG_VERSION:
        current = seed_legacy_session(db, actor, uuid4(), NOW, catalog_version=version)
    else:
        current, _ = start(svc, actor, catalog=version)
    ordered = list(current.rules.test_ids)
    if "dynamic-vision" in ordered and dynamic_position == "first":
        ordered.remove("dynamic-vision")
        ordered.insert(0, "dynamic-vision")

    for index, test_id in enumerate(ordered, 1):
        saved, _ = svc.submit(
            actor=actor,
            session_id=current.id,
            value=submission(test_id, catalog_version=version),
            now=NOW,
        )
        assert saved.session.status == ("completed" if index == required else "in_progress")
        assert (saved.session.report_id is not None) is (index == required)

    report = db.get(TalentTestReport, saved.session.report_id)
    assert report.report_snapshot["person"]["name"] == "测试学员"
    assert report.report_snapshot["rules"]["catalog_version"] == version
    if version == "talent-training-v3":
        assert report.report_snapshot["rules"]["tests"]["dynamic-vision"]["max_score"] == 100
    assert (
        db.scalar(
            select(func.count())
            .select_from(TalentTestResult)
            .where(TalentTestResult.session_id == current.id)
        )
        == required
    )


def test_invalid_submission_does_not_reserve_key_and_changed_body_conflicts(db):
    from pydantic import ValidationError

    from app.talent_tests.errors import TalentConflict

    actor, svc = student_actor(db), service(db)
    current, _ = start(svc, actor)
    value = submission("reactiontime")
    invalid = value.model_copy(update={"engine_version": "wrong"})
    with pytest.raises((ValidationError, ValueError)):
        svc.submit(actor=actor, session_id=current.id, value=invalid, now=NOW)
    saved, created = svc.submit(actor=actor, session_id=current.id, value=value, now=NOW)
    assert created and len(saved.session.results) == 1
    changed = value.model_copy(update={"duration_ms": 12000})
    with pytest.raises(TalentConflict):
        svc.submit(actor=actor, session_id=current.id, value=changed, now=NOW)
    assert db.scalar(select(1)) == 1
