from __future__ import annotations

from datetime import date
from importlib import import_module
from uuid import UUID, uuid4

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

ESPORTS_TABLES = {
    "esports_assessment_sessions",
    "esports_assessment_attempts",
    "esports_assessment_results",
    "esports_assessment_reports",
    "esports_assessment_practice_runs",
}


def _insert_person_and_account(db: Session) -> tuple[UUID, UUID]:
    person_id = uuid4()
    account_id = uuid4()
    db.execute(
        text(
            "INSERT INTO people ("
            "id, full_name, identity_ciphertext, identity_digest, sex, date_of_birth"
            ") VALUES (:id, '电竞评测学员', 'ciphertext', :digest, 'female', :birth_date)"
        ),
        {
            "id": person_id,
            "digest": person_id.hex,
            "birth_date": date(2012, 1, 2),
        },
    )
    db.execute(
        text(
            "INSERT INTO user_accounts ("
            "id, person_id, password_hash, status, force_password_change, failed_login_count"
            ") VALUES (:id, :person_id, 'not-used', 'active', false, 0)"
        ),
        {"id": account_id, "person_id": person_id},
    )
    return person_id, account_id


def _insert_session(
    db: Session,
    *,
    person_id: UUID,
    account_id: UUID,
    suite_id: str = "pc-fps",
    status: str = "in_progress",
) -> UUID:
    session_id = uuid4()
    db.execute(
        text(
            "INSERT INTO esports_assessment_sessions ("
            "id, person_id, created_by_user_id, suite_id, creation_key, "
            "creation_request_digest, catalog_snapshot, scoring_snapshot, "
            "report_rules_snapshot, status, completed_at"
            ") VALUES ("
            ":id, :person_id, :account_id, :suite_id, :creation_key, :digest, "
            "CAST(:catalog AS jsonb), CAST('{}' AS jsonb), CAST('{}' AS jsonb), "
            "CAST(:status AS varchar), CASE WHEN CAST(:status AS varchar) = 'completed' "
            "THEN CURRENT_TIMESTAMP ELSE NULL END)"
        ),
        {
            "id": session_id,
            "person_id": person_id,
            "account_id": account_id,
            "suite_id": suite_id,
            "creation_key": uuid4(),
            "digest": "a" * 64,
            "catalog": f'{{"suite_id":"{suite_id}"}}',
            "status": status,
        },
    )
    return session_id


def _insert_attempt(
    db: Session,
    *,
    session_id: UUID,
    account_id: UUID,
    test_id: str = "precision",
    state: str = "running",
    sequence_no: int = 1,
) -> UUID:
    attempt_id = uuid4()
    db.execute(
        text(
            "INSERT INTO esports_assessment_attempts ("
            "id, session_id, created_by_user_id, test_id, sequence_no, seed, "
            "config_snapshot, settings_snapshot, environment_snapshot, state, "
            "creation_key, creation_request_digest, run_until, submit_until, "
            "submitted_at, interrupted_at"
            ") VALUES ("
            ":id, :session_id, :account_id, :test_id, :sequence_no, 4294967295, "
            "CAST('{}' AS jsonb), CAST('{}' AS jsonb), CAST('{}' AS jsonb), "
            "CAST(:state AS varchar), "
            ":creation_key, :digest, CURRENT_TIMESTAMP + INTERVAL '90 seconds', "
            "CURRENT_TIMESTAMP + INTERVAL '30 minutes', "
            "CASE WHEN CAST(:state AS varchar) = 'submitted' "
            "THEN CURRENT_TIMESTAMP ELSE NULL END, "
            "CASE WHEN CAST(:state AS varchar) = 'interrupted' "
            "THEN CURRENT_TIMESTAMP ELSE NULL END)"
        ),
        {
            "id": attempt_id,
            "session_id": session_id,
            "account_id": account_id,
            "test_id": test_id,
            "state": state,
            "sequence_no": sequence_no,
            "creation_key": uuid4(),
            "digest": "b" * 64,
        },
    )
    return attempt_id


def _insert_result(
    db: Session,
    *,
    attempt_id: UUID,
    session_id: UUID,
    account_id: UUID,
    test_id: str = "precision",
    submission_key: UUID | None = None,
) -> UUID:
    result_id = uuid4()
    db.execute(
        text(
            "INSERT INTO esports_assessment_results ("
            "id, attempt_id, session_id, test_id, created_by_user_id, submission_key, "
            "submission_request_digest, raw_result, metrics, score, grade, quality_flags, "
            "comparison_key"
            ") VALUES ("
            ":id, :attempt_id, :session_id, :test_id, :account_id, :submission_key, "
            ":digest, CAST('{}' AS jsonb), CAST('{}' AS jsonb), 50, 'medium', "
            "CAST('[]' AS jsonb), :comparison_key)"
        ),
        {
            "id": result_id,
            "attempt_id": attempt_id,
            "session_id": session_id,
            "test_id": test_id,
            "account_id": account_id,
            "submission_key": submission_key or uuid4(),
            "digest": "c" * 64,
            "comparison_key": "f" * 64,
        },
    )
    return result_id


def test_new_models_publish_exact_independent_tables_and_constraints(db: Session) -> None:
    models = import_module("app.esports_assessments.models")
    assert {
        table_name
        for table_name in models.Base.metadata.tables
        if table_name.startswith("esports_assessment_")
    } == ESPORTS_TABLES
    assert models.AssessmentSession.__tablename__ == "esports_assessment_sessions"
    assert models.AssessmentAttempt.__tablename__ == "esports_assessment_attempts"
    assert models.AssessmentResult.__tablename__ != "talent_test_results"
    assert models.AssessmentReport.__tablename__ == "esports_assessment_reports"
    assert models.AssessmentPracticeRun.__tablename__ == "esports_assessment_practice_runs"

    inspector = inspect(db.connection())
    assert ESPORTS_TABLES <= set(inspector.get_table_names())
    assert {
        "uq_ea_open_person_suite",
        "uq_ea_session_creator_creation",
        "uq_ea_session_creator_abandon",
        "ix_ea_session_person_suite_status_created",
    } <= {row["name"] for row in inspector.get_indexes("esports_assessment_sessions")}
    assert {
        "uq_ea_attempt_identity",
        "uq_ea_attempt_creator_creation",
        "uq_ea_attempt_creator_interrupt",
        "uq_ea_attempt_sequence",
        "uq_ea_attempt_submittable_session_test",
    } <= {row["name"] for row in inspector.get_indexes("esports_assessment_attempts")}
    assert {
        "uq_ea_result_session_test",
        "uq_ea_result_attempt",
        "uq_ea_result_creator_submission",
        "ix_ea_result_comparison_created",
    } <= {row["name"] for row in inspector.get_indexes("esports_assessment_results")}
    assert "uq_ea_report_session" in {
        row["name"] for row in inspector.get_indexes("esports_assessment_reports")
    }
    practice_indexes = {
        row["name"] for row in inspector.get_indexes("esports_assessment_practice_runs")
    }
    assert {
        "uq_ea_practice_creator_creation",
        "uq_ea_practice_creator_submission",
        "uq_ea_practice_creator_interrupt",
        "ix_ea_practice_comparison_created",
    } <= practice_indexes

    result_foreign_keys = {
        (
            tuple(row["constrained_columns"]),
            row["referred_table"],
            tuple(row["referred_columns"]),
            row["options"].get("ondelete"),
        )
        for row in inspector.get_foreign_keys("esports_assessment_results")
    }
    assert (
        ("attempt_id", "session_id", "test_id"),
        "esports_assessment_attempts",
        ("id", "session_id", "test_id"),
        "RESTRICT",
    ) in result_foreign_keys


def test_session_partial_unique_is_per_person_and_suite(db: Session) -> None:
    person_id, account_id = _insert_person_and_account(db)
    _insert_session(db, person_id=person_id, account_id=account_id, suite_id="pc-fps")

    with pytest.raises(IntegrityError), db.begin_nested():
        _insert_session(db, person_id=person_id, account_id=account_id, suite_id="pc-fps")

    mobile_session_id = _insert_session(
        db,
        person_id=person_id,
        account_id=account_id,
        suite_id="mobile-fps",
    )
    assert db.scalar(
        text("SELECT count(*) FROM esports_assessment_sessions WHERE id = :id"),
        {"id": mobile_session_id},
    ) == 1


def test_result_uniqueness_and_attempt_ownership_are_database_enforced(db: Session) -> None:
    person_id, account_id = _insert_person_and_account(db)
    session_id = _insert_session(db, person_id=person_id, account_id=account_id)
    attempt_id = _insert_attempt(
        db,
        session_id=session_id,
        account_id=account_id,
        state="submitted",
    )
    submission_key = uuid4()
    _insert_result(
        db,
        attempt_id=attempt_id,
        session_id=session_id,
        account_id=account_id,
        submission_key=submission_key,
    )

    second_attempt = _insert_attempt(
        db,
        session_id=session_id,
        account_id=account_id,
        state="interrupted",
        sequence_no=2,
    )
    with pytest.raises(IntegrityError), db.begin_nested():
        _insert_result(
            db,
            attempt_id=second_attempt,
            session_id=session_id,
            account_id=account_id,
        )

    other_session = _insert_session(
        db,
        person_id=person_id,
        account_id=account_id,
        suite_id="pc-moba",
    )
    other_attempt = _insert_attempt(
        db,
        session_id=other_session,
        account_id=account_id,
        test_id="last-hit",
    )
    with pytest.raises(IntegrityError), db.begin_nested():
        _insert_result(
            db,
            attempt_id=attempt_id,
            session_id=other_session,
            account_id=account_id,
            test_id="last-hit",
        )

    with pytest.raises(IntegrityError), db.begin_nested():
        _insert_result(
            db,
            attempt_id=other_attempt,
            session_id=other_session,
            account_id=account_id,
            test_id="last-hit",
            submission_key=submission_key,
        )


def test_running_attempt_index_does_not_block_a_different_project_after_lease(
    db: Session,
) -> None:
    person_id, account_id = _insert_person_and_account(db)
    session_id = _insert_session(db, person_id=person_id, account_id=account_id)
    first_attempt = _insert_attempt(db, session_id=session_id, account_id=account_id)
    db.execute(
        text(
            "UPDATE esports_assessment_attempts "
            "SET created_at = CURRENT_TIMESTAMP - INTERVAL '2 minutes', "
            "run_until = CURRENT_TIMESTAMP - INTERVAL '1 second' "
            "WHERE id = :id"
        ),
        {"id": first_attempt},
    )

    second_attempt = _insert_attempt(
        db,
        session_id=session_id,
        account_id=account_id,
        test_id="switching",
    )
    assert db.scalar(
        text("SELECT count(*) FROM esports_assessment_attempts WHERE id = :id"),
        {"id": second_attempt},
    ) == 1


def test_soft_deleted_account_retains_esports_history_and_restricts_hard_delete(
    db: Session,
) -> None:
    person_id, account_id = _insert_person_and_account(db)
    session_id = _insert_session(
        db,
        person_id=person_id,
        account_id=account_id,
        status="completed",
    )
    attempt_id = _insert_attempt(
        db,
        session_id=session_id,
        account_id=account_id,
        state="submitted",
    )
    result_id = _insert_result(
        db,
        attempt_id=attempt_id,
        session_id=session_id,
        account_id=account_id,
    )
    report_id = uuid4()
    db.execute(
        text(
            "INSERT INTO esports_assessment_reports (id, session_id, report_snapshot) "
            "VALUES (:id, :session_id, CAST('{}' AS jsonb))"
        ),
        {"id": report_id, "session_id": session_id},
    )

    db.execute(
        text("UPDATE user_accounts SET status = 'disabled' WHERE id = :id"),
        {"id": account_id},
    )
    db.execute(
        text("UPDATE people SET deleted_at = CURRENT_TIMESTAMP WHERE id = :id"),
        {"id": person_id},
    )
    assert db.scalar(
        text("SELECT count(*) FROM esports_assessment_results WHERE id = :id"),
        {"id": result_id},
    ) == 1
    assert db.scalar(
        text("SELECT count(*) FROM esports_assessment_reports WHERE id = :id"),
        {"id": report_id},
    ) == 1

    with pytest.raises(IntegrityError), db.begin_nested():
        db.execute(text("DELETE FROM people WHERE id = :id"), {"id": person_id})
    with pytest.raises(IntegrityError), db.begin_nested():
        db.execute(
            text("DELETE FROM esports_assessment_sessions WHERE id = :id"),
            {"id": session_id},
        )
