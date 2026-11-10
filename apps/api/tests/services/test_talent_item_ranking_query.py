"""Real PostgreSQL bounds and statement-snapshot regression tests."""

import json
import os
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete, event, insert, text
from sqlalchemy.orm import Session

from app.identity.models import Person
from app.talent_tests.access import TalentAccess
from app.talent_tests.item_ranking import _ITEM_RANK_QUERY, TalentItemRankingService
from app.talent_tests.item_ranking_keys import build_item_group
from app.talent_tests.models import TalentTestResult, TalentTestSession
from tests.api.test_talent_item_ranking import seed_item
from tests.api.test_talent_ranking import NOW
from tests.services.test_talent_access import talent_people


def _parameters(session, result):
    group = build_item_group(session, result)
    assert group is not None
    return {
        "test_id": group.test_id,
        "catalog_version": group.catalog_version,
        "scoring_version": group.scoring_version,
        "rule_json": group.rule_json,
        "engine_version": group.engine_version,
        "locale": group.locale,
        "input_kind": group.input_kind,
        "person_id": session.person_id,
        "result_id": result.id,
    }


def _history(db, actor, template_session, template_result, count):
    """Mixed unrelated item/input/version histories; people need no login account."""
    people = [uuid4() for _ in range(40)]
    db.execute(
        insert(Person),
        [
            {
                "id": key,
                "full_name": "synthetic-scale",
                "identity_ciphertext": "cipher",
                "identity_digest": key.hex,
                "sex": "male",
                "date_of_birth": actor.person.date_of_birth,
            }
            for key in people
        ],
    )
    sessions, results = [], []
    for index in range(count):
        session_id = uuid4()
        rules = deepcopy(template_session.rules_snapshot)
        catalog = template_session.catalog_version
        # One in eight rows shares item, input, locale, and version.
        if index % 4 == 1:
            catalog = "synthetic-archived-version"
            rules["catalog_version"] = catalog
        sessions.append(
            {
                "id": session_id,
                "person_id": people[(index // 32) % 40],
                "created_by_user_id": actor.account.id,
                "creation_key": uuid4(),
                "creation_request_digest": "a" * 64,
                "catalog_version": catalog,
                "scoring_version": template_session.scoring_version,
                "report_version": template_session.report_version,
                "rules_snapshot": rules,
                "status": "abandoned",
                "abandoned_at": NOW,
                "created_at": NOW,
                "abandon_request_id": uuid4(),
                "abandon_request_digest": "b" * 64,
            }
        )
        device = deepcopy(template_result.device_snapshot)
        device["input_kind"] = "touch" if index % 4 == 2 else "mouse"
        results.append(
            {
                "id": uuid4(),
                "session_id": session_id,
                "test_id": "reactiontime" if index % 8 < 4 else "aim",
                "engine_version": template_result.engine_version,
                "locale": "en-US" if index % 4 == 3 else template_result.locale,
                "raw_result": template_result.raw_result,
                "device_snapshot": device,
                "normalized_result": template_result.normalized_result,
                "score": 10 + index % 40,
                "quality_flags": [],
                "duration_ms": 1000,
                "background_interrupted": False,
                "submission_id": uuid4(),
                "submission_digest": "c" * 64,
                "created_at": NOW,
            }
        )
    db.execute(insert(TalentTestSession), sessions)
    db.execute(insert(TalentTestResult), results)
    db.commit()
    return people


def _measured_read(db, actor, result):
    connection = db.connection()
    statements, returned = [], []

    def capture(_conn, cursor, statement, _params, _context, _many):
        if statement.lstrip().startswith(("SELECT", "WITH")):
            statements.append(statement)
            if "talent_test_" in statement:
                returned.append(cursor.rowcount)

    event.listen(connection, "after_cursor_execute", capture)
    try:
        view = TalentItemRankingService(db, TalentAccess(db, actor, NOW)).for_result(result.id)
    finally:
        event.remove(connection, "after_cursor_execute", capture)
    return view, statements, returned


def test_history_growth_keeps_query_and_result_bounds_and_selective_access(db, talent_people):
    _, actors, _ = talent_people
    actor = actors["student"]
    session, result = seed_item(db, actor, 50)
    for role, score in (("other", 90), ("guardian", 80), ("coach", 70)):
        seed_item(db, actors[role], score)
    evidence, statement_counts = [], []
    for count in (100, 9900):
        _history(db, actor, session, result, count)
        for table in ("talent_test_sessions", "talent_test_results", "people", "user_accounts"):
            db.execute(text(f"ANALYZE {table}"))
        view, statements, returned = _measured_read(db, actor, result)
        statement_counts.append(len(statements))
        assert sum("WITH comparable AS" in sql for sql in statements) == 1
        assert max(returned) <= 1  # PostgreSQL returns aggregates, never historical rows.
        assert view.current.rank == 4 and view.best.rank == 4
        assert [row.values["score"] for row in view.top_three] == [90, 80, 70]
        assert len(view.top_three) == 3
        assert view.participant_count == (8 if count == 100 else 48)
        params = _parameters(session, result)
        raw = db.execute(_ITEM_RANK_QUERY, params).mappings().all()
        assert len(raw) == 1 and len(raw[0]["top_three"]) == 3
        plan = db.execute(
            text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + _ITEM_RANK_QUERY.text), params
        ).scalar_one()
        evidence.append(
            {
                "history_rows": 100 if count == 100 else 10000,
                "sql_count": len(statements),
                "returned_rows": returned,
                "plan": plan,
            }
        )
    assert statement_counts[0] == statement_counts[1]
    assert statement_counts[0] < 20
    directory = os.environ.get("TALENT_QUERY_PLAN_DIR")
    if directory:
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        (path / "query-plans.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")

    def nodes(plan):
        yield plan
        for child in plan.get("Plans", []):
            yield from nodes(child)

    # Selective mixed histories must not visit every unrelated result row.
    assert not any(
        node.get("Relation Name") == "talent_test_results" and node["Node Type"] == "Seq Scan"
        for node in nodes(evidence[-1]["plan"][0]["Plan"])
    )


def test_live_candidate_deletion_and_account_disable_recalculate(db, talent_people):
    _, actors, _ = talent_people
    _, result = seed_item(db, actors["student"], 50)
    seed_item(db, actors["other"], 90)
    seed_item(db, actors["coach"], 80)
    service = TalentItemRankingService(db, TalentAccess(db, actors["student"], NOW))
    assert service.for_result(result.id).participant_count == 3
    actors["other"].person.deleted_at = NOW
    actors["coach"].account.status = "disabled"
    db.commit()
    value = service.for_result(result.id)
    assert value.participant_count == 1
    assert value.current.rank == value.best.rank == value.top_three[0].rank == 1
    assert value.top_three[0].is_self


def test_concurrent_committed_save_cannot_split_one_ranking_snapshot(postgres_engine):
    from app.auth.models import UserAccount
    from app.identity.models import GuardianRelationship, StaffProfile
    from app.rbac.models import UserRole
    from app.talent_tests.models import TalentTestReport

    db = Session(postgres_engine)
    people, actors, _ = talent_people.__wrapped__(db)
    session, result = seed_item(db, actors["student"], 50)
    other_session, _ = seed_item(db, actors["other"], 40)
    connection = db.connection()
    fired = False
    concurrent_sessions = []

    def save_during_statement(_conn, _cursor, statement, _params, _ctx, _many):
        nonlocal fired
        if "WITH comparable AS" in statement and not fired:
            fired = True
            with Session(connection.engine) as writer:
                saved_session, _ = seed_item(writer, actors["coach"], 90)
                concurrent_sessions.append(saved_session.id)

    # Commit a genuinely new third person's result after the rank statement took
    # its snapshot, but before the application consumes that statement's rows.
    event.listen(connection, "after_cursor_execute", save_during_statement)
    try:
        view = TalentItemRankingService(db, TalentAccess(db, actors["student"], NOW)).for_result(
            result.id
        )
        assert fired
        assert view.participant_count == 2
        assert view.current.rank == view.best.rank == 1
        assert [row.values["score"] for row in view.top_three] == [50, 40]
        latest = TalentItemRankingService(db, TalentAccess(db, actors["student"], NOW)).for_result(
            result.id
        )
        assert latest.participant_count == 3
        assert latest.current.rank == latest.best.rank == 2
        assert [row.values["score"] for row in latest.top_three] == [90, 50, 40]
    finally:
        event.remove(connection, "after_cursor_execute", save_during_statement)
        # Fixture schema is disposable; remove only the explicitly committed rows.
        ids = [session.id, other_session.id, *concurrent_sessions]
        db.execute(delete(TalentTestReport).where(TalentTestReport.session_id.in_(ids)))
        db.execute(delete(TalentTestResult).where(TalentTestResult.session_id.in_(ids)))
        db.execute(delete(TalentTestSession).where(TalentTestSession.id.in_(ids)))
        person_ids = [person.id for person in people.values()]
        account_ids = [actor.account.id for actor in actors.values()]
        db.execute(
            delete(GuardianRelationship).where(
                GuardianRelationship.guardian_person_id.in_(person_ids)
            )
        )
        db.execute(delete(StaffProfile).where(StaffProfile.person_id.in_(person_ids)))
        db.execute(delete(UserRole).where(UserRole.user_id.in_(account_ids)))
        db.execute(delete(UserAccount).where(UserAccount.id.in_(account_ids)))
        db.execute(delete(Person).where(Person.id.in_(person_ids)))
        db.commit()
        db.close()
