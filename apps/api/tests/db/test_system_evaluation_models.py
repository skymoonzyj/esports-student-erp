from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Connection, Engine, inspect, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

PROJECT_ONE_ID = UUID("30000000-0000-4000-8000-000000000001")
PROJECT_TWO_ID = UUID("30000000-0000-4000-8000-000000000002")

SYSTEM_EVALUATION_TABLES = {
    "system_evaluation_upload_sessions",
    "system_evaluation_upload_session_media",
    "system_evaluation_tasks",
    "system_evaluation_task_media",
    "system_evaluation_attempts",
    "system_evaluation_result_versions",
    "system_evaluation_manual_drafts",
    "system_evaluation_events",
    "evaluation_adapter_certifications",
    "evaluation_idempotency_keys",
}

EXPECTED_UNIQUES = {
    "system_evaluation_upload_session_media": {
        "uq_system_evaluation_upload_media_position",
        "uq_system_evaluation_upload_media_slot",
    },
    "system_evaluation_tasks": {
        "uq_system_evaluation_tasks_public_number",
        "uq_system_evaluation_tasks_upload_session",
    },
    "system_evaluation_task_media": {
        "uq_system_evaluation_task_media_position",
        "uq_system_evaluation_task_media_slot",
        "uq_system_evaluation_task_media_asset",
    },
    "system_evaluation_attempts": {"uq_system_evaluation_attempts_task_attempt"},
    "system_evaluation_result_versions": {
        "uq_system_evaluation_results_task_version",
        "uq_system_evaluation_results_id_task",
    },
    "system_evaluation_events": {"uq_system_evaluation_events_task_sequence"},
    "evaluation_adapter_certifications": {
        "uq_evaluation_adapter_certifications_version"
    },
    "evaluation_idempotency_keys": {"uq_evaluation_idempotency_scope_actor_key"},
}

EXPECTED_CHECKS = {
    "system_evaluation_upload_sessions": {
        "ck_system_evaluation_upload_sessions_status",
        "ck_system_evaluation_upload_sessions_expiry",
    },
    "system_evaluation_upload_session_media": {
        "ck_system_evaluation_upload_media_position",
        "ck_system_evaluation_upload_media_sha256",
        "ck_system_evaluation_upload_media_storage_key",
    },
    "system_evaluation_tasks": {
        "ck_system_evaluation_tasks_status",
        "ck_system_evaluation_tasks_lock_version",
        "ck_system_evaluation_tasks_attempts",
        "ck_system_evaluation_tasks_window",
        "ck_system_evaluation_tasks_target",
    },
    "system_evaluation_task_media": {"ck_system_evaluation_task_media_position"},
    "system_evaluation_attempts": {
        "ck_system_evaluation_attempts_number",
        "ck_system_evaluation_attempts_status",
        "ck_system_evaluation_attempts_lease",
    },
    "system_evaluation_result_versions": {
        "ck_system_evaluation_results_version",
        "ck_system_evaluation_results_source",
        "ck_system_evaluation_results_score",
        "ck_system_evaluation_results_review",
        "ck_system_evaluation_results_warning_severity",
    },
    "system_evaluation_manual_drafts": {
        "ck_system_evaluation_manual_drafts_fields",
        "ck_system_evaluation_manual_drafts_confidences",
        "ck_system_evaluation_manual_drafts_warnings",
        "ck_system_evaluation_manual_drafts_components",
        "ck_system_evaluation_manual_drafts_missing_fields",
        "ck_system_evaluation_manual_drafts_lock_version",
        "ck_system_evaluation_manual_drafts_score",
    },
    "system_evaluation_events": {"ck_system_evaluation_events_sequence"},
    "evaluation_adapter_certifications": {
        "ck_evaluation_adapter_certifications_manifest"
    },
    "evaluation_idempotency_keys": {
        "ck_evaluation_idempotency_key_digest",
        "ck_evaluation_idempotency_payload_digest",
    },
}

EXPECTED_MODEL_EXPORTS = {
    "SystemEvaluationUploadSession",
    "SystemEvaluationUploadSessionMedia",
    "SystemEvaluationTask",
    "SystemEvaluationTaskMedia",
    "SystemEvaluationAttempt",
    "SystemEvaluationResultVersion",
    "SystemEvaluationManualDraft",
    "SystemEvaluationEvent",
    "EvaluationAdapterCertification",
    "EvaluationIdempotencyKey",
}


@dataclass(frozen=True)
class EvaluationTarget:
    student_id: UUID
    enrollment_id: UUID
    cohort_id: UUID
    class_id: UUID
    project_id: UUID


@dataclass(frozen=True)
class SystemEvaluationContext:
    connection: Connection
    actor_id: UUID
    task_id: UUID
    media_id: UUID
    profile_id: UUID
    profile_version: int
    targets: tuple[EvaluationTarget, ...]


@pytest.fixture
def system_evaluation_context(
    postgres_engine: Engine,
) -> Iterator[SystemEvaluationContext]:
    actor_person_id = uuid4()
    actor_id = uuid4()
    student_ids = (uuid4(), uuid4(), uuid4())
    cohort_id = uuid4()
    class_ids = (uuid4(), uuid4(), uuid4())
    admission_case_ids = (uuid4(), uuid4(), uuid4())
    enrollment_ids = (uuid4(), uuid4(), uuid4())
    upload_session_id = uuid4()
    task_id = uuid4()
    blob_id = uuid4()
    media_id = uuid4()
    task_media_id = uuid4()

    with postgres_engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(
                text(
                    "INSERT INTO people (id, full_name, identity_ciphertext, "
                    "identity_digest, sex, date_of_birth) VALUES "
                    "(:actor_person_id, '系统评价测试操作人', 'actor-ciphertext', "
                    ":actor_digest, 'female', DATE '1988-01-02')"
                ),
                {
                    "actor_person_id": actor_person_id,
                    "actor_digest": f"system-evaluation-actor-{actor_person_id}",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO user_accounts (id, person_id, login_phone_digest, "
                    "email_normalized, password_hash, status, force_password_change, "
                    "failed_login_count) VALUES (:actor_id, :actor_person_id, "
                    ":phone_digest, :email, 'test-password-hash', 'active', false, 0)"
                ),
                {
                    "actor_id": actor_id,
                    "actor_person_id": actor_person_id,
                    "phone_digest": f"system-evaluation-phone-{actor_id}",
                    "email": f"system-evaluation-{actor_id}@example.test",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO account_login_identifiers "
                    "(id, account_id, kind, identifier_digest, verified_at, "
                    "verification_source, is_primary) VALUES "
                    "(:id, :account_id, 'phone', :identifier_digest, "
                    "CURRENT_TIMESTAMP, 'test_fixture', true)"
                ),
                {
                    "id": uuid4(),
                    "account_id": actor_id,
                    "identifier_digest": actor_id.hex * 2,
                },
            )
            for position, student_id in enumerate(student_ids, start=1):
                connection.execute(
                    text(
                        "INSERT INTO people (id, full_name, identity_ciphertext, "
                        "identity_digest, sex, date_of_birth) VALUES "
                        "(:student_id, :full_name, 'student-ciphertext', "
                        ":identity_digest, 'male', DATE '2012-01-02')"
                    ),
                    {
                        "student_id": student_id,
                        "full_name": f"系统评价测试学员{position}",
                        "identity_digest": f"system-evaluation-student-{student_id}",
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO student_profiles "
                        "(person_id, student_number, status) "
                        "VALUES (:student_id, :student_number, 'active')"
                    ),
                    {
                        "student_id": student_id,
                        "student_number": f"eval-{student_id.hex[:20]}",
                    },
                )
            connection.execute(
                text(
                    "INSERT INTO cohorts (id, name, starts_at, ends_at, "
                    "default_check_in_at, default_check_out_at, status, "
                    "created_by_user_id) VALUES (:id, :name, "
                    "TIMESTAMPTZ '2026-09-01 08:00:00+08', "
                    "TIMESTAMPTZ '2026-12-31 18:00:00+08', "
                    "TIMESTAMPTZ '2026-09-01 08:00:00+08', "
                    "TIMESTAMPTZ '2026-09-01 18:00:00+08', 'draft', :actor_id)"
                ),
                {
                    "id": cohort_id,
                    "name": f"系统评价不变量测试-{cohort_id.hex[:12]}",
                    "actor_id": actor_id,
                },
            )
            project_ids = (PROJECT_ONE_ID, PROJECT_ONE_ID, PROJECT_TWO_ID)
            for position, (class_id, project_id) in enumerate(
                zip(class_ids, project_ids, strict=True), start=1
            ):
                connection.execute(
                    text(
                        "INSERT INTO teaching_classes "
                        "(id, cohort_id, project_id, name, class_type, capacity, "
                        "default_tuition_cents, created_by_user_id) VALUES "
                        "(:id, :cohort_id, :project_id, :name, 'standard', 20, 0, "
                        ":actor_id)"
                    ),
                    {
                        "id": class_id,
                        "cohort_id": cohort_id,
                        "project_id": project_id,
                        "name": f"系统评价不变量测试班{position}",
                        "actor_id": actor_id,
                    },
                )
            for admission_case_id, enrollment_id, student_id, class_id in zip(
                admission_case_ids,
                enrollment_ids,
                student_ids,
                class_ids,
                strict=True,
            ):
                connection.execute(
                    text(
                        "INSERT INTO admission_cases "
                        "(id, student_person_id, status, created_by_user_id) "
                        "VALUES (:id, :student_id, 'ready', :actor_id)"
                    ),
                    {
                        "id": admission_case_id,
                        "student_id": student_id,
                        "actor_id": actor_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO enrollments (id, admission_case_id, "
                        "student_person_id, cohort_id, class_id, agreed_tuition_cents, "
                        "status, confirmed_at, created_by_user_id) VALUES "
                        "(:id, :case_id, :student_id, :cohort_id, :class_id, 0, "
                        "'active', CURRENT_TIMESTAMP, :actor_id)"
                    ),
                    {
                        "id": enrollment_id,
                        "case_id": admission_case_id,
                        "student_id": student_id,
                        "cohort_id": cohort_id,
                        "class_id": class_id,
                        "actor_id": actor_id,
                    },
                )

            profile_id, profile_version = connection.execute(
                text(
                    "SELECT id, version FROM project_analysis_profiles "
                    "WHERE project_id = :project_id ORDER BY version DESC LIMIT 1"
                ),
                {"project_id": PROJECT_ONE_ID},
            ).one()
            connection.execute(
                text(
                    "INSERT INTO system_evaluation_upload_sessions "
                    "(id, created_by_user_id, project_id, status, expires_at) VALUES "
                    "(:id, :actor_id, :project_id, 'open', "
                    "CURRENT_TIMESTAMP + INTERVAL '1 hour')"
                ),
                {
                    "id": upload_session_id,
                    "actor_id": actor_id,
                    "project_id": PROJECT_ONE_ID,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO system_evaluation_tasks (id, public_number, "
                    "upload_session_id, created_by_user_id, project_id, "
                    "analysis_profile_id, analysis_profile_version, "
                    "analysis_config_snapshot, upload_window_start, upload_window_end, "
                    "status) VALUES (:id, :public_number, :upload_session_id, "
                    ":actor_id, :project_id, :profile_id, :profile_version, "
                    "'{}'::jsonb, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'pending_match')"
                ),
                {
                    "id": task_id,
                    "public_number": f"SE-{task_id.hex[:20]}",
                    "upload_session_id": upload_session_id,
                    "actor_id": actor_id,
                    "project_id": PROJECT_ONE_ID,
                    "profile_id": profile_id,
                    "profile_version": profile_version,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO evaluation_media_blobs (id, storage_key, "
                    "preview_storage_key, sha256, mime_type, canonical_extension, "
                    "size_bytes, width_pixels, height_pixels, storage_status, "
                    "validation_status, retention_status) VALUES (:id, :storage_key, "
                    ":preview_key, :sha256, 'image/webp', 'webp', 100, 10, 10, "
                    "'available', 'validated', 'retained')"
                ),
                {
                    "id": blob_id,
                    "storage_key": f"objects/{blob_id}.webp",
                    "preview_key": f"previews/{blob_id}.webp",
                    "sha256": blob_id.hex * 2,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO evaluation_media_assets "
                    "(id, blob_id, uploaded_by_user_id, project_id) "
                    "VALUES (:id, :blob_id, :actor_id, :project_id)"
                ),
                {
                    "id": media_id,
                    "blob_id": blob_id,
                    "actor_id": actor_id,
                    "project_id": PROJECT_ONE_ID,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO system_evaluation_task_media "
                    "(id, task_id, media_id, position, slot_key) "
                    "VALUES (:id, :task_id, :media_id, 1, 'result')"
                ),
                {"id": task_media_id, "task_id": task_id, "media_id": media_id},
            )

            targets = tuple(
                EvaluationTarget(
                    student_id=student_id,
                    enrollment_id=enrollment_id,
                    cohort_id=cohort_id,
                    class_id=class_id,
                    project_id=project_id,
                )
                for student_id, enrollment_id, class_id, project_id in zip(
                    student_ids,
                    enrollment_ids,
                    class_ids,
                    project_ids,
                    strict=True,
                )
            )
            yield SystemEvaluationContext(
                connection=connection,
                actor_id=actor_id,
                task_id=task_id,
                media_id=media_id,
                profile_id=profile_id,
                profile_version=profile_version,
                targets=targets,
            )
        finally:
            transaction.rollback()


def _confirm_task_target(
    session: Session, context: SystemEvaluationContext, target: EvaluationTarget
) -> None:
    session.execute(
        text(
            "UPDATE system_evaluation_tasks SET student_person_id = :student_id, "
            "enrollment_id = :enrollment_id, cohort_id = :cohort_id, "
            "class_id = :class_id, matched_at = CURRENT_TIMESTAMP, "
            "matched_by_user_id = :actor_id WHERE id = :task_id"
        ),
        {
            "student_id": target.student_id,
            "enrollment_id": target.enrollment_id,
            "cohort_id": target.cohort_id,
            "class_id": target.class_id,
            "actor_id": context.actor_id,
            "task_id": context.task_id,
        },
    )


def _confirm_media_target(
    session: Session, context: SystemEvaluationContext, target: EvaluationTarget
) -> None:
    session.execute(
        text(
            "UPDATE evaluation_media_assets SET student_person_id = :student_id, "
            "enrollment_id = :enrollment_id, class_id = :class_id, "
            "matched_at = CURRENT_TIMESTAMP, matched_by_user_id = :actor_id, "
            "match_reason = 'system evaluation invariant test' WHERE id = :media_id"
        ),
        {
            "student_id": target.student_id,
            "enrollment_id": target.enrollment_id,
            "class_id": target.class_id,
            "actor_id": context.actor_id,
            "media_id": context.media_id,
        },
    )


def _confirm_task_and_media(
    session: Session,
    context: SystemEvaluationContext,
    *,
    task_target: EvaluationTarget,
    media_target: EvaluationTarget,
    task_first: bool,
) -> None:
    if task_first:
        _confirm_task_target(session, context, task_target)
        _confirm_media_target(session, context, media_target)
    else:
        _confirm_media_target(session, context, media_target)
        _confirm_task_target(session, context, task_target)


def _unique_names(engine: Engine, table: str) -> set[str]:
    inspector = inspect(engine)
    names = {
        row["name"]
        for row in inspector.get_unique_constraints(table)
        if row["name"] is not None
    }
    names.update(
        row["name"]
        for row in inspector.get_indexes(table)
        if row["name"] is not None and row.get("unique")
    )
    return names


def test_system_evaluation_models_are_registered_for_metadata_discovery() -> None:
    from app.evaluations import models

    assert EXPECTED_MODEL_EXPORTS <= set(models.__all__)
    assert SYSTEM_EVALUATION_TABLES <= set(models.Base.metadata.tables)


def test_system_evaluation_schema_has_fixed_tables_and_constraints(
    postgres_engine: Engine,
) -> None:
    inspector = inspect(postgres_engine)
    assert SYSTEM_EVALUATION_TABLES <= set(inspector.get_table_names())

    for table, expected in EXPECTED_UNIQUES.items():
        assert expected <= _unique_names(postgres_engine, table)
    for table, expected in EXPECTED_CHECKS.items():
        names = {
            row["name"]
            for row in inspector.get_check_constraints(table)
            if row["name"] is not None
        }
        assert expected <= names


def test_system_evaluation_business_foreign_keys_are_restrictive(
    postgres_engine: Engine,
) -> None:
    inspector = inspect(postgres_engine)
    assert SYSTEM_EVALUATION_TABLES <= set(inspector.get_table_names())
    for table in SYSTEM_EVALUATION_TABLES:
        for foreign_key in inspector.get_foreign_keys(table):
            assert foreign_key["options"].get("ondelete") == "RESTRICT", (
                table,
                foreign_key["name"],
                foreign_key["options"],
            )


def test_system_evaluation_result_links_cannot_cross_task_boundaries(
    postgres_engine: Engine,
) -> None:
    inspector = inspect(postgres_engine)

    def foreign_keys(table: str) -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
        return {
            row["name"]: (
                tuple(row["constrained_columns"]),
                tuple(row["referred_columns"]),
            )
            for row in inspector.get_foreign_keys(table)
            if row["name"] is not None
        }

    task_keys = foreign_keys("system_evaluation_tasks")
    assert task_keys["fk_system_evaluation_tasks_current_result"] == (
        ("current_result_version_id", "id"),
        ("id", "task_id"),
    )
    assert task_keys["fk_system_evaluation_tasks_published_result"] == (
        ("published_result_version_id", "id"),
        ("id", "task_id"),
    )
    assert task_keys["fk_system_evaluation_tasks_revision_result"] == (
        ("revision_of_version_id", "revision_of_task_id"),
        ("id", "task_id"),
    )
    assert foreign_keys("system_evaluation_result_versions")[
        "fk_system_evaluation_results_previous"
    ] == (
        ("previous_version_id", "task_id"),
        ("id", "task_id"),
    )
    assert foreign_keys("system_evaluation_events")[
        "fk_system_evaluation_events_result"
    ] == (
        ("result_version_id", "task_id"),
        ("id", "task_id"),
    )


def test_system_evaluation_history_and_targets_have_database_guards(
    postgres_engine: Engine,
) -> None:
    assert SYSTEM_EVALUATION_TABLES <= set(inspect(postgres_engine).get_table_names())
    with postgres_engine.connect() as connection:
        trigger_names = set(
            connection.scalars(
                text(
                    "SELECT tgname FROM pg_trigger "
                    "WHERE NOT tgisinternal AND tgrelid IN ("
                    "'system_evaluation_tasks'::regclass, "
                    "'system_evaluation_task_media'::regclass, "
                    "'system_evaluation_result_versions'::regclass, "
                    "'system_evaluation_events'::regclass, "
                    "'evaluation_media_assets'::regclass, "
                    "'evaluation_adapter_certifications'::regclass)"
                )
            )
        )
    assert {
        "system_evaluation_tasks_no_delete",
        "system_evaluation_tasks_target_immutable",
        "system_evaluation_results_immutable",
        "system_evaluation_events_immutable",
        "system_evaluation_task_media_target_guard",
        "system_evaluation_task_media_task_target_guard",
        "system_evaluation_task_media_asset_target_guard",
        "evaluation_adapter_certifications_immutable",
    } <= trigger_names

    with postgres_engine.connect() as connection:
        task_media_guard_rows = {
            row.tgname: (row.tgdeferrable, row.tginitdeferred)
            for row in connection.execute(
                text(
                    "SELECT tgname, tgdeferrable, tginitdeferred FROM pg_trigger "
                    "WHERE tgname IN ('system_evaluation_task_media_target_guard', "
                    "'system_evaluation_task_media_task_target_guard', "
                    "'system_evaluation_task_media_asset_target_guard')"
                )
            )
        }
    assert task_media_guard_rows == {
        "system_evaluation_task_media_target_guard": (True, True),
        "system_evaluation_task_media_task_target_guard": (True, True),
        "system_evaluation_task_media_asset_target_guard": (True, True),
    }


def test_analysis_profiles_and_target_tables_expose_composite_contracts(
    postgres_engine: Engine,
) -> None:
    inspector = inspect(postgres_engine)
    assert SYSTEM_EVALUATION_TABLES <= set(inspector.get_table_names())
    profile_columns = {
        column["name"] for column in inspector.get_columns("project_analysis_profiles")
    }
    assert {"media_slots", "result_schema_version"} <= profile_columns

    enrollment_uniques = {
        row["name"] for row in inspector.get_unique_constraints("enrollments")
    }
    class_uniques = {
        row["name"] for row in inspector.get_unique_constraints("teaching_classes")
    }
    assert "uq_enrollments_system_evaluation_target" in enrollment_uniques
    assert "uq_teaching_classes_system_evaluation_target" in class_uniques
    assert (
        "uq_project_analysis_profiles_system_evaluation_identity"
        in {
            row["name"]
            for row in inspector.get_unique_constraints("project_analysis_profiles")
        }
    )

    task_foreign_keys = {
        row["name"]: (
            tuple(row["constrained_columns"]),
            row["referred_table"],
            tuple(row["referred_columns"]),
        )
        for row in inspector.get_foreign_keys("system_evaluation_tasks")
    }
    assert task_foreign_keys["fk_system_evaluation_tasks_enrollment_target"] == (
        ("enrollment_id", "student_person_id", "cohort_id", "class_id"),
        "enrollments",
        ("id", "student_person_id", "cohort_id", "class_id"),
    )
    assert task_foreign_keys["fk_system_evaluation_tasks_class_target"] == (
        ("class_id", "cohort_id", "project_id"),
        "teaching_classes",
        ("id", "cohort_id", "project_id"),
    )
    assert task_foreign_keys["fk_system_evaluation_tasks_profile_identity"] == (
        ("analysis_profile_id", "project_id", "analysis_profile_version"),
        "project_analysis_profiles",
        ("id", "project_id", "version"),
    )


def test_task_project_is_immutable_during_initial_target_confirmation(
    system_evaluation_context: SystemEvaluationContext,
) -> None:
    context = system_evaluation_context
    target = context.targets[2]
    savepoint = context.connection.begin_nested()
    try:
        with pytest.raises(
            DBAPIError, match="system evaluation task project is immutable"
        ):
            context.connection.execute(
                text(
                    "UPDATE system_evaluation_tasks SET project_id = :project_id, "
                    "student_person_id = :student_id, enrollment_id = :enrollment_id, "
                    "cohort_id = :cohort_id, class_id = :class_id, "
                    "matched_at = CURRENT_TIMESTAMP, matched_by_user_id = :actor_id "
                    "WHERE id = :task_id"
                ),
                {
                    "project_id": target.project_id,
                    "student_id": target.student_id,
                    "enrollment_id": target.enrollment_id,
                    "cohort_id": target.cohort_id,
                    "class_id": target.class_id,
                    "actor_id": context.actor_id,
                    "task_id": context.task_id,
                },
            )
    finally:
        savepoint.rollback()


@pytest.mark.parametrize("mismatch", ["project", "version"])
def test_task_analysis_profile_identity_must_match_project_and_version(
    system_evaluation_context: SystemEvaluationContext,
    mismatch: str,
) -> None:
    context = system_evaluation_context
    upload_session_id = uuid4()
    task_id = uuid4()
    project_id = PROJECT_TWO_ID if mismatch == "project" else PROJECT_ONE_ID
    profile_version = (
        context.profile_version
        if mismatch == "project"
        else context.profile_version + 1
    )
    savepoint = context.connection.begin_nested()
    try:
        context.connection.execute(
            text(
                "INSERT INTO system_evaluation_upload_sessions "
                "(id, created_by_user_id, project_id, status, expires_at) VALUES "
                "(:id, :actor_id, :project_id, 'open', "
                "CURRENT_TIMESTAMP + INTERVAL '1 hour')"
            ),
            {
                "id": upload_session_id,
                "actor_id": context.actor_id,
                "project_id": project_id,
            },
        )
        with pytest.raises(
            DBAPIError, match="fk_system_evaluation_tasks_profile_identity"
        ):
            context.connection.execute(
                text(
                    "INSERT INTO system_evaluation_tasks (id, public_number, "
                    "upload_session_id, created_by_user_id, project_id, "
                    "analysis_profile_id, analysis_profile_version, "
                    "analysis_config_snapshot, upload_window_start, "
                    "upload_window_end, status) VALUES (:id, :public_number, "
                    ":upload_session_id, :actor_id, :project_id, :profile_id, "
                    ":profile_version, '{}'::jsonb, CURRENT_TIMESTAMP, "
                    "CURRENT_TIMESTAMP, 'pending_match')"
                ),
                {
                    "id": task_id,
                    "public_number": f"SE-{task_id.hex[:20]}",
                    "upload_session_id": upload_session_id,
                    "actor_id": context.actor_id,
                    "project_id": project_id,
                    "profile_id": context.profile_id,
                    "profile_version": profile_version,
                },
            )
    finally:
        savepoint.rollback()


@pytest.mark.parametrize("task_first", [True, False], ids=["task-first", "media-first"])
def test_linked_task_and_media_targets_can_be_confirmed_in_either_order(
    system_evaluation_context: SystemEvaluationContext,
    task_first: bool,
) -> None:
    context = system_evaluation_context
    target = context.targets[0]
    with Session(
        bind=context.connection, join_transaction_mode="create_savepoint"
    ) as session:
        with session.begin():
            _confirm_task_and_media(
                session,
                context,
                task_target=target,
                media_target=target,
                task_first=task_first,
            )
            session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

        assert session.scalar(
            text(
                "SELECT count(*) FROM system_evaluation_task_media AS link "
                "JOIN system_evaluation_tasks AS task ON task.id = link.task_id "
                "JOIN evaluation_media_assets AS media ON media.id = link.media_id "
                "WHERE link.task_id = :task_id "
                "AND media.student_person_id = task.student_person_id "
                "AND media.enrollment_id = task.enrollment_id "
                "AND media.class_id = task.class_id "
                "AND media.project_id = task.project_id"
            ),
            {"task_id": context.task_id},
        ) == 1
        session.commit()


@pytest.mark.parametrize("task_first", [True, False], ids=["task-first", "media-first"])
def test_linked_task_and_media_target_mismatch_is_deferred_and_session_recovers(
    system_evaluation_context: SystemEvaluationContext,
    task_first: bool,
) -> None:
    context = system_evaluation_context
    with Session(
        bind=context.connection, join_transaction_mode="create_savepoint"
    ) as session:
        with pytest.raises(
            DBAPIError, match="system evaluation task media target mismatch"
        ), session.begin():
            _confirm_task_and_media(
                session,
                context,
                task_target=context.targets[0],
                media_target=context.targets[1],
                task_first=task_first,
            )
            session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

        assert session.scalar(text("SELECT 1")) == 1
        session.commit()
        assert session.scalar(
            text(
                "SELECT student_person_id IS NULL FROM system_evaluation_tasks "
                "WHERE id = :task_id"
            ),
            {"task_id": context.task_id},
        ) is True
        session.commit()


def test_linked_media_project_change_is_deferred_and_session_recovers(
    system_evaluation_context: SystemEvaluationContext,
) -> None:
    context = system_evaluation_context
    with Session(
        bind=context.connection, join_transaction_mode="create_savepoint"
    ) as session:
        with pytest.raises(
            DBAPIError, match="system evaluation task media target mismatch"
        ), session.begin():
            session.execute(
                text(
                    "UPDATE evaluation_media_assets SET project_id = :project_id "
                    "WHERE id = :media_id"
                ),
                {"project_id": PROJECT_TWO_ID, "media_id": context.media_id},
            )
            session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

        assert session.scalar(text("SELECT 1")) == 1
        session.commit()
        assert session.scalar(
            text(
                "SELECT project_id = :project_id FROM evaluation_media_assets "
                "WHERE id = :media_id"
            ),
            {"project_id": PROJECT_ONE_ID, "media_id": context.media_id},
        ) is True
        session.commit()


def test_manual_draft_defaults_allow_an_incomplete_edit(
    system_evaluation_context: SystemEvaluationContext,
) -> None:
    context = system_evaluation_context
    context.connection.execute(
        text(
            "INSERT INTO system_evaluation_manual_drafts "
            "(task_id, last_edited_by_user_id) VALUES (:task_id, :actor_id)"
        ),
        {"task_id": context.task_id, "actor_id": context.actor_id},
    )
    row = context.connection.execute(
        text(
            "SELECT fields, confidences, warnings, components, missing_fields, "
            "score, grade, advice, lock_version FROM "
            "system_evaluation_manual_drafts WHERE task_id = :task_id"
        ),
        {"task_id": context.task_id},
    ).one()
    assert row == ({}, {}, [], [], [], None, None, None, 1)

    context.connection.execute(
        text(
            "UPDATE system_evaluation_manual_drafts SET "
            "fields = '{\"score\": 88}'::jsonb, score = 88, "
            "lock_version = 2 WHERE task_id = :task_id"
        ),
        {"task_id": context.task_id},
    )
    assert context.connection.execute(
        text(
            "SELECT fields, score, lock_version FROM "
            "system_evaluation_manual_drafts WHERE task_id = :task_id"
        ),
        {"task_id": context.task_id},
    ).one() == ({"score": 88}, 88, 2)


@pytest.mark.parametrize(
    ("column", "value", "constraint"),
    [
        (
            "fields",
            "'[]'::jsonb",
            "ck_system_evaluation_manual_drafts_fields",
        ),
        (
            "confidences",
            "'[]'::jsonb",
            "ck_system_evaluation_manual_drafts_confidences",
        ),
        (
            "warnings",
            "'{}'::jsonb",
            "ck_system_evaluation_manual_drafts_warnings",
        ),
        (
            "components",
            "'{}'::jsonb",
            "ck_system_evaluation_manual_drafts_components",
        ),
        (
            "missing_fields",
            "'{}'::jsonb",
            "ck_system_evaluation_manual_drafts_missing_fields",
        ),
        (
            "score",
            "101",
            "ck_system_evaluation_manual_drafts_score",
        ),
        (
            "lock_version",
            "0",
            "ck_system_evaluation_manual_drafts_lock_version",
        ),
    ],
)
def test_manual_draft_rejects_invalid_structured_values_and_recovers(
    system_evaluation_context: SystemEvaluationContext,
    column: str,
    value: str,
    constraint: str,
) -> None:
    context = system_evaluation_context
    savepoint = context.connection.begin_nested()
    try:
        with pytest.raises(DBAPIError, match=constraint):
            context.connection.execute(
                text(
                    "INSERT INTO system_evaluation_manual_drafts "
                    "(task_id, last_edited_by_user_id, "
                    f"{column}) VALUES (:task_id, :actor_id, {value})"
                ),
                {"task_id": context.task_id, "actor_id": context.actor_id},
            )
    finally:
        savepoint.rollback()
    assert context.connection.scalar(text("SELECT 1")) == 1


def _insert_pending_result(
    context: SystemEvaluationContext,
    *,
    version: int,
) -> UUID:
    result_id = uuid4()
    context.connection.execute(
        text(
            "INSERT INTO system_evaluation_result_versions "
            "(id, task_id, version, source, fields, confidences, warnings, "
            "components, missing_fields, score, grade, advice, "
            "result_schema_version) VALUES (:id, :task_id, :version, 'automatic', "
            "'{}'::jsonb, '{}'::jsonb, '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, "
            "90, 'A', 'keep going', '1')"
        ),
        {"id": result_id, "task_id": context.task_id, "version": version},
    )
    return result_id


@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_result_versions_allow_one_pending_review_transition(
    system_evaluation_context: SystemEvaluationContext,
    decision: str,
) -> None:
    context = system_evaluation_context
    result_id = _insert_pending_result(
        context, version=1 if decision == "approved" else 2
    )
    reviewed_at = "2026-08-08 12:34:56+08"
    context.connection.execute(
        text(
            "UPDATE system_evaluation_result_versions SET "
            "review_decision = :decision, reviewed_by_user_id = :actor_id, "
            "reviewed_at = :reviewed_at, rejection_reason = :reason, "
            "published_at = :published_at, updated_at = TIMESTAMPTZ '2000-01-01' "
            "WHERE id = :result_id"
        ),
        {
            "decision": decision,
            "actor_id": context.actor_id,
            "reviewed_at": reviewed_at,
            "reason": "needs correction" if decision == "rejected" else None,
            "published_at": reviewed_at if decision == "approved" else None,
            "result_id": result_id,
        },
    )
    row = context.connection.execute(
        text(
            "SELECT review_decision, rejection_reason, published_at, reviewed_at, "
            "updated_at, score, advice FROM system_evaluation_result_versions "
            "WHERE id = :result_id"
        ),
        {"result_id": result_id},
    ).one()
    assert row.review_decision == decision
    assert row.rejection_reason == ("needs correction" if decision == "rejected" else None)
    assert (row.published_at is not None) if decision == "approved" else (
        row.published_at is None
    )
    assert row.updated_at == row.reviewed_at
    assert row.score == 90
    assert row.advice == "keep going"


def test_result_versions_reject_content_smuggling_noop_second_review_and_delete(
    system_evaluation_context: SystemEvaluationContext,
) -> None:
    context = system_evaluation_context
    result_id = _insert_pending_result(context, version=1)

    for statement in (
        text(
            "UPDATE system_evaluation_result_versions SET "
            "review_decision = 'pending' WHERE id = :result_id"
        ),
    ):
        savepoint = context.connection.begin_nested()
        try:
            with pytest.raises(DBAPIError, match="system evaluation result .*immutable"):
                context.connection.execute(statement, {"result_id": result_id})
        finally:
            savepoint.rollback()

    context.connection.execute(
        text(
            "UPDATE system_evaluation_result_versions SET "
            "review_decision = 'approved', reviewed_by_user_id = :actor_id, "
            "reviewed_at = CURRENT_TIMESTAMP, published_at = CURRENT_TIMESTAMP "
            "WHERE id = :result_id"
        ),
        {"result_id": result_id, "actor_id": context.actor_id},
    )

    for statement, message in (
        (
            text(
                "UPDATE system_evaluation_result_versions SET score = 1 "
                "WHERE id = :result_id"
            ),
            "system evaluation result .*immutable",
        ),
        (
            text(
                "UPDATE system_evaluation_result_versions SET "
                "review_decision = 'rejected', rejection_reason = 'again' "
                "WHERE id = :result_id"
            ),
            "system evaluation result .*immutable",
        ),
        (
            text("DELETE FROM system_evaluation_result_versions WHERE id = :result_id"),
            "system evaluation result versions cannot be deleted",
        ),
    ):
        savepoint = context.connection.begin_nested()
        try:
            with pytest.raises(DBAPIError, match=message):
                context.connection.execute(statement, {"result_id": result_id})
        finally:
            savepoint.rollback()
    assert context.connection.scalar(text("SELECT 1")) == 1
