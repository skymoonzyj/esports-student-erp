"""Exact rational SQL ranking, not rounded presentation scores."""

import importlib
import importlib.util
import json
import random
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest


def ranking_module():
    assert importlib.util.find_spec("app.reactionx.ranking") is not None
    return importlib.import_module("app.reactionx.ranking")


def test_embedding_matches_crossproducts_including_ties_maximum_and_closest_gaps():
    key = ranking_module().exact_order_key
    pairs = [
        (1, 3, 2, 6),
        (9998, 9999, 9999, 10000),
        (9007199254740991, 10000, 9007199254740990, 10000),
        (9007199254740991, 1, 9007199254740990, 1),
    ]
    rng = random.Random(4291)
    pairs.extend(
        (
            rng.randrange(9007199254740992),
            rng.randint(1, 10000),
            rng.randrange(9007199254740992),
            rng.randint(1, 10000),
        )
        for _ in range(2000)
    )
    for a, b, c, d in pairs:
        cross = a * d - c * b
        order = key(a, b) - key(c, d)
        assert (order > 0) - (order < 0) == (cross > 0) - (cross < 0)


@pytest.mark.parametrize(
    "numerator,denominator", [(-1, 1), (1, 0), (1, 10001), (9007199254740992, 1), (True, 1)]
)
def test_unproven_rational_domain_fails_closed(numerator, denominator):
    with pytest.raises(ValueError):
        ranking_module().exact_order_key(numerator, denominator)


def history(db, owner, connector, subject, *, n=300, d=1, saved=None, result_id=None):
    from app.reactionx.metrics import calculate_metrics
    from app.reactionx.models import ReactionxResultReceipt, ReactionxRun
    from app.reactionx.schemas import ResultEnvelope, RunManifest, canonical_digest

    fixtures = json.loads(
        (Path(__file__).parents[4] / "shared/reactionx/v1/fixtures.json").read_text()
    )
    case = deepcopy(next(c for c in fixtures["cases"] if c["name"] == "standard"))
    run_id = uuid4()
    manifest = case["manifest"]
    manifest.update(
        run_id=str(run_id),
        actor_account_id=str(owner.account.id),
        subject_person_id=str(subject),
        connector_id=str(connector.id),
    )
    manifest["manifest_digest"] = canonical_digest(manifest, exclude={"manifest_digest"})
    body = case["result"]
    body.update(run_id=str(run_id), manifest_digest=manifest["manifest_digest"])
    metrics = calculate_metrics(
        RunManifest.model_validate(manifest),
        ResultEnvelope.model_validate(body).events,
        ResultEnvelope.model_validate(body).end,
    ).model_dump(mode="json")
    metrics["dimensions"][0]["numerator"] = 0
    metrics["dimensions"][1].update(numerator=n, denominator=d)
    metrics["values"].update(miss_count=0, mean_hit_ms=n / d)
    now = saved or datetime.now(UTC)
    run = ReactionxRun(
        id=run_id,
        actor_account_id=owner.account.id,
        subject_person_id=subject,
        connector_id=connector.id,
        browser_session_id=owner.session.id,
        browser_tab_id=uuid4(),
        manifest=manifest,
        manifest_digest=manifest["manifest_digest"],
        lease_expires_at=now,
        status="completed",
        last_sequence=0,
        stop_confirmed=True,
    )
    receipt = ReactionxResultReceipt(
        run_id=run_id,
        result_id=result_id or uuid4(),
        digest=canonical_digest(body),
        envelope=body,
        metrics=metrics,
        eligible=True,
        ranking_reason=None,
        comparison_key=metrics["comparison_key"],
        saved_at=now,
        rank_n1=0,
        rank_d1=1,
        rank_sign1=1,
        rank_n2=n,
        rank_d2=d,
        rank_sign2=1,
    )
    db.add(run)
    db.flush()
    db.add(receipt)
    db.flush()
    return run, receipt


def subject(db):
    from app.identity.models import Person

    person = Person(
        full_name="synthetic-ranking-person",
        identity_ciphertext="synthetic",
        identity_digest=uuid4().hex,
        sex="male",
        date_of_birth=date(2000, 1, 1),
    )
    db.add(person)
    db.flush()
    return person


def test_person_best_one_seat_current_excludes_own_better_and_ties_are_anonymous(db):
    module = ranking_module()
    assert hasattr(module, "ReactionxRankingService")
    from app.reactionx.access import ReactionxAccess
    from tests.services.test_reactionx_commands import seed

    owner, connector = seed(db)
    second_owner, second_connector = seed(db)
    target_person, peer, third = subject(db), subject(db), subject(db)
    _, target = history(db, owner, connector, target_person.id, n=300)
    history(db, second_owner, second_connector, target_person.id, n=200)
    history(db, owner, connector, peer.id, n=400, d=2)
    history(db, owner, connector, third.id, n=250)
    view = module.ReactionxRankingService(db, ReactionxAccess(db, owner)).for_result(
        target.result_id
    )
    assert view.participant_count == 3
    assert view.current.rank == 3 and view.best.rank == 1
    assert [r.rank for r in view.top_three] == [1, 1, 3]
    assert [r.tie_count for r in view.top_three] == [2, 2, 1]
    assert sum(r.is_self for r in view.top_three) == 1
    assert all(
        set(r.model_dump()) == {"rank", "values", "is_self", "tie_count", "gaps"}
        for r in view.top_three
    )


def test_near_tie_exact_order_not_float_or_rounded_mean(db):
    module = ranking_module()
    assert hasattr(module, "ReactionxRankingService")
    from app.reactionx.access import ReactionxAccess
    from tests.services.test_reactionx_commands import seed

    owner, connector = seed(db)
    p1, p2 = subject(db), subject(db)
    _, target = history(db, owner, connector, p1.id, n=9999, d=10000)
    history(db, owner, connector, p2.id, n=9998, d=9999)
    view = module.ReactionxRankingService(db, ReactionxAccess(db, owner)).for_result(
        target.result_id
    )
    assert view.current.rank == 2 and view.top_three[0].tie_count == 1
    assert view.top_three[0].gaps[1].amount > 0


def test_deleted_and_all_accounts_disabled_excluded_unactivated_profiles_included(db):
    from app.reactionx.access import ReactionxAccess
    from tests.services.test_reactionx_commands import seed

    owner, connector = seed(db)
    inactive, inactive_connector = seed(db)
    deleted, unactivated = subject(db), subject(db)
    _, target = history(db, owner, connector, unactivated.id, n=200)
    history(db, inactive, inactive_connector, inactive.person.id, n=100)
    history(db, owner, connector, deleted.id, n=50)
    inactive.account.status = "disabled"
    deleted.deleted_at = datetime.now(UTC)
    db.flush()
    view = (
        ranking_module()
        .ReactionxRankingService(db, ReactionxAccess(db, owner))
        .for_result(target.result_id)
    )
    assert view.participant_count == 1 and view.current.rank == 1


def test_ranking_and_cursor_fail_closed_after_report_grant_revocation(db):
    from app.core.errors import ProblemError
    from app.rbac.models import UserPermissionOverride
    from app.rbac.seed import PERMISSIONS
    from app.reactionx.access import ReactionxAccess
    from app.reactionx.results import ResultService
    from tests.services.test_reactionx_commands import seed

    owner, connector = seed(db)
    person = subject(db)
    _, target = history(db, owner, connector, person.id)
    history(db, owner, connector, person.id)
    service = ResultService(db)
    page = service.list_visible(owner, person.id, limit=1)
    assert page.next_cursor and len(page.items) == 1
    assert len(service.list_visible(owner, person.id, page.next_cursor, 1).items) == 1
    for cursor, filter_id, limit in [
        (page.next_cursor + "x", person.id, 1),
        (page.next_cursor, None, 1),
        (page.next_cursor, person.id, 2),
    ]:
        with pytest.raises(ProblemError):
            service.list_visible(owner, filter_id, cursor, limit)
    db.add(
        UserPermissionOverride(
            user_id=owner.account.id,
            permission_id=PERMISSIONS["talent_tests.read"].id,
            scope_kind="person",
            scope_id=person.id,
            effect="deny",
        )
    )
    db.flush()
    with pytest.raises(ProblemError):
        service.list_visible(owner, person.id, page.next_cursor, 1)
    with pytest.raises(ProblemError):
        ranking_module().ReactionxRankingService(db, ReactionxAccess(db, owner)).for_result(
            target.result_id
        )


def test_ten_thousand_histories_stay_in_sql_with_bounded_rows_and_stable_query_count(db):
    from sqlalchemy import event, insert, text

    from app.reactionx.access import ReactionxAccess
    from app.reactionx.models import ReactionxResultReceipt, ReactionxRun
    from tests.services.test_reactionx_commands import seed

    owner, connector = seed(db)
    people = [subject(db) for _ in range(20)]
    _, target = history(db, owner, connector, owner.person.id, n=300)
    service = ranking_module().ReactionxRankingService(db, ReactionxAccess(db, owner))
    observations = []

    def observed(connection, cursor, statement, parameters, context, many):
        observations.append((statement, cursor.rowcount))

    event.listen(db.bind, "after_cursor_execute", observed)
    try:
        service.for_result(target.result_id)
        baseline = len(observations)
        now = datetime.now(UTC)
        runs, receipts = [], []
        for index in range(10000):
            run_id = uuid4()
            runs.append(
                {
                    "id": run_id,
                    "actor_account_id": owner.account.id,
                    "subject_person_id": people[index % 20].id,
                    "connector_id": connector.id,
                    "browser_session_id": owner.session.id,
                    "browser_tab_id": uuid4(),
                    "manifest": {"mode": "standard"},
                    "manifest_digest": "a" * 64,
                    "lease_expires_at": now,
                    "status": "completed",
                    "last_sequence": 0,
                    "stop_confirmed": True,
                }
            )
            # Synthetic normalized historical projection; ingestion completeness is
            # independently tested through real envelopes, not these bulk fixtures.
            receipts.append(
                {
                    "run_id": run_id,
                    "result_id": uuid4(),
                    "digest": "b" * 64,
                    "envelope": {},
                    "metrics": {},
                    "eligible": True,
                    "comparison_key": target.comparison_key,
                    "saved_at": now,
                    "rank_n1": 0,
                    "rank_d1": 1,
                    "rank_sign1": 1,
                    "rank_n2": 200 + index % 20,
                    "rank_d2": 1,
                    "rank_sign2": 1,
                }
            )
        db.execute(insert(ReactionxRun), runs)
        db.execute(insert(ReactionxResultReceipt), receipts)
        db.execute(text("ANALYZE reactionx_result_receipts"))
        db.execute(text("ANALYZE reactionx_runs"))
        observations.clear()
        result = service.for_result(target.result_id)
        assert result.participant_count == 21 and result.current.rank == 21
        assert len(result.top_three) == 3
        assert len(observations) == baseline
        assert all(count <= 1 for sql, count in observations if "reactionx_result_receipts" in sql)
        cohort = [(sql, count) for sql, count in observations if "WITH comparable AS" in sql]
        assert len(cohort) == 1 and cohort[0][1] == 1
        query = ranking_module().RANK_QUERY
        plan = db.execute(
            text("EXPLAIN (ANALYZE, FORMAT JSON) " + query.text),
            {
                "comparison_key": target.comparison_key,
                "result_id": target.result_id,
                "person_id": owner.person.id,
            },
        ).scalar_one()[0]
        assert plan["Plan"]["Actual Rows"] == 1
        indexes = (
            db.execute(
                text("SELECT indexname FROM pg_indexes WHERE tablename='reactionx_result_receipts'")
            )
            .scalars()
            .all()
        )
        assert "ix_reactionx_rank_group" in indexes
        print(
            "B4_10K_EXPLAIN",
            json.dumps(
                {
                    "execution_ms": plan["Execution Time"],
                    "rows": plan["Plan"]["Actual Rows"],
                    "queries": baseline,
                    "index": "ix_reactionx_rank_group",
                }
            ),
        )
    finally:
        event.remove(db.bind, "after_cursor_execute", observed)


def test_list_selects_only_summary_not_full_event_envelopes(db):
    from sqlalchemy import event

    from app.reactionx.results import ResultService
    from tests.services.test_reactionx_commands import seed

    owner, connector = seed(db)
    history(db, owner, connector, owner.person.id)
    selected = []

    def observe(connection, cursor, statement, parameters, context, many):
        if "FROM reactionx_result_receipts" in statement:
            selected.extend(column.name for column in cursor.description)

    event.listen(db.bind, "after_cursor_execute", observe)
    try:
        page = ResultService(db).list_visible(owner)
        assert len(page.items) == 1
        assert "envelope" not in selected and "manifest" not in selected
    finally:
        event.remove(db.bind, "after_cursor_execute", observe)


def test_sql_numeric_crossproducts_do_not_overflow_at_maximum_numerator(db):
    from app.reactionx.access import ReactionxAccess
    from tests.services.test_reactionx_commands import seed

    owner, connector = seed(db)
    _, target = history(db, owner, connector, owner.person.id, n=9007199254740991, d=10000)
    history(db, owner, connector, subject(db).id, n=9007199254740990, d=10000)
    view = (
        ranking_module()
        .ReactionxRankingService(db, ReactionxAccess(db, owner))
        .for_result(target.result_id)
    )
    assert view.current.rank == 2 and view.top_three[0].tie_count == 1
    assert view.top_three[0].gaps[1].amount == 0.0001


def test_top_three_ties_use_earliest_saved_and_full_cohort_size(db):
    from app.reactionx.access import ReactionxAccess
    from tests.services.test_reactionx_commands import seed

    owner, connector = seed(db)
    now = datetime.now(UTC)
    _, target = history(
        db, owner, connector, owner.person.id, n=200, saved=now + timedelta(seconds=1)
    )
    for _ in range(3):
        history(db, owner, connector, subject(db).id, n=400, d=2, saved=now)
    view = (
        ranking_module()
        .ReactionxRankingService(db, ReactionxAccess(db, owner))
        .for_result(target.result_id)
    )
    assert view.participant_count == 4 and view.current.rank == 1
    assert len(view.top_three) == 3 and all(
        r.rank == 1 and r.tie_count == 4 and not r.is_self for r in view.top_three
    )


def test_report_only_reader_never_receives_conduct_authority(db):
    from sqlalchemy import delete

    from app.core.errors import ProblemError
    from app.rbac.models import UserPermissionOverride, UserRole
    from app.rbac.seed import PERMISSIONS
    from app.reactionx.access import ReactionxAccess
    from app.reactionx.results import ResultService
    from tests.services.test_reactionx_commands import seed

    owner, connector = seed(db)
    reader, _ = seed(db)
    _, target = history(db, owner, connector, owner.person.id)
    db.execute(delete(UserRole).where(UserRole.user_id == reader.account.id))
    db.add(
        UserPermissionOverride(
            user_id=reader.account.id,
            permission_id=PERMISSIONS["talent_tests.read"].id,
            scope_kind="person",
            scope_id=owner.person.id,
            effect="allow",
        )
    )
    db.flush()
    access = ReactionxAccess(db, reader)
    assert len(ResultService(db).list_visible(reader).items) == 1
    assert (
        ranking_module().ReactionxRankingService(db, access).for_result(target.result_id).status
        == "ranked"
    )
    with pytest.raises(ProblemError):
        access.require_conduct(owner.person.id)
    with pytest.raises(ProblemError):
        access.require_manage()
