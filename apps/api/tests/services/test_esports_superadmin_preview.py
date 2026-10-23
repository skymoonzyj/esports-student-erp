"""Closed-suite preview remains live-role-bound and keeps normal write guards."""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from app.audit.models import AuditEvent
from app.esports_assessments import catalog
from app.esports_assessments.errors import AssessmentForbidden, AssessmentUnavailable
from app.esports_assessments.formal_service import FormalAssessmentService
from app.esports_assessments.models import (
    AssessmentAttempt,
    AssessmentPracticeRun,
    AssessmentReport,
    AssessmentResult,
    AssessmentSession,
)
from app.esports_assessments.practice_service import PracticeAssessmentService
from app.esports_assessments.queries import AssessmentQueries
from app.rbac.models import UserRole
from app.rbac.seed import ROLES
from tests.api import test_esports_access as access_fixtures
from tests.api.test_esports_access import NOW, grant
from tests.esports_helpers import attempt_request, result_request, session_request
from tests.services.test_esports_practice import practice_request

esports_people = access_fixtures.esports_people
SUITES = ("pc-fps", "mobile-fps", "pc-moba", "mobile-moba")


@pytest.fixture(autouse=True)
def closed_suites(monkeypatch):
    monkeypatch.setattr(catalog, "AVAILABLE_SUITES", frozenset())


def counts(db):
    return tuple(
        db.scalar(select(func.count()).select_from(model))
        for model in (
            AssessmentSession,
            AssessmentAttempt,
            AssessmentResult,
            AssessmentReport,
            AssessmentPracticeRun,
            AuditEvent,
        )
    )


@pytest.mark.parametrize("suite_id", SUITES)
def test_closed_suite_superadmin_can_complete_own_formal_report_and_practice(
    db, esports_people, suite_id
):
    actor = esports_people[1]["super_admin"]
    queries = AssessmentQueries(db, actor, NOW)
    context = queries.context()
    assert context.can_take and context.can_read_self
    assert context.available_suites == SUITES
    assert tuple(item.suite_id for item in queries.suites().items) == SUITES

    formal = FormalAssessmentService(db)
    session = formal.create(actor, session_request(suite_id), NOW)
    stored = db.get(AssessmentSession, session.id)
    assert stored.person_id == actor.person.id
    assert stored.created_by_user_id == actor.account.id
    for index, test_id in enumerate(catalog.get_suite(suite_id).test_ids):
        at = NOW + timedelta(seconds=index * 64)
        run = formal.start_attempt(actor, session.id, attempt_request(suite_id, test_id), at)
        saved = formal.submit(
            actor,
            run.id,
            result_request(
                suite_id,
                test_id,
                seed=run.seed,
                catalog_version=run.config.catalog_version,
            ),
            at + timedelta(seconds=63),
        )
    assert saved.session is not None and saved.session.report_id is not None
    report = queries.report(saved.session.report_id)
    assert report.person.person_id == actor.person.id
    assert queries.history("formal").visible_total == 1

    practice = PracticeAssessmentService(db)
    test_id = catalog.get_suite(suite_id).test_ids[0]
    at = NOW + timedelta(minutes=5)
    run = practice.start(actor, practice_request(suite_id, test_id), at)
    saved_practice = practice.submit(
        actor,
        run.id,
        result_request(
            suite_id,
            test_id,
            seed=run.seed,
            catalog_version=run.config.catalog_version,
        ),
        at + timedelta(seconds=63),
    )
    assert saved_practice.scored.score is saved_practice.scored.grade is None
    assert saved_practice.session is None
    row = db.get(AssessmentPracticeRun, run.id)
    assert row.person_id == actor.person.id and row.created_by_user_id == actor.account.id
    assert db.scalar(select(func.count()).select_from(AssessmentReport)) == 1
    assert not catalog.AVAILABLE_SUITES


@pytest.mark.parametrize("role", ["student", "admin", "global_reader"])
def test_non_superadmin_cannot_start_closed_suite_even_with_global_read_and_take(
    db, esports_people, monkeypatch, role
):
    actor = esports_people[1]["other" if role == "global_reader" else role]
    if role != "student":
        grant(db, actor)
        grant(db, actor, "esports_assessments.take", scope="self")
    # A pre-existing round must not turn the start-attempt endpoint into a bypass.
    monkeypatch.setattr(catalog, "AVAILABLE_SUITES", frozenset({"pc-fps"}))
    formal = FormalAssessmentService(db)
    session = formal.create(actor, session_request(), NOW)
    monkeypatch.setattr(catalog, "AVAILABLE_SUITES", frozenset())
    queries = AssessmentQueries(db, actor, NOW)
    assert queries.context().can_take
    assert queries.context().available_suites == ()
    assert queries.suites().items == ()
    before = counts(db)
    for action in (
        lambda: formal.create(actor, session_request("pc-moba"), NOW),
        lambda: formal.start_attempt(
            actor, session.id, attempt_request("pc-fps", "precision"), NOW
        ),
        lambda: PracticeAssessmentService(db).start(actor, practice_request(), NOW),
    ):
        with pytest.raises(AssessmentUnavailable):
            action()
        assert counts(db) == before


@pytest.mark.parametrize("role", ["admin", "other"])
def test_no_take_permission_is_not_replaced_by_preview_or_global_read(db, esports_people, role):
    actor = esports_people[1][role]
    if role == "other":
        grant(db, actor)
    queries = AssessmentQueries(db, actor, NOW)
    assert not queries.context().can_take
    assert queries.context().available_suites == ()
    assert queries.suites().items == ()
    before = counts(db)
    with pytest.raises(AssessmentForbidden):
        FormalAssessmentService(db).create(actor, session_request(), NOW)
    with pytest.raises(AssessmentForbidden):
        PracticeAssessmentService(db).start(actor, practice_request(), NOW)
    assert counts(db) == before


@pytest.mark.parametrize("revocation", ["deny_take", "deny_read", "disabled", "role_removed"])
def test_preview_revocation_is_live_and_blocks_fresh_writes_and_idempotent_replays(
    db, esports_people, revocation
):
    actor = esports_people[1]["super_admin"]
    queries = AssessmentQueries(db, actor, NOW)
    formal = FormalAssessmentService(db)
    practice = PracticeAssessmentService(db)
    create = session_request()
    attempt = attempt_request("pc-fps", "precision")
    practice_create = practice_request()
    session = formal.create(actor, create, NOW)
    run = formal.start_attempt(actor, session.id, attempt, NOW)
    formal.interrupt(actor, run.id, uuid4(), NOW + timedelta(seconds=1))
    practice.start(actor, practice_create, NOW + timedelta(seconds=2))
    assert queries.context().available_suites == SUITES

    if revocation.startswith("deny_"):
        permission = (
            "esports_assessments.take" if revocation == "deny_take" else "esports_assessments.read"
        )
        grant(db, actor, permission, effect="deny", scope="self")
    elif revocation == "disabled":
        actor.account.status = "disabled"
        db.commit()
    else:
        db.execute(delete(UserRole).where(UserRole.user_id == actor.account.id))
        db.commit()
    assert not queries.context().can_take
    assert queries.context().available_suites == ()
    assert queries.suites().items == ()
    before = counts(db)
    for action in (
        lambda: formal.create(actor, create, NOW),
        lambda: formal.create(actor, session_request("pc-moba"), NOW),
        lambda: formal.start_attempt(actor, session.id, attempt, NOW),
        lambda: formal.start_attempt(
            actor, session.id, attempt_request("pc-fps", "precision"), NOW
        ),
        lambda: practice.start(actor, practice_create, NOW),
        lambda: practice.start(actor, practice_request(), NOW),
    ):
        with pytest.raises(AssessmentForbidden):
            action()
        assert counts(db) == before


def test_removed_superadmin_role_cannot_preview_with_surviving_take_and_global_read(
    db, esports_people
):
    actor = esports_people[1]["super_admin"]
    queries = AssessmentQueries(db, actor, NOW)
    assert queries.context().available_suites == SUITES
    grant(db, actor)
    grant(db, actor, "esports_assessments.take", scope="self")
    db.execute(
        delete(UserRole).where(
            UserRole.user_id == actor.account.id, UserRole.role_id == ROLES["super_admin"].id
        )
    )
    db.commit()
    assert queries.context().can_take
    assert queries.context().available_suites == ()
    assert queries.suites().items == ()
    with pytest.raises(AssessmentUnavailable):
        FormalAssessmentService(db).create(actor, session_request(), NOW)
