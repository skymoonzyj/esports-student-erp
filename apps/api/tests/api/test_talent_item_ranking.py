# Imported fixtures are deliberately named by pytest arguments.
# ruff: noqa: F811
from copy import deepcopy
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, event, select

from app.auth.service import AuthContext
from app.identity.models import Person
from app.talent_tests.models import TalentTestResult
from tests.api.test_talent_ranking import NOW, seed_round
from tests.api.test_talent_routes import PREFIX, start, talent_http  # noqa: F401
from tests.services.test_talent_access import grant, talent_people  # noqa: F401
from tests.unit.talent_test_helpers import valid_payload


def seed_item(db, actor, score, *, test_id="reactiontime", **kwargs):
    session, _ = seed_round(db, actor, score, **kwargs)
    result = db.scalar(
        select(TalentTestResult).where(
            TalentTestResult.session_id == session.id, TalentTestResult.test_id == test_id
        )
    )
    return session, result


def ranking(client, result):
    response = client.get(f"{PREFIX}/results/{result.id}/ranking")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store, private"
    return response.json()


def test_item_best_and_current_are_distinct_and_self_does_not_lower_current_rank(talent_http, db):
    client, _, actors, _ = talent_http
    seed_item(db, actors["student"], 92, age=1)
    _, current = seed_item(db, actors["student"], 89)
    seed_item(db, actors["other"], 95)
    seed_item(db, actors["guardian"], 92)
    seed_item(db, actors["coach"], 70)
    value = ranking(client, current)
    assert value["status"] == "ranked" and value["reason"] is None
    assert value["best"] == {"rank": 2, "values": {"score": 92}}
    assert value["current"] == {"rank": 3, "values": {"score": 89}}
    assert value["participant_count"] == 4
    assert [row["values"]["score"] for row in value["top_three"]] == [95, 92, 92]
    assert [row["rank"] for row in value["top_three"]] == [1, 2, 2]
    assert [row["gaps"] for row in value["top_three"]] == [
        [{"key": "score", "amount": 6, "state": "behind"}],
        [{"key": "score", "amount": 3, "state": "behind"}],
        [{"key": "score", "amount": 3, "state": "behind"}],
    ]
    assert sum(row["is_self"] for row in value["top_three"]) == 1
    assert set(value) == {
        "status",
        "reason",
        "group_label",
        "calculated_at",
        "participant_count",
        "metrics",
        "current",
        "best",
        "top_three",
    }
    for row in value["top_three"]:
        assert set(row) == {"rank", "values", "is_self", "tie_count", "gaps"}
    assert all(str(actor.person.id) not in str(value) for actor in actors.values())


def test_one_person_ties_and_ahead_gap_are_well_defined(talent_http, db):
    client, _, actors, _ = talent_http
    _, current = seed_item(db, actors["student"], 90)
    single = ranking(client, current)
    assert single["participant_count"] == 1
    assert single["current"] == single["best"] == {"rank": 1, "values": {"score": 90}}
    assert single["top_three"][0]["gaps"] == [{"key": "score", "amount": 0, "state": "equal"}]
    seed_item(db, actors["other"], 70)
    value = ranking(client, current)
    assert value["top_three"][1]["gaps"] == [{"key": "score", "amount": 20, "state": "ahead"}]


def test_ties_are_not_split_and_tie_count_includes_rows_beyond_top_three(talent_http, db):
    client, _, actors, _ = talent_http
    _, current = seed_item(db, actors["student"], 50)
    for name in ("other", "guardian", "coach", "admin"):
        seed_item(db, actors[name], 80)
    value = ranking(client, current)
    assert value["current"]["rank"] == 5 and value["participant_count"] == 5
    assert len(value["top_three"]) == 3
    assert [row["rank"] for row in value["top_three"]] == [1, 1, 1]
    assert [row["tie_count"] for row in value["top_three"]] == [4, 4, 4]


@pytest.mark.parametrize("status", ["abandoned", "in_progress", "completed"])
def test_valid_items_survive_partial_rounds_and_other_item_warnings(talent_http, db, status):
    client, _, actors, _ = talent_http
    own, current = seed_item(db, actors["student"], 60, status="in_progress")
    other, _ = seed_item(db, actors["other"], 80, status=status)
    other_rule = deepcopy(other.rules_snapshot)
    other_rule["tests"]["aim"]["anchors"][0] = 999
    other.rules_snapshot = other_rule
    for session in (own, other):
        db.execute(
            delete(TalentTestResult).where(
                TalentTestResult.session_id == session.id,
                TalentTestResult.test_id == "dynamic-vision",
            )
        )
        aim = db.scalar(
            select(TalentTestResult).where(
                TalentTestResult.session_id == session.id, TalentTestResult.test_id == "aim"
            )
        )
        aim.quality_flags = ["background_interrupted"]
    db.commit()
    value = ranking(client, current)
    assert value["participant_count"] == 2 and value["current"]["rank"] == 2


def test_subject_identity_not_name_or_operator_defines_one_position(talent_http, db):
    client, _, actors, _ = talent_http
    _, current = seed_item(db, actors["student"], 60)
    actors["other"].person.full_name = actors["student"].person.full_name
    alternate_operator = AuthContext(
        account=actors["super_admin"].account,
        person=actors["other"].person,
        session=actors["super_admin"].session,
    )
    seed_item(db, actors["other"], 70)
    seed_item(db, alternate_operator, 90)
    value = ranking(client, current)
    assert value["participant_count"] == 2
    assert [row["values"]["score"] for row in value["top_three"]] == [90, 60]


def test_no_account_subject_qualifies_but_deleted_and_disabled_subjects_do_not(talent_http, db):
    client, _, actors, _ = talent_http
    _, current = seed_item(db, actors["student"], 60)
    person = Person(
        full_name="synthetic-unactivated",
        identity_ciphertext="cipher",
        identity_digest=uuid4().hex,
        sex="male",
        date_of_birth=actors["student"].person.date_of_birth,
    )
    db.add(person)
    db.flush()
    unactivated = AuthContext(
        account=actors["super_admin"].account, person=person, session=actors["super_admin"].session
    )
    seed_item(db, unactivated, 70)
    seed_item(db, actors["other"], 100)
    actors["other"].account.status = "disabled"
    seed_item(db, actors["coach"], 100)
    actors["coach"].person.deleted_at = NOW
    db.commit()
    value = ranking(client, current)
    assert value["participant_count"] == 2 and value["current"]["rank"] == 2
    assert [row["values"]["score"] for row in value["top_three"]] == [70, 60]


def test_disabled_target_cannot_fall_back_to_an_old_good_record(talent_http, db):
    client, sign_in, actors, _ = talent_http
    _, current = seed_item(db, actors["student"], 80)
    actors["student"].account.status = "disabled"
    db.commit()
    sign_in("super_admin")
    value = ranking(client, current)
    assert value["status"] == "not_eligible" and value["reason"] == "subject_inactive"
    assert value["participant_count"] == 0 and value["best"] is None


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("warning", "quality_warning"),
        ("flags_null", "quality_warning"),
        ("interrupted", "quality_warning"),
        ("missing_input", "missing_comparison_metadata"),
        ("missing_rule", "missing_comparison_metadata"),
        ("missing_scoring", "missing_comparison_metadata"),
        ("unknown_input", "incompatible_input"),
        ("engine", "incompatible_input"),
    ],
)
def test_invalid_target_never_falls_back_and_invalid_candidates_are_excluded(
    talent_http, db, mutation, reason
):
    client, _, actors, _ = talent_http
    # Historical v1 rows must not acquire today's comparison metadata by fallback.
    version = 1 if mutation == "missing_scoring" else 2
    seed_item(db, actors["student"], 100, age=2, version=version)
    session, current = seed_item(db, actors["student"], 50, version=version)
    other, invalid = seed_item(db, actors["other"], 100, version=version)
    for parent, row in ((session, current), (other, invalid)):
        if mutation == "warning":
            row.quality_flags = ["background_interrupted"]
        elif mutation == "flags_null":
            row.quality_flags = None
        elif mutation == "interrupted":
            row.background_interrupted = True
        elif mutation in {"missing_input", "unknown_input"}:
            device = deepcopy(row.device_snapshot)
            device.pop("input_kind", None)
            if mutation == "unknown_input":
                device["input_kind"] = "keyboard"
            row.device_snapshot = device
        elif mutation == "missing_rule":
            rules = deepcopy(parent.rules_snapshot)
            rules["tests"].pop("reactiontime")
            parent.rules_snapshot = rules
        elif mutation == "missing_scoring":
            normalized = deepcopy(row.normalized_result)
            normalized.pop("scoring_version")
            row.normalized_result = normalized
        elif mutation == "engine":
            row.engine_version = "unknown-engine"
    db.commit()
    value = ranking(client, current)
    assert value["status"] == "not_eligible" and value["reason"] == reason
    assert value["current"] is value["best"] is None and value["top_three"] == []
    assert value["participant_count"] == 0
    _, valid = seed_item(db, actors["student"], 60, age=3, version=version)
    assert ranking(client, valid)["participant_count"] == 1


@pytest.mark.parametrize("difference", ["input", "locale", "engine", "rule", "version"])
def test_only_comparable_item_conditions_share_a_group(talent_http, db, difference):
    client, _, actors, _ = talent_http
    _, current = seed_item(db, actors["student"], 60)
    kwargs = {"inputs": ["touch"] * 8} if difference == "input" else {}
    if difference == "locale":
        kwargs["locale"] = "en-US"
    if difference == "version":
        kwargs["version"] = 3
    other, row = seed_item(db, actors["other"], 100, **kwargs)
    if difference == "engine":
        row.engine_version = "legacy-engine"
    elif difference == "rule":
        rules = deepcopy(other.rules_snapshot)
        rules["tests"]["reactiontime"]["anchors"][0] = 999
        other.rules_snapshot = rules
    db.commit()
    value = ranking(client, current)
    assert value["participant_count"] == 1


def test_identical_submissions_replay_without_adding_people_or_results(talent_http):
    client, _, _, _ = talent_http
    session = start(client).json()
    payload = valid_payload("reactiontime")
    payload["submission_id"] = str(uuid4())
    endpoint = f"{PREFIX}/sessions/{session['id']}/results"
    first = client.post(endpoint, json=payload)
    assert first.status_code == 201, first.text
    result_id = first.json()["result"]["id"]
    for _ in range(3):
        assert client.post(endpoint, json=payload).status_code == 200
        response = client.get(f"{PREFIX}/results/{result_id}/ranking")
        assert response.status_code == 200, response.text
        assert response.json()["participant_count"] == 1


@pytest.mark.parametrize("status", ["completed", "in_progress", "abandoned"])
def test_result_access_keeps_partial_restrictions_and_private_missing_equivalence(
    talent_http, db, status
):
    client, sign_in, actors, _ = talent_http
    _, current = seed_item(db, actors["student"], 60, status=status)
    endpoint = f"{PREFIX}/results/{current.id}/ranking"
    client.cookies.clear()
    unauthorized = client.get(endpoint)
    assert unauthorized.status_code == 401
    assert unauthorized.headers["cache-control"] == "no-store, private"
    sign_in("admin")
    denied = client.get(endpoint)
    missing = client.get(f"{PREFIX}/results/{uuid4()}/ranking")
    assert denied.status_code == missing.status_code == 404
    assert denied.content == missing.content and dict(denied.headers) == dict(missing.headers)
    sign_in("guardian")
    assert client.get(endpoint).status_code == (200 if status == "completed" else 404)
    sign_in("coach")
    grant(db, actors["coach"])
    assert client.get(endpoint).status_code == (200 if status == "completed" else 404)
    sign_in("super_admin")
    assert client.get(endpoint).status_code == 200
    sign_in("student")
    grant(db, actors["student"], effect="deny", scope="self")
    assert client.get(endpoint).status_code == 404


def test_one_snapshot_query_and_stable_top_order_do_not_grow_with_histories(talent_http, db):
    client, _, actors, _ = talent_http
    _, current = seed_item(db, actors["student"], 70, age=1)
    _, other = seed_item(db, actors["other"], 70, age=1)
    current.id = UUID("20000000-0000-4000-8000-000000000001")
    other.id = UUID("10000000-0000-4000-8000-000000000001")
    db.commit()
    statements = []
    connection = db.connection()

    def capture(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().startswith("WITH "):
            statements.append(statement)

    event.listen(connection, "before_cursor_execute", capture)
    try:
        value = ranking(client, current)
        assert [row["is_self"] for row in value["top_three"]] == [False, True]
        assert len(statements) == 1
        for age in range(2, 7):
            seed_item(db, actors["other"], 50, age=age)
        statements.clear()
        assert ranking(client, current)["participant_count"] == 2
        assert len(statements) == 1
    finally:
        event.remove(connection, "before_cursor_execute", capture)


def test_group_builder_ignores_other_tests_and_whole_round_report_metadata(talent_http, db):
    from app.talent_tests.item_ranking_keys import build_item_group

    _, _, actors, _ = talent_http
    session, current = seed_item(db, actors["student"], 60)
    original = build_item_group(session, current)
    assert original is not None
    rules = deepcopy(session.rules_snapshot)
    rules["tests"].pop("aim")
    rules["test_ids"] = ["reactiontime"]
    rules["report_version"] = "unrelated-report-version"
    session.rules_snapshot = rules
    session.report_version = "unrelated-report-version"
    assert build_item_group(session, current) == original
