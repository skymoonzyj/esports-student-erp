from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from io import StringIO
from pathlib import Path
from queue import Queue
from time import monotonic, sleep
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, inspect, text
from sqlalchemy.exc import DBAPIError

API_ROOT = Path(__file__).resolve().parents[2]

EXPECTED_TABLES = {
    "people",
    "student_profiles",
    "staff_profiles",
    "visitor_profiles",
    "guardian_relationships",
    "user_accounts",
    "auth_sessions",
    "auth_rate_limits",
    "roles",
    "permissions",
    "role_permissions",
    "user_roles",
    "user_permission_overrides",
    "audit_events",
}

TEACHING_TABLES = {
    "game_projects",
    "professional_teams",
    "cohorts",
    "cohort_status_events",
    "teaching_classes",
    "coach_assignments",
}
EXPECTED_TABLES |= TEACHING_TABLES

ADMISSIONS_TABLES = {
    "service_accounts",
    "service_account_credentials",
    "service_account_request_limits",
    "service_account_nonces",
    "payments",
    "payment_import_events",
    "payment_conflicts",
    "payment_refunds",
    "admission_cases",
    "admission_case_payments",
    "enrollments",
    "enrollment_events",
    "enrollment_withdrawals",
}
EXPECTED_TABLES |= ADMISSIONS_TABLES

EVALUATION_TABLES = {
    "evaluation_bridge_requests",
    "evaluation_bridge_tickets",
}
EXPECTED_TABLES |= EVALUATION_TABLES

EVALUATION_FOUNDATION_TABLES = {
    "evaluation_staff_roles",
    "cohort_staff_assignments",
    "cohort_staff_assignment_permissions",
    "student_game_accounts",
    "student_auxiliary_phones",
    "project_analysis_profiles",
    "evaluation_media_blobs",
    "evaluation_media_assets",
}
EXPECTED_TABLES |= EVALUATION_FOUNDATION_TABLES

HOUSING_FOUNDATION_TABLES = {
    "housing_buildings",
    "housing_floors",
    "housing_rooms",
    "housing_import_batches",
}
EXPECTED_TABLES |= HOUSING_FOUNDATION_TABLES

HOUSING_OCCUPANCY_TABLES = {
    "housing_room_issues",
    "housing_stays",
    "housing_transfers",
}
EXPECTED_TABLES |= HOUSING_OCCUPANCY_TABLES

HOUSING_REPORTING_TABLES = {
    "housing_report_batches",
    "housing_report_batch_scopes",
}
EXPECTED_TABLES |= HOUSING_REPORTING_TABLES

REGISTRATION_TABLES = {"registration_requests"}
EXPECTED_TABLES |= REGISTRATION_TABLES

MAIL_TABLES = {"mail_delivery_settings"}
EXPECTED_TABLES |= MAIL_TABLES

INITIAL_PROJECT_IDS = (
    UUID("30000000-0000-4000-8000-000000000001"),
    UUID("30000000-0000-4000-8000-000000000002"),
    UUID("30000000-0000-4000-8000-000000000003"),
    UUID("30000000-0000-4000-8000-000000000004"),
)
TEACHING_PERMISSION_IDS = tuple(
    UUID(f"20000000-0000-4000-8000-{suffix:012d}") for suffix in range(10, 16)
)
ADMISSIONS_PERMISSION_IDS = tuple(
    UUID(f"20000000-0000-4000-8000-{suffix:012d}") for suffix in range(16, 25)
)
PORTAL_PERMISSION_ID = UUID("20000000-0000-4000-8000-000000000025")
EVALUATION_ACCESS_PERMISSION_ID = UUID("20000000-0000-4000-8000-000000000026")
EVALUATION_FOUNDATION_PERMISSION_IDS = {
    "evaluation_media.upload": UUID("20000000-0000-4000-8000-000000000027"),
    "evaluation_media.original.read": UUID(
        "20000000-0000-4000-8000-000000000028"
    ),
    "analysis_profiles.manage": UUID("20000000-0000-4000-8000-000000000030"),
    "student_game_accounts.manage": UUID(
        "20000000-0000-4000-8000-000000000031"
    ),
    "cohort_staff.manage": UUID("20000000-0000-4000-8000-000000000036"),
}
HOUSING_PERMISSION_IDS = {
    "housing.read": UUID("20000000-0000-4000-8000-000000000037"),
    "housing.rooms.manage": UUID("20000000-0000-4000-8000-000000000038"),
}
HOUSING_OCCUPANCY_PERMISSION_IDS = {
    "housing.stays.manage": UUID("20000000-0000-4000-8000-000000000039"),
    "housing.transfers.manage": UUID("20000000-0000-4000-8000-000000000040"),
    "housing.checkouts.manage": UUID("20000000-0000-4000-8000-000000000041"),
    "housing.issues.manage": UUID("20000000-0000-4000-8000-000000000042"),
}
HOUSING_REPORT_PERMISSION_ID = UUID("20000000-0000-4000-8000-000000000043")
HOUSING_OCCUPANCY_GRANTS = {
    ("admin", permission_key, "global")
    for permission_key in HOUSING_OCCUPANCY_PERMISSION_IDS
} | {
    ("student", "housing.read", "self"),
    ("guardian", "housing.read", "family"),
}
EVALUATION_LIFE_TEACHER_ROLE_ID = UUID(
    "41000000-0000-4000-8000-000000000001"
)
EVALUATION_ACCESS_GRANTS = {
    ("admin", "evaluation_center.access", "global"),
    ("coach", "evaluation_center.access", "global"),
    ("life_teacher", "evaluation_center.access", "global"),
    ("staff", "evaluation_center.access", "global"),
    ("guardian", "evaluation_center.access", "global"),
    ("student", "evaluation_center.access", "global"),
}
PORTAL_GRANTS = {
    ("admin", "coach_roster.read", "global"),
    ("coach", "coach_roster.read", "assigned_class"),
    ("coach", "enrollments.read", "assigned_class"),
    ("guardian", "teaching.read", "family"),
    ("guardian", "enrollments.read", "family"),
    ("guardian", "payments.read", "self"),
    ("student", "teaching.read", "self"),
    ("student", "enrollments.read", "self"),
    ("student", "payments.read", "self"),
}
PORTAL_INDEX_NAMES = {
    "ix_cohorts_portal_next",
    "ix_coach_assignments_portal_current",
    "ix_enrollments_student_status_cohort",
}


def _alembic_config(postgres_engine: Engine) -> Config:
    database_url = postgres_engine.url.render_as_string(hide_password=False)
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    config.attributes["database_url"] = database_url
    return config


def _role_permission_grants(connection: Connection) -> set[tuple[str, str, str]]:
    return set(
        connection.execute(
            text(
                "SELECT roles.key, permissions.key, role_permissions.scope_kind "
                "FROM role_permissions "
                "JOIN roles ON roles.id = role_permissions.role_id "
                "JOIN permissions ON permissions.id = role_permissions.permission_id"
            )
        ).tuples()
    )


def _portal_index_names(connection: Connection) -> set[str]:
    return set(
        connection.scalars(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname = 'public' "
                "AND indexname = ANY(CAST(:names AS text[]))"
            ),
            {"names": sorted(PORTAL_INDEX_NAMES)},
        )
    )


def _assert_portal_ddl_is_intact(
    connection: Connection,
    expected_revision: str = "0011_housing_real_name_reporting",
) -> None:
    assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
        expected_revision
    )
    assert _portal_index_names(connection) == PORTAL_INDEX_NAMES


def _insert_evaluation_bridge_request(postgres_engine: Engine) -> UUID:
    request_id = uuid4()
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO evaluation_bridge_requests "
                "(id, client_id, mode, state_ciphertext, expires_at) "
                "VALUES ("
                ":request_id, :client_id, 'standalone', :state_ciphertext, "
                "CURRENT_TIMESTAMP + INTERVAL '1 hour'"
                ")"
            ),
            {
                "request_id": request_id,
                "client_id": f"migration-guard-{request_id}",
                "state_ciphertext": f"migration-guard-state-{request_id}",
            },
        )
    return request_id


def _assert_evaluation_downgrade_state_is_intact(
    connection: Connection,
    request_id: UUID,
) -> None:
    assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
        "0011_housing_real_name_reporting"
    )
    assert EVALUATION_TABLES <= set(inspect(connection).get_table_names())
    assert connection.execute(
        text(
            "SELECT client_id, mode, state_ciphertext "
            "FROM evaluation_bridge_requests WHERE id = :request_id"
        ),
        {"request_id": request_id},
    ).one() == (
        f"migration-guard-{request_id}",
        "standalone",
        f"migration-guard-state-{request_id}",
    )


def _insert_people(postgres_engine: Engine) -> tuple[UUID, UUID]:
    guardian_id = uuid4()
    student_id = uuid4()
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO people (
                    id,
                    full_name,
                    identity_ciphertext,
                    identity_digest,
                    sex,
                    date_of_birth
                ) VALUES (
                    :guardian_id,
                    '迁移测试监护人',
                    'guardian-ciphertext',
                    :guardian_digest,
                    'female',
                    DATE '1980-01-02'
                ), (
                    :student_id,
                    '迁移测试学员',
                    'student-ciphertext',
                    :student_digest,
                    'male',
                    DATE '2012-01-02'
                )
                """
            ),
            {
                "guardian_id": guardian_id,
                "student_id": student_id,
                "guardian_digest": f"guardian-{guardian_id}",
                "student_digest": f"student-{student_id}",
            },
        )
    return guardian_id, student_id


def _delete_people(
    postgres_engine: Engine,
    guardian_id: UUID,
    student_id: UUID,
) -> None:
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "DELETE FROM guardian_relationships "
                "WHERE guardian_person_id = :guardian_id "
                "AND student_person_id = :student_id"
            ),
            {"guardian_id": guardian_id, "student_id": student_id},
        )
        connection.execute(
            text("DELETE FROM people WHERE id IN (:guardian_id, :student_id)"),
            {"guardian_id": guardian_id, "student_id": student_id},
        )


def _insert_test_account(postgres_engine: Engine) -> tuple[UUID, UUID]:
    person_id = uuid4()
    account_id = uuid4()
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO people (
                    id,
                    full_name,
                    identity_ciphertext,
                    identity_digest,
                    sex,
                    date_of_birth
                ) VALUES (
                    :person_id,
                    '迁移测试操作人',
                    'operator-ciphertext',
                    :identity_digest,
                    'female',
                    DATE '1988-01-02'
                )
                """
            ),
            {
                "person_id": person_id,
                "identity_digest": f"operator-{person_id}",
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO user_accounts (
                    id,
                    person_id,
                    login_phone_digest,
                    email_normalized,
                    password_hash,
                    status,
                    force_password_change,
                    failed_login_count
                ) VALUES (
                    :account_id,
                    :person_id,
                    :login_phone_digest,
                    :email,
                    'test-password-hash',
                    'active',
                    false,
                    0
                )
                """
            ),
            {
                "account_id": account_id,
                "person_id": person_id,
                "login_phone_digest": f"operator-phone-{account_id}",
                "email": f"operator-{account_id}@example.test",
            },
        )
    return person_id, account_id


def _delete_test_account(
    postgres_engine: Engine,
    *,
    person_id: UUID,
    account_id: UUID,
) -> None:
    with postgres_engine.begin() as connection:
        connection.execute(
            text("DELETE FROM user_permission_overrides WHERE user_id = :account_id"),
            {"account_id": account_id},
        )
        connection.execute(
            text("DELETE FROM user_accounts WHERE id = :account_id"),
            {"account_id": account_id},
        )
        connection.execute(
            text("DELETE FROM people WHERE id = :person_id"),
            {"person_id": person_id},
        )


def test_audit_model_maps_the_metadata_database_column() -> None:
    from app.audit.models import AuditEvent

    assert "metadata" in AuditEvent.__table__.columns


def test_alembic_head_creates_platform_tables(postgres_engine: Engine) -> None:
    tables = set(inspect(postgres_engine).get_table_names())

    assert EXPECTED_TABLES <= tables


def test_housing_foundation_migration_seeds_catalog_and_permissions(
    postgres_engine: Engine,
) -> None:
    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "0011_housing_real_name_reporting"
        )
        assert connection.scalar(text("SELECT count(*) FROM housing_buildings")) == 4
        assert connection.scalar(text("SELECT count(*) FROM housing_floors")) == 11
        assert connection.scalar(text("SELECT count(*) FROM housing_rooms")) == 188
        assert connection.scalar(
            text("SELECT count(*) FROM housing_rooms WHERE room_type = 'functional'")
        ) == 17
        assert connection.scalar(
            text(
                "SELECT count(*) FROM housing_rooms "
                "WHERE room_type <> 'functional' "
                "AND verification_status = 'pending_verification'"
            )
        ) == 171
        assert connection.scalar(
            text("SELECT COALESCE(sum(capacity), 0) FROM housing_rooms")
        ) == 223
        assert set(
            connection.execute(
                text("SELECT key, id FROM permissions WHERE key LIKE 'housing.%'")
            ).tuples()
        ) == {
            (key, permission_id)
            for key, permission_id in (
                HOUSING_PERMISSION_IDS
                | HOUSING_OCCUPANCY_PERMISSION_IDS
                | {"housing.reports.export_sensitive": HOUSING_REPORT_PERMISSION_ID}
            ).items()
        }
        assert {
            row[0]
            for row in connection.execute(
                text(
                    "SELECT rooms.room_number FROM housing_rooms rooms "
                    "WHERE rooms.room_number IN "
                    "('4-409', '9-305', '5-311', 'A-205')"
                )
            ).tuples()
        } == {"4-409", "9-305", "5-311", "A-205"}


def test_housing_reporting_upgrade_and_downgrade_preserves_0010_data(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    with postgres_engine.connect() as connection:
        before = connection.execute(
            text(
                "SELECT id, room_number, building_id, floor_id "
                "FROM housing_rooms ORDER BY id LIMIT 1"
            )
        ).one()
    try:
        command.downgrade(config, "0010_housing_occupancy_lifecycle")
        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0010_housing_occupancy_lifecycle"
            )
            assert HOUSING_REPORTING_TABLES.isdisjoint(
                set(inspect(connection).get_table_names())
            )
            assert connection.execute(
                text(
                    "SELECT id, room_number, building_id, floor_id "
                    "FROM housing_rooms WHERE id = :id"
                ),
                {"id": before.id},
            ).one() == before
            assert connection.scalar(
                text("SELECT count(*) FROM permissions WHERE id = :id"),
                {"id": HOUSING_REPORT_PERMISSION_ID},
            ) == 0
    finally:
        command.upgrade(config, "head")


def test_housing_models_declare_foundation_query_indexes() -> None:
    from app.housing.models import HousingBuilding, HousingFloor, HousingRoom

    expected_indexes = {
        "housing_buildings": {
            "ix_housing_buildings_display_order": ("display_order", "code", "id"),
        },
        "housing_floors": {
            "ix_housing_floors_display_order": (
                "building_id",
                "display_order",
                "number",
                "id",
            ),
        },
        "housing_rooms": {
            "ix_housing_rooms_floor_number": ("floor_id", "room_number", "id"),
            "ix_housing_rooms_verification_status": ("verification_status", "id"),
            "ix_housing_rooms_service_status": ("service_status", "id"),
            "ix_housing_rooms_type": ("room_type", "id"),
        },
    }

    for model in (HousingBuilding, HousingFloor, HousingRoom):
        assert {
            index.name: tuple(column.name for column in index.columns)
            for index in model.__table__.indexes
        } == expected_indexes[model.__tablename__]


def test_housing_occupancy_models_and_database_schema_are_aligned(
    postgres_engine: Engine,
) -> None:
    from app.housing.models import HousingRoomIssue, HousingStay, HousingTransfer

    inspector = inspect(postgres_engine)
    expected_columns = {
        "housing_room_issues": {
            "id",
            "room_id",
            "description",
            "previous_service_status",
            "reported_at",
            "reported_by_user_id",
            "status",
            "resolved_at",
            "resolved_by_user_id",
            "resolution_note",
            "created_at",
            "updated_at",
        },
        "housing_stays": {
            "id",
            "person_id",
            "room_id",
            "stay_purpose",
            "duration_mode",
            "cohort_id",
            "visit_reason",
            "planned_check_in_at",
            "planned_check_out_at",
            "actual_check_in_at",
            "actual_check_out_at",
            "status",
            "room_name_snapshot",
            "floor_name_snapshot",
            "building_name_snapshot",
            "notes",
            "cancellation_reason",
            "checkout_requested_actual_at",
            "checkout_requested_at",
            "checkout_requested_by_user_id",
            "created_by_user_id",
            "checked_in_by_user_id",
            "checked_out_by_user_id",
            "cancelled_by_user_id",
            "version",
            "created_at",
            "updated_at",
        },
        "housing_transfers": {
            "id",
            "person_id",
            "from_stay_id",
            "to_stay_id",
            "transferred_at",
            "reason",
            "transferred_by_user_id",
            "created_at",
            "updated_at",
        },
    }
    expected_checks = {
        "housing_room_issues": {
            "ck_housing_room_issues_description",
            "ck_housing_room_issues_previous_service_status",
            "ck_housing_room_issues_status",
            "ck_housing_room_issues_resolution",
            "ck_housing_room_issues_resolved_at",
        },
        "housing_stays": {
            "ck_housing_stays_purpose",
            "ck_housing_stays_duration_mode",
            "ck_housing_stays_planned_range",
            "ck_housing_stays_camp_cohort",
            "ck_housing_stays_temporary_details",
            "ck_housing_stays_status",
            "ck_housing_stays_status_fields",
            "ck_housing_stays_snapshot_names",
            "ck_housing_stays_actual_range",
            "ck_housing_stays_checkout_request_fields",
            "ck_housing_stays_version",
        },
        "housing_transfers": {
            "ck_housing_transfers_distinct_stays",
            "ck_housing_transfers_reason",
        },
    }
    expected_indexes = {
        "housing_room_issues": {"uq_housing_room_issues_open_room"},
        "housing_stays": {
            "ix_housing_stays_room_status_time",
            "ix_housing_stays_person_status_time",
            "ix_housing_stays_cohort_status",
        },
        "housing_transfers": set(),
    }
    models = (HousingRoomIssue, HousingStay, HousingTransfer)

    assert HOUSING_OCCUPANCY_TABLES <= set(inspector.get_table_names())
    for model in models:
        table_name = model.__tablename__
        assert set(model.__table__.columns.keys()) == expected_columns[table_name]
        assert {
            column["name"] for column in inspector.get_columns(table_name)
        } == expected_columns[table_name]
        model_check_names = {
            constraint.name
            for constraint in model.__table__.constraints
            if constraint.__class__.__name__ == "CheckConstraint"
        }
        database_check_names = {
            constraint["name"]
            for constraint in inspector.get_check_constraints(table_name)
        }
        assert model_check_names == expected_checks[table_name]
        assert database_check_names == expected_checks[table_name]
        model_index_names = {index.name for index in model.__table__.indexes}
        database_index_names = {
            index["name"] for index in inspector.get_indexes(table_name)
        }
        assert model_index_names == expected_indexes[table_name]
        assert expected_indexes[table_name] <= database_index_names

    with postgres_engine.connect() as connection:
        exclusion_names = set(
            connection.scalars(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = 'housing_stays'::regclass AND contype = 'x'"
                )
            )
        )
        issue_index_definition = connection.scalar(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE schemaname = 'public' "
                "AND indexname = 'uq_housing_room_issues_open_room'"
            )
        )

    assert exclusion_names == {"excl_housing_stays_person_active_overlap"}
    assert issue_index_definition is not None
    assert "unique" in issue_index_definition.lower()
    assert "where ((status)::text = 'open'::text)" in " ".join(
        issue_index_definition.lower().split()
    )
    assert {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("housing_transfers")
    } == {
        "uq_housing_transfers_from_stay",
        "uq_housing_transfers_to_stay",
    }
    for table_name in HOUSING_OCCUPANCY_TABLES:
        assert all(
            foreign_key["options"].get("ondelete") == "RESTRICT"
            for foreign_key in inspector.get_foreign_keys(table_name)
        )


_HOUSING_STAY_INSERT = text(
    "INSERT INTO housing_stays ("
    "id, person_id, room_id, stay_purpose, duration_mode, cohort_id, "
    "visit_reason, planned_check_in_at, planned_check_out_at, "
    "actual_check_in_at, actual_check_out_at, status, room_name_snapshot, "
    "floor_name_snapshot, building_name_snapshot, notes, cancellation_reason, "
    "checkout_requested_actual_at, checkout_requested_at, "
    "checkout_requested_by_user_id, created_by_user_id, checked_in_by_user_id, "
    "checked_out_by_user_id, cancelled_by_user_id, version"
    ") VALUES ("
    ":id, :person_id, :room_id, :stay_purpose, :duration_mode, :cohort_id, "
    ":visit_reason, CAST(:planned_check_in_at AS timestamptz), "
    "CAST(:planned_check_out_at AS timestamptz), "
    "CAST(:actual_check_in_at AS timestamptz), "
    "CAST(:actual_check_out_at AS timestamptz), :status, :room_name_snapshot, "
    ":floor_name_snapshot, :building_name_snapshot, :notes, "
    ":cancellation_reason, CAST(:checkout_requested_actual_at AS timestamptz), "
    "CAST(:checkout_requested_at AS timestamptz), "
    ":checkout_requested_by_user_id, :created_by_user_id, "
    ":checked_in_by_user_id, :checked_out_by_user_id, :cancelled_by_user_id, "
    ":version)"
)


def _housing_room_snapshots(connection: Connection, *, count: int) -> list[dict[str, object]]:
    return [
        dict(row)
        for row in connection.execute(
            text(
                "SELECT rooms.id AS room_id, "
                "rooms.room_number AS room_name_snapshot, "
                "floors.name AS floor_name_snapshot, "
                "buildings.name AS building_name_snapshot "
                "FROM housing_rooms AS rooms "
                "JOIN housing_floors AS floors ON floors.id = rooms.floor_id "
                "JOIN housing_buildings AS buildings "
                "ON buildings.id = rooms.building_id "
                "WHERE rooms.room_type <> 'functional' "
                "ORDER BY rooms.id LIMIT :count"
            ),
            {"count": count},
        ).mappings()
    ]


def _housing_stay_values(
    *,
    stay_id: UUID,
    person_id: UUID,
    account_id: UUID,
    room: dict[str, object],
    planned_check_in_at: str,
    planned_check_out_at: str | None,
    **overrides: object,
) -> dict[str, object]:
    values: dict[str, object] = {
        "id": stay_id,
        "person_id": person_id,
        "room_id": room["room_id"],
        "stay_purpose": "permanent",
        "duration_mode": "fixed_term",
        "cohort_id": None,
        "visit_reason": None,
        "planned_check_in_at": planned_check_in_at,
        "planned_check_out_at": planned_check_out_at,
        "actual_check_in_at": None,
        "actual_check_out_at": None,
        "status": "planned",
        "room_name_snapshot": room["room_name_snapshot"],
        "floor_name_snapshot": room["floor_name_snapshot"],
        "building_name_snapshot": room["building_name_snapshot"],
        "notes": None,
        "cancellation_reason": None,
        "checkout_requested_actual_at": None,
        "checkout_requested_at": None,
        "checkout_requested_by_user_id": None,
        "created_by_user_id": account_id,
        "checked_in_by_user_id": None,
        "checked_out_by_user_id": None,
        "cancelled_by_user_id": None,
        "version": 1,
    }
    values.update(overrides)
    return values


def test_housing_room_issue_constraints_reject_invalid_and_duplicate_open_rows(
    postgres_engine: Engine,
) -> None:
    person_id, account_id = _insert_test_account(postgres_engine)
    issue_id = uuid4()
    duplicate_id = uuid4()
    invalid_id = uuid4()
    try:
        with postgres_engine.begin() as connection:
            room_id = connection.scalar(
                text(
                    "SELECT id FROM housing_rooms "
                    "WHERE room_type <> 'functional' ORDER BY id LIMIT 1"
                )
            )
            assert room_id is not None
            connection.execute(
                text(
                    "INSERT INTO housing_room_issues "
                    "(id, room_id, description, previous_service_status, "
                    "reported_at, reported_by_user_id, status) VALUES "
                    "(:id, :room_id, '空调故障', 'available', CURRENT_TIMESTAMP, "
                    ":account_id, 'open')"
                ),
                {"id": issue_id, "room_id": room_id, "account_id": account_id},
            )

            with pytest.raises(
                DBAPIError,
                match="uq_housing_room_issues_open_room",
            ), connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO housing_room_issues "
                        "(id, room_id, description, previous_service_status, "
                        "reported_at, reported_by_user_id, status) VALUES "
                        "(:id, :room_id, '重复问题', 'problem', CURRENT_TIMESTAMP, "
                        ":account_id, 'open')"
                    ),
                    {
                        "id": duplicate_id,
                        "room_id": room_id,
                        "account_id": account_id,
                    },
                )

            with pytest.raises(
                DBAPIError,
                match="ck_housing_room_issues_resolution",
            ), connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO housing_room_issues "
                        "(id, room_id, description, previous_service_status, "
                        "reported_at, reported_by_user_id, status, "
                        "resolution_note) VALUES "
                        "(:id, :room_id, '状态字段不完整', 'available', "
                        "CURRENT_TIMESTAMP, :account_id, 'resolved', '已处理')"
                    ),
                    {
                        "id": invalid_id,
                        "room_id": room_id,
                        "account_id": account_id,
                    },
                )

            assert connection.scalar(
                text(
                    "SELECT count(*) FROM housing_room_issues "
                    "WHERE id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": [issue_id, duplicate_id, invalid_id]},
            ) == 1
            connection.execute(
                text("DELETE FROM housing_room_issues WHERE id = :id"),
                {"id": issue_id},
            )
    finally:
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )


def test_housing_stay_constraints_and_active_overlap_are_enforced(
    postgres_engine: Engine,
) -> None:
    person_id, account_id = _insert_test_account(postgres_engine)
    stay_ids: list[UUID] = []
    try:
        with postgres_engine.begin() as connection:
            rooms = _housing_room_snapshots(connection, count=3)
            assert len(rooms) == 3
            first_id = uuid4()
            adjacent_id = uuid4()
            stay_ids.extend((first_id, adjacent_id))
            connection.execute(
                _HOUSING_STAY_INSERT,
                _housing_stay_values(
                    stay_id=first_id,
                    person_id=person_id,
                    account_id=account_id,
                    room=rooms[0],
                    planned_check_in_at="2026-10-01 08:00:00+08",
                    planned_check_out_at="2026-10-10 08:00:00+08",
                ),
            )

            with pytest.raises(
                DBAPIError,
                match="excl_housing_stays_person_active_overlap",
            ), connection.begin_nested():
                connection.execute(
                    _HOUSING_STAY_INSERT,
                    _housing_stay_values(
                        stay_id=uuid4(),
                        person_id=person_id,
                        account_id=account_id,
                        room=rooms[1],
                        planned_check_in_at="2026-10-05 08:00:00+08",
                        planned_check_out_at="2026-10-11 08:00:00+08",
                    ),
                )

            connection.execute(
                _HOUSING_STAY_INSERT,
                _housing_stay_values(
                    stay_id=adjacent_id,
                    person_id=person_id,
                    account_id=account_id,
                    room=rooms[1],
                    planned_check_in_at="2026-10-10 08:00:00+08",
                    planned_check_out_at="2026-10-20 08:00:00+08",
                ),
            )

            invalid_cases = (
                (
                    "ck_housing_stays_planned_range",
                    {"planned_check_out_at": None},
                ),
                (
                    "ck_housing_stays_camp_cohort",
                    {"stay_purpose": "camp"},
                ),
                (
                    "ck_housing_stays_temporary_details",
                    {"stay_purpose": "temporary", "visit_reason": "   "},
                ),
                (
                    "ck_housing_stays_status_fields",
                    {"status": "checked_in"},
                ),
                (
                    "ck_housing_stays_checkout_request_fields",
                    {
                        "status": "checkout_pending",
                        "actual_check_in_at": "2026-12-01 09:00:00+08",
                        "checked_in_by_user_id": account_id,
                        "checkout_requested_actual_at": "2026-12-09 08:00:00+08",
                    },
                ),
                (
                    "ck_housing_stays_actual_range",
                    {
                        "status": "checked_out",
                        "actual_check_in_at": "2026-12-02 09:00:00+08",
                        "actual_check_out_at": "2026-12-02 08:00:00+08",
                        "checked_in_by_user_id": account_id,
                        "checked_out_by_user_id": account_id,
                    },
                ),
                (
                    "ck_housing_stays_version",
                    {
                        "status": "cancelled",
                        "cancellation_reason": "计划取消",
                        "cancelled_by_user_id": account_id,
                        "version": 0,
                    },
                ),
            )
            for constraint_name, overrides in invalid_cases:
                values = _housing_stay_values(
                    stay_id=uuid4(),
                    person_id=person_id,
                    account_id=account_id,
                    room=rooms[2],
                    planned_check_in_at="2026-12-01 08:00:00+08",
                    planned_check_out_at="2026-12-10 08:00:00+08",
                )
                values.update(overrides)
                with pytest.raises(
                    DBAPIError,
                    match=constraint_name,
                ), connection.begin_nested():
                    connection.execute(_HOUSING_STAY_INSERT, values)

            system_pending_id = uuid4()
            manual_pending_id = uuid4()
            stay_ids.extend((system_pending_id, manual_pending_id))
            connection.execute(
                _HOUSING_STAY_INSERT,
                _housing_stay_values(
                    stay_id=system_pending_id,
                    person_id=person_id,
                    account_id=account_id,
                    room=rooms[2],
                    planned_check_in_at="2026-12-01 08:00:00+08",
                    planned_check_out_at="2026-12-10 08:00:00+08",
                )
                | {
                    "status": "checkout_pending",
                    "actual_check_in_at": "2026-12-01 09:00:00+08",
                    "checked_in_by_user_id": account_id,
                },
            )
            connection.execute(
                _HOUSING_STAY_INSERT,
                _housing_stay_values(
                    stay_id=manual_pending_id,
                    person_id=person_id,
                    account_id=account_id,
                    room=rooms[2],
                    planned_check_in_at="2026-12-10 08:00:00+08",
                    planned_check_out_at="2026-12-20 08:00:00+08",
                )
                | {
                    "status": "checkout_pending",
                    "actual_check_in_at": "2026-12-10 09:00:00+08",
                    "checked_in_by_user_id": account_id,
                    "checkout_requested_actual_at": "2026-12-19 08:00:00+08",
                    "checkout_requested_at": "2026-12-15 08:00:00+08",
                    "checkout_requested_by_user_id": account_id,
                },
            )

            assert connection.scalar(
                text(
                    "SELECT count(*) FROM housing_stays "
                    "WHERE id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": stay_ids},
            ) == 4
            connection.execute(
                text(
                    "DELETE FROM housing_stays "
                    "WHERE id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": stay_ids},
            )
    finally:
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )


def test_housing_stay_exclusion_serializes_concurrent_overlaps(
    postgres_engine: Engine,
) -> None:
    person_id, account_id = _insert_test_account(postgres_engine)
    first_stay_id = uuid4()
    competing_stay_id = uuid4()
    backend_pids: Queue[int] = Queue()
    first_connection = postgres_engine.connect()
    first_transaction = first_connection.begin()
    pool = ThreadPoolExecutor(max_workers=1)

    with postgres_engine.connect() as connection:
        rooms = _housing_room_snapshots(connection, count=2)
    assert len(rooms) == 2
    first_values = _housing_stay_values(
        stay_id=first_stay_id,
        person_id=person_id,
        account_id=account_id,
        room=rooms[0],
        planned_check_in_at="2027-05-01 08:00:00+08",
        planned_check_out_at="2027-05-10 08:00:00+08",
    )
    competing_values = _housing_stay_values(
        stay_id=competing_stay_id,
        person_id=person_id,
        account_id=account_id,
        room=rooms[1],
        planned_check_in_at="2027-05-05 08:00:00+08",
        planned_check_out_at="2027-05-12 08:00:00+08",
    )

    def insert_competing_stay() -> None:
        with postgres_engine.connect() as connection:
            backend_pids.put(
                int(connection.scalar(text("SELECT pg_backend_pid()")) or 0)
            )
            connection.commit()
            transaction = connection.begin()
            try:
                connection.execute(text("SET LOCAL statement_timeout = '8s'"))
                connection.execute(_HOUSING_STAY_INSERT, competing_values)
                transaction.commit()
            except DBAPIError:
                transaction.rollback()
                assert connection.scalar(text("SELECT 1")) == 1
                raise

    future = None
    try:
        first_connection.execute(_HOUSING_STAY_INSERT, first_values)
        future = pool.submit(insert_competing_stay)
        competing_backend_pid = backend_pids.get(timeout=5)
        assert competing_backend_pid > 0

        deadline = monotonic() + 5
        waiting_on_lock = False
        while monotonic() < deadline:
            with postgres_engine.connect() as connection:
                waiting_on_lock = bool(
                    connection.scalar(
                        text(
                            "SELECT wait_event_type = 'Lock' "
                            "FROM pg_stat_activity WHERE pid = :pid"
                        ),
                        {"pid": competing_backend_pid},
                    )
                )
            if waiting_on_lock:
                break
            sleep(0.02)
        assert waiting_on_lock, "overlapping insert did not wait on the first stay"
        assert future.done() is False

        first_transaction.commit()
        assert first_connection.scalar(text("SELECT 1")) == 1
        with pytest.raises(
            DBAPIError,
            match="excl_housing_stays_person_active_overlap",
        ):
            future.result(timeout=10)
    finally:
        if first_transaction.is_active:
            first_transaction.rollback()
        first_connection.close()
        if future is not None:
            with suppress(DBAPIError, AssertionError, TimeoutError):
                future.result(timeout=10)
        pool.shutdown(wait=True)
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM housing_stays "
                    "WHERE id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": [first_stay_id, competing_stay_id]},
            )
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )


def test_housing_transfer_constraints_enforce_distinct_and_one_to_one_stays(
    postgres_engine: Engine,
) -> None:
    person_id, account_id = _insert_test_account(postgres_engine)
    stay_ids = [uuid4() for _ in range(4)]
    transfer_id = uuid4()
    try:
        with postgres_engine.begin() as connection:
            rooms = _housing_room_snapshots(connection, count=4)
            assert len(rooms) == 4
            for index, (stay_id, room) in enumerate(zip(stay_ids, rooms, strict=True)):
                connection.execute(
                    _HOUSING_STAY_INSERT,
                    _housing_stay_values(
                        stay_id=stay_id,
                        person_id=person_id,
                        account_id=account_id,
                        room=room,
                        planned_check_in_at=f"2027-0{index + 1}-01 08:00:00+08",
                        planned_check_out_at=f"2027-0{index + 1}-10 08:00:00+08",
                        status="cancelled",
                        cancellation_reason="调房测试准备",
                        cancelled_by_user_id=account_id,
                    ),
                )
            connection.execute(
                text(
                    "INSERT INTO housing_transfers "
                    "(id, person_id, from_stay_id, to_stay_id, transferred_at, "
                    "reason, transferred_by_user_id) VALUES "
                    "(:id, :person_id, :from_id, :to_id, CURRENT_TIMESTAMP, "
                    "'更换房间', :account_id)"
                ),
                {
                    "id": transfer_id,
                    "person_id": person_id,
                    "from_id": stay_ids[0],
                    "to_id": stay_ids[1],
                    "account_id": account_id,
                },
            )

            invalid_transfers = (
                (
                    "ck_housing_transfers_distinct_stays",
                    stay_ids[2],
                    stay_ids[2],
                    "不同入住记录",
                ),
                (
                    "uq_housing_transfers_from_stay",
                    stay_ids[0],
                    stay_ids[2],
                    "重复来源",
                ),
                (
                    "uq_housing_transfers_to_stay",
                    stay_ids[2],
                    stay_ids[1],
                    "重复目标",
                ),
                (
                    "ck_housing_transfers_reason",
                    stay_ids[2],
                    stay_ids[3],
                    "   ",
                ),
            )
            for constraint_name, from_id, to_id, reason in invalid_transfers:
                with pytest.raises(
                    DBAPIError,
                    match=constraint_name,
                ), connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO housing_transfers "
                            "(id, person_id, from_stay_id, to_stay_id, "
                            "transferred_at, reason, transferred_by_user_id) "
                            "VALUES (:id, :person_id, :from_id, :to_id, "
                            "CURRENT_TIMESTAMP, :reason, :account_id)"
                        ),
                        {
                            "id": uuid4(),
                            "person_id": person_id,
                            "from_id": from_id,
                            "to_id": to_id,
                            "reason": reason,
                            "account_id": account_id,
                        },
                    )

            assert connection.scalar(
                text(
                    "SELECT count(*) FROM housing_transfers WHERE id = :id"
                ),
                {"id": transfer_id},
            ) == 1
            connection.execute(
                text("DELETE FROM housing_transfers WHERE id = :id"),
                {"id": transfer_id},
            )
            connection.execute(
                text(
                    "DELETE FROM housing_stays "
                    "WHERE id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": stay_ids},
            )
    finally:
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )


def test_evaluation_bridge_schema_has_fixed_constraints(
    postgres_engine: Engine,
) -> None:
    inspector = inspect(postgres_engine)
    tables = set(inspector.get_table_names())
    request_columns = {
        item["name"]: item
        for item in inspector.get_columns("evaluation_bridge_requests")
    }
    ticket_columns = {
        item["name"]: item
        for item in inspector.get_columns("evaluation_bridge_tickets")
    }
    request_checks = {
        item["name"]: item["sqltext"]
        for item in inspector.get_check_constraints("evaluation_bridge_requests")
    }
    ticket_checks = {
        item["name"]: item["sqltext"]
        for item in inspector.get_check_constraints("evaluation_bridge_tickets")
    }
    request_indexes = {
        item["name"]: item
        for item in inspector.get_indexes("evaluation_bridge_requests")
    }
    ticket_indexes = {
        item["name"]: item
        for item in inspector.get_indexes("evaluation_bridge_tickets")
    }

    assert EVALUATION_TABLES <= tables
    assert {
        name: (str(column["type"]), column["nullable"], column["default"])
        for name, column in request_columns.items()
    } == {
        "id": ("UUID", False, None),
        "client_id": ("VARCHAR(64)", False, None),
        "mode": ("VARCHAR(16)", False, None),
        "state_ciphertext": ("TEXT", False, None),
        "expires_at": ("TIMESTAMP", False, None),
        "authorized_at": ("TIMESTAMP", True, None),
        "created_at": ("TIMESTAMP", False, "now()"),
        "updated_at": ("TIMESTAMP", False, "now()"),
    }
    assert {
        name: (str(column["type"]), column["nullable"], column["default"])
        for name, column in ticket_columns.items()
    } == {
        "id": ("UUID", False, None),
        "login_request_id": ("UUID", False, None),
        "auth_session_id": ("UUID", False, None),
        "account_id": ("UUID", False, None),
        "token_digest": ("VARCHAR(64)", False, None),
        "token_ciphertext": ("TEXT", True, None),
        "expires_at": ("TIMESTAMP", False, None),
        "consumed_at": ("TIMESTAMP", True, None),
        "created_at": ("TIMESTAMP", False, "now()"),
        "updated_at": ("TIMESTAMP", False, "now()"),
    }
    for column_name in ("expires_at", "authorized_at", "created_at", "updated_at"):
        assert request_columns[column_name]["type"].timezone is True
    for column_name in ("expires_at", "consumed_at", "created_at", "updated_at"):
        assert ticket_columns[column_name]["type"].timezone is True
    assert inspector.get_pk_constraint("evaluation_bridge_requests")[
        "constrained_columns"
    ] == ["id"]
    assert inspector.get_pk_constraint("evaluation_bridge_tickets")[
        "constrained_columns"
    ] == ["id"]
    assert request_checks["ck_evaluation_bridge_requests_expiry"] == (
        "expires_at > created_at"
    )
    mode_check = request_checks["ck_evaluation_bridge_requests_mode"]
    assert "standalone" in mode_check and "embedded" in mode_check
    assert ticket_checks == {
        "ck_evaluation_bridge_tickets_expiry": "expires_at > created_at"
    }
    request_expiry_index = request_indexes["ix_evaluation_bridge_requests_expiry"]
    assert request_expiry_index["unique"] is False
    assert request_expiry_index["column_names"] == ["expires_at", "id"]
    ticket_expiry_index = ticket_indexes["ix_evaluation_bridge_tickets_expiry"]
    assert ticket_expiry_index["unique"] is False
    assert ticket_expiry_index["column_names"] == ["expires_at", "id"]
    assert {
        tuple(item["column_names"])
        for item in inspector.get_unique_constraints("evaluation_bridge_tickets")
    } == {("login_request_id",), ("token_digest",)}
    assert inspector.get_unique_constraints("evaluation_bridge_requests") == []
    assert inspector.get_foreign_keys("evaluation_bridge_requests") == []
    assert {
        (
            tuple(item["constrained_columns"]),
            item["referred_table"],
            tuple(item["referred_columns"]),
            item["options"].get("ondelete"),
        )
        for item in inspector.get_foreign_keys("evaluation_bridge_tickets")
    } == {
        (("login_request_id",), "evaluation_bridge_requests", ("id",), "CASCADE"),
        (("auth_session_id",), "auth_sessions", ("id",), "CASCADE"),
        (("account_id",), "user_accounts", ("id",), "CASCADE"),
    }


def test_evaluation_foundation_schema_has_history_and_uniqueness_guards(
    postgres_engine: Engine,
) -> None:
    inspector = inspect(postgres_engine)
    assert EVALUATION_FOUNDATION_TABLES <= set(inspector.get_table_names())

    student_columns = {
        column["name"]: column for column in inspector.get_columns("student_profiles")
    }
    assert student_columns["game_account_limit"]["nullable"] is False
    assert student_columns["game_account_limit"]["default"] == "3"
    assert "ck_student_profiles_game_account_limit" in {
        check["name"] for check in inspector.get_check_constraints("student_profiles")
    }

    override_columns = {
        column["name"]: column
        for column in inspector.get_columns("user_permission_overrides")
    }
    assert {
        "valid_from",
        "valid_until",
        "created_by_user_id",
        "grant_reason",
        "created_at",
    } <= override_columns.keys()
    assert override_columns["valid_from"]["type"].timezone is True
    assert override_columns["valid_until"]["type"].timezone is True
    assert override_columns["created_at"]["nullable"] is False
    override_checks = {
        check["name"]: str(check["sqltext"]).lower()
        for check in inspector.get_check_constraints("user_permission_overrides")
    }
    assert {
        "ck_permission_overrides_scope_kind",
        "ck_permission_overrides_scope_id",
        "ck_permission_overrides_validity",
    } <= override_checks.keys()
    for scope_kind in ("cohort", "project", "enrollment"):
        assert scope_kind in override_checks["ck_permission_overrides_scope_kind"]
        assert scope_kind in override_checks["ck_permission_overrides_scope_id"]
    override_actor_fk = next(
        foreign_key
        for foreign_key in inspector.get_foreign_keys("user_permission_overrides")
        if foreign_key["constrained_columns"] == ["created_by_user_id"]
    )
    assert override_actor_fk["referred_table"] == "user_accounts"
    assert override_actor_fk["options"]["ondelete"] == "RESTRICT"

    assert ("id", "student_person_id") in {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("enrollments")
    }
    assert "uq_student_game_accounts_active_project_digest" in {
        index["name"] for index in inspector.get_indexes("student_game_accounts")
    }
    with postgres_engine.connect() as connection:
        history_index_definitions = dict(
            connection.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE schemaname = 'public' "
                    "AND indexname = ANY(CAST(:names AS text[]))"
                ),
                {
                    "names": [
                        "ix_student_game_accounts_student_history",
                        "ix_student_auxiliary_phones_student_history",
                    ]
                },
            ).tuples().all()
        )
    expected_history_indexes = {
        "ix_student_game_accounts_student_history": "student_game_accounts",
        "ix_student_auxiliary_phones_student_history": "student_auxiliary_phones",
    }
    assert history_index_definitions.keys() == expected_history_indexes.keys()
    for index_name, table_name in expected_history_indexes.items():
        definition = " ".join(history_index_definitions[index_name].lower().split())
        assert (
            f"on public.{table_name} using btree "
            "(student_person_id, valid_from desc, id desc)"
        ) in definition
    assert "uq_project_analysis_profiles_project_version" in {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("project_analysis_profiles")
    }
    assert "uq_evaluation_media_blobs_sha256" in {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("evaluation_media_blobs")
    }

    asset_foreign_keys = {
        foreign_key["name"]: foreign_key
        for foreign_key in inspector.get_foreign_keys("evaluation_media_assets")
    }
    enrollment_target = asset_foreign_keys["fk_evaluation_media_assets_enrollment_student"]
    assert enrollment_target["constrained_columns"] == [
        "enrollment_id",
        "student_person_id",
    ]
    assert enrollment_target["referred_columns"] == ["id", "student_person_id"]
    assert enrollment_target["options"]["ondelete"] == "RESTRICT"

    with postgres_engine.connect() as connection:
        exclusions = set(
            connection.scalars(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid IN ("
                    "'cohort_staff_assignments'::regclass, "
                    "'student_game_accounts'::regclass) AND contype = 'x'"
                )
            )
        )
        triggers = set(
            connection.scalars(
                text(
                    "SELECT tgname FROM pg_trigger "
                    "WHERE tgrelid = 'evaluation_media_assets'::regclass "
                    "AND NOT tgisinternal"
                )
            )
        )
        profiles = set(
            connection.execute(
                text(
                    "SELECT project_id, version, mode, publish_mode, "
                    "configured_by_user_id, change_reason "
                    "FROM project_analysis_profiles"
                )
            ).tuples()
        )

    assert {
        "excl_cohort_staff_assignments_project_overlap",
        "excl_cohort_staff_assignments_class_overlap",
        "excl_student_game_accounts_project_digest_overlap",
    } <= exclusions
    assert "trg_evaluation_media_assets_target_immutable" in triggers
    assert profiles == {
        (project_id, 1, "manual_only", "review_required", None, "migration backfill")
        for project_id in INITIAL_PROJECT_IDS
    }

    with postgres_engine.connect() as connection:
        role = connection.execute(
            text(
                "SELECT key, name, category, is_builtin, is_active, "
                "created_by_user_id FROM evaluation_staff_roles WHERE id = :role_id"
            ),
            {"role_id": EVALUATION_LIFE_TEACHER_ROLE_ID},
        ).one()
    assert role == ("life_teacher", "生活老师", "life_service", True, True, None)


def test_project_analysis_profile_project_and_version_are_immutable(
    postgres_engine: Engine,
) -> None:
    person_id, account_id = _insert_test_account(postgres_engine)
    replacement_project_id = uuid4()
    try:
        with postgres_engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(
                    text(
                        "INSERT INTO game_projects "
                        "(id, name, sort_order, is_active, created_by_user_id) "
                        "VALUES (:id, '分析档案不可变测试项目', 99, true, :actor_id)"
                    ),
                    {"id": replacement_project_id, "actor_id": account_id},
                )
                with pytest.raises(
                    DBAPIError,
                    match="analysis profile project and version are immutable",
                ):
                    connection.execute(
                        text(
                            "UPDATE project_analysis_profiles SET project_id = :project_id "
                            "WHERE project_id = :original_project_id AND version = 1"
                        ),
                        {
                            "project_id": replacement_project_id,
                            "original_project_id": INITIAL_PROJECT_IDS[0],
                        },
                    )
            finally:
                transaction.rollback()

        with postgres_engine.connect() as connection:
            transaction = connection.begin()
            try:
                with pytest.raises(
                    DBAPIError,
                    match="analysis profile project and version are immutable",
                ):
                    connection.execute(
                        text(
                            "UPDATE project_analysis_profiles SET version = 2, "
                            "configured_by_user_id = :actor_id, "
                            "change_reason = 'version rewrite test' "
                            "WHERE project_id = :project_id AND version = 1"
                        ),
                        {
                            "actor_id": account_id,
                            "project_id": INITIAL_PROJECT_IDS[0],
                        },
                    )
            finally:
                transaction.rollback()
    finally:
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )


def test_project_analysis_profile_rows_are_immutable(
    postgres_engine: Engine,
) -> None:
    with postgres_engine.connect() as connection:
        transaction = connection.begin()
        try:
            with pytest.raises(
                DBAPIError,
                match="analysis profile rows are immutable",
            ):
                connection.execute(
                    text(
                        "UPDATE project_analysis_profiles "
                        "SET effective_from = effective_from + INTERVAL '1 second' "
                        "WHERE project_id = :project_id AND version = 1"
                    ),
                    {"project_id": INITIAL_PROJECT_IDS[0]},
                )
        finally:
            transaction.rollback()


def test_used_evaluation_staff_role_can_only_be_deactivated(
    postgres_engine: Engine,
) -> None:
    staff_person_id, actor_id = _insert_test_account(postgres_engine)
    cohort_id = uuid4()
    role_id = uuid4()
    assignment_id = uuid4()
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO cohorts "
                    "(id, name, starts_at, ends_at, default_check_in_at, "
                    "default_check_out_at, status, created_by_user_id) VALUES "
                    "(:id, '角色不可变测试届次', TIMESTAMPTZ '2026-09-01 08:00:00+08', "
                    "TIMESTAMPTZ '2026-09-21 18:00:00+08', "
                    "TIMESTAMPTZ '2026-09-01 08:00:00+08', "
                    "TIMESTAMPTZ '2026-09-21 18:00:00+08', 'draft', :actor_id)"
                ),
                {"id": cohort_id, "actor_id": actor_id},
            )
            connection.execute(
                text(
                    "INSERT INTO evaluation_staff_roles "
                    "(id, key, name, category, is_builtin, is_active, "
                    "created_by_user_id) VALUES "
                    "(:id, :key, '角色不可变测试', 'operations', false, true, "
                    ":actor_id)"
                ),
                {
                    "id": role_id,
                    "key": f"immutable_{role_id.hex[:20]}",
                    "actor_id": actor_id,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO cohort_staff_assignments "
                    "(id, cohort_id, role_id, staff_person_id, project_id, "
                    "role_name_snapshot, role_category_snapshot, valid_from, "
                    "created_by_user_id) VALUES "
                    "(:id, :cohort_id, :role_id, :staff_person_id, :project_id, "
                    "'角色不可变测试', 'operations', CURRENT_TIMESTAMP, :actor_id)"
                ),
                {
                    "id": assignment_id,
                    "cohort_id": cohort_id,
                    "role_id": role_id,
                    "staff_person_id": staff_person_id,
                    "project_id": INITIAL_PROJECT_IDS[0],
                    "actor_id": actor_id,
                },
            )

        with (
            pytest.raises(DBAPIError, match="used evaluation staff role is immutable"),
            postgres_engine.begin() as connection,
        ):
            connection.execute(
                text(
                    "UPDATE evaluation_staff_roles SET name = '覆盖历史名称' "
                    "WHERE id = :id"
                ),
                {"id": role_id},
            )

        with postgres_engine.begin() as connection:
            connection.execute(
                text("UPDATE evaluation_staff_roles SET is_active = false WHERE id = :id"),
                {"id": role_id},
            )
            assert connection.scalar(
                text("SELECT is_active FROM evaluation_staff_roles WHERE id = :id"),
                {"id": role_id},
            ) is False

        with (
            pytest.raises(DBAPIError, match="used evaluation staff role is immutable"),
            postgres_engine.begin() as connection,
        ):
            connection.execute(
                text("UPDATE evaluation_staff_roles SET is_active = true WHERE id = :id"),
                {"id": role_id},
            )
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM cohort_staff_assignments WHERE id = :id"),
                {"id": assignment_id},
            )
            connection.execute(
                text("DELETE FROM evaluation_staff_roles WHERE id = :id"),
                {"id": role_id},
            )
            connection.execute(
                text("DELETE FROM cohorts WHERE id = :id"),
                {"id": cohort_id},
            )
        _delete_test_account(
            postgres_engine,
            person_id=staff_person_id,
            account_id=actor_id,
        )


@pytest.mark.parametrize(
    ("sql", "params", "constraint_name"),
    [
        (
            (
                "INSERT INTO evaluation_media_blobs "
                "(id, storage_key, preview_storage_key, sha256, mime_type, "
                "canonical_extension, size_bytes, width_pixels, height_pixels, "
                "storage_status, validation_status, retention_status) VALUES "
                "(:id, '/absolute/file.jpg', 'previews/file.jpg', :sha256, "
                "'image/jpeg', 'jpg', 100, 10, 10, 'available', 'validated', "
                "'retained')"
            ),
            {},
            "ck_evaluation_media_blobs_storage_keys",
        ),
        (
            (
                "INSERT INTO evaluation_media_blobs "
                "(id, storage_key, preview_storage_key, sha256, mime_type, "
                "canonical_extension, size_bytes, width_pixels, height_pixels, "
                "storage_status, validation_status, retention_status) VALUES "
                "(:id, 'objects/file.png', 'previews/file.png', :sha256, "
                "'image/png', 'webp', 100, 10, 10, 'available', 'validated', "
                "'retained')"
            ),
            {},
            "ck_evaluation_media_blobs_mime_extension",
        ),
    ],
    ids=("absolute-path", "mime-extension"),
)
def test_evaluation_media_blob_checks_reject_invalid_sql(
    postgres_engine: Engine,
    sql: str,
    params: dict[str, object],
    constraint_name: str,
) -> None:
    values = {"id": uuid4(), "sha256": uuid4().hex * 2, **params}
    with pytest.raises(DBAPIError) as error, postgres_engine.begin() as connection:
        connection.execute(text(sql), values)

    assert getattr(error.value.orig.diag, "constraint_name", None) == constraint_name


def test_evaluation_override_checks_reject_invalid_new_scope_and_window(
    postgres_engine: Engine,
) -> None:
    person_id, account_id = _insert_test_account(postgres_engine)
    try:
        with pytest.raises(DBAPIError) as scope_error, postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO user_permission_overrides "
                    "(id, user_id, permission_id, effect, scope_kind, scope_id) "
                    "VALUES (:id, :user_id, :permission_id, 'allow', 'project', NULL)"
                ),
                {
                    "id": uuid4(),
                    "user_id": account_id,
                    "permission_id": EVALUATION_FOUNDATION_PERMISSION_IDS[
                        "evaluation_media.upload"
                    ],
                },
            )
        assert getattr(scope_error.value.orig.diag, "constraint_name", None) == (
            "ck_permission_overrides_scope_id"
        )

        with pytest.raises(DBAPIError) as time_error, postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO user_permission_overrides "
                    "(id, user_id, permission_id, effect, scope_kind, scope_id, "
                    "valid_until) VALUES "
                    "(:id, :user_id, :permission_id, 'allow', 'project', "
                    ":project_id, CURRENT_TIMESTAMP)"
                ),
                {
                    "id": uuid4(),
                    "user_id": account_id,
                    "permission_id": EVALUATION_FOUNDATION_PERMISSION_IDS[
                        "evaluation_media.upload"
                    ],
                    "project_id": INITIAL_PROJECT_IDS[0],
                },
            )
        assert getattr(time_error.value.orig.diag, "constraint_name", None) == (
            "ck_permission_overrides_validity"
        )
    finally:
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )


def test_student_game_account_history_rejects_overlap_and_allows_adjacency(
    postgres_engine: Engine,
) -> None:
    _guardian_id, student_id = _insert_people(postgres_engine)
    actor_person_id, actor_id = _insert_test_account(postgres_engine)
    first_id = uuid4()
    adjacent_id = uuid4()
    digest = "a" * 64
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO student_profiles "
                    "(person_id, student_number, status) "
                    "VALUES (:student_id, :student_number, 'active')"
                ),
                {
                    "student_id": student_id,
                    "student_number": f"migration-{student_id.hex[:20]}",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO student_game_accounts "
                    "(id, student_person_id, project_id, identifier_ciphertext, "
                    "identifier_digest, valid_from, unbound_at, unbound_by_user_id, "
                    "unbind_reason, created_by_user_id) VALUES "
                    "(:id, :student_id, :project_id, 'ciphertext', :digest, "
                    "TIMESTAMPTZ '2026-08-01 00:00:00+00', "
                    "TIMESTAMPTZ '2026-08-10 00:00:00+00', :actor_id, "
                    "'history test', :actor_id)"
                ),
                {
                    "id": first_id,
                    "student_id": student_id,
                    "project_id": INITIAL_PROJECT_IDS[0],
                    "digest": digest,
                    "actor_id": actor_id,
                },
            )

        with pytest.raises(DBAPIError) as error, postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO student_game_accounts "
                    "(id, student_person_id, project_id, identifier_ciphertext, "
                    "identifier_digest, valid_from, unbound_at, unbound_by_user_id, "
                    "unbind_reason, created_by_user_id) VALUES "
                    "(:id, :student_id, :project_id, 'ciphertext', :digest, "
                    "TIMESTAMPTZ '2026-08-09 00:00:00+00', "
                    "TIMESTAMPTZ '2026-08-20 00:00:00+00', :actor_id, "
                    "'overlap test', :actor_id)"
                ),
                {
                    "id": uuid4(),
                    "student_id": student_id,
                    "project_id": INITIAL_PROJECT_IDS[0],
                    "digest": digest,
                    "actor_id": actor_id,
                },
            )
        assert getattr(error.value.orig.diag, "constraint_name", None) == (
            "excl_student_game_accounts_project_digest_overlap"
        )

        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO student_game_accounts "
                    "(id, student_person_id, project_id, identifier_ciphertext, "
                    "identifier_digest, valid_from, unbound_at, unbound_by_user_id, "
                    "unbind_reason, created_by_user_id) VALUES "
                    "(:id, :student_id, :project_id, 'ciphertext', :digest, "
                    "TIMESTAMPTZ '2026-08-10 00:00:00+00', "
                    "TIMESTAMPTZ '2026-08-20 00:00:00+00', :actor_id, "
                    "'adjacent test', :actor_id)"
                ),
                {
                    "id": adjacent_id,
                    "student_id": student_id,
                    "project_id": INITIAL_PROJECT_IDS[0],
                    "digest": digest,
                    "actor_id": actor_id,
                },
            )
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM student_game_accounts WHERE id IN (:first, :adjacent)"),
                {"first": first_id, "adjacent": adjacent_id},
            )
            connection.execute(
                text("DELETE FROM student_profiles WHERE person_id = :student_id"),
                {"student_id": student_id},
            )
        _delete_test_account(
            postgres_engine,
            person_id=actor_person_id,
            account_id=actor_id,
        )
        _delete_people(postgres_engine, _guardian_id, student_id)


def test_evaluation_media_asset_target_can_be_set_once_only(
    postgres_engine: Engine,
) -> None:
    guardian_id, student_id = _insert_people(postgres_engine)
    actor_person_id, actor_id = _insert_test_account(postgres_engine)
    cohort_id = uuid4()
    class_id = uuid4()
    case_id = uuid4()
    enrollment_id = uuid4()
    blob_id = uuid4()
    asset_id = uuid4()
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO student_profiles "
                    "(person_id, student_number, status) "
                    "VALUES (:student_id, :student_number, 'active')"
                ),
                {
                    "student_id": student_id,
                    "student_number": f"media-{student_id.hex[:24]}",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO cohorts "
                    "(id, name, starts_at, ends_at, default_check_in_at, "
                    "default_check_out_at, status, created_by_user_id) VALUES "
                    "(:id, '媒体目标测试届次', TIMESTAMPTZ '2026-09-01 08:00:00+08', "
                    "TIMESTAMPTZ '2026-12-31 18:00:00+08', "
                    "TIMESTAMPTZ '2026-09-01 08:00:00+08', "
                    "TIMESTAMPTZ '2026-09-01 18:00:00+08', 'draft', :actor_id)"
                ),
                {"id": cohort_id, "actor_id": actor_id},
            )
            connection.execute(
                text(
                    "INSERT INTO teaching_classes "
                    "(id, cohort_id, project_id, name, class_type, capacity, "
                    "default_tuition_cents, created_by_user_id) VALUES "
                    "(:id, :cohort_id, :project_id, '媒体目标测试班', 'standard', "
                    "20, 0, :actor_id)"
                ),
                {
                    "id": class_id,
                    "cohort_id": cohort_id,
                    "project_id": INITIAL_PROJECT_IDS[0],
                    "actor_id": actor_id,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO admission_cases "
                    "(id, student_person_id, status, created_by_user_id) "
                    "VALUES (:id, :student_id, 'ready', :actor_id)"
                ),
                {"id": case_id, "student_id": student_id, "actor_id": actor_id},
            )
            connection.execute(
                text(
                    "INSERT INTO enrollments "
                    "(id, admission_case_id, student_person_id, cohort_id, class_id, "
                    "agreed_tuition_cents, status, confirmed_at, created_by_user_id) "
                    "VALUES (:id, :case_id, :student_id, :cohort_id, :class_id, 0, "
                    "'active', CURRENT_TIMESTAMP, :actor_id)"
                ),
                {
                    "id": enrollment_id,
                    "case_id": case_id,
                    "student_id": student_id,
                    "cohort_id": cohort_id,
                    "class_id": class_id,
                    "actor_id": actor_id,
                },
            )
            connection.execute(
                text(
                    "UPDATE admission_cases SET status = 'enrolled', "
                    "current_enrollment_id = :enrollment_id WHERE id = :case_id"
                ),
                {"enrollment_id": enrollment_id, "case_id": case_id},
            )
            connection.execute(
                text(
                    "INSERT INTO evaluation_media_blobs "
                    "(id, storage_key, preview_storage_key, sha256, mime_type, "
                    "canonical_extension, size_bytes, width_pixels, height_pixels, "
                    "storage_status, validation_status, retention_status) VALUES "
                    "(:id, :storage_key, :preview_key, :sha256, 'image/jpeg', 'jpg', "
                    "100, 10, 10, 'available', 'validated', 'retained')"
                ),
                {
                    "id": blob_id,
                    "storage_key": f"objects/{blob_id}.jpg",
                    "preview_key": f"previews/{blob_id}.jpg",
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
                    "id": asset_id,
                    "blob_id": blob_id,
                    "actor_id": actor_id,
                    "project_id": INITIAL_PROJECT_IDS[0],
                },
            )
            connection.execute(
                text(
                    "UPDATE evaluation_media_assets SET "
                    "student_person_id = :student_id, enrollment_id = :enrollment_id, "
                    "class_id = :class_id, matched_at = CURRENT_TIMESTAMP, "
                    "matched_by_user_id = :actor_id, match_reason = 'verified target' "
                    "WHERE id = :asset_id"
                ),
                {
                    "student_id": student_id,
                    "enrollment_id": enrollment_id,
                    "class_id": class_id,
                    "actor_id": actor_id,
                    "asset_id": asset_id,
                },
            )

        with (
            pytest.raises(DBAPIError, match="evaluation media target is immutable"),
            postgres_engine.begin() as connection,
        ):
            connection.execute(
                text(
                    "UPDATE evaluation_media_assets "
                    "SET match_reason = 'rewritten target' WHERE id = :asset_id"
                ),
                {"asset_id": asset_id},
            )
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM evaluation_media_assets WHERE id = :id"),
                {"id": asset_id},
            )
            connection.execute(
                text("DELETE FROM evaluation_media_blobs WHERE id = :id"),
                {"id": blob_id},
            )
            connection.execute(
                text(
                    "UPDATE admission_cases SET status = 'ready', "
                    "current_enrollment_id = NULL WHERE id = :case_id"
                ),
                {"case_id": case_id},
            )
            connection.execute(
                text("DELETE FROM enrollments WHERE id = :id"),
                {"id": enrollment_id},
            )
            connection.execute(
                text("DELETE FROM admission_cases WHERE id = :id"),
                {"id": case_id},
            )
            connection.execute(
                text("DELETE FROM teaching_classes WHERE id = :id"),
                {"id": class_id},
            )
            connection.execute(
                text("DELETE FROM cohorts WHERE id = :id"),
                {"id": cohort_id},
            )
            connection.execute(
                text("DELETE FROM student_profiles WHERE person_id = :student_id"),
                {"student_id": student_id},
            )
        _delete_test_account(
            postgres_engine,
            person_id=actor_person_id,
            account_id=actor_id,
        )
        _delete_people(postgres_engine, guardian_id, student_id)


@pytest.mark.parametrize(
    ("mode", "created_at", "expires_at"),
    [
        (
            "unsupported",
            "2026-07-30T08:00:00+00:00",
            "2026-07-30T09:00:00+00:00",
        ),
        (
            "standalone",
            "2026-07-30T08:00:00+00:00",
            "2026-07-30T08:00:00+00:00",
        ),
    ],
    ids=("mode", "expiry"),
)
def test_evaluation_bridge_request_constraints_reject_invalid_rows(
    postgres_engine: Engine,
    mode: str,
    created_at: str,
    expires_at: str,
) -> None:
    with pytest.raises(DBAPIError), postgres_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO evaluation_bridge_requests "
                "(id, client_id, mode, state_ciphertext, expires_at, "
                "created_at, updated_at) VALUES "
                "(:id, 'constraint-test', :mode, 'ciphertext', :expires_at, "
                ":created_at, :created_at)"
            ),
            {
                "id": uuid4(),
                "mode": mode,
                "created_at": created_at,
                "expires_at": expires_at,
            },
        )


def test_teaching_schema_has_cross_resource_and_history_constraints(
    postgres_engine: Engine,
) -> None:
    inspector = inspect(postgres_engine)
    class_checks = {
        item["name"] for item in inspector.get_check_constraints("teaching_classes")
    }
    cohort_checks = {item["name"] for item in inspector.get_check_constraints("cohorts")}
    event_checks = {
        item["name"] for item in inspector.get_check_constraints("cohort_status_events")
    }
    assignment_checks = {
        item["name"] for item in inspector.get_check_constraints("coach_assignments")
    }
    class_uniques = {
        tuple(item["column_names"])
        for item in inspector.get_unique_constraints("teaching_classes")
    }
    team_uniques = {
        tuple(item["column_names"])
        for item in inspector.get_unique_constraints("professional_teams")
    }
    class_foreign_keys = {
        (
            tuple(item["constrained_columns"]),
            item["referred_table"],
            tuple(item["referred_columns"]),
        )
        for item in inspector.get_foreign_keys("teaching_classes")
    }
    with postgres_engine.connect() as connection:
        exclusions = set(
            connection.scalars(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = 'coach_assignments'::regclass AND contype = 'x'"
                )
            )
        )
    assert {
        "ck_teaching_classes_type_team",
        "ck_teaching_classes_capacity",
    } <= class_checks
    assert "ck_cohorts_status" in cohort_checks
    assert {
        "ck_cohort_status_events_trigger",
        "ck_cohort_status_events_actor",
    } <= event_checks
    assert {
        "ck_coach_assignments_type",
        "ck_coach_assignments_valid_until",
        "ck_coach_assignments_ended_at",
        "ck_coach_assignments_end_fields",
    } <= assignment_checks
    assert ("cohort_id", "name") in class_uniques
    assert ("id", "cohort_id") in class_uniques
    assert ("project_id", "name") in team_uniques
    assert ("id", "project_id") in team_uniques
    assert (
        ("professional_team_id", "project_id"),
        "professional_teams",
        ("id", "project_id"),
    ) in class_foreign_keys
    assert {
        "excl_coach_assignments_one_primary",
        "excl_coach_assignments_no_duplicate_coach",
    } <= exclusions


def test_admissions_schema_protects_money_idempotency_and_active_enrollment(
    postgres_engine: Engine,
) -> None:
    inspector = inspect(postgres_engine)
    assert ADMISSIONS_TABLES <= set(inspector.get_table_names())

    payment_checks = {
        row["name"]: str(row["sqltext"]).lower()
        for row in inspector.get_check_constraints("payments")
    }
    refund_checks = {
        row["name"]: str(row["sqltext"]).lower()
        for row in inspector.get_check_constraints("payment_refunds")
    }
    enrollment_checks = {
        row["name"]: str(row["sqltext"]).lower()
        for row in inspector.get_check_constraints("enrollments")
    }
    assert "ck_payments_positive_cny" in payment_checks
    assert "amount_cents > 0" in payment_checks["ck_payments_positive_cny"]
    assert "currency" in payment_checks["ck_payments_positive_cny"]
    assert "cny" in payment_checks["ck_payments_positive_cny"]
    assert "ck_payment_refunds_positive" in refund_checks
    assert "amount_cents > 0" in refund_checks["ck_payment_refunds_positive"]
    assert "ck_enrollments_status" in enrollment_checks
    assert all(
        status in enrollment_checks["ck_enrollments_status"]
        for status in ("active", "cancelled", "completed")
    )
    assert "ck_enrollments_tuition" in enrollment_checks
    assert "agreed_tuition_cents >= 0" in enrollment_checks["ck_enrollments_tuition"]

    account_uniques = {
        tuple(row["column_names"])
        for row in inspector.get_unique_constraints("service_accounts")
    }
    credential_uniques = {
        tuple(row["column_names"])
        for row in inspector.get_unique_constraints("service_account_credentials")
    }
    nonce_uniques = {
        tuple(row["column_names"])
        for row in inspector.get_unique_constraints("service_account_nonces")
    }
    case_payment_uniques = {
        tuple(row["column_names"])
        for row in inspector.get_unique_constraints("admission_case_payments")
    }
    enrollment_uniques = {
        tuple(row["column_names"])
        for row in inspector.get_unique_constraints("enrollments")
    }
    withdrawal_uniques = {
        tuple(row["column_names"])
        for row in inspector.get_unique_constraints("enrollment_withdrawals")
    }
    assert ("source_system",) in account_uniques
    assert ("public_id",) in credential_uniques
    assert ("credential_id", "nonce_digest") in nonce_uniques
    assert ("payment_id",) in case_payment_uniques
    assert ("id", "admission_case_id") in enrollment_uniques
    assert ("enrollment_id",) in withdrawal_uniques

    enrollment_foreign_keys = {
        row["name"]: row for row in inspector.get_foreign_keys("enrollments")
    }
    case_foreign_keys = {
        row["name"]: row for row in inspector.get_foreign_keys("admission_cases")
    }
    import_foreign_keys = {
        row["name"]: row for row in inspector.get_foreign_keys("payment_import_events")
    }
    conflict_foreign_keys = {
        row["name"]: row for row in inspector.get_foreign_keys("payment_conflicts")
    }
    class_cohort = enrollment_foreign_keys["fk_enrollments_class_cohort"]
    assert class_cohort["constrained_columns"] == ["class_id", "cohort_id"]
    assert class_cohort["referred_table"] == "teaching_classes"
    assert class_cohort["referred_columns"] == ["id", "cohort_id"]
    assert class_cohort["options"]["ondelete"] == "RESTRICT"

    current_enrollment = case_foreign_keys["fk_admission_cases_current_enrollment"]
    assert current_enrollment["constrained_columns"] == [
        "current_enrollment_id",
        "id",
    ]
    assert current_enrollment["referred_table"] == "enrollments"
    assert current_enrollment["referred_columns"] == ["id", "admission_case_id"]
    assert current_enrollment["options"]["ondelete"] == "RESTRICT"
    assert current_enrollment["options"]["deferrable"] is True
    assert current_enrollment["options"]["initially"] == "DEFERRED"

    assert import_foreign_keys["fk_payment_import_events_conflict"][
        "referred_table"
    ] == "payment_conflicts"
    assert conflict_foreign_keys["fk_payment_conflicts_original_event"][
        "referred_table"
    ] == "payment_import_events"

    with postgres_engine.connect() as connection:
        indexes = {
            row.indexname: row.indexdef
            for row in connection.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE schemaname = 'public'"
                )
            )
        }
    assert "uq_payment_import_source_order" in indexes
    assert "uq_payment_import_idempotency" in indexes
    assert "uq_enrollments_active_student_cohort" in indexes
    active_index = indexes["uq_enrollments_active_student_cohort"].lower()
    assert "unique index" in active_index
    assert "student_person_id" in active_index
    assert "cohort_id" in active_index
    assert "where" in active_index
    assert "status" in active_index
    assert "active" in active_index


def test_professional_team_name_is_unique_within_project(
    postgres_engine: Engine,
) -> None:
    person_id, account_id = _insert_test_account(postgres_engine)
    team_id = uuid4()
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO professional_teams (
                        id, project_id, name, is_active, created_by_user_id
                    ) VALUES (
                        :id, :project_id, '迁移测试战队', true, :created_by_user_id
                    )
                    """
                ),
                {
                    "id": team_id,
                    "project_id": INITIAL_PROJECT_IDS[0],
                    "created_by_user_id": account_id,
                },
            )

        with pytest.raises(DBAPIError) as error, postgres_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO professional_teams (
                        id, project_id, name, is_active, created_by_user_id
                    ) VALUES (
                        :id, :project_id, '迁移测试战队', true, :created_by_user_id
                    )
                    """
                ),
                {
                    "id": uuid4(),
                    "project_id": INITIAL_PROJECT_IDS[0],
                    "created_by_user_id": account_id,
                },
            )
        constraint_name = getattr(getattr(error.value.orig, "diag", None), "constraint_name", None)
        assert constraint_name == "uq_professional_teams_project_name"
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM professional_teams WHERE id = :id"),
                {"id": team_id},
            )
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )


def test_teaching_class_rejects_team_from_another_project(
    postgres_engine: Engine,
) -> None:
    person_id, account_id = _insert_test_account(postgres_engine)
    team_id = uuid4()
    cohort_id = uuid4()
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO professional_teams (
                        id, project_id, name, is_active, created_by_user_id
                    ) VALUES (
                        :team_id, :project_id, '跨项目迁移测试战队', true, :actor_id
                    )
                    """
                ),
                {
                    "team_id": team_id,
                    "project_id": INITIAL_PROJECT_IDS[0],
                    "actor_id": account_id,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO cohorts (
                        id,
                        name,
                        starts_at,
                        ends_at,
                        default_check_in_at,
                        default_check_out_at,
                        status,
                        created_by_user_id
                    ) VALUES (
                        :cohort_id,
                        '跨项目迁移测试届次',
                        TIMESTAMPTZ '2026-09-01 08:00:00+08',
                        TIMESTAMPTZ '2026-12-31 18:00:00+08',
                        TIMESTAMPTZ '2026-09-01 08:00:00+08',
                        TIMESTAMPTZ '2026-09-01 18:00:00+08',
                        'draft',
                        :actor_id
                    )
                    """
                ),
                {"cohort_id": cohort_id, "actor_id": account_id},
            )

        with pytest.raises(DBAPIError) as error, postgres_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO teaching_classes (
                        id,
                        cohort_id,
                        name,
                        class_type,
                        project_id,
                        professional_team_id,
                        capacity,
                        default_tuition_cents,
                        created_by_user_id
                    ) VALUES (
                        :id,
                        :cohort_id,
                        '跨项目迁移测试班',
                        'elite',
                        :project_id,
                        :team_id,
                        20,
                        100000,
                        :actor_id
                    )
                    """
                ),
                {
                    "id": uuid4(),
                    "cohort_id": cohort_id,
                    "project_id": INITIAL_PROJECT_IDS[1],
                    "team_id": team_id,
                    "actor_id": account_id,
                },
            )
        constraint_name = getattr(getattr(error.value.orig, "diag", None), "constraint_name", None)
        assert constraint_name == "fk_teaching_classes_team_project"
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM professional_teams WHERE id = :id"),
                {"id": team_id},
            )
            connection.execute(
                text("DELETE FROM cohorts WHERE id = :id"),
                {"id": cohort_id},
            )
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )


def test_ended_coach_assignment_requires_a_non_null_reason(
    postgres_engine: Engine,
) -> None:
    person_id, account_id = _insert_test_account(postgres_engine)
    cohort_id = uuid4()
    class_id = uuid4()
    assignment_id = uuid4()
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO cohorts (
                        id,
                        name,
                        starts_at,
                        ends_at,
                        default_check_in_at,
                        default_check_out_at,
                        status,
                        created_by_user_id
                    ) VALUES (
                        :cohort_id,
                        '结束原因约束测试营期',
                        TIMESTAMPTZ '2026-09-01 08:00:00+08',
                        TIMESTAMPTZ '2026-12-31 18:00:00+08',
                        TIMESTAMPTZ '2026-09-01 08:00:00+08',
                        TIMESTAMPTZ '2026-09-01 18:00:00+08',
                        'draft',
                        :actor_id
                    )
                    """
                ),
                {"cohort_id": cohort_id, "actor_id": account_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO teaching_classes (
                        id,
                        cohort_id,
                        project_id,
                        name,
                        class_type,
                        capacity,
                        default_tuition_cents,
                        created_by_user_id
                    ) VALUES (
                        :class_id,
                        :cohort_id,
                        :project_id,
                        '结束原因约束测试班',
                        'standard',
                        20,
                        100000,
                        :actor_id
                    )
                    """
                ),
                {
                    "class_id": class_id,
                    "cohort_id": cohort_id,
                    "project_id": INITIAL_PROJECT_IDS[0],
                    "actor_id": account_id,
                },
            )

        with pytest.raises(DBAPIError) as error, postgres_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO coach_assignments (
                        id,
                        class_id,
                        coach_person_id,
                        assignment_type,
                        valid_from,
                        ended_at,
                        ended_by_user_id,
                        end_reason,
                        created_by_user_id
                    ) VALUES (
                        :assignment_id,
                        :class_id,
                        :coach_person_id,
                        'assistant',
                        TIMESTAMPTZ '2026-09-01 08:00:00+08',
                        TIMESTAMPTZ '2026-09-02 08:00:00+08',
                        :actor_id,
                        NULL,
                        :actor_id
                    )
                    """
                ),
                {
                    "assignment_id": assignment_id,
                    "class_id": class_id,
                    "coach_person_id": person_id,
                    "actor_id": account_id,
                },
            )
        constraint_name = getattr(getattr(error.value.orig, "diag", None), "constraint_name", None)
        assert constraint_name == "ck_coach_assignments_end_fields"
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM coach_assignments WHERE id = :id"),
                {"id": assignment_id},
            )
            connection.execute(
                text("DELETE FROM teaching_classes WHERE id = :id"),
                {"id": class_id},
            )
            connection.execute(
                text("DELETE FROM cohorts WHERE id = :id"),
                {"id": cohort_id},
            )
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )


def test_guardian_relationships_preserve_history(postgres_engine: Engine) -> None:
    inspector = inspect(postgres_engine)
    columns = {column["name"]: column for column in inspector.get_columns("guardian_relationships")}
    primary_key = inspector.get_pk_constraint("guardian_relationships")
    indexes = {index["name"]: index for index in inspector.get_indexes("guardian_relationships")}
    foreign_keys = inspector.get_foreign_keys("guardian_relationships")
    with postgres_engine.connect() as connection:
        exclusion_constraints = set(
            connection.scalars(
                text(
                    """
                    SELECT conname
                    FROM pg_constraint
                    WHERE conrelid = 'guardian_relationships'::regclass
                      AND contype = 'x'
                    """
                )
            )
        )

    assert {
        "id",
        "guardian_person_id",
        "student_person_id",
        "relationship",
        "is_primary",
        "valid_from",
        "valid_until",
        "created_at",
        "updated_at",
        "ended_at",
        "ended_by_user_id",
        "end_reason",
    } <= set(columns)
    assert primary_key["constrained_columns"] == ["id"]
    assert columns["id"]["nullable"] is False
    assert columns["created_at"]["nullable"] is False
    assert columns["updated_at"]["nullable"] is False
    assert columns["ended_at"]["nullable"] is True
    assert columns["ended_by_user_id"]["nullable"] is True
    assert columns["end_reason"]["nullable"] is True
    pair_index = indexes["ix_guardian_relationships_pair"]
    assert pair_index["column_names"] == ["guardian_person_id", "student_person_id"]
    assert pair_index["unique"] is False
    student_history_index = indexes["ix_guardian_relationships_student_history"]
    assert student_history_index["column_names"] == ["student_person_id", "id"]
    assert student_history_index["unique"] is False
    ended_by_foreign_key = next(
        foreign_key
        for foreign_key in foreign_keys
        if foreign_key["constrained_columns"] == ["ended_by_user_id"]
    )
    assert ended_by_foreign_key["referred_table"] == "user_accounts"
    assert ended_by_foreign_key["referred_columns"] == ["id"]
    assert ended_by_foreign_key["options"]["ondelete"] == "RESTRICT"
    assert "excl_guardian_relationships_overlapping_dates" in exclusion_constraints


def test_guardian_history_offline_downgrade_script_is_generatable() -> None:
    output = StringIO()
    config = Config(str(API_ROOT / "alembic.ini"), output_buffer=output)
    config.set_main_option(
        "sqlalchemy.url",
        "postgresql+psycopg://offline:offline@localhost/offline",
    )

    command.downgrade(
        config,
        "0003_guardian_relationship_history:0002_seed_rbac",
        sql=True,
    )

    script = output.getvalue()
    assert "DO $$" in script
    assert "cannot downgrade guardian history with repeated pairs" in script
    assert "DROP CONSTRAINT excl_guardian_relationships_overlapping_dates" in script


def test_guardian_history_upgrade_preserves_existing_relationship(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0002_seed_rbac")
    guardian_id, student_id = _insert_people(postgres_engine)
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO guardian_relationships (
                        guardian_person_id,
                        student_person_id,
                        relationship,
                        is_primary,
                        valid_from,
                        valid_until
                    ) VALUES (
                        :guardian_id,
                        :student_id,
                        'mother',
                        true,
                        DATE '2026-01-01',
                        NULL
                    )
                    """
                ),
                {"guardian_id": guardian_id, "student_id": student_id},
            )

        command.upgrade(config, "0003_guardian_relationship_history")

        with postgres_engine.connect() as connection:
            migrated = (
                connection.execute(
                    text(
                        """
                        SELECT id, relationship, is_primary, valid_from, valid_until,
                               created_at, updated_at
                        FROM guardian_relationships
                        WHERE guardian_person_id = :guardian_id
                          AND student_person_id = :student_id
                        """
                    ),
                    {"guardian_id": guardian_id, "student_id": student_id},
                )
                .mappings()
                .one()
            )
        assert migrated["id"] is not None
        assert migrated["relationship"] == "mother"
        assert migrated["is_primary"] is True
        assert str(migrated["valid_from"]) == "2026-01-01"
        assert migrated["valid_until"] is None
        assert migrated["created_at"] is not None
        assert migrated["updated_at"] is not None
    finally:
        command.upgrade(config, "head")
        _delete_people(postgres_engine, guardian_id, student_id)


def test_guardian_history_normal_downgrade_preserves_single_pair(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    guardian_id, student_id = _insert_people(postgres_engine)
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO guardian_relationships (
                        id,
                        guardian_person_id,
                        student_person_id,
                        relationship,
                        is_primary,
                        valid_from,
                        valid_until
                    ) VALUES (
                        :id,
                        :guardian_id,
                        :student_id,
                        'father',
                        false,
                        DATE '2026-02-01',
                        DATE '2026-12-31'
                    )
                    """
                ),
                {
                    "id": uuid4(),
                    "guardian_id": guardian_id,
                    "student_id": student_id,
                },
            )

        command.downgrade(config, "0002_seed_rbac")

        inspector = inspect(postgres_engine)
        assert inspector.get_pk_constraint("guardian_relationships")[
            "constrained_columns"
        ] == ["guardian_person_id", "student_person_id"]
        with postgres_engine.connect() as connection:
            relationship = (
                connection.execute(
                    text(
                        """
                        SELECT relationship, is_primary, valid_from, valid_until
                        FROM guardian_relationships
                        WHERE guardian_person_id = :guardian_id
                          AND student_person_id = :student_id
                        """
                    ),
                    {"guardian_id": guardian_id, "student_id": student_id},
                )
                .mappings()
                .one()
            )
        assert relationship["relationship"] == "father"
        assert relationship["is_primary"] is False
        assert str(relationship["valid_from"]) == "2026-02-01"
        assert str(relationship["valid_until"]) == "2026-12-31"
    finally:
        _delete_people(postgres_engine, guardian_id, student_id)
        command.upgrade(config, "head")


def test_guardian_history_downgrade_refuses_repeated_pair_before_ddl(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    guardian_id, student_id = _insert_people(postgres_engine)
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO guardian_relationships (
                        id,
                        guardian_person_id,
                        student_person_id,
                        relationship,
                        is_primary,
                        valid_from,
                        valid_until
                    ) VALUES (
                        :first_id,
                        :guardian_id,
                        :student_id,
                        'mother',
                        true,
                        DATE '2025-01-01',
                        DATE '2025-12-31'
                    ), (
                        :second_id,
                        :guardian_id,
                        :student_id,
                        'mother',
                        true,
                        DATE '2026-01-01',
                        NULL
                    )
                    """
                ),
                {
                    "first_id": uuid4(),
                    "second_id": uuid4(),
                    "guardian_id": guardian_id,
                    "student_id": student_id,
                },
            )

        with pytest.raises(
            DBAPIError,
            match="cannot downgrade guardian history with repeated pairs",
        ):
            command.downgrade(config, "0002_seed_rbac")

        inspector = inspect(postgres_engine)
        assert "id" in {
            column["name"]
            for column in inspector.get_columns("guardian_relationships")
        }
        with postgres_engine.connect() as connection:
            count = connection.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM guardian_relationships
                    WHERE guardian_person_id = :guardian_id
                      AND student_person_id = :student_id
                    """
                ),
                {"guardian_id": guardian_id, "student_id": student_id},
            )
        assert count == 2
    finally:
        command.upgrade(config, "head")
        _delete_people(postgres_engine, guardian_id, student_id)


def test_guardian_history_exclusion_constraint_rejects_real_overlap(
    postgres_engine: Engine,
) -> None:
    guardian_id, student_id = _insert_people(postgres_engine)
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO guardian_relationships (
                        id,
                        guardian_person_id,
                        student_person_id,
                        relationship,
                        is_primary,
                        valid_from,
                        valid_until
                    ) VALUES (
                        :id,
                        :guardian_id,
                        :student_id,
                        'mother',
                        true,
                        DATE '2026-01-01',
                        DATE '2026-12-31'
                    )
                    """
                ),
                {
                    "id": uuid4(),
                    "guardian_id": guardian_id,
                    "student_id": student_id,
                },
            )

        with pytest.raises(DBAPIError) as error, postgres_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO guardian_relationships (
                        id,
                        guardian_person_id,
                        student_person_id,
                        relationship,
                        is_primary,
                        valid_from,
                        valid_until
                    ) VALUES (
                        :id,
                        :guardian_id,
                        :student_id,
                        'father',
                        false,
                        DATE '2026-06-01',
                        NULL
                    )
                    """
                ),
                {
                    "id": uuid4(),
                    "guardian_id": guardian_id,
                    "student_id": student_id,
                },
            )
        constraint_name = getattr(
            getattr(error.value.orig, "diag", None),
            "constraint_name",
            None,
        )
        assert constraint_name == "excl_guardian_relationships_overlapping_dates"
    finally:
        _delete_people(postgres_engine, guardian_id, student_id)


def test_audit_events_reject_update_and_delete(postgres_engine: Engine) -> None:
    event_id = uuid4()
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO audit_events (
                        id, action, resource_type, outcome, request_id, metadata
                    ) VALUES (
                        :id, 'migration.test', 'audit_event', 'allowed',
                        'test-request', '{}'::jsonb
                    )
                    """
                ),
                {"id": event_id},
            )

        with (
            pytest.raises(DBAPIError, match="audit_events is append-only"),
            postgres_engine.begin() as connection,
        ):
            connection.execute(
                text("UPDATE audit_events SET action = 'changed' WHERE id = :id"),
                {"id": event_id},
            )

        with (
            pytest.raises(DBAPIError, match="audit_events is append-only"),
            postgres_engine.begin() as connection,
        ):
            connection.execute(
                text("DELETE FROM audit_events WHERE id = :id"),
                {"id": event_id},
            )
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update")
            )
            try:
                connection.execute(
                    text("DELETE FROM audit_events WHERE id = :id"),
                    {"id": event_id},
                )
            finally:
                connection.execute(
                    text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update")
                )

    with postgres_engine.connect() as connection:
        assert connection.scalar(
            text("SELECT count(*) FROM audit_events WHERE id = :id"),
            {"id": event_id},
        ) == 0


def test_sensitive_lookup_columns_are_unique(postgres_engine: Engine) -> None:
    inspector = inspect(postgres_engine)
    people_uniques = {
        tuple(item["column_names"]) for item in inspector.get_unique_constraints("people")
    }
    account_uniques = {
        tuple(item["column_names"]) for item in inspector.get_unique_constraints("user_accounts")
    }

    assert ("identity_digest",) in people_uniques
    assert ("login_phone_digest",) in account_uniques


def test_teaching_seed_only_downgrade_returns_to_guardian_history(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    try:
        command.downgrade(config, "0003_guardian_relationship_history")

        inspector = inspect(postgres_engine)
        with postgres_engine.connect() as connection:
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
            teaching_permissions = connection.scalar(
                text(
                    "SELECT count(*) FROM permissions "
                    "WHERE id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": [str(permission_id) for permission_id in TEACHING_PERMISSION_IDS]},
            )
        assert revision == "0003_guardian_relationship_history"
        assert TEACHING_TABLES.isdisjoint(inspector.get_table_names())
        assert teaching_permissions == 0
    finally:
        command.upgrade(config, "head")


def test_teaching_downgrade_refuses_non_seed_data_before_changes(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    person_id, account_id = _insert_test_account(postgres_engine)
    project_id = uuid4()
    profile_id = uuid4()
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO game_projects (
                        id, name, sort_order, is_active, created_by_user_id
                    ) VALUES (
                        :id, '迁移测试自定义项目', 50, true, :actor_id
                    )
                    """
                ),
                {"id": project_id, "actor_id": account_id},
            )
            connection.execute(
                text(
                    "INSERT INTO project_analysis_profiles "
                    "(id, project_id, version, mode, required_fields, "
                    "confidence_thresholds, auto_publish_warning_levels, "
                    "publish_mode, effective_from, configured_by_user_id, "
                    "change_reason) VALUES "
                    "(:id, :project_id, 1, 'manual_only', '[]'::jsonb, '{}'::jsonb, "
                    "'[]'::jsonb, 'review_required', CURRENT_TIMESTAMP, NULL, "
                    "'migration backfill')"
                ),
                {"id": profile_id, "project_id": project_id},
            )

        with pytest.raises(
            DBAPIError,
            match="cannot downgrade teaching configuration with non-seed teaching data",
        ):
            command.downgrade(config, "0003_guardian_relationship_history")

        with postgres_engine.connect() as connection:
            project_name = connection.scalar(
                text("SELECT name FROM game_projects WHERE id = :id"),
                {"id": project_id},
            )
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        assert project_name == "迁移测试自定义项目"
        assert revision == "0011_housing_real_name_reporting"
        assert TEACHING_TABLES <= set(inspect(postgres_engine).get_table_names())
    finally:
        command.upgrade(config, "head")
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM project_analysis_profiles WHERE id = :id"),
                {"id": profile_id},
            )
            connection.execute(
                text("DELETE FROM game_projects WHERE id = :id"),
                {"id": project_id},
            )
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )


def test_teaching_downgrade_refuses_modified_initial_project_before_changes(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    project_id = INITIAL_PROJECT_IDS[0]
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE game_projects "
                    "SET name = '管理员修改后的项目', sort_order = 999, is_active = false "
                    "WHERE id = :id"
                ),
                {"id": project_id},
            )

        with pytest.raises(
            DBAPIError,
            match="cannot downgrade teaching configuration with modified initial projects",
        ):
            command.downgrade(config, "0003_guardian_relationship_history")

        with postgres_engine.connect() as connection:
            project = connection.execute(
                text(
                    "SELECT name, sort_order, is_active FROM game_projects WHERE id = :id"
                ),
                {"id": project_id},
            ).one()
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        assert project == ("管理员修改后的项目", 999, False)
        assert revision == "0011_housing_real_name_reporting"
    finally:
        command.upgrade(config, "head")
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE game_projects "
                    "SET name = '英雄联盟', sort_order = 10, is_active = true "
                    "WHERE id = :id"
                ),
                {"id": project_id},
            )


def test_teaching_downgrade_refuses_account_override_before_changes(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    person_id, account_id = _insert_test_account(postgres_engine)
    override_id = uuid4()
    permission_id = TEACHING_PERMISSION_IDS[0]
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO user_permission_overrides (
                        id, user_id, permission_id, effect, scope_kind, scope_id
                    ) VALUES (
                        :id, :user_id, :permission_id, 'allow', 'global', NULL
                    )
                    """
                ),
                {
                    "id": override_id,
                    "user_id": account_id,
                    "permission_id": permission_id,
                },
            )

        with pytest.raises(
            DBAPIError,
            match="cannot downgrade teaching configuration with account permission overrides",
        ):
            command.downgrade(config, "0003_guardian_relationship_history")

        with postgres_engine.connect() as connection:
            override = connection.execute(
                text(
                    "SELECT user_id, permission_id, effect, scope_kind "
                    "FROM user_permission_overrides WHERE id = :id"
                ),
                {"id": override_id},
            ).one()
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        assert override == (account_id, permission_id, "allow", "global")
        assert revision == "0011_housing_real_name_reporting"
    finally:
        command.upgrade(config, "head")
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )


def test_teaching_downgrade_refuses_modified_permission_before_changes(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    permission_id = TEACHING_PERMISSION_IDS[0]
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text("UPDATE permissions SET name = '管理员修改后的权限' WHERE id = :id"),
                {"id": permission_id},
            )

        with pytest.raises(
            DBAPIError,
            match="cannot downgrade teaching configuration with modified permission seeds",
        ):
            command.downgrade(config, "0003_guardian_relationship_history")

        with postgres_engine.connect() as connection:
            permission_name = connection.scalar(
                text("SELECT name FROM permissions WHERE id = :id"),
                {"id": permission_id},
            )
        assert permission_name == "管理员修改后的权限"
    finally:
        command.upgrade(config, "head")
        with postgres_engine.begin() as connection:
            connection.execute(
                text("UPDATE permissions SET name = '查看教学配置' WHERE id = :id"),
                {"id": permission_id},
            )


def test_teaching_downgrade_refuses_customized_grant_before_changes(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    permission_id = TEACHING_PERMISSION_IDS[0]
    guardian_role_id = UUID("10000000-0000-4000-8000-000000000006")
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO role_permissions (role_id, permission_id, scope_kind) "
                    "VALUES (:role_id, :permission_id, 'global')"
                ),
                {"role_id": guardian_role_id, "permission_id": permission_id},
            )
        with pytest.raises(
            DBAPIError,
            match="cannot downgrade teaching configuration with customized role grants",
        ):
            command.downgrade(config, "0003_guardian_relationship_history")

        with postgres_engine.connect() as connection:
            grant_exists = connection.scalar(
                text(
                    "SELECT EXISTS ("
                    "SELECT 1 FROM role_permissions "
                    "WHERE role_id = :role_id AND permission_id = :permission_id "
                    "AND scope_kind = 'global')"
                ),
                {"role_id": guardian_role_id, "permission_id": permission_id},
            )
        assert grant_exists is True
    finally:
        command.upgrade(config, "head")
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM role_permissions "
                    "WHERE role_id = :role_id AND permission_id = :permission_id "
                    "AND scope_kind = 'global'"
                ),
                {"role_id": guardian_role_id, "permission_id": permission_id},
            )


def test_admissions_upgrade_from_teaching_revision(postgres_engine: Engine) -> None:
    config = _alembic_config(postgres_engine)
    try:
        command.downgrade(config, "0004_teaching_configuration")

        with postgres_engine.connect() as connection:
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
            permission_count = connection.scalar(
                text(
                    "SELECT count(*) FROM permissions "
                    "WHERE id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": [str(permission_id) for permission_id in ADMISSIONS_PERMISSION_IDS]},
            )
            grant_count = connection.scalar(
                text(
                    "SELECT count(*) FROM role_permissions "
                    "WHERE permission_id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": [str(permission_id) for permission_id in ADMISSIONS_PERMISSION_IDS]},
            )
        assert revision == "0004_teaching_configuration"
        assert ADMISSIONS_TABLES.isdisjoint(inspect(postgres_engine).get_table_names())
        assert permission_count == 0
        assert grant_count == 0

        command.upgrade(config, "0005_admissions_enrollment")

        with postgres_engine.connect() as connection:
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        assert revision == "0005_admissions_enrollment"
        assert ADMISSIONS_TABLES <= set(inspect(postgres_engine).get_table_names())
    finally:
        command.upgrade(config, "head")


def test_admissions_downgrade_refuses_business_data_before_changes(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    person_id, account_id = _insert_test_account(postgres_engine)
    service_account_id = uuid4()
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO service_accounts (
                        id, name, source_system, is_active, created_by_user_id
                    ) VALUES (:id, '迁移测试导入账号', :source_system, true, :actor_id)
                    """
                ),
                {
                    "id": service_account_id,
                    "source_system": f"migration-test-{service_account_id}",
                    "actor_id": account_id,
                },
            )

        with pytest.raises(
            DBAPIError,
            match="cannot downgrade admissions enrollment with business data",
        ):
            command.downgrade(config, "0004_teaching_configuration")

        with postgres_engine.connect() as connection:
            exists = connection.scalar(
                text("SELECT EXISTS (SELECT 1 FROM service_accounts WHERE id = :id)"),
                {"id": service_account_id},
            )
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        assert exists is True
        assert revision == "0011_housing_real_name_reporting"
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM service_accounts WHERE id = :id"),
                {"id": service_account_id},
            )
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )


def test_admissions_downgrade_refuses_permission_override_before_changes(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    person_id, account_id = _insert_test_account(postgres_engine)
    override_id = uuid4()
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO user_permission_overrides (
                        id, user_id, permission_id, effect, scope_kind, scope_id
                    ) VALUES (:id, :user_id, :permission_id, 'allow', 'global', NULL)
                    """
                ),
                {
                    "id": override_id,
                    "user_id": account_id,
                    "permission_id": ADMISSIONS_PERMISSION_IDS[0],
                },
            )

        with pytest.raises(
            DBAPIError,
            match="cannot downgrade admissions enrollment with account permission overrides",
        ):
            command.downgrade(config, "0004_teaching_configuration")

        with postgres_engine.connect() as connection:
            exists = connection.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM user_permission_overrides "
                    "WHERE id = :id)"
                ),
                {"id": override_id},
            )
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        assert exists is True
        assert revision == "0011_housing_real_name_reporting"
    finally:
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )


def test_admissions_downgrade_refuses_customized_grant_before_changes(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    guardian_role_id = UUID("10000000-0000-4000-8000-000000000006")
    permission_id = ADMISSIONS_PERMISSION_IDS[0]
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO role_permissions (role_id, permission_id, scope_kind) "
                    "VALUES (:role_id, :permission_id, 'family')"
                ),
                {"role_id": guardian_role_id, "permission_id": permission_id},
            )

        with pytest.raises(
            DBAPIError,
            match="cannot downgrade admissions enrollment with customized role grants",
        ):
            command.downgrade(config, "0004_teaching_configuration")

        with postgres_engine.connect() as connection:
            exists = connection.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM role_permissions "
                    "WHERE role_id = :role_id AND permission_id = :permission_id "
                    "AND scope_kind = 'family')"
                ),
                {"role_id": guardian_role_id, "permission_id": permission_id},
            )
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        assert exists is True
        assert revision == "0011_housing_real_name_reporting"
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM role_permissions WHERE role_id = :role_id "
                    "AND permission_id = :permission_id AND scope_kind = 'family'"
                ),
                {"role_id": guardian_role_id, "permission_id": permission_id},
            )


def test_portal_query_indexes_exist(postgres_engine: Engine) -> None:
    with postgres_engine.connect() as connection:
        indexes = dict(
            connection.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE schemaname = 'public' "
                    "AND indexname = ANY(CAST(:names AS text[]))"
                ),
                {"names": sorted(PORTAL_INDEX_NAMES)},
            ).tuples().all()
        )

    assert set(indexes) == PORTAL_INDEX_NAMES
    assert "(status, starts_at, id)" in indexes["ix_cohorts_portal_next"].lower()
    coach_index = indexes["ix_coach_assignments_portal_current"].lower()
    assert "(coach_person_id, valid_from, valid_until, class_id)" in coach_index
    assert "where (ended_at is null)" in coach_index
    assert "(student_person_id, status, cohort_id, id)" in indexes[
        "ix_enrollments_student_status_cohort"
    ].lower()


def test_portal_migration_upgrades_from_and_downgrades_to_0005(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0005_admissions_enrollment")
    try:
        with postgres_engine.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == "0005_admissions_enrollment"
            assert connection.scalar(
                text(
                    "SELECT count(*) FROM permissions "
                    "WHERE id = :permission_id OR key = 'coach_roster.read'"
                ),
                {"permission_id": PORTAL_PERMISSION_ID},
            ) == 0
            assert _portal_index_names(connection) == set()
            assert _role_permission_grants(connection).isdisjoint(PORTAL_GRANTS)

        command.upgrade(config, "0006_coach_family_portal")
        with postgres_engine.connect() as connection:
            _assert_portal_ddl_is_intact(connection, "0006_coach_family_portal")
            assert connection.scalar(
                text("SELECT id FROM permissions WHERE key = 'coach_roster.read'")
            ) == PORTAL_PERMISSION_ID
            assert PORTAL_GRANTS <= _role_permission_grants(connection)

        command.downgrade(config, "0005_admissions_enrollment")
        with postgres_engine.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == "0005_admissions_enrollment"
            assert connection.scalar(
                text(
                    "SELECT count(*) FROM permissions "
                    "WHERE id = :permission_id OR key = 'coach_roster.read'"
                ),
                {"permission_id": PORTAL_PERMISSION_ID},
            ) == 0
            assert _portal_index_names(connection) == set()
            assert _role_permission_grants(connection).isdisjoint(PORTAL_GRANTS)
    finally:
        command.upgrade(config, "head")


def test_evaluation_migration_upgrade_downgrade_upgrade_restores_schema_and_grants(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0006_coach_family_portal")
    try:
        with postgres_engine.connect() as connection:
            assert EVALUATION_TABLES.isdisjoint(
                set(inspect(connection).get_table_names())
            )
            assert connection.scalar(
                text(
                    "SELECT count(*) FROM permissions "
                    "WHERE id = :permission_id OR key = 'evaluation_center.access'"
                ),
                {"permission_id": EVALUATION_ACCESS_PERMISSION_ID},
            ) == 0
            assert _role_permission_grants(connection).isdisjoint(
                EVALUATION_ACCESS_GRANTS
            )

        command.upgrade(config, "0007_evaluation_page_integration")
        with postgres_engine.connect() as connection:
            assert EVALUATION_TABLES <= set(inspect(connection).get_table_names())
            assert connection.scalar(
                text("SELECT id FROM permissions WHERE key = 'evaluation_center.access'")
            ) == EVALUATION_ACCESS_PERMISSION_ID
            assert {
                grant
                for grant in _role_permission_grants(connection)
                if grant[1] == "evaluation_center.access"
            } == EVALUATION_ACCESS_GRANTS

        command.downgrade(config, "0006_coach_family_portal")
        with postgres_engine.connect() as connection:
            assert EVALUATION_TABLES.isdisjoint(
                set(inspect(connection).get_table_names())
            )
            assert connection.scalar(
                text(
                    "SELECT count(*) FROM permissions "
                    "WHERE id = :permission_id OR key = 'evaluation_center.access'"
                ),
                {"permission_id": EVALUATION_ACCESS_PERMISSION_ID},
            ) == 0

        command.upgrade(config, "0007_evaluation_page_integration")
        with postgres_engine.connect() as connection:
            assert EVALUATION_TABLES <= set(inspect(connection).get_table_names())
            assert {
                grant
                for grant in _role_permission_grants(connection)
                if grant[1] == "evaluation_center.access"
            } == EVALUATION_ACCESS_GRANTS
    finally:
        command.upgrade(config, "head")


@pytest.mark.parametrize(
    ("permission_id", "permission_key"),
    [
        (EVALUATION_ACCESS_PERMISSION_ID, "custom.evaluation.access"),
        (UUID("20000000-0000-4000-8000-000000000027"), "evaluation_center.access"),
    ],
    ids=("fixed-uuid", "fixed-key"),
)
def test_evaluation_upgrade_refuses_preexisting_permission_before_ddl(
    postgres_engine: Engine,
    permission_id: UUID,
    permission_key: str,
) -> None:
    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0006_coach_family_portal")
    params = {
        "permission_id": permission_id,
        "permission_key": permission_key,
        "permission_name": f"预先存在的权限 {permission_key}",
    }
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO permissions (id, key, name, sensitive) "
                    "VALUES (:permission_id, :permission_key, :permission_name, true)"
                ),
                params,
            )

        with pytest.raises(
            RuntimeError,
            match=(
                "cannot upgrade evaluation page with preexisting "
                "evaluation access permission"
            ),
        ):
            command.upgrade(config, "0007_evaluation_page_integration")

        with postgres_engine.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == "0006_coach_family_portal"
            assert connection.execute(
                text(
                    "SELECT key, name, sensitive FROM permissions "
                    "WHERE id = :permission_id"
                ),
                params,
            ).one() == (permission_key, params["permission_name"], True)
            assert EVALUATION_TABLES.isdisjoint(
                set(inspect(connection).get_table_names())
            )
            assert {
                grant
                for grant in _role_permission_grants(connection)
                if grant[1] == "evaluation_center.access"
            } == set()
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM permissions WHERE id = :permission_id"),
                params,
            )
        command.upgrade(config, "head")


def test_evaluation_downgrade_refuses_account_override_before_ddl(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    request_id = _insert_evaluation_bridge_request(postgres_engine)
    person_id, account_id = _insert_test_account(postgres_engine)
    override_id = uuid4()
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO user_permission_overrides "
                    "(id, user_id, permission_id, effect, scope_kind, scope_id) "
                    "VALUES (:id, :user_id, :permission_id, 'deny', 'self', NULL)"
                ),
                {
                    "id": override_id,
                    "user_id": account_id,
                    "permission_id": EVALUATION_ACCESS_PERMISSION_ID,
                },
            )

        with pytest.raises(
            RuntimeError,
            match="cannot downgrade evaluation page with account permission overrides",
        ):
            command.downgrade(config, "0006_coach_family_portal")

        with postgres_engine.connect() as connection:
            _assert_evaluation_downgrade_state_is_intact(connection, request_id)
            assert connection.execute(
                text(
                    "SELECT user_id, permission_id, effect, scope_kind, scope_id "
                    "FROM user_permission_overrides WHERE id = :id"
                ),
                {"id": override_id},
            ).one() == (
                account_id,
                EVALUATION_ACCESS_PERMISSION_ID,
                "deny",
                "self",
                None,
            )
    finally:
        command.upgrade(config, "head")
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM evaluation_bridge_requests WHERE id = :request_id"),
                {"request_id": request_id},
            )


def test_evaluation_downgrade_refuses_customized_immutable_permission_before_ddl(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    request_id = _insert_evaluation_bridge_request(postgres_engine)
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE permissions "
                    "SET name = '管理员修改后的评价权限', sensitive = true "
                    "WHERE id = :permission_id"
                ),
                {"permission_id": EVALUATION_ACCESS_PERMISSION_ID},
            )

        with pytest.raises(
            RuntimeError,
            match="cannot downgrade evaluation page with modified permission seed",
        ):
            command.downgrade(config, "0006_coach_family_portal")

        with postgres_engine.connect() as connection:
            _assert_evaluation_downgrade_state_is_intact(connection, request_id)
            assert connection.execute(
                text(
                    "SELECT id, key, name, sensitive FROM permissions "
                    "WHERE id = :permission_id"
                ),
                {"permission_id": EVALUATION_ACCESS_PERMISSION_ID},
            ).one() == (
                EVALUATION_ACCESS_PERMISSION_ID,
                "evaluation_center.access",
                "管理员修改后的评价权限",
                True,
            )
    finally:
        command.upgrade(config, "head")
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE permissions SET name = '进入评价中心', sensitive = false "
                    "WHERE id = :permission_id"
                ),
                {"permission_id": EVALUATION_ACCESS_PERMISSION_ID},
            )
            connection.execute(
                text("DELETE FROM evaluation_bridge_requests WHERE id = :request_id"),
                {"request_id": request_id},
            )


@pytest.mark.parametrize(
    "replacement_scope",
    ["self", None],
    ids=("customized", "missing"),
)
def test_evaluation_downgrade_refuses_customized_or_missing_fixed_global_grant_before_ddl(
    postgres_engine: Engine,
    replacement_scope: str | None,
) -> None:
    config = _alembic_config(postgres_engine)
    request_id = _insert_evaluation_bridge_request(postgres_engine)
    fixed_grant = ("student", "evaluation_center.access", "global")
    assert fixed_grant in EVALUATION_ACCESS_GRANTS
    with postgres_engine.connect() as connection:
        role_id = connection.scalar(text("SELECT id FROM roles WHERE key = 'student'"))
    params = {
        "role_id": role_id,
        "permission_id": EVALUATION_ACCESS_PERMISSION_ID,
        "replacement_scope": replacement_scope,
    }
    try:
        with postgres_engine.begin() as connection:
            if replacement_scope is None:
                connection.execute(
                    text(
                        "DELETE FROM role_permissions "
                        "WHERE role_id = :role_id AND permission_id = :permission_id "
                        "AND scope_kind = 'global'"
                    ),
                    params,
                )
            else:
                connection.execute(
                    text(
                        "UPDATE role_permissions SET scope_kind = :replacement_scope "
                        "WHERE role_id = :role_id AND permission_id = :permission_id "
                        "AND scope_kind = 'global'"
                    ),
                    params,
                )

        with pytest.raises(
            RuntimeError,
            match="cannot downgrade evaluation page with customized grants",
        ):
            command.downgrade(config, "0006_coach_family_portal")

        expected_grants = EVALUATION_ACCESS_GRANTS - {fixed_grant}
        if replacement_scope is not None:
            expected_grants.add(
                ("student", "evaluation_center.access", replacement_scope)
            )
        with postgres_engine.connect() as connection:
            _assert_evaluation_downgrade_state_is_intact(connection, request_id)
            assert {
                grant
                for grant in _role_permission_grants(connection)
                if grant[1] == "evaluation_center.access"
            } == expected_grants
    finally:
        command.upgrade(config, "head")
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM role_permissions "
                    "WHERE role_id = :role_id AND permission_id = :permission_id"
                ),
                params,
            )
            connection.execute(
                text(
                    "INSERT INTO role_permissions "
                    "(role_id, permission_id, scope_kind) "
                    "VALUES (:role_id, :permission_id, 'global')"
                ),
                params,
            )
            connection.execute(
                text("DELETE FROM evaluation_bridge_requests WHERE id = :request_id"),
                {"request_id": request_id},
            )


def test_evaluation_downgrade_refuses_extra_grant_before_ddl(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    request_id = _insert_evaluation_bridge_request(postgres_engine)
    extra_grant = ("customer_service", "evaluation_center.access", "global")
    params = {
        "role_id": UUID("10000000-0000-4000-8000-000000000003"),
        "permission_id": EVALUATION_ACCESS_PERMISSION_ID,
    }
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO role_permissions "
                    "(role_id, permission_id, scope_kind) "
                    "VALUES (:role_id, :permission_id, 'global')"
                ),
                params,
            )

        with pytest.raises(
            RuntimeError,
            match="cannot downgrade evaluation page with customized grants",
        ):
            command.downgrade(config, "0006_coach_family_portal")

        with postgres_engine.connect() as connection:
            _assert_evaluation_downgrade_state_is_intact(connection, request_id)
            grants = {
                grant
                for grant in _role_permission_grants(connection)
                if grant[1] == "evaluation_center.access"
            }
            assert grants == EVALUATION_ACCESS_GRANTS | {extra_grant}
    finally:
        command.upgrade(config, "head")
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM role_permissions "
                    "WHERE role_id = :role_id AND permission_id = :permission_id "
                    "AND scope_kind = 'global'"
                ),
                params,
            )
            connection.execute(
                text("DELETE FROM evaluation_bridge_requests WHERE id = :request_id"),
                {"request_id": request_id},
            )


@pytest.mark.parametrize(
    "mutation_sql",
    [
        (
            "UPDATE permissions SET name = '并发修改后的评价权限' "
            "WHERE id = :permission_id"
        ),
        (
            "DELETE FROM role_permissions "
            "WHERE role_id = '10000000-0000-4000-8000-000000000007' "
            "AND permission_id = :permission_id AND scope_kind = 'global'"
        ),
        (
            "INSERT INTO role_permissions (role_id, permission_id, scope_kind) "
            "VALUES ('10000000-0000-4000-8000-000000000003', "
            ":permission_id, 'global')"
        ),
    ],
    ids=("permission", "fixed-grant", "extra-grant"),
)
def test_evaluation_downgrade_locks_seed_rows_against_concurrent_customization(
    postgres_engine: Engine,
    mutation_sql: str,
) -> None:
    config = _alembic_config(postgres_engine)
    lock_key = 7_300_007
    trigger_name = "trg_pause_evaluation_downgrade"
    function_name = "pause_evaluation_downgrade"
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                f"""
                CREATE FUNCTION {function_name}() RETURNS trigger
                LANGUAGE plpgsql AS $$
                BEGIN
                    PERFORM pg_advisory_lock({lock_key});
                    PERFORM pg_advisory_unlock({lock_key});
                    RETURN OLD;
                END;
                $$
                """
            )
        )
        connection.execute(
            text(
                f"""
                CREATE TRIGGER {trigger_name}
                BEFORE DELETE ON role_permissions
                FOR EACH ROW
                WHEN (
                    OLD.role_id = '10000000-0000-4000-8000-000000000001'
                    AND OLD.permission_id =
                        '20000000-0000-4000-8000-000000000026'
                )
                EXECUTE FUNCTION {function_name}()
                """
            )
        )

    control_connection = postgres_engine.connect()
    pool = None
    future = None
    try:
        control_connection.execute(
            text("SELECT pg_advisory_lock(:lock_key)"),
            {"lock_key": lock_key},
        )
        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(
            command.downgrade,
            config,
            "0006_coach_family_portal",
        )
        deadline = monotonic() + 5
        reached_trigger = False
        while monotonic() < deadline:
            with postgres_engine.connect() as connection:
                reached_trigger = bool(
                    connection.scalar(
                        text(
                            "SELECT EXISTS ("
                            "SELECT 1 FROM pg_stat_activity "
                            "WHERE wait_event_type = 'Lock' "
                            "AND wait_event = 'advisory' "
                            "AND query LIKE 'DELETE FROM role_permissions%')"
                        )
                    )
                )
            if reached_trigger:
                break
            sleep(0.02)
        assert reached_trigger, "downgrade did not reach the concurrency barrier"

        with pytest.raises(DBAPIError), postgres_engine.begin() as connection:
            connection.execute(text("SET LOCAL lock_timeout = '200ms'"))
            connection.execute(
                text(mutation_sql),
                {"permission_id": EVALUATION_ACCESS_PERMISSION_ID},
            )
    finally:
        control_connection.execute(
            text("SELECT pg_advisory_unlock(:lock_key)"),
            {"lock_key": lock_key},
        )
        control_connection.close()
        try:
            if future is not None:
                future.result(timeout=10)
        finally:
            if pool is not None:
                pool.shutdown(wait=True)
            command.upgrade(config, "head")
            with postgres_engine.begin() as connection:
                connection.execute(
                    text(f"DROP TRIGGER IF EXISTS {trigger_name} ON role_permissions")
                )
                connection.execute(text(f"DROP FUNCTION IF EXISTS {function_name}()"))


@pytest.mark.parametrize(
    ("permission_id", "permission_key"),
    [
        (PORTAL_PERMISSION_ID, "custom.coach_roster.read"),
        (UUID("20000000-0000-4000-8000-000000000026"), "coach_roster.read"),
    ],
    ids=("fixed-uuid", "fixed-key"),
)
def test_portal_upgrade_refuses_preexisting_permission_before_ddl(
    postgres_engine: Engine,
    permission_id: UUID,
    permission_key: str,
) -> None:
    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0005_admissions_enrollment")
    params = {
        "permission_id": permission_id,
        "permission_key": permission_key,
        "permission_name": f"预先存在的权限 {permission_key}",
    }
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO permissions (id, key, name, sensitive) "
                    "VALUES (:permission_id, :permission_key, :permission_name, true)"
                ),
                params,
            )

        with pytest.raises(
            RuntimeError,
            match="cannot upgrade portal with preexisting coach roster permission",
        ):
            command.upgrade(config, "0006_coach_family_portal")

        with postgres_engine.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == "0005_admissions_enrollment"
            assert connection.execute(
                text(
                    "SELECT key, name, sensitive FROM permissions "
                    "WHERE id = :permission_id"
                ),
                params,
            ).one() == (permission_key, params["permission_name"], True)
            assert _portal_index_names(connection) == set()
            assert _role_permission_grants(connection).isdisjoint(PORTAL_GRANTS)
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM permissions WHERE id = :permission_id"),
                params,
            )
        command.upgrade(config, "head")


def test_portal_upgrade_refuses_preexisting_default_grant_before_ddl(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    params = {
        "role_id": UUID("10000000-0000-4000-8000-000000000002"),
        "permission_id": UUID("20000000-0000-4000-8000-000000000021"),
    }
    command.downgrade(config, "0005_admissions_enrollment")
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO role_permissions "
                    "(role_id, permission_id, scope_kind) "
                    "VALUES (:role_id, :permission_id, 'assigned_class')"
                ),
                params,
            )

        with pytest.raises(
            RuntimeError,
            match="cannot upgrade portal with preexisting default grants",
        ):
            command.upgrade(config, "0006_coach_family_portal")

        with postgres_engine.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == "0005_admissions_enrollment"
            assert connection.scalar(
                text(
                    "SELECT count(*) FROM role_permissions "
                    "WHERE role_id = :role_id AND permission_id = :permission_id "
                    "AND scope_kind = 'assigned_class'"
                ),
                params,
            ) == 1
            assert connection.scalar(
                text(
                    "SELECT count(*) FROM permissions "
                    "WHERE id = :permission_id"
                ),
                {"permission_id": PORTAL_PERMISSION_ID},
            ) == 0
            assert _portal_index_names(connection) == set()
            assert _role_permission_grants(connection).intersection(PORTAL_GRANTS) == {
                ("coach", "enrollments.read", "assigned_class")
            }
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM role_permissions "
                    "WHERE role_id = :role_id AND permission_id = :permission_id "
                    "AND scope_kind = 'assigned_class'"
                ),
                params,
            )
        command.upgrade(config, "head")


def test_portal_downgrade_refuses_account_override_before_ddl(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    with postgres_engine.connect() as connection:
        _assert_portal_ddl_is_intact(connection)
    person_id, account_id = _insert_test_account(postgres_engine)
    override_id = uuid4()
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO user_permission_overrides "
                    "(id, user_id, permission_id, effect, scope_kind, scope_id) "
                    "VALUES (:id, :user_id, :permission_id, 'deny', 'self', NULL)"
                ),
                {
                    "id": override_id,
                    "user_id": account_id,
                    "permission_id": PORTAL_PERMISSION_ID,
                },
            )

        with pytest.raises(
            RuntimeError,
            match="cannot downgrade portal with account permission overrides",
        ):
            command.downgrade(config, "0005_admissions_enrollment")

        with postgres_engine.connect() as connection:
            _assert_portal_ddl_is_intact(connection)
            assert connection.scalar(
                text(
                    "SELECT EXISTS ("
                    "SELECT 1 FROM user_permission_overrides WHERE id = :id)"
                ),
                {"id": override_id},
            ) is True
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM user_permission_overrides WHERE id = :id"),
                {"id": override_id},
            )
        command.upgrade(config, "head")
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )


def test_portal_downgrade_refuses_modified_permission_seed_before_ddl(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    with postgres_engine.connect() as connection:
        _assert_portal_ddl_is_intact(connection)
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE permissions SET name = '已修改名单权限' "
                    "WHERE id = :permission_id"
                ),
                {"permission_id": PORTAL_PERMISSION_ID},
            )

        with pytest.raises(
            RuntimeError,
            match="cannot downgrade portal with modified permission seed",
        ):
            command.downgrade(config, "0005_admissions_enrollment")

        with postgres_engine.connect() as connection:
            _assert_portal_ddl_is_intact(connection)
            assert connection.scalar(
                text("SELECT name FROM permissions WHERE id = :permission_id"),
                {"permission_id": PORTAL_PERMISSION_ID},
            ) == "已修改名单权限"
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE permissions SET name = '查看教练下一期名单' "
                    "WHERE id = :permission_id"
                ),
                {"permission_id": PORTAL_PERMISSION_ID},
            )
        command.upgrade(config, "head")


def test_portal_downgrade_refuses_customized_roster_grant_before_ddl(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    with postgres_engine.connect() as connection:
        _assert_portal_ddl_is_intact(connection)
    params = {
        "role_id": UUID("10000000-0000-4000-8000-000000000006"),
        "permission_id": PORTAL_PERMISSION_ID,
    }
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO role_permissions (role_id, permission_id, scope_kind) "
                    "VALUES (:role_id, :permission_id, 'family')"
                ),
                params,
            )

        with pytest.raises(
            RuntimeError,
            match="cannot downgrade portal with customized coach roster grants",
        ):
            command.downgrade(config, "0005_admissions_enrollment")

        with postgres_engine.connect() as connection:
            _assert_portal_ddl_is_intact(connection)
            assert connection.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM role_permissions "
                    "WHERE role_id = :role_id AND permission_id = :permission_id "
                    "AND scope_kind = 'family')"
                ),
                params,
            ) is True
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM role_permissions WHERE role_id = :role_id "
                    "AND permission_id = :permission_id AND scope_kind = 'family'"
                ),
                params,
            )
        command.upgrade(config, "head")


def test_portal_downgrade_refuses_modified_default_grant_before_ddl(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    with postgres_engine.connect() as connection:
        _assert_portal_ddl_is_intact(connection)
    params = {
        "role_id": UUID("10000000-0000-4000-8000-000000000002"),
        "permission_id": UUID("20000000-0000-4000-8000-000000000021"),
    }
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE role_permissions SET scope_kind = 'global' "
                    "WHERE role_id = :role_id AND permission_id = :permission_id "
                    "AND scope_kind = 'assigned_class'"
                ),
                params,
            )

        with pytest.raises(
            RuntimeError,
            match="cannot downgrade portal with modified default grants",
        ):
            command.downgrade(config, "0005_admissions_enrollment")

        with postgres_engine.connect() as connection:
            _assert_portal_ddl_is_intact(connection)
            assert connection.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM role_permissions "
                    "WHERE role_id = :role_id AND permission_id = :permission_id "
                    "AND scope_kind = 'global')"
                ),
                params,
            ) is True
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE role_permissions SET scope_kind = 'assigned_class' "
                    "WHERE role_id = :role_id AND permission_id = :permission_id "
                    "AND scope_kind = 'global'"
                ),
                params,
            )
        command.upgrade(config, "head")


def test_evaluation_foundation_upgrade_downgrade_upgrade_restores_clean_schema(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0007_evaluation_page_integration")
    try:
        with postgres_engine.connect() as connection:
            inspector = inspect(connection)
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0007_evaluation_page_integration"
            )
            assert EVALUATION_FOUNDATION_TABLES.isdisjoint(
                set(inspector.get_table_names())
            )
            assert "game_account_limit" not in {
                column["name"] for column in inspector.get_columns("student_profiles")
            }
            assert "valid_from" not in {
                column["name"]
                for column in inspector.get_columns("user_permission_overrides")
            }
            assert connection.scalar(
                text(
                    "SELECT count(*) FROM permissions "
                    "WHERE id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": list(EVALUATION_FOUNDATION_PERMISSION_IDS.values())},
            ) == 0

        command.upgrade(config, "0008_evaluation_data_media_foundation")
        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0008_evaluation_data_media_foundation"
            )
            assert EVALUATION_FOUNDATION_TABLES <= set(
                inspect(connection).get_table_names()
            )
            assert connection.scalar(
                text("SELECT count(*) FROM project_analysis_profiles")
            ) == len(INITIAL_PROJECT_IDS)
    finally:
        command.upgrade(config, "head")


def test_evaluation_foundation_downgrade_refuses_business_rows_before_ddl(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    guardian_id, student_id = _insert_people(postgres_engine)
    actor_person_id, actor_id = _insert_test_account(postgres_engine)
    cohort_id = uuid4()
    role_id = uuid4()
    assignment_id = uuid4()
    game_account_id = uuid4()
    phone_id = uuid4()
    profile_id = uuid4()
    blob_id = uuid4()
    asset_id = uuid4()
    override_id = uuid4()
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO student_profiles "
                    "(person_id, student_number, status) "
                    "VALUES (:student_id, :student_number, 'active')"
                ),
                {
                    "student_id": student_id,
                    "student_number": f"downgrade-{student_id.hex[:20]}",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO cohorts "
                    "(id, name, starts_at, ends_at, default_check_in_at, "
                    "default_check_out_at, status, created_by_user_id) VALUES "
                    "(:id, '评价降级保护届次', TIMESTAMPTZ '2026-09-01 08:00:00+08', "
                    "TIMESTAMPTZ '2026-12-31 18:00:00+08', "
                    "TIMESTAMPTZ '2026-09-01 08:00:00+08', "
                    "TIMESTAMPTZ '2026-09-01 18:00:00+08', 'draft', :actor_id)"
                ),
                {"id": cohort_id, "actor_id": actor_id},
            )
            connection.execute(
                text(
                    "INSERT INTO evaluation_staff_roles "
                    "(id, key, name, category, is_builtin, is_active, "
                    "created_by_user_id) VALUES "
                    "(:id, :key, '评价降级保护角色', 'operations', false, true, :actor_id)"
                ),
                {
                    "id": role_id,
                    "key": f"downgrade_guard_{role_id.hex[:20]}",
                    "actor_id": actor_id,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO cohort_staff_assignments "
                    "(id, cohort_id, role_id, staff_person_id, project_id, "
                    "role_name_snapshot, role_category_snapshot, valid_from, "
                    "created_by_user_id) VALUES "
                    "(:id, :cohort_id, :role_id, :staff_id, :project_id, "
                    "'评价降级保护角色', 'operations', CURRENT_TIMESTAMP, :actor_id)"
                ),
                {
                    "id": assignment_id,
                    "cohort_id": cohort_id,
                    "role_id": role_id,
                    "staff_id": actor_person_id,
                    "project_id": INITIAL_PROJECT_IDS[0],
                    "actor_id": actor_id,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO cohort_staff_assignment_permissions "
                    "(assignment_id, permission_id, created_by_user_id) VALUES "
                    "(:assignment_id, :permission_id, :actor_id)"
                ),
                {
                    "assignment_id": assignment_id,
                    "permission_id": EVALUATION_FOUNDATION_PERMISSION_IDS[
                        "evaluation_media.upload"
                    ],
                    "actor_id": actor_id,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO student_game_accounts "
                    "(id, student_person_id, project_id, identifier_ciphertext, "
                    "identifier_digest, valid_from, created_by_user_id) VALUES "
                    "(:id, :student_id, :project_id, 'ciphertext', :digest, "
                    "CURRENT_TIMESTAMP, :actor_id)"
                ),
                {
                    "id": game_account_id,
                    "student_id": student_id,
                    "project_id": INITIAL_PROJECT_IDS[0],
                    "digest": game_account_id.hex * 2,
                    "actor_id": actor_id,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO student_auxiliary_phones "
                    "(id, student_person_id, phone_ciphertext, phone_digest, "
                    "valid_from, created_by_user_id) VALUES "
                    "(:id, :student_id, 'ciphertext', :digest, CURRENT_TIMESTAMP, "
                    ":actor_id)"
                ),
                {
                    "id": phone_id,
                    "student_id": student_id,
                    "digest": phone_id.hex * 2,
                    "actor_id": actor_id,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO project_analysis_profiles "
                    "(id, project_id, version, mode, required_fields, "
                    "confidence_thresholds, auto_publish_warning_levels, "
                    "publish_mode, effective_from, configured_by_user_id, "
                    "change_reason) VALUES "
                    "(:id, :project_id, 2, 'manual_only', '[]'::jsonb, '{}'::jsonb, "
                    "'[]'::jsonb, 'review_required', CURRENT_TIMESTAMP, :actor_id, "
                    "'downgrade preservation test')"
                ),
                {
                    "id": profile_id,
                    "project_id": INITIAL_PROJECT_IDS[0],
                    "actor_id": actor_id,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO evaluation_media_blobs "
                    "(id, storage_key, preview_storage_key, sha256, mime_type, "
                    "canonical_extension, size_bytes, width_pixels, height_pixels, "
                    "storage_status, validation_status, retention_status) VALUES "
                    "(:id, :storage_key, :preview_key, :sha256, 'image/webp', 'webp', "
                    "100, 10, 10, 'available', 'validated', 'retained')"
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
                    "(id, blob_id, uploaded_by_user_id, project_id, "
                    "delegated_upload_reason) VALUES "
                    "(:id, :blob_id, :actor_id, :project_id, 'downgrade test')"
                ),
                {
                    "id": asset_id,
                    "blob_id": blob_id,
                    "actor_id": actor_id,
                    "project_id": INITIAL_PROJECT_IDS[0],
                },
            )
            connection.execute(
                text(
                    "INSERT INTO user_permission_overrides "
                    "(id, user_id, permission_id, effect, scope_kind, scope_id, "
                    "valid_from, created_by_user_id, grant_reason) VALUES "
                    "(:id, :actor_id, :permission_id, 'allow', 'project', "
                    ":project_id, CURRENT_TIMESTAMP, :actor_id, 'downgrade test')"
                ),
                {
                    "id": override_id,
                    "actor_id": actor_id,
                    "permission_id": EVALUATION_FOUNDATION_PERMISSION_IDS[
                        "evaluation_media.upload"
                    ],
                    "project_id": INITIAL_PROJECT_IDS[0],
                },
            )

        with pytest.raises(
            RuntimeError,
            match="cannot downgrade evaluation foundation with business data",
        ):
            command.downgrade(config, "0007_evaluation_page_integration")

        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0011_housing_real_name_reporting"
            )
            for table_name, row_id in (
                ("evaluation_staff_roles", role_id),
                ("cohort_staff_assignments", assignment_id),
                ("student_game_accounts", game_account_id),
                ("student_auxiliary_phones", phone_id),
                ("project_analysis_profiles", profile_id),
                ("evaluation_media_blobs", blob_id),
                ("evaluation_media_assets", asset_id),
                ("user_permission_overrides", override_id),
            ):
                assert connection.scalar(
                    text(f"SELECT EXISTS (SELECT 1 FROM {table_name} WHERE id = :id)"),
                    {"id": row_id},
                ) is True
            assert connection.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM "
                    "cohort_staff_assignment_permissions "
                    "WHERE assignment_id = :assignment_id)"
                ),
                {"assignment_id": assignment_id},
            ) is True
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM user_permission_overrides WHERE id = :id"),
                {"id": override_id},
            )
            connection.execute(
                text("DELETE FROM evaluation_media_assets WHERE id = :id"),
                {"id": asset_id},
            )
            connection.execute(
                text("DELETE FROM evaluation_media_blobs WHERE id = :id"),
                {"id": blob_id},
            )
            connection.execute(
                text("DELETE FROM project_analysis_profiles WHERE id = :id"),
                {"id": profile_id},
            )
            connection.execute(
                text("DELETE FROM student_auxiliary_phones WHERE id = :id"),
                {"id": phone_id},
            )
            connection.execute(
                text("DELETE FROM student_game_accounts WHERE id = :id"),
                {"id": game_account_id},
            )
            connection.execute(
                text(
                    "DELETE FROM cohort_staff_assignment_permissions "
                    "WHERE assignment_id = :id"
                ),
                {"id": assignment_id},
            )
            connection.execute(
                text("DELETE FROM cohort_staff_assignments WHERE id = :id"),
                {"id": assignment_id},
            )
            connection.execute(
                text("DELETE FROM evaluation_staff_roles WHERE id = :id"),
                {"id": role_id},
            )
            connection.execute(
                text("DELETE FROM student_profiles WHERE person_id = :student_id"),
                {"student_id": student_id},
            )
            connection.execute(
                text("DELETE FROM cohorts WHERE id = :id"),
                {"id": cohort_id},
            )
        command.upgrade(config, "head")
        _delete_test_account(
            postgres_engine,
            person_id=actor_person_id,
            account_id=actor_id,
        )
        _delete_people(postgres_engine, guardian_id, student_id)


def _assert_housing_downgrade_refusal_preserved(
    connection: Connection,
    expected_revision: str = "0011_housing_real_name_reporting",
) -> None:
    assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
        expected_revision
    )
    assert HOUSING_FOUNDATION_TABLES <= set(inspect(connection).get_table_names())


def test_housing_foundation_upgrade_downgrade_upgrade_restores_clean_schema(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0008_evaluation_data_media_foundation")
    try:
        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0008_evaluation_data_media_foundation"
            )
            assert HOUSING_FOUNDATION_TABLES.isdisjoint(
                set(inspect(connection).get_table_names())
            )
            assert connection.scalar(
                text(
                    "SELECT count(*) FROM permissions "
                    "WHERE id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": list(HOUSING_PERMISSION_IDS.values())},
            ) == 0

        command.upgrade(config, "0009_housing_room_status_foundation")
        with postgres_engine.connect() as connection:
            _assert_housing_downgrade_refusal_preserved(
                connection,
                "0009_housing_room_status_foundation",
            )
            assert connection.scalar(text("SELECT count(*) FROM housing_rooms")) == 188
            assert connection.scalar(
                text(
                    "SELECT count(*) FROM permissions "
                    "WHERE id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": list(HOUSING_PERMISSION_IDS.values())},
            ) == 2
    finally:
        command.upgrade(config, "head")


def test_housing_downgrade_refuses_edited_seeded_room_before_ddl(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    room_number = "4-201"
    custom_notes = "保留人工核验结果"
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE housing_rooms SET notes = :notes "
                    "WHERE room_number = :room_number"
                ),
                {"notes": custom_notes, "room_number": room_number},
            )

        with pytest.raises(
            RuntimeError,
            match="cannot downgrade housing foundation with customized housing data",
        ):
            command.downgrade(config, "0008_evaluation_data_media_foundation")

        with postgres_engine.connect() as connection:
            _assert_housing_downgrade_refusal_preserved(connection)
            assert connection.scalar(
                text(
                    "SELECT notes FROM housing_rooms WHERE room_number = :room_number"
                ),
                {"room_number": room_number},
            ) == custom_notes
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE housing_rooms SET notes = NULL "
                    "WHERE room_number = :room_number"
                ),
                {"room_number": room_number},
            )
        command.upgrade(config, "head")


def test_housing_downgrade_refuses_added_room_before_ddl(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    room_id = uuid4()
    with postgres_engine.connect() as connection:
        building_id, floor_id = connection.execute(
            text(
                "SELECT buildings.id, floors.id FROM housing_buildings buildings "
                "JOIN housing_floors floors ON floors.building_id = buildings.id "
                "WHERE buildings.code = '4' AND floors.number = 2"
            )
        ).one()
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO housing_rooms "
                    "(id, building_id, floor_id, room_number, normalized_key, "
                    "room_type, capacity, area_square_meters, verification_status, "
                    "service_status, version) VALUES "
                    "(:id, :building_id, :floor_id, '4-X999', 'x999', "
                    "'custom', 4, 50, 'verified', 'available', 1)"
                ),
                {"id": room_id, "building_id": building_id, "floor_id": floor_id},
            )

        with pytest.raises(
            RuntimeError,
            match="cannot downgrade housing foundation with customized housing data",
        ):
            command.downgrade(config, "0008_evaluation_data_media_foundation")

        with postgres_engine.connect() as connection:
            _assert_housing_downgrade_refusal_preserved(connection)
            assert connection.scalar(
                text("SELECT count(*) FROM housing_rooms WHERE id = :id"),
                {"id": room_id},
            ) == 1
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM housing_rooms WHERE id = :id"),
                {"id": room_id},
            )
        command.upgrade(config, "head")


def test_housing_downgrade_refuses_edited_permission_before_ddl(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    permission_id = HOUSING_PERMISSION_IDS["housing.read"]
    custom_name = "自定义房态查看权限"
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text("UPDATE permissions SET name = :name WHERE id = :id"),
                {"name": custom_name, "id": permission_id},
            )

        with pytest.raises(
            RuntimeError,
            match="cannot downgrade housing foundation with customized housing data",
        ):
            command.downgrade(config, "0008_evaluation_data_media_foundation")

        with postgres_engine.connect() as connection:
            _assert_housing_downgrade_refusal_preserved(connection)
            assert connection.scalar(
                text("SELECT name FROM permissions WHERE id = :id"),
                {"id": permission_id},
            ) == custom_name
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text("UPDATE permissions SET name = '查看房态' WHERE id = :id"),
                {"id": permission_id},
            )
        command.upgrade(config, "head")


def test_housing_downgrade_refuses_removed_admin_grant_before_ddl(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    permission_id = HOUSING_PERMISSION_IDS["housing.rooms.manage"]
    with postgres_engine.connect() as connection:
        admin_role_id = connection.scalar(text("SELECT id FROM roles WHERE key = 'admin'"))
    assert admin_role_id is not None
    params = {"role_id": admin_role_id, "permission_id": permission_id}
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM role_permissions WHERE role_id = :role_id "
                    "AND permission_id = :permission_id AND scope_kind = 'global'"
                ),
                params,
            )

        with pytest.raises(
            RuntimeError,
            match="cannot downgrade housing foundation with customized housing data",
        ):
            command.downgrade(config, "0008_evaluation_data_media_foundation")

        with postgres_engine.connect() as connection:
            _assert_housing_downgrade_refusal_preserved(connection)
            assert connection.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM role_permissions "
                    "WHERE role_id = :role_id AND permission_id = :permission_id "
                    "AND scope_kind = 'global')"
                ),
                params,
            ) is False
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO role_permissions "
                    "(role_id, permission_id, scope_kind) "
                    "VALUES (:role_id, :permission_id, 'global') "
                    "ON CONFLICT DO NOTHING"
                ),
                params,
            )
        command.upgrade(config, "head")


@pytest.mark.parametrize("scope_kind", ["building", "floor"])
def test_housing_downgrade_refuses_housing_scope_override_before_ddl(
    postgres_engine: Engine,
    scope_kind: str,
) -> None:
    config = _alembic_config(postgres_engine)
    person_id, account_id = _insert_test_account(postgres_engine)
    override_id = uuid4()
    table_name = "housing_buildings" if scope_kind == "building" else "housing_floors"
    with postgres_engine.connect() as connection:
        scope_id = connection.scalar(text(f"SELECT id FROM {table_name} LIMIT 1"))
        permission_id = connection.scalar(
            text("SELECT id FROM permissions WHERE key = 'people.read'")
        )
    assert scope_id is not None
    assert permission_id is not None
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO user_permission_overrides "
                    "(id, user_id, permission_id, effect, scope_kind, scope_id) "
                    "VALUES (:id, :user_id, :permission_id, 'allow', "
                    ":scope_kind, :scope_id)"
                ),
                {
                    "id": override_id,
                    "user_id": account_id,
                    "permission_id": permission_id,
                    "scope_kind": scope_kind,
                    "scope_id": scope_id,
                },
            )

        with pytest.raises(
            RuntimeError,
            match="cannot downgrade housing foundation with customized housing data",
        ):
            command.downgrade(config, "0008_evaluation_data_media_foundation")

        with postgres_engine.connect() as connection:
            _assert_housing_downgrade_refusal_preserved(connection)
            assert connection.execute(
                text(
                    "SELECT scope_kind, scope_id FROM user_permission_overrides "
                    "WHERE id = :id"
                ),
                {"id": override_id},
            ).one() == (scope_kind, scope_id)
    finally:
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )
        command.upgrade(config, "head")


def test_housing_downgrade_refuses_override_using_housing_permission_before_ddl(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    person_id, account_id = _insert_test_account(postgres_engine)
    override_id = uuid4()
    permission_id = HOUSING_PERMISSION_IDS["housing.read"]
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO user_permission_overrides "
                    "(id, user_id, permission_id, effect, scope_kind, scope_id) "
                    "VALUES (:id, :user_id, :permission_id, 'allow', 'global', NULL)"
                ),
                {
                    "id": override_id,
                    "user_id": account_id,
                    "permission_id": permission_id,
                },
            )

        with pytest.raises(
            RuntimeError,
            match="cannot downgrade housing foundation with customized housing data",
        ):
            command.downgrade(config, "0008_evaluation_data_media_foundation")

        with postgres_engine.connect() as connection:
            _assert_housing_downgrade_refusal_preserved(connection)
            assert connection.execute(
                text(
                    "SELECT permission_id, scope_kind, scope_id "
                    "FROM user_permission_overrides WHERE id = :id"
                ),
                {"id": override_id},
            ).one() == (permission_id, "global", None)
    finally:
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )
        command.upgrade(config, "head")


def test_housing_downgrade_refuses_role_grant_using_housing_scope_before_ddl(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    with postgres_engine.connect() as connection:
        role_id = connection.scalar(
            text("SELECT id FROM roles WHERE key = 'customer_service'")
        )
        permission_id = connection.scalar(
            text("SELECT id FROM permissions WHERE key = 'people.read'")
        )
    assert role_id is not None
    assert permission_id is not None
    params = {"role_id": role_id, "permission_id": permission_id}
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO role_permissions "
                    "(role_id, permission_id, scope_kind) "
                    "VALUES (:role_id, :permission_id, 'building')"
                ),
                params,
            )

        with pytest.raises(
            RuntimeError,
            match="cannot downgrade housing foundation with customized housing data",
        ):
            command.downgrade(config, "0008_evaluation_data_media_foundation")

        with postgres_engine.connect() as connection:
            _assert_housing_downgrade_refusal_preserved(connection)
            assert connection.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM role_permissions "
                    "WHERE role_id = :role_id AND permission_id = :permission_id "
                    "AND scope_kind = 'building')"
                ),
                params,
            ) is True
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM role_permissions "
                    "WHERE role_id = :role_id AND permission_id = :permission_id "
                    "AND scope_kind = 'building'"
                ),
                params,
            )
        command.upgrade(config, "head")


def test_housing_downgrade_refuses_assignment_using_housing_permission_before_ddl(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    person_id, account_id = _insert_test_account(postgres_engine)
    cohort_id = uuid4()
    assignment_id = uuid4()
    permission_id = HOUSING_PERMISSION_IDS["housing.read"]
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO cohorts "
                    "(id, name, starts_at, ends_at, default_check_in_at, "
                    "default_check_out_at, status, created_by_user_id) VALUES "
                    "(:id, '房态降级依赖测试届次', "
                    "TIMESTAMPTZ '2026-09-01 08:00:00+08', "
                    "TIMESTAMPTZ '2026-09-21 18:00:00+08', "
                    "TIMESTAMPTZ '2026-09-01 08:00:00+08', "
                    "TIMESTAMPTZ '2026-09-21 18:00:00+08', "
                    "'draft', :actor_id)"
                ),
                {"id": cohort_id, "actor_id": account_id},
            )
            connection.execute(
                text(
                    "INSERT INTO cohort_staff_assignments "
                    "(id, cohort_id, role_id, staff_person_id, project_id, "
                    "role_name_snapshot, role_category_snapshot, valid_from, "
                    "created_by_user_id) VALUES "
                    "(:id, :cohort_id, :role_id, :staff_person_id, :project_id, "
                    "'生活老师', 'life_service', CURRENT_TIMESTAMP, :actor_id)"
                ),
                {
                    "id": assignment_id,
                    "cohort_id": cohort_id,
                    "role_id": EVALUATION_LIFE_TEACHER_ROLE_ID,
                    "staff_person_id": person_id,
                    "project_id": INITIAL_PROJECT_IDS[0],
                    "actor_id": account_id,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO cohort_staff_assignment_permissions "
                    "(assignment_id, permission_id, created_by_user_id) "
                    "VALUES (:assignment_id, :permission_id, :actor_id)"
                ),
                {
                    "assignment_id": assignment_id,
                    "permission_id": permission_id,
                    "actor_id": account_id,
                },
            )

        with pytest.raises(
            RuntimeError,
            match="cannot downgrade housing foundation with customized housing data",
        ):
            command.downgrade(config, "0008_evaluation_data_media_foundation")

        with postgres_engine.connect() as connection:
            _assert_housing_downgrade_refusal_preserved(connection)
            assert connection.execute(
                text(
                    "SELECT assignment_id, permission_id "
                    "FROM cohort_staff_assignment_permissions "
                    "WHERE assignment_id = :assignment_id "
                    "AND permission_id = :permission_id"
                ),
                {"assignment_id": assignment_id, "permission_id": permission_id},
            ).one() == (assignment_id, permission_id)
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM cohort_staff_assignment_permissions "
                    "WHERE assignment_id = :assignment_id"
                ),
                {"assignment_id": assignment_id},
            )
            connection.execute(
                text("DELETE FROM cohort_staff_assignments WHERE id = :id"),
                {"id": assignment_id},
            )
            connection.execute(
                text("DELETE FROM cohorts WHERE id = :id"),
                {"id": cohort_id},
            )
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )
        command.upgrade(config, "head")


def _assert_housing_occupancy_downgrade_state_is_intact(
    connection: Connection,
    expected_revision: str = "0011_housing_real_name_reporting",
) -> None:
    assert connection.scalar(text("SELECT version_num FROM alembic_version")) == expected_revision
    assert HOUSING_OCCUPANCY_TABLES <= set(inspect(connection).get_table_names())
    assert connection.scalar(
        text(
            "SELECT count(*) FROM permissions "
            "WHERE id = ANY(CAST(:ids AS uuid[]))"
        ),
        {"ids": list(HOUSING_OCCUPANCY_PERMISSION_IDS.values())},
    ) == len(HOUSING_OCCUPANCY_PERMISSION_IDS)


def test_housing_occupancy_upgrade_downgrade_upgrade_restores_clean_schema(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0009_housing_room_status_foundation")
    try:
        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0009_housing_room_status_foundation"
            )
            assert HOUSING_OCCUPANCY_TABLES.isdisjoint(
                set(inspect(connection).get_table_names())
            )
            assert connection.scalar(
                text(
                    "SELECT count(*) FROM permissions "
                    "WHERE id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": list(HOUSING_OCCUPANCY_PERMISSION_IDS.values())},
            ) == 0
            grants = _role_permission_grants(connection)
            assert ("admin", "housing.read", "global") in grants
            assert ("admin", "housing.rooms.manage", "global") in grants
            assert ("student", "housing.read", "self") not in grants
            assert ("guardian", "housing.read", "family") not in grants

        command.upgrade(config, "0010_housing_occupancy_lifecycle")
        with postgres_engine.connect() as connection:
            _assert_housing_occupancy_downgrade_state_is_intact(
                connection,
                expected_revision="0010_housing_occupancy_lifecycle",
            )
            assert HOUSING_OCCUPANCY_GRANTS <= _role_permission_grants(connection)
    finally:
        command.upgrade(config, "head")


def test_housing_occupancy_downgrade_refuses_business_data_before_ddl(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    person_id, account_id = _insert_test_account(postgres_engine)
    stay_id = uuid4()
    with postgres_engine.connect() as connection:
        room = connection.execute(
            text(
                "SELECT rooms.id, rooms.room_number, "
                "floors.name AS floor_name, buildings.name AS building_name "
                "FROM housing_rooms AS rooms "
                "JOIN housing_floors AS floors ON floors.id = rooms.floor_id "
                "JOIN housing_buildings AS buildings ON buildings.id = rooms.building_id "
                "WHERE rooms.room_type <> 'functional' ORDER BY rooms.id LIMIT 1"
            )
        ).one()
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO housing_stays "
                    "(id, person_id, room_id, stay_purpose, duration_mode, "
                    "planned_check_in_at, status, room_name_snapshot, "
                    "floor_name_snapshot, building_name_snapshot, created_by_user_id, "
                    "version) VALUES "
                    "(:id, :person_id, :room_id, 'permanent', 'long_term', "
                    "TIMESTAMPTZ '2026-10-01 12:00:00+08', 'planned', "
                    ":room_name, :floor_name, :building_name, :account_id, 1)"
                ),
                {
                    "id": stay_id,
                    "person_id": person_id,
                    "room_id": room.id,
                    "room_name": room.room_number,
                    "floor_name": room.floor_name,
                    "building_name": room.building_name,
                    "account_id": account_id,
                },
            )

        with pytest.raises(
            RuntimeError,
            match="cannot downgrade housing occupancy with business data",
        ):
            command.downgrade(config, "0009_housing_room_status_foundation")

        with postgres_engine.connect() as connection:
            _assert_housing_occupancy_downgrade_state_is_intact(connection)
            assert connection.scalar(
                text("SELECT EXISTS (SELECT 1 FROM housing_stays WHERE id = :id)"),
                {"id": stay_id},
            ) is True
    finally:
        try:
            command.upgrade(config, "head")
        finally:
            try:
                with postgres_engine.begin() as connection:
                    if connection.scalar(
                        text("SELECT to_regclass('public.housing_stays') IS NOT NULL")
                    ):
                        connection.execute(
                            text("DELETE FROM housing_stays WHERE id = :id"),
                            {"id": stay_id},
                        )
            finally:
                _delete_test_account(
                    postgres_engine,
                    person_id=person_id,
                    account_id=account_id,
                )
                command.upgrade(config, "head")


def test_housing_occupancy_downgrade_refuses_modified_permission_before_ddl(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    permission_id = HOUSING_OCCUPANCY_PERMISSION_IDS["housing.stays.manage"]
    custom_name = "自定义住宿管理权限"
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text("UPDATE permissions SET name = :name WHERE id = :id"),
                {"name": custom_name, "id": permission_id},
            )

        with pytest.raises(
            RuntimeError,
            match="cannot downgrade housing occupancy with customized grants",
        ):
            command.downgrade(config, "0009_housing_room_status_foundation")

        with postgres_engine.connect() as connection:
            _assert_housing_occupancy_downgrade_state_is_intact(connection)
            assert connection.scalar(
                text("SELECT name FROM permissions WHERE id = :id"),
                {"id": permission_id},
            ) == custom_name
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text("UPDATE permissions SET name = '管理住宿与预约' WHERE id = :id"),
                {"id": permission_id},
            )
        command.upgrade(config, "head")


def test_housing_occupancy_downgrade_refuses_missing_default_grant_before_ddl(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    with postgres_engine.connect() as connection:
        student_role_id = connection.scalar(
            text("SELECT id FROM roles WHERE key = 'student'")
        )
    assert student_role_id is not None
    params = {
        "role_id": student_role_id,
        "permission_id": HOUSING_PERMISSION_IDS["housing.read"],
    }
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM role_permissions WHERE role_id = :role_id "
                    "AND permission_id = :permission_id AND scope_kind = 'self'"
                ),
                params,
            )

        with pytest.raises(
            RuntimeError,
            match="cannot downgrade housing occupancy with customized grants",
        ):
            command.downgrade(config, "0009_housing_room_status_foundation")

        with postgres_engine.connect() as connection:
            _assert_housing_occupancy_downgrade_state_is_intact(connection)
            assert connection.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM role_permissions "
                    "WHERE role_id = :role_id AND permission_id = :permission_id "
                    "AND scope_kind = 'self')"
                ),
                params,
            ) is False
    finally:
        try:
            command.upgrade(config, "head")
        finally:
            with postgres_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO role_permissions "
                        "(role_id, permission_id, scope_kind) "
                        "VALUES (:role_id, :permission_id, 'self') "
                        "ON CONFLICT DO NOTHING"
                    ),
                    params,
                )


def test_housing_occupancy_downgrade_refuses_permission_override_before_ddl(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    person_id, account_id = _insert_test_account(postgres_engine)
    override_id = uuid4()
    permission_id = HOUSING_OCCUPANCY_PERMISSION_IDS["housing.checkouts.manage"]
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO user_permission_overrides "
                    "(id, user_id, permission_id, effect, scope_kind, scope_id) "
                    "VALUES (:id, :user_id, :permission_id, 'allow', 'global', NULL)"
                ),
                {
                    "id": override_id,
                    "user_id": account_id,
                    "permission_id": permission_id,
                },
            )

        with pytest.raises(
            RuntimeError,
            match="cannot downgrade housing occupancy with customized grants",
        ):
            command.downgrade(config, "0009_housing_room_status_foundation")

        with postgres_engine.connect() as connection:
            _assert_housing_occupancy_downgrade_state_is_intact(connection)
            assert connection.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM user_permission_overrides "
                    "WHERE id = :id)"
                ),
                {"id": override_id},
            ) is True
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM user_permission_overrides WHERE id = :id"),
                {"id": override_id},
            )
        command.upgrade(config, "head")
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )


def test_housing_occupancy_downgrade_refuses_extra_role_grant_before_ddl(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    with postgres_engine.connect() as connection:
        role_id = connection.scalar(
            text("SELECT id FROM roles WHERE key = 'customer_service'")
        )
    assert role_id is not None
    params = {
        "role_id": role_id,
        "permission_id": HOUSING_OCCUPANCY_PERMISSION_IDS["housing.issues.manage"],
    }
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO role_permissions "
                    "(role_id, permission_id, scope_kind) "
                    "VALUES (:role_id, :permission_id, 'global')"
                ),
                params,
            )

        with pytest.raises(
            RuntimeError,
            match="cannot downgrade housing occupancy with customized grants",
        ):
            command.downgrade(config, "0009_housing_room_status_foundation")

        with postgres_engine.connect() as connection:
            _assert_housing_occupancy_downgrade_state_is_intact(connection)
            assert connection.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM role_permissions "
                    "WHERE role_id = :role_id AND permission_id = :permission_id "
                    "AND scope_kind = 'global')"
                ),
                params,
            ) is True
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM role_permissions WHERE role_id = :role_id "
                    "AND permission_id = :permission_id AND scope_kind = 'global'"
                ),
                params,
            )
        command.upgrade(config, "head")


def test_housing_occupancy_downgrade_refuses_staff_permission_before_ddl(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    person_id, account_id = _insert_test_account(postgres_engine)
    cohort_id = uuid4()
    assignment_id = uuid4()
    permission_id = HOUSING_OCCUPANCY_PERMISSION_IDS["housing.transfers.manage"]
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO cohorts "
                    "(id, name, starts_at, ends_at, default_check_in_at, "
                    "default_check_out_at, status, created_by_user_id) VALUES "
                    "(:id, '入住生命周期降级依赖届次', "
                    "TIMESTAMPTZ '2026-11-01 08:00:00+08', "
                    "TIMESTAMPTZ '2026-11-21 18:00:00+08', "
                    "TIMESTAMPTZ '2026-11-01 08:00:00+08', "
                    "TIMESTAMPTZ '2026-11-21 18:00:00+08', "
                    "'draft', :actor_id)"
                ),
                {"id": cohort_id, "actor_id": account_id},
            )
            connection.execute(
                text(
                    "INSERT INTO cohort_staff_assignments "
                    "(id, cohort_id, role_id, staff_person_id, project_id, "
                    "role_name_snapshot, role_category_snapshot, valid_from, "
                    "created_by_user_id) VALUES "
                    "(:id, :cohort_id, :role_id, :staff_person_id, :project_id, "
                    "'生活老师', 'life_service', CURRENT_TIMESTAMP, :actor_id)"
                ),
                {
                    "id": assignment_id,
                    "cohort_id": cohort_id,
                    "role_id": EVALUATION_LIFE_TEACHER_ROLE_ID,
                    "staff_person_id": person_id,
                    "project_id": INITIAL_PROJECT_IDS[0],
                    "actor_id": account_id,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO cohort_staff_assignment_permissions "
                    "(assignment_id, permission_id, created_by_user_id) "
                    "VALUES (:assignment_id, :permission_id, :actor_id)"
                ),
                {
                    "assignment_id": assignment_id,
                    "permission_id": permission_id,
                    "actor_id": account_id,
                },
            )

        with pytest.raises(
            RuntimeError,
            match="cannot downgrade housing occupancy with customized grants",
        ):
            command.downgrade(config, "0009_housing_room_status_foundation")

        with postgres_engine.connect() as connection:
            _assert_housing_occupancy_downgrade_state_is_intact(connection)
            assert connection.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM "
                    "cohort_staff_assignment_permissions "
                    "WHERE assignment_id = :assignment_id "
                    "AND permission_id = :permission_id)"
                ),
                {"assignment_id": assignment_id, "permission_id": permission_id},
            ) is True
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM cohort_staff_assignment_permissions "
                    "WHERE assignment_id = :assignment_id"
                ),
                {"assignment_id": assignment_id},
            )
            connection.execute(
                text("DELETE FROM cohort_staff_assignments WHERE id = :id"),
                {"id": assignment_id},
            )
            connection.execute(
                text("DELETE FROM cohorts WHERE id = :id"),
                {"id": cohort_id},
            )
        command.upgrade(config, "head")
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )


@pytest.mark.parametrize(
    "mutation_kind",
    ["permission", "extra_grant", "business"],
    ids=("permission", "extra-role-grant", "business-row"),
)
def test_housing_occupancy_downgrade_locks_concurrent_customization_until_ddl(
    postgres_engine: Engine,
    mutation_kind: str,
) -> None:
    config = _alembic_config(postgres_engine)
    person_id, account_id = _insert_test_account(postgres_engine)
    permission_id = HOUSING_OCCUPANCY_PERMISSION_IDS["housing.stays.manage"]
    issue_id = uuid4()
    lock_key = 7_300_010
    trigger_name = "trg_pause_housing_occupancy_downgrade"
    function_name = "pause_housing_occupancy_downgrade"
    with postgres_engine.connect() as connection:
        room_id = connection.scalar(
            text(
                "SELECT id FROM housing_rooms "
                "WHERE room_type <> 'functional' ORDER BY id LIMIT 1"
            )
        )
        customer_service_role_id = connection.scalar(
            text("SELECT id FROM roles WHERE key = 'customer_service'")
        )
    assert room_id is not None
    assert customer_service_role_id is not None

    mutation_sql = {
        "permission": (
            "UPDATE permissions SET name = '并发修改住宿权限' "
            "WHERE id = :permission_id"
        ),
        "extra_grant": (
            "INSERT INTO role_permissions (role_id, permission_id, scope_kind) "
            "VALUES (:customer_service_role_id, :permission_id, 'global')"
        ),
        "business": (
            "INSERT INTO housing_room_issues "
            "(id, room_id, description, previous_service_status, reported_at, "
            "reported_by_user_id, status) VALUES "
            "(:issue_id, :room_id, '并发降级屏障测试', 'available', "
            "CURRENT_TIMESTAMP, :account_id, 'open')"
        ),
    }[mutation_kind]
    mutation_params = {
        "permission_id": permission_id,
        "customer_service_role_id": customer_service_role_id,
        "issue_id": issue_id,
        "room_id": room_id,
        "account_id": account_id,
    }

    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                f"""
                CREATE FUNCTION {function_name}() RETURNS trigger
                LANGUAGE plpgsql AS $$
                BEGIN
                    PERFORM pg_advisory_lock({lock_key});
                    PERFORM pg_advisory_unlock({lock_key});
                    RETURN OLD;
                END;
                $$
                """
            )
        )
        connection.execute(
            text(
                f"""
                CREATE TRIGGER {trigger_name}
                BEFORE DELETE ON role_permissions
                FOR EACH ROW
                WHEN (
                    OLD.role_id = '10000000-0000-4000-8000-000000000001'
                    AND OLD.permission_id =
                        '20000000-0000-4000-8000-000000000039'
                )
                EXECUTE FUNCTION {function_name}()
                """
            )
        )

    control_connection = postgres_engine.connect()
    pool = None
    future = None
    advisory_lock_held = False
    try:
        control_connection.execute(
            text("SELECT pg_advisory_lock(:lock_key)"),
            {"lock_key": lock_key},
        )
        advisory_lock_held = True
        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(
            command.downgrade,
            config,
            "0009_housing_room_status_foundation",
        )

        deadline = monotonic() + 5
        reached_barrier = False
        while monotonic() < deadline:
            with postgres_engine.connect() as connection:
                reached_barrier = bool(
                    connection.scalar(
                        text(
                            "SELECT EXISTS ("
                            "SELECT 1 FROM pg_stat_activity "
                            "WHERE wait_event_type = 'Lock' "
                            "AND wait_event = 'advisory' "
                            "AND query LIKE 'DELETE FROM role_permissions%')"
                        )
                    )
                )
            if reached_barrier:
                break
            sleep(0.02)
        assert reached_barrier, "downgrade did not reach the post-preflight barrier"

        with pytest.raises(DBAPIError, match="lock timeout"), postgres_engine.begin() as connection:
            connection.execute(text("SET LOCAL lock_timeout = '200ms'"))
            connection.execute(text(mutation_sql), mutation_params)

        control_connection.execute(
            text("SELECT pg_advisory_unlock(:lock_key)"),
            {"lock_key": lock_key},
        )
        advisory_lock_held = False
        future.result(timeout=10)
        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0009_housing_room_status_foundation"
            )
            assert HOUSING_OCCUPANCY_TABLES.isdisjoint(
                set(inspect(connection).get_table_names())
            )
    finally:
        if advisory_lock_held:
            control_connection.execute(
                text("SELECT pg_advisory_unlock(:lock_key)"),
                {"lock_key": lock_key},
            )
        control_connection.close()
        if future is not None:
            with suppress(DBAPIError, AssertionError, TimeoutError):
                future.result(timeout=10)
        if pool is not None:
            pool.shutdown(wait=True)

        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE permissions SET name = '管理住宿与预约' "
                    "WHERE id = :permission_id"
                ),
                {"permission_id": permission_id},
            )
            connection.execute(
                text(
                    "DELETE FROM role_permissions "
                    "WHERE role_id = :role_id AND permission_id = :permission_id "
                    "AND scope_kind = 'global'"
                ),
                {
                    "role_id": customer_service_role_id,
                    "permission_id": permission_id,
                },
            )
            if connection.scalar(
                text("SELECT to_regclass('public.housing_room_issues') IS NOT NULL")
            ):
                connection.execute(
                    text("DELETE FROM housing_room_issues WHERE id = :id"),
                    {"id": issue_id},
                )
        try:
            command.upgrade(config, "head")
        finally:
            with postgres_engine.begin() as connection:
                connection.execute(
                    text(f"DROP TRIGGER IF EXISTS {trigger_name} ON role_permissions")
                )
                connection.execute(text(f"DROP FUNCTION IF EXISTS {function_name}()"))
            _delete_test_account(
                postgres_engine,
                person_id=person_id,
                account_id=account_id,
            )
            command.upgrade(config, "head")
