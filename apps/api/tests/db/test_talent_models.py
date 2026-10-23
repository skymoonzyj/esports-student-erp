from __future__ import annotations

from datetime import date
from importlib import import_module
from uuid import uuid4

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.rbac.models import Permission, Role, RolePermission
from app.rbac.seed import seed_roles_and_permissions

TALENT_TABLES = {
    "talent_test_sessions",
    "talent_test_results",
    "talent_test_reports",
}


def test_talent_models_publish_required_schema(db: Session) -> None:
    inspector = inspect(db.connection())
    assert TALENT_TABLES <= set(inspector.get_table_names())

    models = import_module("app.talent_tests.models")
    assert models.TalentTestSession.__tablename__ == "talent_test_sessions"
    assert models.TalentTestResult.__tablename__ == "talent_test_results"
    assert models.TalentTestReport.__tablename__ == "talent_test_reports"

    session_indexes = {index["name"] for index in inspector.get_indexes("talent_test_sessions")}
    session_checks = {
        constraint["name"] for constraint in inspector.get_check_constraints("talent_test_sessions")
    }
    assert {
        "uq_talent_sessions_person_creation",
        "uq_talent_sessions_active_person",
        "ix_talent_sessions_person_created",
    } <= session_indexes
    assert {
        "ck_talent_sessions_state_times",
        "ck_talent_sessions_abandon_pair",
        "ck_talent_sessions_creation_digest",
    } <= session_checks

    result_indexes = {index["name"] for index in inspector.get_indexes("talent_test_results")}
    result_check_rows = inspector.get_check_constraints("talent_test_results")
    result_checks = {constraint["name"] for constraint in result_check_rows}
    assert {
        "uq_talent_results_session_test",
        "uq_talent_results_session_submission",
    } <= result_indexes
    assert {
        "ck_talent_results_score",
        "ck_talent_results_test_id",
        "ck_talent_results_submission_digest",
    } <= result_checks
    test_id_check = next(
        constraint["sqltext"]
        for constraint in result_check_rows
        if constraint["name"] == "ck_talent_results_test_id"
    )
    assert "dynamic-vision" in test_id_check

    report_indexes = {index["name"] for index in inspector.get_indexes("talent_test_reports")}
    assert {"uq_talent_reports_session", "ix_talent_reports_created"} <= report_indexes

    for table_name, expected in {
        "talent_test_sessions": {
            ("person_id", "people", "RESTRICT"),
            ("created_by_user_id", "user_accounts", "RESTRICT"),
        },
        "talent_test_results": {
            ("session_id", "talent_test_sessions", "RESTRICT"),
        },
        "talent_test_reports": {
            ("session_id", "talent_test_sessions", "RESTRICT"),
        },
    }.items():
        actual = {
            (
                foreign_key["constrained_columns"][0],
                foreign_key["referred_table"],
                foreign_key["options"].get("ondelete"),
            )
            for foreign_key in inspector.get_foreign_keys(table_name)
        }
        assert expected <= actual


def test_talent_default_grants_are_not_admin_global(db: Session) -> None:
    seed_roles_and_permissions(db)
    rows = db.execute(
        select(Role.key, Permission.key, RolePermission.scope_kind)
        .join(RolePermission, RolePermission.role_id == Role.id)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .where(Permission.key.in_(["talent_tests.take", "talent_tests.read"]))
    ).all()
    assert set(rows) == {
        ("super_admin", "talent_tests.take", "self"),
        ("super_admin", "talent_tests.read", "global"),
        ("student", "talent_tests.take", "self"),
        ("student", "talent_tests.read", "self"),
        ("guardian", "talent_tests.read", "family"),
        ("coach", "talent_tests.read", "assigned_class"),
    }


def test_talent_constraints_reject_invalid_state_and_duplicate_active_session(
    db: Session,
) -> None:
    person_id, account_id = _insert_person_and_account(db)
    session_id = uuid4()
    db.execute(
        text(
            "INSERT INTO talent_test_sessions ("
            "id, person_id, created_by_user_id, creation_key, creation_request_digest, "
            "catalog_version, scoring_version, report_version, rules_snapshot, status"
            ") VALUES ("
            ":id, :person_id, :account_id, :creation_key, :digest, "
            "'talent-training-v1', 'talent-score-v1', 'talent-report-v1', "
            "CAST('{}' AS jsonb), 'in_progress')"
        ),
        {
            "id": session_id,
            "person_id": person_id,
            "account_id": account_id,
            "creation_key": uuid4(),
            "digest": "a" * 64,
        },
    )

    with pytest.raises(IntegrityError), db.begin_nested():
        db.execute(
            text(
                "INSERT INTO talent_test_sessions ("
                "id, person_id, created_by_user_id, creation_key, creation_request_digest, "
                "catalog_version, scoring_version, report_version, rules_snapshot, status"
                ") VALUES ("
                ":id, :person_id, :account_id, :creation_key, :digest, "
                "'talent-training-v1', 'talent-score-v1', 'talent-report-v1', "
                "CAST('{}' AS jsonb), 'in_progress')"
            ),
            {
                "id": uuid4(),
                "person_id": person_id,
                "account_id": account_id,
                "creation_key": uuid4(),
                "digest": "b" * 64,
            },
        )

    with pytest.raises(IntegrityError), db.begin_nested():
        db.execute(
            text(
                "UPDATE talent_test_sessions SET status = 'completed', "
                "abandoned_at = CURRENT_TIMESTAMP WHERE id = :id"
            ),
            {"id": session_id},
        )


def test_abandoned_session_requires_non_null_request_digest(db: Session) -> None:
    person_id, account_id = _insert_person_and_account(db)

    with pytest.raises(IntegrityError), db.begin_nested():
        db.execute(
            text(
                "INSERT INTO talent_test_sessions ("
                "id, person_id, created_by_user_id, creation_key, "
                "creation_request_digest, abandon_request_id, "
                "abandon_request_digest, catalog_version, scoring_version, "
                "report_version, rules_snapshot, status, abandoned_at"
                ") VALUES ("
                ":id, :person_id, :account_id, :creation_key, :digest, "
                ":abandon_request_id, NULL, 'talent-training-v1', "
                "'talent-score-v1', 'talent-report-v1', CAST('{}' AS jsonb), "
                "'abandoned', CURRENT_TIMESTAMP)"
            ),
            {
                "id": uuid4(),
                "person_id": person_id,
                "account_id": account_id,
                "creation_key": uuid4(),
                "digest": "e" * 64,
                "abandon_request_id": uuid4(),
            },
        )


def test_soft_deleted_account_retains_talent_history_and_foreign_keys_restrict_delete(
    db: Session,
) -> None:
    person_id, account_id = _insert_person_and_account(db)
    session_id = uuid4()
    result_id = uuid4()
    report_id = uuid4()
    db.execute(
        text(
            "INSERT INTO talent_test_sessions ("
            "id, person_id, created_by_user_id, creation_key, creation_request_digest, "
            "catalog_version, scoring_version, report_version, rules_snapshot, status, completed_at"
            ") VALUES ("
            ":id, :person_id, :account_id, :creation_key, :digest, "
            "'talent-training-v1', 'talent-score-v1', 'talent-report-v1', "
            "CAST('{}' AS jsonb), 'completed', CURRENT_TIMESTAMP)"
        ),
        {
            "id": session_id,
            "person_id": person_id,
            "account_id": account_id,
            "creation_key": uuid4(),
            "digest": "c" * 64,
        },
    )
    db.execute(
        text(
            "INSERT INTO talent_test_results ("
            "id, session_id, test_id, engine_version, locale, raw_result, device_snapshot, "
            "normalized_result, score, quality_flags, duration_ms, "
            "background_interrupted, submission_id, submission_digest"
            ") VALUES ("
            ":id, :session_id, 'reactiontime', 'reactiontime-clone-v1', 'zh-CN', "
            "CAST('{}' AS jsonb), CAST('{}' AS jsonb), CAST('{}' AS jsonb), 50, "
            "CAST('[]' AS jsonb), 1000, false, :submission_id, :digest)"
        ),
        {
            "id": result_id,
            "session_id": session_id,
            "submission_id": uuid4(),
            "digest": "d" * 64,
        },
    )
    db.execute(
        text(
            "INSERT INTO talent_test_reports (id, session_id, report_snapshot) "
            "VALUES (:id, :session_id, CAST('{}' AS jsonb))"
        ),
        {"id": report_id, "session_id": session_id},
    )

    db.execute(
        text("UPDATE user_accounts SET status = 'disabled' WHERE id = :account_id"),
        {"account_id": account_id},
    )
    db.execute(
        text("UPDATE people SET deleted_at = CURRENT_TIMESTAMP WHERE id = :person_id"),
        {"person_id": person_id},
    )
    assert (
        db.scalar(
            text("SELECT count(*) FROM talent_test_sessions WHERE id = :id"),
            {"id": session_id},
        )
        == 1
    )
    assert (
        db.scalar(
            text("SELECT count(*) FROM talent_test_results WHERE id = :id"),
            {"id": result_id},
        )
        == 1
    )
    assert (
        db.scalar(
            text("SELECT count(*) FROM talent_test_reports WHERE id = :id"),
            {"id": report_id},
        )
        == 1
    )

    with pytest.raises(IntegrityError), db.begin_nested():
        db.execute(text("DELETE FROM people WHERE id = :id"), {"id": person_id})
    with pytest.raises(IntegrityError), db.begin_nested():
        db.execute(
            text("DELETE FROM talent_test_sessions WHERE id = :id"),
            {"id": session_id},
        )


def _insert_person_and_account(db: Session) -> tuple[object, object]:
    person_id = uuid4()
    account_id = uuid4()
    db.execute(
        text(
            "INSERT INTO people ("
            "id, full_name, identity_ciphertext, identity_digest, sex, date_of_birth"
            ") VALUES (:id, '天赋测试学员', 'ciphertext', :digest, 'female', :birth_date)"
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
