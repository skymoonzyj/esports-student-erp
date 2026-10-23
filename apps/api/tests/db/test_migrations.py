from __future__ import annotations

import base64
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from io import StringIO
from pathlib import Path
from queue import Queue
from threading import Event
from time import monotonic, sleep
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, event, inspect, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.rbac.seed import SELF_SCOPE_ONLY_PERMISSIONS

API_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_TEST_KEY_BYTES = bytes(range(32))
MIGRATION_TEST_KEY = base64.urlsafe_b64encode(MIGRATION_TEST_KEY_BYTES).decode("ascii")

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
    "project_coach_assignments",
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
    "enrollment_programs",
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

HOUSING_HISTORY_VOID_TABLES = {"housing_stay_void_operations"}
EXPECTED_TABLES |= HOUSING_HISTORY_VOID_TABLES
HOUSING_ROOM_CARE_TABLES = {"housing_room_photos"}
EXPECTED_TABLES |= HOUSING_ROOM_CARE_TABLES

HOUSING_REPORTING_TABLES = {
    "housing_report_batches",
    "housing_report_batch_scopes",
}
EXPECTED_TABLES |= HOUSING_REPORTING_TABLES

SYSTEM_EVALUATION_TABLES = {
    "evaluation_quarantine_cleanup",
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
EXPECTED_TABLES |= SYSTEM_EVALUATION_TABLES

TEACHING_REPORT_TABLES = {
    "student_reports",
    "student_report_drafts",
    "student_report_versions",
    "student_report_reviews",
    "student_report_window_grants",
}
EXPECTED_TABLES |= TEACHING_REPORT_TABLES

FOOD_PREFERENCE_TABLES = {
    "food_cuisine_options",
    "person_food_preferences",
    "person_food_preference_cuisines",
    "person_food_preference_dishes",
    "person_food_preference_versions",
}
EXPECTED_TABLES |= FOOD_PREFERENCE_TABLES

CURRENT_HEAD = "0071_housing_room_care"
ASSISTED_PERSONNEL_TABLES = {
    "profile_claim_sessions", "profile_claims", "profile_claim_bindings", "profile_claim_decisions",
    "personnel_intake_requests", "personnel_intake_revisions", "personnel_credential_reservations",
    "assisted_approvals", "assisted_person_profiles", "coach_person_responsibilities",
    "assisted_activation_sessions",
}
EXPECTED_TABLES |= ASSISTED_PERSONNEL_TABLES
ASSISTED_PERSONNEL_PERMISSION_IDS = {
    "personnel_intake.submit": UUID("20000000-0000-4000-8000-000000000087"),
    "personnel_intake.review": UUID("20000000-0000-4000-8000-000000000088"),
    "personnel_intake.housing.read": UUID("20000000-0000-4000-8000-000000000089"),
}
ASSISTED_PERSONNEL_GRANTS = {
    (role, permission, "global")
    for role in ("admin", "super_admin")
    for permission in ASSISTED_PERSONNEL_PERMISSION_IDS
} | {
    (role, permission, "global")
    for role in ("coach", "team_manager", "administration")
    for permission in ("personnel_intake.submit", "personnel_intake.housing.read")
}
INVITED_HOUSING_TABLES = {"revenue_share_organization_manager_assignments"}
EXPECTED_TABLES |= INVITED_HOUSING_TABLES
CLOUD_BACKUP_TABLES = {"cloud_backup_targets", "cloud_backup_jobs"}
EXPECTED_TABLES |= CLOUD_BACKUP_TABLES
CLOUD_AUTH_TABLES = {
    "cloud_auth_applications", "cloud_auth_sessions", "cloud_auth_candidates",
    "cloud_auth_operations", "cloud_backup_target_history", "cloud_connector_state",
}
EXPECTED_TABLES |= CLOUD_AUTH_TABLES

CAMP_SURVEY_TABLES = {
    "camp_survey_cycles",
    "camp_survey_snapshot_versions",
    "camp_survey_staff_snapshots",
    "camp_survey_submissions",
    "camp_survey_versions",
    "camp_survey_staff_ratings",
    "camp_survey_supplements",
    "camp_survey_supplement_ratings",
}
EXPECTED_TABLES |= CAMP_SURVEY_TABLES

REGISTRATION_TABLES = {
    "account_login_identifiers",
    "guardian_profiles",
    "registration_applications",
    "registration_verification_challenges",
    "registration_sessions",
    "guardian_registration_invitations",
    "staff_identity_applications",
    "registration_consent_acceptances",
    "registration_channel_settings",
}
EXPECTED_TABLES |= REGISTRATION_TABLES

MAIL_TABLES = {
    "mail_delivery_settings",
    "mail_templates",
}
EXPECTED_TABLES |= MAIL_TABLES

ASSET_TABLES = {
    "assets",
    "asset_assignments",
}
EXPECTED_TABLES |= ASSET_TABLES

REVENUE_SHARE_TABLES = {
    "revenue_share_organizations",
    "referral_codes",
    "student_referral_bindings",
    "revenue_share_rules",
    "revenue_share_payment_statuses",
    "revenue_share_ledger_entries",
    "revenue_share_settlements",
    "revenue_share_reconcile_queue",
}
EXPECTED_TABLES |= REVENUE_SHARE_TABLES

PROCUREMENT_TABLES = {
    "procurement_settlement_centers",
    "procurement_reserve_fund_entries",
    "daily_purchase_records",
    "procurement_invoices",
    "procurement_invoice_purchases",
}
EXPECTED_TABLES |= PROCUREMENT_TABLES

OPERATIONS_TABLES = {
    "operations_service_heartbeats", "operations_metric_batches",
    "operations_request_minutes", "operations_alerts",
}
EXPECTED_TABLES |= OPERATIONS_TABLES

TRAVEL_REPORT_TABLES = {"travel_report_conversions"}
EXPECTED_TABLES |= TRAVEL_REPORT_TABLES

TALENT_TEST_TABLES = {
    "talent_test_sessions",
    "talent_test_results",
    "talent_test_reports",
}
EXPECTED_TABLES |= TALENT_TEST_TABLES

ESPORTS_ASSESSMENT_TABLES = {
    "esports_assessment_sessions",
    "esports_assessment_attempts",
    "esports_assessment_results",
    "esports_assessment_reports",
    "esports_assessment_practice_runs",
}
EXPECTED_TABLES |= ESPORTS_ASSESSMENT_TABLES

TEAM_MANAGEMENT_TABLES = {
    "team_memberships",
    "team_manager_assignments",
    "team_operations",
}
EXPECTED_TABLES |= TEAM_MANAGEMENT_TABLES
TEAM_MANAGEMENT_PERMISSION_KEYS = {
    "team_memberships.read",
    "team_memberships.manage",
    "team_memberships.transfer",
    "team_manager_assignments.manage",
}

ESPORTS_ASSESSMENT_PERMISSION_IDS = {
    "esports_assessments.take": UUID("20000000-0000-4000-8000-000000000080"),
    "esports_assessments.read": UUID("20000000-0000-4000-8000-000000000081"),
}

TALENT_TEST_PERMISSION_IDS = {
    "talent_tests.take": UUID("20000000-0000-4000-8000-000000000078"),
    "talent_tests.read": UUID("20000000-0000-4000-8000-000000000079"),
}
TALENT_TEST_GRANTS = {
    ("super_admin", "talent_tests.take", "self"),
    ("super_admin", "talent_tests.read", "global"),
    ("student", "talent_tests.take", "self"),
    ("student", "talent_tests.read", "self"),
    ("guardian", "talent_tests.read", "family"),
    ("coach", "talent_tests.read", "assigned_class"),
}

FINANCE_ROLE_ID = UUID("10000000-0000-4000-8000-000000000011")
REVENUE_SHARE_PERMISSION_IDS = {
    "revenue_share.read_self": UUID("20000000-0000-4000-8000-000000000061"),
    "revenue_share.finance": UUID("20000000-0000-4000-8000-000000000062"),
    "revenue_share.manage": UUID("20000000-0000-4000-8000-000000000063"),
    "revenue_share.export": UUID("20000000-0000-4000-8000-000000000064"),
}
PROCUREMENT_PERMISSION_IDS = {
    "procurement.read": UUID("20000000-0000-4000-8000-000000000065"),
    "procurement.manage": UUID("20000000-0000-4000-8000-000000000066"),
    "procurement.settle": UUID("20000000-0000-4000-8000-000000000067"),
    "procurement.export": UUID("20000000-0000-4000-8000-000000000068"),
    "procurement.centers.manage": UUID("20000000-0000-4000-8000-000000000069"),
    "procurement.invoices.manage": UUID("20000000-0000-4000-8000-000000000070"),
}
TRAVEL_REPORT_PERMISSION_IDS = {
    "travel_reports.read": UUID("20000000-0000-4000-8000-000000000072"),
    "travel_reports.import": UUID("20000000-0000-4000-8000-000000000073"),
    "travel_reports.export": UUID("20000000-0000-4000-8000-000000000074"),
}
ENROLLMENT_APPLICATION_PERMISSION_ID = UUID("20000000-0000-4000-8000-000000000075")
PROJECT_COACH_PERMISSION_ID = UUID("20000000-0000-4000-8000-000000000077")
ENROLLMENT_PROGRAM_PRICES = [
    ("周末半天班", 80_000),
    ("周末全天班", 150_000),
    ("七日体验营", 699_900),
    ("月度体验营", 1_288_800),
    ("季度体验营", 3_288_800),
    ("家庭教育及亲子关系修复课程", 300_000),
    ("普通暑期班", 1_188_800),
    ("职业班冲刺班", 1_388_800),
    ("暑期进阶班", 1_488_800),
]

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
    "evaluation_media.original.read": UUID("20000000-0000-4000-8000-000000000028"),
    "analysis_profiles.manage": UUID("20000000-0000-4000-8000-000000000030"),
    "student_game_accounts.manage": UUID("20000000-0000-4000-8000-000000000031"),
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
SYSTEM_EVALUATION_PERMISSION_IDS = {
    "evaluation_jobs.review": UUID("20000000-0000-4000-8000-000000000029"),
    "evaluation_jobs.match": UUID("20000000-0000-4000-8000-000000000044"),
    "evaluation_jobs.manual_input": UUID("20000000-0000-4000-8000-000000000045"),
    "evaluation_adapters.certify": UUID("20000000-0000-4000-8000-000000000046"),
}
TEACHING_REPORT_PERMISSION_IDS = {
    "student_reports.write": UUID("20000000-0000-4000-8000-000000000032"),
    "student_reports.review": UUID("20000000-0000-4000-8000-000000000033"),
}
FOOD_PREFERENCE_PERMISSION_IDS = {
    "food_preferences.read": UUID("20000000-0000-4000-8000-000000000047"),
    "food_preferences.update": UUID("20000000-0000-4000-8000-000000000048"),
    "food_preferences.catalog.manage": UUID("20000000-0000-4000-8000-000000000049"),
    "food_preferences.export": UUID("20000000-0000-4000-8000-000000000050"),
}
CAMP_SURVEY_PERMISSION_IDS = {
    "camp_surveys.identity.read": UUID("20000000-0000-4000-8000-000000000034"),
    "camp_surveys.print": UUID("20000000-0000-4000-8000-000000000035"),
}
REGISTERED_USER_ROLE_ID = UUID("10000000-0000-4000-8000-000000000008")
REGISTRATION_PERMISSION_IDS = {
    "registration.review": UUID("20000000-0000-4000-8000-000000000051"),
    "registration.sensitive.read": UUID("20000000-0000-4000-8000-000000000052"),
    "staff_identity_applications.review": UUID("20000000-0000-4000-8000-000000000053"),
}
CAMP_SURVEY_GRANTS = {
    ("admin", permission_key, "global") for permission_key in CAMP_SURVEY_PERMISSION_IDS
}
REGISTRATION_GRANTS = {
    *(
        (role_key, key, "global")
        for role_key in ("super_admin", "admin")
        for key in REGISTRATION_PERMISSION_IDS
    ),
    ("registered_user", "food_preferences.read", "self"),
    ("registered_user", "food_preferences.update", "self"),
}
FOOD_PREFERENCE_GRANTS = {
    ("admin", permission_key, "global") for permission_key in FOOD_PREFERENCE_PERMISSION_IDS
} | {
    ("student", "food_preferences.read", "self"),
    ("student", "food_preferences.update", "self"),
    ("guardian", "food_preferences.read", "family"),
    ("guardian", "food_preferences.update", "family"),
}
HOUSING_OCCUPANCY_GRANTS = {
    ("admin", permission_key, "global") for permission_key in HOUSING_OCCUPANCY_PERMISSION_IDS
} | {
    ("student", "housing.read", "self"),
    ("guardian", "housing.read", "family"),
}
EVALUATION_LIFE_TEACHER_ROLE_ID = UUID("41000000-0000-4000-8000-000000000001")
LEGACY_EVALUATION_ACCESS_GRANTS = {
    ("admin", "evaluation_center.access", "global"),
    ("coach", "evaluation_center.access", "global"),
    ("life_teacher", "evaluation_center.access", "global"),
    ("staff", "evaluation_center.access", "global"),
    ("guardian", "evaluation_center.access", "global"),
    ("student", "evaluation_center.access", "global"),
}
EVALUATION_ACCESS_GRANTS = LEGACY_EVALUATION_ACCESS_GRANTS | {
    ("super_admin", "evaluation_center.access", "global"),
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


def _assert_assisted_rbac_transition(connection: Connection, *, installed: bool) -> None:
    permissions = set(
        connection.execute(
            text("SELECT id, key, sensitive FROM permissions WHERE key = ANY(CAST(:keys AS text[]))"),
            {"keys": sorted(ASSISTED_PERSONNEL_PERMISSION_IDS)},
        ).tuples()
    )
    assert permissions == (
        {(id_, key, True) for key, id_ in ASSISTED_PERSONNEL_PERMISSION_IDS.items()}
        if installed else set()
    )
    grants = _role_permission_grants(connection)
    assert {grant for grant in grants if grant[1] in ASSISTED_PERSONNEL_PERMISSION_IDS} == (
        ASSISTED_PERSONNEL_GRANTS if installed else set()
    )
    # 0066 retires this grant; downgrading 0066 does not resurrect global access.
    assert ("coach", "housing.read", "global") not in grants


def _unrelated_rbac_snapshot(
    connection: Connection, excluded_keys: set[str]
) -> tuple[set[tuple], set[tuple[str, str, str]]]:
    permissions = set(
        connection.execute(
            text("SELECT id, key, name, sensitive FROM permissions WHERE key <> ALL(CAST(:keys AS text[]))"),
            {"keys": sorted(excluded_keys)},
        ).tuples()
    )
    grants = {
        grant for grant in _role_permission_grants(connection) if grant[1] not in excluded_keys
    }
    return permissions, grants


def test_0062_cloud_upgrade_empty_downgrade_preserves_existing_rows(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    person_id, account_id = _insert_test_account(postgres_engine)
    excluded_permission_keys = {
        "referrals.read_invited_housing", "housing.history.void", *ASSISTED_PERSONNEL_PERMISSION_IDS
    }

    def row_counts() -> dict[str, int]:
        with postgres_engine.connect() as connection:
            counts = {}
            for table in inspect(connection).get_table_names():
                if table in CLOUD_BACKUP_TABLES | CLOUD_AUTH_TABLES | INVITED_HOUSING_TABLES | ASSISTED_PERSONNEL_TABLES | HOUSING_HISTORY_VOID_TABLES | HOUSING_ROOM_CARE_TABLES | {"alembic_version"}:
                    continue
                if table == "permissions":
                    statement = text("SELECT count(*) FROM permissions WHERE key <> ALL(CAST(:keys AS text[]))")
                elif table == "role_permissions":
                    statement = text(
                        "SELECT count(*) FROM role_permissions rp JOIN permissions p "
                        "ON p.id = rp.permission_id WHERE p.key <> ALL(CAST(:keys AS text[]))"
                    )
                else:
                    statement = text(f'SELECT count(*) FROM "{table}"')
                counts[table] = connection.scalar(statement, {"keys": sorted(excluded_permission_keys)})
            return counts

    def sentinel_rows() -> tuple[tuple, tuple]:
        with postgres_engine.connect() as connection:
            return (
                tuple(connection.execute(text("SELECT to_jsonb(people) - ARRAY['profile_notes','document_type','document_issuing_country','passport_number_digest','intake_mode','team_allocation_status','team_allocation_version']::text[] "
                                              "FROM people WHERE id = :id"), {"id": person_id}).one()),
                tuple(connection.execute(text("SELECT id, person_id, password_hash, status, "
                                              "login_phone_digest, email_normalized, created_at, updated_at "
                                              "FROM user_accounts WHERE id = :id"), {"id": account_id}).one()),
            )

    try:
        before_counts = row_counts()
        before_sentinel = sentinel_rows()
        with postgres_engine.connect() as connection:
            assert connection.scalar(
                text("SELECT profile_notes FROM people WHERE id = :id"), {"id": person_id}
            ) is None
            _assert_assisted_rbac_transition(connection, installed=True)
            before_rbac = _unrelated_rbac_snapshot(connection, excluded_permission_keys)
            before_tables = set(inspect(connection).get_table_names())
            assert CLOUD_BACKUP_TABLES <= before_tables
            for table in CLOUD_BACKUP_TABLES:
                assert connection.scalar(text(f'SELECT count(*) FROM "{table}"')) == 0
        command.downgrade(config, "0061_team_management")
        with postgres_engine.connect() as connection:
            _assert_assisted_rbac_transition(connection, installed=False)
            assert _unrelated_rbac_snapshot(connection, excluded_permission_keys) == before_rbac
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0061_team_management"
            assert set(inspect(connection).get_table_names()) == before_tables - CLOUD_BACKUP_TABLES - CLOUD_AUTH_TABLES - INVITED_HOUSING_TABLES - ASSISTED_PERSONNEL_TABLES - HOUSING_HISTORY_VOID_TABLES - HOUSING_ROOM_CARE_TABLES
        assert row_counts() == before_counts
        assert sentinel_rows() == before_sentinel
        command.upgrade(config, CURRENT_HEAD)
        with postgres_engine.connect() as connection:
            _assert_assisted_rbac_transition(connection, installed=True)
            assert _unrelated_rbac_snapshot(connection, excluded_permission_keys) == before_rbac
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == CURRENT_HEAD
            assert set(inspect(connection).get_table_names()) == before_tables
            indexes = {index['name']: index for index in inspect(connection).get_indexes('cloud_backup_jobs')}
            assert indexes['uq_cloud_job_live_target']['unique']
            assert indexes['uq_cloud_job_auto_backup']['unique']
            assert connection.scalar(
                text("SELECT profile_notes FROM people WHERE id = :id"), {"id": person_id}
            ) is None
        assert row_counts() == before_counts
        assert sentinel_rows() == before_sentinel
    finally:
        command.upgrade(config, CURRENT_HEAD)
        _delete_test_account(postgres_engine, person_id=person_id, account_id=account_id)


def test_0065_invited_housing_preserves_real_referral_and_revenue_rows(
    postgres_engine: Engine,
) -> None:
    from datetime import UTC, datetime

    from app.admissions.models import Payment
    from app.revenue_share.models import (
        ReferralCode,
        RevenueShareLedgerEntry,
        RevenueShareOrganization,
        StudentReferralBinding,
    )

    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0064_cloud_auth_builtin_import")
    person_id, account_id = _insert_test_account(postgres_engine)
    org_id, code_id, binding_id, payment_id, ledger_id = (uuid4() for _ in range(5))
    now = datetime.now(UTC)
    tables = (
        "revenue_share_organizations",
        "referral_codes",
        "student_referral_bindings",
        "payments",
        "revenue_share_ledger_entries",
    )
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                RevenueShareOrganization.__table__.insert().values(
                    id=org_id,
                    name=f"升级保留机构-{org_id}",
                    created_by_user_id=account_id,
                )
            )
            connection.execute(
                ReferralCode.__table__.insert().values(
                    id=code_id,
                    code=f"M-{code_id.hex[:16]}",
                    label="迁移邀请码",
                    source_kind="organization",
                    referrer_organization_id=org_id,
                    created_by_user_id=account_id,
                )
            )
            connection.execute(
                StudentReferralBinding.__table__.insert().values(
                    id=binding_id,
                    student_person_id=person_id,
                    referral_code_id=code_id,
                    source_kind="organization",
                    referrer_organization_id=org_id,
                )
            )
            connection.execute(
                Payment.__table__.insert().values(
                    id=payment_id,
                    source_kind="manual",
                    amount_cents=10000,
                    paid_at=now,
                    manual_note="迁移测试",
                    created_by_user_id=account_id,
                )
            )
            connection.execute(
                RevenueShareLedgerEntry.__table__.insert().values(
                    id=ledger_id,
                    event_key=f"migrate-{ledger_id}",
                    payment_id=payment_id,
                    entry_kind="earning",
                    role_kind="inviter",
                    beneficiary_kind="organization",
                    beneficiary_organization_id=org_id,
                    calculation_type="fixed",
                    base_amount_cents=10000,
                    amount_cents=1000,
                    occurred_at=now,
                )
            )

        def snapshot():
            with postgres_engine.connect() as connection:
                return {
                    table: list(
                        connection.execute(text(f'SELECT * FROM "{table}" ORDER BY id')).tuples()
                    )
                    for table in tables
                }

        before = snapshot()
        command.upgrade(config, CURRENT_HEAD)
        assert snapshot() == before
        with postgres_engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM revenue_share_organization_manager_assignments")
                )
                == 0
            )
            grants = set(
                connection.execute(
                    text(
                        "SELECT r.key, rp.scope_kind FROM role_permissions rp JOIN roles r ON r.id=rp.role_id "
                        "WHERE rp.permission_id='20000000-0000-4000-8000-000000000086'"
                    )
                ).tuples()
            )
            assert grants == {
                (key, "self")
                for key in (
                    "admin",
                    "coach",
                    "customer_service",
                    "life_teacher",
                    "staff",
                    "guardian",
                    "student",
                    "registered_user",
                    "super_admin",
                    "administration",
                    "finance",
                    "team_member",
                    "team_manager",
                )
            }
        command.downgrade(config, "0064_cloud_auth_builtin_import")
        assert snapshot() == before
        assert (
            "revenue_share_organization_manager_assignments"
            not in inspect(postgres_engine).get_table_names()
        )
    finally:
        command.upgrade(config, CURRENT_HEAD)
        with postgres_engine.begin() as connection:
            for table, identifier in reversed(
                list(zip(tables, (org_id, code_id, binding_id, payment_id, ledger_id)))
            ):
                connection.execute(text(f'DELETE FROM "{table}" WHERE id=:id'), {"id": identifier})
            connection.execute(
                text("DELETE FROM revenue_share_reconcile_queue WHERE payment_id=:id"),
                {"id": payment_id},
            )
        _delete_test_account(postgres_engine, person_id=person_id, account_id=account_id)


@pytest.mark.parametrize(
    "retained", ["assignment", "revoked_assignment", "override", "custom_role", "global_role_grant"]
)
def test_0065_downgrade_rejects_history_or_custom_permission_rules_without_mutation(
    postgres_engine: Engine, retained: str
) -> None:
    config = _alembic_config(postgres_engine)
    person_id, account_id = _insert_test_account(postgres_engine)
    org_id, assignment_id, override_id, role_id = (uuid4() for _ in range(4))
    permission_id = UUID("20000000-0000-4000-8000-000000000086")
    try:
        with postgres_engine.begin() as connection:
            if retained in {"assignment", "revoked_assignment"}:
                connection.execute(
                    text(
                        "INSERT INTO revenue_share_organizations(id,name,created_by_user_id) VALUES (:id,:name,:actor)"
                    ),
                    {"id": org_id, "name": f"降级机构-{org_id}", "actor": account_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO revenue_share_organization_manager_assignments(id,organization_id,account_id,granted_by_user_id) VALUES (:id,:org,:actor,:actor)"
                    ),
                    {"id": assignment_id, "org": org_id, "actor": account_id},
                )
                if retained == "revoked_assignment":
                    connection.execute(
                        text(
                            "UPDATE revenue_share_organization_manager_assignments SET revoked_at=granted_at, revoked_by_user_id=:actor WHERE id=:id"
                        ),
                        {"actor": account_id, "id": assignment_id},
                    )
            elif retained == "override":
                connection.execute(
                    text(
                        "INSERT INTO user_permission_overrides(id,user_id,permission_id,effect,scope_kind) VALUES (:id,:actor,:perm,'deny','self')"
                    ),
                    {"id": override_id, "actor": account_id, "perm": permission_id},
                )
            elif retained == "custom_role":
                connection.execute(
                    text("INSERT INTO roles(id,key,name) VALUES (:id,:key,'自定义角色')"),
                    {"id": role_id, "key": f"custom-{role_id}"},
                )
                connection.execute(
                    text(
                        "INSERT INTO role_permissions(role_id,permission_id,scope_kind) VALUES (:role,:perm,'self')"
                    ),
                    {"role": role_id, "perm": permission_id},
                )
            else:
                connection.execute(
                    text(
                        "INSERT INTO role_permissions(role_id,permission_id,scope_kind) SELECT id,:perm,'global' FROM roles WHERE key='admin'"
                    ),
                    {"perm": permission_id},
                )
        with postgres_engine.connect() as connection:
            before = {
                table: list(connection.execute(text(f'SELECT * FROM "{table}"')).tuples())
                for table in (
                    "revenue_share_organization_manager_assignments",
                    "permissions",
                    "role_permissions",
                    "user_permission_overrides",
                )
            }
        with pytest.raises(
            RuntimeError, match="organization manager history|custom permission rules"
        ):
            command.downgrade(config, "0064_cloud_auth_builtin_import")
        with postgres_engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version")) == CURRENT_HEAD
            )
            for table, rows in before.items():
                assert list(connection.execute(text(f'SELECT * FROM "{table}"')).tuples()) == rows
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM revenue_share_organization_manager_assignments WHERE id=:id"),
                {"id": assignment_id},
            )
            connection.execute(
                text("DELETE FROM revenue_share_organizations WHERE id=:id"), {"id": org_id}
            )
            connection.execute(
                text("DELETE FROM user_permission_overrides WHERE id=:id"), {"id": override_id}
            )
            connection.execute(
                text("DELETE FROM role_permissions WHERE role_id=:id"), {"id": role_id}
            )
            connection.execute(text("DELETE FROM roles WHERE id=:id"), {"id": role_id})
            connection.execute(
                text(
                    "DELETE FROM role_permissions WHERE permission_id=:perm AND scope_kind='global'"
                ),
                {"perm": permission_id},
            )
        _delete_test_account(postgres_engine, person_id=person_id, account_id=account_id)


def test_0065_downgrade_blocks_new_history_between_guard_and_ddl(postgres_engine: Engine) -> None:
    config = _alembic_config(postgres_engine)
    person_id, account_id = _insert_test_account(postgres_engine)
    org_id = uuid4()
    guard_checked, release_downgrade = Event(), Event()

    def pause_after_guard(connection, cursor, statement, parameters, context, executemany):
        if (
            statement
            != "SELECT EXISTS(SELECT 1 FROM revenue_share_organization_manager_assignments)"
        ):
            return
        guard_checked.set()
        if not release_downgrade.wait(timeout=10):
            raise TimeoutError("organization manager downgrade guard was not released")

    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO revenue_share_organizations(id,name,created_by_user_id) VALUES (:id,:name,:actor)"
            ),
            {"id": org_id, "name": f"并发降级机构-{org_id}", "actor": account_id},
        )
    pool = ThreadPoolExecutor(max_workers=1)
    event.listen(Engine, "after_cursor_execute", pause_after_guard)
    future = pool.submit(command.downgrade, config, "0064_cloud_auth_builtin_import")
    try:
        assert guard_checked.wait(timeout=10), "downgrade did not reach its guard"
        with postgres_engine.connect() as writer:
            transaction = writer.begin()
            try:
                writer.execute(text("SET LOCAL lock_timeout = '200ms'"))
                with pytest.raises(DBAPIError, match="lock timeout"):
                    writer.execute(
                        text(
                            "INSERT INTO revenue_share_organization_manager_assignments(id,organization_id,account_id,granted_by_user_id) VALUES (:id,:org,:actor,:actor)"
                        ),
                        {"id": uuid4(), "org": org_id, "actor": account_id},
                    )
            finally:
                transaction.rollback()
        release_downgrade.set()
        future.result(timeout=10)
        with postgres_engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "0064_cloud_auth_builtin_import"
            )
    finally:
        release_downgrade.set()
        try:
            future.result(timeout=10)
        finally:
            pool.shutdown(wait=True)
            event.remove(Engine, "after_cursor_execute", pause_after_guard)
            command.upgrade(config, CURRENT_HEAD)
            with postgres_engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM revenue_share_organizations WHERE id=:id"), {"id": org_id}
                )
            _delete_test_account(postgres_engine, person_id=person_id, account_id=account_id)


def test_0065_downgrade_serializes_with_grant_holding_referenced_locks(
    postgres_engine: Engine, monkeypatch
) -> None:
    from app.revenue_share.organization_manager_service import OrganizationManagerService

    config = _alembic_config(postgres_engine)
    actor_person_id, actor_id = _insert_test_account(postgres_engine)
    target_person_id, target_id = _insert_test_account(postgres_engine)
    org_id = uuid4()
    grant_locked, release_grant, downgrade_started = Event(), Event(), Event()
    downgrade_pid = []
    original_organization = OrganizationManagerService._organization

    def pause_grant(service, organization_id, *, lock=False):
        row = original_organization(service, organization_id, lock=lock)
        if lock:
            grant_locked.set()
            if not release_grant.wait(timeout=15):
                raise TimeoutError("grant referenced locks were not released")
        return row

    def observe_downgrade(connection, cursor, statement, parameters, context, executemany):
        if downgrade_pid or not statement.startswith(("LOCK TABLE", "ALTER TABLE people ")):
            return
        downgrade_pid.append(connection.connection.driver_connection.info.backend_pid)
        connection.execute(text("SET LOCAL statement_timeout = '10s'"))
        downgrade_started.set()

    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO user_roles(user_id,role_id) SELECT :actor,id FROM roles WHERE key='super_admin'"
            ),
            {"actor": actor_id},
        )
        connection.execute(
            text(
                "INSERT INTO revenue_share_organizations(id,name,created_by_user_id) VALUES (:id,:name,:actor)"
            ),
            {"id": org_id, "name": f"锁序降级机构-{org_id}", "actor": actor_id},
        )
    monkeypatch.setattr(OrganizationManagerService, "_organization", pause_grant)

    def perform_grant():
        with Session(postgres_engine) as db:
            db.execute(text("SET LOCAL statement_timeout = '10s'"))
            return OrganizationManagerService(db).grant_manager(
                actor_user_id=actor_id,
                organization_id=org_id,
                account_id=target_id,
                request_id="test-downgrade-real-grant",
            )

    pool = ThreadPoolExecutor(max_workers=2)
    event.listen(Engine, "before_cursor_execute", observe_downgrade)
    grant_future = pool.submit(perform_grant)
    downgrade_future = None
    try:
        assert grant_locked.wait(timeout=10), "grant did not acquire referenced row locks"
        downgrade_future = pool.submit(command.downgrade, config, "0064_cloud_auth_builtin_import")
        assert downgrade_started.wait(timeout=10), "downgrade did not attempt its lock"
        # Wait for an observed database lock wait, not an assumed scheduling delay.
        deadline = monotonic() + 10
        with postgres_engine.connect() as observer:
            while not observer.scalar(
                text(
                    "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=:pid AND NOT granted "
                    "AND relation IN ('people'::regclass, 'user_accounts'::regclass, "
                    "'revenue_share_organizations'::regclass))"
                ),
                {"pid": downgrade_pid[0]},
            ):
                assert monotonic() < deadline, "downgrade did not wait on referenced tables"
                release_grant.wait(timeout=0.01)
        release_grant.set()
        result = grant_future.result(timeout=15)
        assert result.account_id == target_id
        with pytest.raises(RuntimeError, match="organization manager history"):
            downgrade_future.result(timeout=15)
        with postgres_engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version")) == CURRENT_HEAD
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM revenue_share_organization_manager_assignments WHERE organization_id=:org AND revoked_at IS NULL"
                    ),
                    {"org": org_id},
                )
                == 1
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM audit_events WHERE actor_user_id=:actor AND action='referral_organization_manager.assign'"
                    ),
                    {"actor": actor_id},
                )
                == 1
            )
    finally:
        release_grant.set()
        for future in (grant_future, downgrade_future):
            if future is not None:
                with suppress(Exception):
                    future.result(timeout=15)
        pool.shutdown(wait=True)
        event.remove(Engine, "before_cursor_execute", observe_downgrade)
        command.upgrade(config, CURRENT_HEAD)
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM revenue_share_organization_manager_assignments WHERE organization_id=:org"
                ),
                {"org": org_id},
            )
            connection.execute(
                text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update")
            )
            connection.execute(
                text("DELETE FROM audit_events WHERE actor_user_id=:actor"), {"actor": actor_id}
            )
            connection.execute(
                text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update")
            )
            connection.execute(
                text("DELETE FROM revenue_share_organizations WHERE id=:org"), {"org": org_id}
            )
        _delete_test_account(postgres_engine, person_id=target_person_id, account_id=target_id)
        _delete_test_account(postgres_engine, person_id=actor_person_id, account_id=actor_id)


def test_0065_manager_schema_and_partial_indexes_match_models(postgres_engine: Engine) -> None:
    from app.core.models import Base

    inspector = inspect(postgres_engine)
    table_name = "revenue_share_organization_manager_assignments"
    table = Base.metadata.tables[table_name]
    assert {column.name for column in table.columns} == {
        column["name"] for column in inspector.get_columns(table_name)
    }
    assert {
        constraint.name
        for constraint in table.constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    } == {constraint["name"] for constraint in inspector.get_check_constraints(table_name)}
    assert {index.name for index in table.indexes} == {
        index["name"] for index in inspector.get_indexes(table_name)
    }
    assert all(
        fk["options"] == {"ondelete": "RESTRICT"} for fk in inspector.get_foreign_keys(table_name)
    )
    for name, expected in (
        (
            "student_referral_bindings",
            {
                "ix_student_referral_bindings_person_source",
                "ix_student_referral_bindings_org_source",
            },
        ),
        ("housing_stays", {"ix_housing_stays_person_latest_checkout"}),
        (table_name, {"uq_referral_org_manager_active", "ix_referral_org_manager_account_active"}),
    ):
        indexes = {index["name"]: index for index in inspector.get_indexes(name)}
        assert expected <= set(indexes)
        assert all("postgresql_where" in indexes[key]["dialect_options"] for key in expected)
    assert indexes["uq_referral_org_manager_active"]["unique"] is True


def test_0064_builtin_import_migrates_real_0063_rows_without_reencrypting(postgres_engine: Engine) -> None:
    from app.operations.cloud_auth_state import aad
    from app.security.encryption import EnvelopeCipher

    config = _alembic_config(postgres_engine)
    person_id, account_id = _insert_test_account(postgres_engine)
    session_id, candidate_id, operation_id = uuid4(), uuid4(), uuid4()
    cipher = EnvelopeCipher(MIGRATION_TEST_KEY_BYTES)
    token_envelope = cipher.encrypt("synthetic-old-token", field_name=aad("baidu", session_id, "token"))
    try:
        command.downgrade(config, "0063_cloud_authorization")
        with postgres_engine.begin() as connection:
            connection.execute(text("INSERT INTO cloud_auth_applications (provider,id,client_id,revision) VALUES ('baidu',:id,'old-app',7)"), {"id": uuid4()})
            connection.execute(text("""INSERT INTO cloud_auth_sessions
                (id,provider,owner_account_id,owner_login_session_id,state,revision,app_revision,target_revision,expires_at,token_ciphertext)
                VALUES (:id,'baidu',:owner,:login,'authorized',2,7,0,now()+interval '10 minutes',:ciphertext)"""),
                {"id": session_id, "owner": account_id, "login": uuid4(), "ciphertext": token_envelope})
            connection.execute(text("""INSERT INTO cloud_auth_candidates
                (id,session_id,provider,operation_id,connector_id,mount_path,config_revision,state,password_ciphertext)
                VALUES (:id,:session,'baidu',:operation,:connector,'/old-mount',3,'configured','legacy-password')"""),
                {"id": candidate_id, "session": session_id, "operation": operation_id, "connector": uuid4()})
            connection.execute(text("""INSERT INTO cloud_auth_operations
                (id,provider,session_id,owner_account_id,owner_login_session_id,idempotency_key,action,status,request_fingerprint,
                 session_revision,app_revision,target_revision,attempts,next_attempt_at,cleanup_status,created_at,payload_ciphertext)
                VALUES (:id,'baidu',:session,:owner,:login,:key,'prepare','succeeded',:fingerprint,2,7,0,1,now(),'none',now(),'legacy-payload')"""),
                {"id": operation_id, "session": session_id, "owner": account_id, "login": uuid4(), "key": uuid4(), "fingerprint": "a"*64})
        command.upgrade(config, CURRENT_HEAD)
        with postgres_engine.connect() as connection:
            for table in ("cloud_auth_sessions", "cloud_auth_candidates", "cloud_auth_operations"):
                row = connection.execute(text(f"SELECT * FROM {table}")).mappings().one()
                assert (row["mode"], row["auth_config_id"], row["auth_config_revision"], row["app_revision"], row["protocol_version"]) == ("own_app", "own-app:baidu", 7, 7, 1)
            session = connection.execute(text("SELECT * FROM cloud_auth_sessions")).mappings().one()
            assert session["application_provider"] == "baidu" and session["token_ciphertext"] == token_envelope
            assert cipher.decrypt(session["token_ciphertext"], field_name=aad("baidu", session_id, "token", mode=session["mode"], protocol_version=session["protocol_version"])) == "synthetic-old-token"
            assert session["verified_at"] is None and session["token_expires_at"] is None and session["expiry_source"] is None
            assert connection.scalar(text("SELECT password_ciphertext FROM cloud_auth_candidates")) == "legacy-password"
            operation = connection.execute(text("SELECT * FROM cloud_auth_operations")).mappings().one()
            assert operation["payload_ciphertext"] == "legacy-payload" and operation["refresh_phase"] == "not_started"
        command.downgrade(config, "0063_cloud_authorization")
        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT token_ciphertext FROM cloud_auth_sessions")) == token_envelope
            assert "mode" not in {c["name"] for c in inspect(connection).get_columns("cloud_auth_sessions")}
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(text("DELETE FROM cloud_auth_operations WHERE id=:id"), {"id": operation_id})
            connection.execute(text("DELETE FROM cloud_auth_candidates WHERE id=:id"), {"id": candidate_id})
            connection.execute(text("DELETE FROM cloud_auth_sessions WHERE id=:id"), {"id": session_id})
            connection.execute(text("DELETE FROM cloud_auth_applications WHERE provider='baidu'"))
        command.upgrade(config, CURRENT_HEAD)
        _delete_test_account(postgres_engine, person_id=person_id, account_id=account_id)


def test_0064_builtin_no_application_null_constraints_live_uniqueness_and_zero_write_downgrade(postgres_engine: Engine) -> None:
    from app.operations.cloud_auth_models import (
        CloudAuthCandidate,
        CloudAuthOperation,
        CloudAuthSession,
    )

    config = _alembic_config(postgres_engine)
    person_id, account_id = _insert_test_account(postgres_engine)
    session_id = uuid4()
    source = {"mode": "openlist_builtin_import", "auth_config_id": "openlist-builtin-import-v1:baidu", "auth_config_revision": 1, "app_revision": None, "protocol_version": 2}
    try:
        with postgres_engine.begin() as connection:
            values = dict(id=session_id, provider="baidu", owner_account_id=account_id,
                          owner_login_session_id=uuid4(), state="awaiting_import", application_provider=None,
                          expires_at="2026-10-10T00:00:00Z", **source)
            connection.execute(CloudAuthSession.__table__.insert().values(**values))
            for malformed in (
                {"app_revision": 1}, {"application_provider": "baidu"}, {"auth_config_revision": None},
                {"auth_config_id": None}, {"auth_config_revision": 0}, {"auth_config_revision": 2},
                {"auth_config_id": "openlist-builtin-import-v1:115"}, {"protocol_version": 1},
                {"mode": "own_app", "auth_config_id": "own-app:baidu", "app_revision": None},
            ):
                with pytest.raises(DBAPIError), connection.begin_nested():
                    connection.execute(CloudAuthSession.__table__.insert().values(**{**values, "id": uuid4(), "state": "cancelled", **malformed}))
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(CloudAuthSession.__table__.insert().values(**{**values, "id": uuid4(), "state": "verifying_import"}))
            # Both new operation actions and candidate sources work without an application.
            operation = dict(id=uuid4(), provider="baidu", session_id=session_id, owner_account_id=account_id,
                             owner_login_session_id=uuid4(), idempotency_key=uuid4(), action="verify_import",
                             request_fingerprint="a"*64, session_revision=1, created_at="2026-10-10T00:00:00Z",
                             next_attempt_at="2026-10-10T00:00:00Z", **source)
            connection.execute(CloudAuthOperation.__table__.insert().values(**operation))
            candidate = dict(id=uuid4(), session_id=session_id, provider="baidu", operation_id=uuid4(), connector_id=uuid4(), mount_path="/builtin", **source)
            connection.execute(CloudAuthCandidate.__table__.insert().values(**candidate))
            for model, row in ((CloudAuthOperation, operation), (CloudAuthCandidate, candidate)):
                for malformed in ({"app_revision": 2}, {"auth_config_revision": None}, {"auth_config_id": None}, {"mode": "own_app", "auth_config_id": "own-app:baidu", "app_revision": None}):
                    # UPDATE isolates the source constraint from unrelated unique keys.
                    with pytest.raises(DBAPIError) as error, connection.begin_nested():
                        connection.execute(model.__table__.update().where(model.__table__.c.id == row["id"]).values(**malformed))
                    assert error.value.orig.diag.constraint_name in ("ck_cloud_auth_operation_source", "ck_cloud_auth_candidate_source", None)
            before = {table: list(connection.execute(text(f"SELECT * FROM {table}")).all()) for table in ("cloud_auth_sessions", "cloud_auth_operations", "cloud_auth_candidates")}
        with pytest.raises(RuntimeError, match="builtin import"):
            command.downgrade(config, "0063_cloud_authorization")
        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == CURRENT_HEAD
            for table, rows in before.items():
                assert list(connection.execute(text(f"SELECT * FROM {table}")).all()) == rows
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(text("DELETE FROM cloud_auth_operations WHERE session_id=:id"), {"id": session_id})
            connection.execute(text("DELETE FROM cloud_auth_candidates WHERE session_id=:id"), {"id": session_id})
            connection.execute(text("DELETE FROM cloud_auth_sessions WHERE id=:id"), {"id": session_id})
        _delete_test_account(postgres_engine, person_id=person_id, account_id=account_id)


def test_0063_cloud_authorization_upgrade_and_empty_downgrade_preserve_targets_and_jobs(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    job_id = uuid4()
    try:
        command.downgrade(config, "0062_cloud_backups")
        with postgres_engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO cloud_backup_targets
                    (provider, enabled, remote_path, username_ciphertext, password_ciphertext,
                     revision, connection_status, last_success_at, last_backup_id)
                VALUES ('baidu', true, '/旧备份', 'encrypted-user', 'encrypted-password',
                        7, 'ok', '2026-10-05T03:00:00Z', 'retained-backup')
            """))
            connection.execute(text("""
                INSERT INTO cloud_backup_jobs
                    (id, provider, kind, status, revision, backup_id, created_at)
                VALUES (:id, 'baidu', 'copy', 'succeeded', 7, 'retained-backup', now())
            """), {"id": job_id})
        with postgres_engine.connect() as connection:
            old_target = tuple(connection.execute(text("SELECT * FROM cloud_backup_targets")).one())
            old_job = tuple(connection.execute(text("SELECT * FROM cloud_backup_jobs")).one())
        command.upgrade(config, CURRENT_HEAD)
        with postgres_engine.connect() as connection:
            target = connection.execute(text("SELECT * FROM cloud_backup_targets")).mappings().one()
            assert target["enabled"] is True
            assert target["revision"] == 7
            assert target["last_backup_id"] == "retained-backup"
            assert target["authorization_status"] == "manual"
            assert target["managed_candidate_id"] is None
            assert target["switch_operation_id"] is None
            assert tuple(connection.execute(text("SELECT * FROM cloud_backup_jobs")).one()) == old_job
            for table in CLOUD_AUTH_TABLES:
                assert connection.scalar(text(f'SELECT count(*) FROM "{table}"')) == 0
        command.downgrade(config, "0062_cloud_backups")
        with postgres_engine.connect() as connection:
            assert not CLOUD_AUTH_TABLES & set(inspect(connection).get_table_names())
            assert tuple(connection.execute(text("SELECT * FROM cloud_backup_targets")).one()) == old_target
            assert tuple(connection.execute(text("SELECT * FROM cloud_backup_jobs")).one()) == old_job
    finally:
        command.upgrade(config, CURRENT_HEAD)
        with postgres_engine.begin() as connection:
            connection.execute(text("DELETE FROM cloud_backup_jobs WHERE id = :id"), {"id": job_id})
            connection.execute(text("DELETE FROM cloud_backup_targets WHERE provider = 'baidu'"))


def test_0063_cloud_authorization_constraints_match_model_metadata(postgres_engine: Engine) -> None:
    from app.core.models import Base
    from app.operations import cloud_auth_models  # noqa: F401

    with postgres_engine.connect() as connection:
        inspector = inspect(connection)
        for name in CLOUD_AUTH_TABLES | {"cloud_backup_targets"}:
            table = Base.metadata.tables[name]
            assert {c.name for c in table.columns} == {c["name"] for c in inspector.get_columns(name)}
            assert {i.name for i in table.indexes} == {i["name"] for i in inspector.get_indexes(name)}
            assert {c.name for c in table.constraints if c.__class__.__name__ == "CheckConstraint"} == {c["name"] for c in inspector.get_check_constraints(name)}
        sessions = {i["name"]: i for i in inspector.get_indexes("cloud_auth_sessions")}
        assert sessions["uq_cloud_auth_session_live_provider"]["unique"]
        operations = {i["name"]: i for i in inspector.get_indexes("cloud_auth_operations")}
        assert operations["uq_cloud_auth_operation_owner_key"]["unique"]
        assert operations["ix_cloud_auth_operation_claim"]["column_names"] == ["status", "next_attempt_at", "lease_expires_at"]


def test_0063_live_session_owner_key_and_lease_constraints_are_enforced(postgres_engine: Engine) -> None:
    from datetime import UTC, datetime, timedelta

    from app.operations.cloud_auth_models import (
        CloudAuthApplication,
        CloudAuthOperation,
        CloudAuthSession,
    )

    person_id, account_id = _insert_test_account(postgres_engine)
    login_id, session_id, key = uuid4(), uuid4(), uuid4()
    now = datetime(2026, 10, 6, tzinfo=UTC)
    try:
        with postgres_engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(CloudAuthApplication.__table__.insert().values(provider="baidu", client_id="own-app"))
                session = {"id": session_id, "provider": "baidu", "owner_account_id": account_id, "owner_login_session_id": login_id, "app_revision": 1, "expires_at": now + timedelta(minutes=10)}
                connection.execute(CloudAuthSession.__table__.insert().values(**session))
                old_style = connection.execute(text("SELECT mode, application_provider, auth_config_id, auth_config_revision, protocol_version FROM cloud_auth_sessions WHERE id=:id"), {"id": session_id}).one()
                assert tuple(old_style) == ("own_app", "baidu", "own-app:baidu", 1, 1)
                with pytest.raises(DBAPIError), connection.begin_nested():
                    connection.execute(CloudAuthSession.__table__.insert().values(**{**session, "id": uuid4()}))
                # A terminal session does not consume the provider's one live slot.
                connection.execute(CloudAuthSession.__table__.insert().values(**{**session, "id": uuid4(), "state": "cancelled"}))
                operation = {"provider": "baidu", "session_id": session_id, "owner_account_id": account_id, "owner_login_session_id": login_id, "idempotency_key": key, "action": "create_qr", "request_fingerprint": "a" * 64, "session_revision": 1, "app_revision": 1, "created_at": now, "next_attempt_at": now}
                connection.execute(CloudAuthOperation.__table__.insert().values(**operation))
                with pytest.raises(DBAPIError), connection.begin_nested():
                    connection.execute(CloudAuthOperation.__table__.insert().values(**{**operation, "action": "prepare", "request_fingerprint": "b" * 64}))
                with pytest.raises(DBAPIError), connection.begin_nested():
                    connection.execute(CloudAuthOperation.__table__.insert().values(**{**operation, "idempotency_key": uuid4(), "lease_id": uuid4()}))
                with pytest.raises(DBAPIError), connection.begin_nested():
                    connection.execute(CloudAuthOperation.__table__.insert().values(**{**operation, "idempotency_key": uuid4(), "lease_expires_at": now}))
            finally:
                transaction.rollback()
    finally:
        _delete_test_account(postgres_engine, person_id=person_id, account_id=account_id)


def test_0063_downgrade_refuses_to_discard_retained_authorization(postgres_engine: Engine) -> None:
    from app.operations.cloud_auth_models import CloudAuthApplication

    config = _alembic_config(postgres_engine)
    try:
        with postgres_engine.begin() as connection:
            connection.execute(CloudAuthApplication.__table__.insert().values(provider="baidu", client_id="retained-app", secret_ciphertext="protected-secret"))
        with pytest.raises(RuntimeError, match="requires empty new tables"):
            command.downgrade(config, "0062_cloud_backups")
        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == CURRENT_HEAD
            assert connection.scalar(text("SELECT secret_ciphertext FROM cloud_auth_applications WHERE provider = 'baidu'")) == "protected-secret"
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(text("DELETE FROM cloud_auth_applications WHERE provider = 'baidu'"))


def _record_registration_schema_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> list[str]:
    from collections.abc import Callable
    from os import PathLike
    from types import ModuleType
    from typing import cast

    from alembic import util

    calls: list[str] = []
    original_load_python_file = util.load_python_file

    def recording_load_python_file(
        dir_: str | PathLike[str],
        filename: str | PathLike[str],
    ) -> ModuleType:
        module = original_load_python_file(dir_, filename)
        if Path(filename).name != "0017_self_service_registration.py":
            return module
        for function_name in ("_create_schema", "_drop_schema"):
            original_function = cast(
                Callable[..., object],
                getattr(module, function_name),
            )

            def recording_schema_call(
                *args: object,
                _function_name: str = function_name,
                _original_function: Callable[..., object] = original_function,
                **kwargs: object,
            ) -> object:
                calls.append(_function_name)
                return _original_function(*args, **kwargs)

            setattr(module, function_name, recording_schema_call)
        return module

    monkeypatch.setattr(util, "load_python_file", recording_load_python_file)
    return calls


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
    expected_revision: str = CURRENT_HEAD,
) -> None:
    assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (expected_revision)
    assert _portal_index_names(connection) == PORTAL_INDEX_NAMES


def _assert_registration_ddl_is_intact(connection: Connection) -> None:
    assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (CURRENT_HEAD)
    inspector = inspect(connection)
    assert REGISTRATION_TABLES <= set(inspector.get_table_names())
    assert "uq_account_login_identifiers_primary_account" not in {
        index["name"] for index in inspector.get_indexes("account_login_identifiers")
    }
    assert (
        connection.scalar(
            text(
                "SELECT count(*) FROM pg_trigger WHERE tgname IN "
                "('trg_account_login_identifiers_primary_account', "
                "'trg_user_accounts_primary_identifier')"
            )
        )
        == 0
    )
    assert "self_service_primary_purpose" in {
        column["name"] for column in inspector.get_columns("user_accounts")
    }
    assert {"email_ciphertext", "email_digest"} <= {
        column["name"] for column in inspector.get_columns("people")
    }
    assert "ck_people_legacy_email_cleared" in {
        constraint["name"] for constraint in inspector.get_check_constraints("people")
    }
    application_columns = {
        column["name"]: column for column in inspector.get_columns("registration_applications")
    }
    assert {
        "review_decision",
        "reviewed_by_user_id",
        "reviewed_candidate_person_id",
        "review_reason",
        "reviewed_at",
    } <= set(application_columns)
    assert all(
        application_columns[column_name]["nullable"] is True
        for column_name in (
            "review_decision",
            "reviewed_by_user_id",
            "reviewed_candidate_person_id",
            "review_reason",
            "reviewed_at",
        )
    )
    application_checks = {
        constraint["name"]: constraint["sqltext"]
        for constraint in inspector.get_check_constraints("registration_applications")
    }
    assert {
        "ck_registration_applications_review_reason",
        "ck_registration_applications_review_state",
        "ck_registration_applications_review_candidate",
    } <= set(application_checks)
    assert "pending_guardian" in application_checks["ck_registration_applications_review_state"]
    reviewer_foreign_keys = [
        foreign_key
        for foreign_key in inspector.get_foreign_keys("registration_applications")
        if foreign_key["constrained_columns"] == ["reviewed_by_user_id"]
    ]
    assert len(reviewer_foreign_keys) == 1
    assert reviewer_foreign_keys[0]["referred_table"] == "user_accounts"
    assert reviewer_foreign_keys[0]["referred_columns"] == ["id"]
    assert reviewer_foreign_keys[0]["options"] == {"ondelete": "RESTRICT"}
    candidate_foreign_keys = [
        foreign_key
        for foreign_key in inspector.get_foreign_keys("registration_applications")
        if foreign_key["constrained_columns"] == ["reviewed_candidate_person_id"]
    ]
    assert len(candidate_foreign_keys) == 1
    assert candidate_foreign_keys[0]["referred_table"] == "people"
    assert candidate_foreign_keys[0]["referred_columns"] == ["id"]
    assert candidate_foreign_keys[0]["options"] == {"ondelete": "RESTRICT"}
    invitation_columns = {
        column["name"]: column
        for column in inspector.get_columns("guardian_registration_invitations")
    }
    assert invitation_columns["handoff_token_digest"]["nullable"] is True
    invitation_checks = {
        constraint["name"]
        for constraint in inspector.get_check_constraints("guardian_registration_invitations")
    }
    assert "ck_guardian_registration_invitations_handoff_token" in invitation_checks
    invitation_indexes = {
        index["name"] for index in inspector.get_indexes("guardian_registration_invitations")
    }
    assert "uq_guardian_registration_invitations_handoff_token" in invitation_indexes


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
    assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (CURRENT_HEAD)
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
        if connection.scalar(text("SELECT to_regclass('guardian_profiles')")):
            connection.execute(
                text(
                    "INSERT INTO guardian_profiles (person_id, legacy_backfilled) "
                    "VALUES (:guardian_id, true), (:student_id, true)"
                ),
                {"guardian_id": guardian_id, "student_id": student_id},
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
        if connection.scalar(text("SELECT to_regclass('guardian_profiles')")):
            connection.execute(
                text(
                    "DELETE FROM guardian_profiles WHERE person_id IN (:guardian_id, :student_id)"
                ),
                {"guardian_id": guardian_id, "student_id": student_id},
            )
        connection.execute(
            text("DELETE FROM people WHERE id IN (:guardian_id, :student_id)"),
            {"guardian_id": guardian_id, "student_id": student_id},
        )


def _insert_test_account(
    postgres_engine: Engine,
    *,
    legacy_phone: str | None = None,
) -> tuple[UUID, UUID]:
    from app.identity.normalization import normalize_phone
    from app.security.encryption import EnvelopeCipher, LookupDigest

    person_id = uuid4()
    account_id = uuid4()
    cipher = EnvelopeCipher(MIGRATION_TEST_KEY_BYTES)
    lookup = LookupDigest(MIGRATION_TEST_KEY_BYTES)
    phone_plaintext = legacy_phone or f"139{account_id.int % 100_000_000:08d}"
    normalized_phone = normalize_phone(phone_plaintext)
    login_phone_digest = lookup.digest(normalized_phone)
    normalized_email = f"operator-{account_id}@example.com"
    with postgres_engine.begin() as connection:
        registration_schema_exists = bool(
            connection.scalar(text("SELECT to_regclass('account_login_identifiers')"))
        )
        connection.execute(
            text(
                """
                INSERT INTO people (
                    id,
                    full_name,
                    identity_ciphertext,
                    identity_digest,
                    phone_ciphertext,
                    phone_digest,
                    sex,
                    date_of_birth
                ) VALUES (
                    :person_id,
                    '迁移测试操作人',
                    'operator-ciphertext',
                    :identity_digest,
                    :phone_ciphertext,
                    :phone_digest,
                    'female',
                    DATE '1988-01-02'
                )
                """
            ),
            {
                "person_id": person_id,
                "identity_digest": f"operator-{person_id}",
                "phone_ciphertext": cipher.encrypt(phone_plaintext, field_name="phone"),
                "phone_digest": login_phone_digest,
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
                "login_phone_digest": login_phone_digest,
                "email": normalized_email,
            },
        )
        if registration_schema_exists:
            connection.execute(
                text(
                    "UPDATE people SET email_ciphertext = :ciphertext, "
                    "email_digest = :digest WHERE id = :person_id"
                ),
                {
                    "person_id": person_id,
                    "ciphertext": cipher.encrypt(normalized_email, field_name="email"),
                    "digest": lookup.digest_domain("person-email", normalized_email),
                },
            )
            connection.execute(
                text("UPDATE user_accounts SET email_normalized = NULL WHERE id = :account_id"),
                {"account_id": account_id},
            )
            connection.execute(
                text(
                    "INSERT INTO account_login_identifiers "
                    "(id, account_id, kind, identifier_digest, verified_at, "
                    "verification_source, is_primary) "
                    "SELECT :id, id, 'phone', :identifier_digest, created_at, "
                    "'legacy_phone_login', true FROM user_accounts WHERE id = :account_id"
                ),
                {
                    "id": uuid4(),
                    "account_id": account_id,
                    "identifier_digest": lookup.digest_domain(
                        "account-login-phone",
                        normalized_phone,
                    ),
                },
            )
            connection.execute(
                text(
                    "INSERT INTO guardian_profiles (person_id, legacy_backfilled) "
                    "VALUES (:person_id, true)"
                ),
                {"person_id": person_id},
            )
            connection.execute(
                text("INSERT INTO user_roles (user_id, role_id) VALUES (:account_id, :role_id)"),
                {"account_id": account_id, "role_id": REGISTERED_USER_ROLE_ID},
            )
    return person_id, account_id


def _delete_test_account(
    postgres_engine: Engine,
    *,
    person_id: UUID,
    account_id: UUID,
) -> None:
    with postgres_engine.begin() as connection:
        if connection.scalar(text("SELECT to_regclass('account_login_identifiers')")):
            connection.execute(
                text("DELETE FROM account_login_identifiers WHERE account_id = :account_id"),
                {"account_id": account_id},
            )
        connection.execute(
            text("DELETE FROM user_permission_overrides WHERE user_id = :account_id"),
            {"account_id": account_id},
        )
        connection.execute(
            text("DELETE FROM user_roles WHERE user_id = :account_id"),
            {"account_id": account_id},
        )
        connection.execute(
            text("DELETE FROM user_accounts WHERE id = :account_id"),
            {"account_id": account_id},
        )
        if connection.scalar(text("SELECT to_regclass('guardian_profiles')")):
            connection.execute(
                text("DELETE FROM guardian_profiles WHERE person_id = :person_id"),
                {"person_id": person_id},
            )
        connection.execute(
            text("DELETE FROM people WHERE id = :person_id"),
            {"person_id": person_id},
        )


def _insert_system_evaluation_task(
    postgres_engine: Engine,
    account_id: UUID,
) -> tuple[UUID, UUID]:
    upload_session_id = uuid4()
    task_id = uuid4()
    with postgres_engine.begin() as connection:
        profile_id, profile_version = connection.execute(
            text(
                "SELECT id, version FROM project_analysis_profiles "
                "WHERE project_id = :project_id ORDER BY version DESC LIMIT 1"
            ),
            {"project_id": INITIAL_PROJECT_IDS[0]},
        ).one()
        connection.execute(
            text(
                "INSERT INTO system_evaluation_upload_sessions "
                "(id, created_by_user_id, project_id, status, expires_at) "
                "VALUES (:id, :account_id, :project_id, 'open', "
                "CURRENT_TIMESTAMP + INTERVAL '1 hour')"
            ),
            {
                "id": upload_session_id,
                "account_id": account_id,
                "project_id": INITIAL_PROJECT_IDS[0],
            },
        )
        connection.execute(
            text(
                "INSERT INTO system_evaluation_tasks "
                "(id, public_number, upload_session_id, created_by_user_id, "
                "project_id, analysis_profile_id, analysis_profile_version, "
                "analysis_config_snapshot, upload_window_start, upload_window_end, "
                "status) VALUES (:id, :public_number, :session_id, :account_id, "
                ":project_id, :profile_id, :profile_version, '{}'::jsonb, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'pending_match')"
            ),
            {
                "id": task_id,
                "public_number": f"SE-{task_id.hex[:20]}",
                "session_id": upload_session_id,
                "account_id": account_id,
                "project_id": INITIAL_PROJECT_IDS[0],
                "profile_id": profile_id,
                "profile_version": profile_version,
            },
        )
    return upload_session_id, task_id


def _delete_system_evaluation_task(
    postgres_engine: Engine,
    *,
    upload_session_id: UUID,
    task_id: UUID,
    result_id: UUID | None = None,
) -> None:
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE system_evaluation_result_versions "
                "DISABLE TRIGGER system_evaluation_results_immutable"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE system_evaluation_tasks "
                "DISABLE TRIGGER system_evaluation_tasks_no_delete"
            )
        )
        try:
            if result_id is not None:
                connection.execute(
                    text("DELETE FROM system_evaluation_result_versions WHERE id = :result_id"),
                    {"result_id": result_id},
                )
            connection.execute(
                text("DELETE FROM system_evaluation_manual_drafts WHERE task_id = :task_id"),
                {"task_id": task_id},
            )
            connection.execute(
                text("DELETE FROM system_evaluation_tasks WHERE id = :task_id"),
                {"task_id": task_id},
            )
            connection.execute(
                text("DELETE FROM system_evaluation_upload_sessions WHERE id = :upload_session_id"),
                {"upload_session_id": upload_session_id},
            )
        finally:
            connection.execute(
                text(
                    "ALTER TABLE system_evaluation_tasks "
                    "ENABLE TRIGGER system_evaluation_tasks_no_delete"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE system_evaluation_result_versions "
                    "ENABLE TRIGGER system_evaluation_results_immutable"
                )
            )


def _insert_teaching_report_root(
    postgres_engine: Engine,
    *,
    author_person_id: UUID,
    author_account_id: UUID,
) -> dict[str, UUID]:
    ids = {
        "student_id": uuid4(),
        "cohort_id": uuid4(),
        "class_id": uuid4(),
        "admission_case_id": uuid4(),
        "enrollment_id": uuid4(),
        "coach_assignment_id": uuid4(),
        "report_id": uuid4(),
    }
    with postgres_engine.begin() as connection:
        if connection.scalar(text("SELECT to_regclass('guardian_profiles')")):
            connection.execute(
                text("DELETE FROM guardian_profiles WHERE person_id = :person_id"),
                {"person_id": author_person_id},
            )
        connection.execute(
            text("INSERT INTO staff_profiles (person_id, staff_type) VALUES (:person_id, 'coach')"),
            {"person_id": author_person_id},
        )
        connection.execute(
            text(
                "INSERT INTO people (id, full_name, identity_ciphertext, "
                "identity_digest, sex, date_of_birth) VALUES "
                "(:id, '评语迁移测试学员', 'student-ciphertext', :digest, "
                "'male', DATE '2012-01-02')"
            ),
            {
                "id": ids["student_id"],
                "digest": f"teaching-report-student-{ids['student_id']}",
            },
        )
        connection.execute(
            text(
                "INSERT INTO student_profiles (person_id, student_number, status) "
                "VALUES (:id, :number, 'active')"
            ),
            {
                "id": ids["student_id"],
                "number": f"report-{ids['student_id'].hex[:20]}",
            },
        )
        connection.execute(
            text(
                "INSERT INTO cohorts (id, name, starts_at, ends_at, "
                "default_check_in_at, default_check_out_at, status, "
                "created_by_user_id) VALUES (:id, '评语迁移测试营期', "
                "TIMESTAMPTZ '2026-08-01 10:00:00+08', "
                "TIMESTAMPTZ '2026-08-21 18:00:00+08', "
                "TIMESTAMPTZ '2026-08-01 08:00:00+08', "
                "TIMESTAMPTZ '2026-08-21 20:00:00+08', 'active', :actor_id)"
            ),
            {"id": ids["cohort_id"], "actor_id": author_account_id},
        )
        connection.execute(
            text(
                "INSERT INTO teaching_classes "
                "(id, cohort_id, project_id, name, class_type, capacity, "
                "default_tuition_cents, created_by_user_id) VALUES "
                "(:id, :cohort_id, :project_id, '评语迁移测试班', "
                "'standard', 20, 0, :actor_id)"
            ),
            {
                "id": ids["class_id"],
                "cohort_id": ids["cohort_id"],
                "project_id": INITIAL_PROJECT_IDS[0],
                "actor_id": author_account_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO admission_cases "
                "(id, student_person_id, status, created_by_user_id) "
                "VALUES (:id, :student_id, 'ready', :actor_id)"
            ),
            {
                "id": ids["admission_case_id"],
                "student_id": ids["student_id"],
                "actor_id": author_account_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO enrollments "
                "(id, admission_case_id, student_person_id, cohort_id, class_id, "
                "agreed_tuition_cents, status, confirmed_at, created_by_user_id) "
                "VALUES (:id, :case_id, :student_id, :cohort_id, :class_id, 0, "
                "'active', TIMESTAMPTZ '2026-07-31 12:00:00+08', :actor_id)"
            ),
            {
                "id": ids["enrollment_id"],
                "case_id": ids["admission_case_id"],
                "student_id": ids["student_id"],
                "cohort_id": ids["cohort_id"],
                "class_id": ids["class_id"],
                "actor_id": author_account_id,
            },
        )
        connection.execute(
            text(
                "UPDATE admission_cases SET status = 'enrolled', "
                "current_enrollment_id = :enrollment_id WHERE id = :case_id"
            ),
            {
                "enrollment_id": ids["enrollment_id"],
                "case_id": ids["admission_case_id"],
            },
        )
        connection.execute(
            text(
                "INSERT INTO coach_assignments "
                "(id, class_id, coach_person_id, assignment_type, valid_from, "
                "valid_until, created_by_user_id) VALUES "
                "(:id, :class_id, :person_id, 'primary', "
                "TIMESTAMPTZ '2026-08-01 00:00:00+08', "
                "TIMESTAMPTZ '2026-08-22 00:00:00+08', :actor_id)"
            ),
            {
                "id": ids["coach_assignment_id"],
                "class_id": ids["class_id"],
                "person_id": author_person_id,
                "actor_id": author_account_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO student_reports "
                "(id, enrollment_id, student_person_id, cohort_id, project_id, "
                "class_id, author_person_id, author_user_id, author_source, "
                "coach_assignment_id, role_key_snapshot, role_name_snapshot, "
                "role_category_snapshot, cohort_name_snapshot, "
                "project_name_snapshot, class_name_snapshot, report_kind, "
                "period_sequence, period_start_date, period_end_date, "
                "submission_opens_at, initial_submission_deadline, status) "
                "VALUES (:id, :enrollment_id, :student_id, :cohort_id, :project_id, "
                ":class_id, :person_id, :actor_id, 'coach_assignment', "
                ":coach_assignment_id, 'primary_coach', '主教练', 'teaching', "
                "'评语迁移测试营期', '英雄联盟', '评语迁移测试班', 'daily', 1, "
                "DATE '2026-08-01', DATE '2026-08-01', "
                "TIMESTAMPTZ '2026-08-01 00:00:00+08', "
                "TIMESTAMPTZ '2026-08-24 23:59:59.999999+08', 'draft')"
            ),
            {
                "id": ids["report_id"],
                "enrollment_id": ids["enrollment_id"],
                "student_id": ids["student_id"],
                "cohort_id": ids["cohort_id"],
                "project_id": INITIAL_PROJECT_IDS[0],
                "class_id": ids["class_id"],
                "person_id": author_person_id,
                "actor_id": author_account_id,
                "coach_assignment_id": ids["coach_assignment_id"],
            },
        )
    return ids


def _delete_teaching_report_root(
    postgres_engine: Engine,
    *,
    ids: dict[str, UUID],
    author_person_id: UUID,
) -> None:
    with postgres_engine.begin() as connection:
        connection.execute(
            text("DELETE FROM student_reports WHERE id = :id"),
            {"id": ids["report_id"]},
        )
        connection.execute(
            text("DELETE FROM coach_assignments WHERE id = :id"),
            {"id": ids["coach_assignment_id"]},
        )
        connection.execute(
            text(
                "UPDATE admission_cases SET status = 'ready', "
                "current_enrollment_id = NULL WHERE id = :id"
            ),
            {"id": ids["admission_case_id"]},
        )
        connection.execute(
            text("DELETE FROM enrollments WHERE id = :id"),
            {"id": ids["enrollment_id"]},
        )
        connection.execute(
            text("DELETE FROM admission_cases WHERE id = :id"),
            {"id": ids["admission_case_id"]},
        )
        connection.execute(
            text("DELETE FROM teaching_classes WHERE id = :id"),
            {"id": ids["class_id"]},
        )
        connection.execute(
            text("DELETE FROM cohorts WHERE id = :id"),
            {"id": ids["cohort_id"]},
        )
        connection.execute(
            text("DELETE FROM student_profiles WHERE person_id = :id"),
            {"id": ids["student_id"]},
        )
        connection.execute(
            text("DELETE FROM people WHERE id = :id"),
            {"id": ids["student_id"]},
        )
        connection.execute(
            text("DELETE FROM staff_profiles WHERE person_id = :id"),
            {"id": author_person_id},
        )


def test_audit_model_maps_the_metadata_database_column() -> None:
    from app.audit.models import AuditEvent

    assert "metadata" in AuditEvent.__table__.columns


def test_alembic_head_creates_platform_tables(postgres_engine: Engine) -> None:
    tables = set(inspect(postgres_engine).get_table_names())

    assert EXPECTED_TABLES <= tables
    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == CURRENT_HEAD
        _assert_registration_ddl_is_intact(connection)
        assert (
            connection.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_class "
                    "WHERE relkind = 'S' AND relname = "
                    "'registration_student_number_seq')"
                )
            )
            is True
        )
    account_column_metadata = {
        column["name"]: column for column in inspect(postgres_engine).get_columns("user_accounts")
    }
    account_columns = set(account_column_metadata)
    assert "food_preferences_onboarding_completed_at" in account_columns
    assert "self_service_primary_purpose" in account_columns
    assert account_column_metadata["login_phone_digest"]["nullable"] is True
    assert account_column_metadata["email_normalized"]["nullable"] is True
    assert CAMP_SURVEY_TABLES <= tables
    assert REGISTRATION_TABLES <= tables
    people_columns = {
        column["name"]: column for column in inspect(postgres_engine).get_columns("people")
    }
    assert people_columns["email"]["nullable"] is True
    assert people_columns["email_ciphertext"]["nullable"] is True
    assert people_columns["email_digest"]["nullable"] is True
    assert people_columns["nickname"]["nullable"] is True
    assert people_columns["nickname"]["type"].length == 40
    with postgres_engine.connect() as connection:
        assert connection.execute(
            text(
                "SELECT key, id, sensitive FROM permissions "
                "WHERE key = ANY(CAST(:keys AS text[])) ORDER BY key"
            ),
            {"keys": list(CAMP_SURVEY_PERMISSION_IDS)},
        ).all() == [
            (
                "camp_surveys.identity.read",
                CAMP_SURVEY_PERMISSION_IDS["camp_surveys.identity.read"],
                True,
            ),
            ("camp_surveys.print", CAMP_SURVEY_PERMISSION_IDS["camp_surveys.print"], True),
        ]
        assert connection.execute(
            text(
                "SELECT key, id, sensitive FROM permissions "
                "WHERE key = ANY(CAST(:keys AS text[])) ORDER BY key"
            ),
            {"keys": list(REGISTRATION_PERMISSION_IDS)},
        ).all() == [
            (key, REGISTRATION_PERMISSION_IDS[key], True)
            for key in sorted(REGISTRATION_PERMISSION_IDS)
        ]
        assert connection.execute(
            text("SELECT id, key, name FROM roles WHERE key = 'registered_user'")
        ).one() == (REGISTERED_USER_ROLE_ID, "registered_user", "注册用户")
        assert {
            grant
            for grant in _role_permission_grants(connection)
            if (grant[0] == "registered_user" or grant[1] in REGISTRATION_PERMISSION_IDS)
        } == REGISTRATION_GRANTS | {("registered_user", "referrals.read_invited_housing", "self")}


def test_revenue_share_schema_and_finance_role_are_seeded(
    postgres_engine: Engine,
) -> None:
    inspector = inspect(postgres_engine)
    registration_columns = {
        column["name"] for column in inspector.get_columns("registration_applications")
    }
    with postgres_engine.connect() as connection:
        finance_role = connection.execute(
            text("SELECT id, name FROM roles WHERE key = 'finance'")
        ).one()
        permissions = {
            key: (permission_id, sensitive)
            for key, permission_id, sensitive in connection.execute(
                text(
                    "SELECT key, id, sensitive FROM permissions "
                    "WHERE key = ANY(CAST(:keys AS text[]))"
                ),
                {"keys": list(REVENUE_SHARE_PERMISSION_IDS)},
            )
        }
        finance_grants = {
            (permission_key, scope_kind)
            for role_key, permission_key, scope_kind in _role_permission_grants(connection)
            if role_key == "finance"
        }
        self_revenue_share_roles = {
            role_key
            for role_key, permission_key, scope_kind in _role_permission_grants(connection)
            if permission_key == "revenue_share.read_self" and scope_kind == "self"
        }

    assert REVENUE_SHARE_TABLES <= set(inspector.get_table_names())
    assert "referral_code_id" in registration_columns
    assert finance_role == (FINANCE_ROLE_ID, "财务")
    assert permissions == {
        key: (
            permission_id,
            key != "revenue_share.read_self",
        )
        for key, permission_id in REVENUE_SHARE_PERMISSION_IDS.items()
    }
    assert finance_grants == {
        ("referrals.read_invited_housing", "self"),
        ("assets.read_self", "self"),
        ("payments.read", "global"),
        ("revenue_share.read_self", "self"),
        ("revenue_share.finance", "global"),
        ("revenue_share.export", "global"),
        ("procurement.read", "global"),
        ("procurement.settle", "global"),
        ("procurement.export", "global"),
        ("procurement.invoices.manage", "global"),
        ("travel_reports.read", "global"),
        ("travel_reports.import", "global"),
        ("travel_reports.export", "global"),
    }
    assert self_revenue_share_roles == {
        "admin",
        "coach",
        "customer_service",
        "life_teacher",
        "staff",
        "guardian",
        "super_admin",
        "administration",
        "finance",
    }


def test_procurement_schema_and_role_permissions_are_seeded(
    postgres_engine: Engine,
) -> None:
    inspector = inspect(postgres_engine)
    purchase_columns = {
        column["name"]: column for column in inspector.get_columns("daily_purchase_records")
    }
    purchase_checks = {
        constraint["name"]
        for constraint in inspector.get_check_constraints("daily_purchase_records")
    }
    purchase_indexes = {index["name"] for index in inspector.get_indexes("daily_purchase_records")}
    with postgres_engine.connect() as connection:
        permissions = {
            key: (permission_id, sensitive)
            for key, permission_id, sensitive in connection.execute(
                text(
                    "SELECT key, id, sensitive FROM permissions "
                    "WHERE key = ANY(CAST(:keys AS text[]))"
                ),
                {"keys": list(PROCUREMENT_PERMISSION_IDS)},
            )
        }
        grants = {
            role_key: {
                permission_key
                for granted_role, permission_key, scope_kind in _role_permission_grants(connection)
                if granted_role == role_key
                and permission_key in PROCUREMENT_PERMISSION_IDS
                and scope_kind == "global"
            }
            for role_key in ("super_admin", "admin", "administration", "finance")
        }

    assert PROCUREMENT_TABLES <= set(inspector.get_table_names())
    assert purchase_columns["purchase_channel"]["nullable"] is False
    assert purchase_columns["purchase_channel"]["type"].length == 16
    assert "offline" in str(purchase_columns["purchase_channel"]["default"])
    assert purchase_columns["product_url"]["nullable"] is True
    assert purchase_columns["product_url"]["type"].length == 2048
    assert purchase_columns["is_reimbursed"]["nullable"] is False
    assert "false" in str(purchase_columns["is_reimbursed"]["default"])
    assert purchase_columns["uses_reserve_fund"]["nullable"] is False
    assert "false" in str(purchase_columns["uses_reserve_fund"]["default"])
    assert "ix_daily_purchase_records_reimbursed_date" in purchase_indexes
    assert "ix_daily_purchase_records_reserve_fund_center" in purchase_indexes
    assert {
        "ck_daily_purchase_records_purchase_channel",
        "ck_daily_purchase_records_product_url",
    } <= purchase_checks
    assert {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("procurement_invoice_purchases")
    } >= {"uq_procurement_invoice_purchases_purchase"}
    assert {
        constraint["name"] for constraint in inspector.get_check_constraints("procurement_invoices")
    } >= {
        "ck_procurement_invoices_amount",
        "ck_procurement_invoices_mime_type",
        "ck_procurement_invoices_extension",
        "ck_procurement_invoices_byte_size",
        "ck_procurement_invoices_sha256",
        "ck_procurement_invoices_filename",
    }
    assert {
        constraint["name"]
        for constraint in inspector.get_check_constraints("procurement_reserve_fund_entries")
    } >= {"ck_procurement_reserve_fund_entries_amount"}
    assert "ix_procurement_reserve_fund_entries_center_created" in {
        index["name"] for index in inspector.get_indexes("procurement_reserve_fund_entries")
    }
    assert permissions == {
        key: (permission_id, True) for key, permission_id in PROCUREMENT_PERMISSION_IDS.items()
    }
    assert grants["super_admin"] == set(PROCUREMENT_PERMISSION_IDS)
    assert grants["admin"] == set()
    assert grants["administration"] == set(PROCUREMENT_PERMISSION_IDS)
    assert grants["finance"] == {
        "procurement.read",
        "procurement.settle",
        "procurement.export",
        "procurement.invoices.manage",
    }


def test_travel_report_schema_and_role_permissions_are_seeded(
    postgres_engine: Engine,
) -> None:
    inspector = inspect(postgres_engine)
    columns = {
        column["name"]: column for column in inspector.get_columns("travel_report_conversions")
    }
    checks = {
        constraint["name"]
        for constraint in inspector.get_check_constraints("travel_report_conversions")
    }
    indexes = {index["name"] for index in inspector.get_indexes("travel_report_conversions")}
    with postgres_engine.connect() as connection:
        permissions = {
            key: (permission_id, sensitive)
            for key, permission_id, sensitive in connection.execute(
                text(
                    "SELECT key, id, sensitive FROM permissions "
                    "WHERE key = ANY(CAST(:keys AS text[]))"
                ),
                {"keys": list(TRAVEL_REPORT_PERMISSION_IDS)},
            )
        }
        grants = {
            role_key: {
                permission_key
                for granted_role, permission_key, scope_kind in _role_permission_grants(connection)
                if granted_role == role_key
                and permission_key in TRAVEL_REPORT_PERMISSION_IDS
                and scope_kind == "global"
            }
            for role_key in ("super_admin", "admin", "administration", "finance")
        }

    assert columns["source_content"]["nullable"] is False
    assert columns["converted_content"]["nullable"] is False
    assert columns["warnings"]["nullable"] is False
    assert {
        "ck_travel_report_source_size",
        "ck_travel_report_converted_size",
        "ck_travel_report_source_sha256",
        "ck_travel_report_converted_sha256",
        "ck_travel_report_billing_period",
        "ck_travel_report_counts",
    } <= checks
    assert {
        "ix_travel_report_conversions_created",
        "ix_travel_report_conversions_period",
    } <= indexes
    assert permissions == {
        key: (permission_id, True) for key, permission_id in TRAVEL_REPORT_PERMISSION_IDS.items()
    }
    assert grants == {
        "super_admin": set(TRAVEL_REPORT_PERMISSION_IDS),
        "admin": set(),
        "administration": set(TRAVEL_REPORT_PERMISSION_IDS),
        "finance": set(TRAVEL_REPORT_PERMISSION_IDS),
    }


def test_enrollment_program_catalog_and_application_permissions_are_seeded(
    postgres_engine: Engine,
) -> None:
    inspector = inspect(postgres_engine)
    admission_columns = {
        column["name"] for column in inspector.get_columns("admission_cases")
    }
    checks = {
        constraint["name"]
        for constraint in inspector.get_check_constraints("admission_cases")
    }
    indexes = {index["name"] for index in inspector.get_indexes("admission_cases")}
    with postgres_engine.connect() as connection:
        programs = connection.execute(
            text("SELECT name, price_cents FROM enrollment_programs ORDER BY sort_order")
        ).all()
        permission = connection.execute(
            text(
                "SELECT id, name, sensitive FROM permissions "
                "WHERE key = 'enrollment_applications.create'"
            )
        ).one()
        grants = {
            (role_key, scope_kind)
            for role_key, permission_key, scope_kind in _role_permission_grants(connection)
            if permission_key == "enrollment_applications.create"
        }

    assert {
        "application_source",
        "selected_project_id",
        "selected_program_id",
        "project_name_snapshot",
        "program_name_snapshot",
        "payment_preference",
    } <= admission_columns
    assert {
        "ck_admission_cases_application_source",
        "ck_admission_cases_payment_preference",
        "ck_admission_cases_self_service_fields",
    } <= checks
    assert "uq_admission_cases_open_self_service_choice" in indexes
    assert programs == ENROLLMENT_PROGRAM_PRICES
    assert permission == (
        ENROLLMENT_APPLICATION_PERMISSION_ID,
        "提交学员报名申请",
        False,
    )
    assert grants == {("student", "self"), ("guardian", "family")}


def test_0045_enrollment_program_migration_roundtrips_cleanly(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    try:
        command.downgrade(config, "0044_travel_report_conversions")
        inspector = inspect(postgres_engine)
        assert "enrollment_programs" not in inspector.get_table_names()
        admission_columns = {
            column["name"] for column in inspector.get_columns("admission_cases")
        }
        assert {
            "application_source",
            "selected_project_id",
            "selected_program_id",
            "project_name_snapshot",
            "program_name_snapshot",
            "payment_preference",
        }.isdisjoint(admission_columns)
        with postgres_engine.connect() as connection:
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM permissions "
                        "WHERE key = 'enrollment_applications.create'"
                    )
                )
                == 0
            )

        command.upgrade(config, CURRENT_HEAD)
        assert "enrollment_programs" in inspect(postgres_engine).get_table_names()
        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM enrollment_programs")) == 9
    finally:
        command.upgrade(config, CURRENT_HEAD)


def test_0046_removes_student_portal_finance_grants_and_roundtrips_cleanly(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    role_keys = {"student", "registered_user"}
    permission_keys = {"assets.read_self", "revenue_share.read_self"}

    def restricted_grants() -> set[tuple[str, str, str]]:
        with postgres_engine.connect() as connection:
            return {
                grant
                for grant in _role_permission_grants(connection)
                if grant[0] in role_keys and grant[1] in permission_keys
            }

    try:
        assert restricted_grants() == set()

        command.downgrade(config, "0045_enrollment_program_catalog")
        assert restricted_grants() == {
            (role_key, permission_key, "self")
            for role_key in role_keys
            for permission_key in permission_keys
        }

        command.upgrade(config, CURRENT_HEAD)
        assert restricted_grants() == set()
    finally:
        command.upgrade(config, CURRENT_HEAD)


def test_0047_self_referral_permission_roundtrips_cleanly(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)

    def permission_and_grants() -> tuple[
        tuple[str, str, bool] | None,
        set[tuple[str, str]],
    ]:
        with postgres_engine.connect() as connection:
            permission = connection.execute(
                text(
                    "SELECT CAST(id AS text), name, sensitive FROM permissions "
                    "WHERE key = 'revenue_share.create_self_referral'"
                )
            ).one_or_none()
            grants = {
                (role_key, scope_kind)
                for role_key, permission_key, scope_kind in _role_permission_grants(connection)
                if permission_key == "revenue_share.create_self_referral"
            }
        return permission, grants

    try:
        assert permission_and_grants() == (
            (
                "20000000-0000-4000-8000-000000000076",
                "生成本人邀请二维码",
                False,
            ),
            {("super_admin", "self"), ("admin", "self"), ("coach", "self")},
        )

        command.downgrade(config, "0046_student_portal_permissions")
        assert permission_and_grants() == (None, set())

        command.upgrade(config, CURRENT_HEAD)
        assert permission_and_grants()[1] == {
            ("super_admin", "self"),
            ("admin", "self"),
            ("coach", "self"),
        }
    finally:
        command.upgrade(config, CURRENT_HEAD)


def test_0048_removes_ordinary_admin_global_asset_grants_and_roundtrips_cleanly(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    permission_keys = {"assets.read", "assets.manage", "assets.assign", "assets.export"}

    def ordinary_admin_asset_grants() -> set[tuple[str, str, str]]:
        with postgres_engine.connect() as connection:
            return {
                grant
                for grant in _role_permission_grants(connection)
                if grant[0] == "admin" and grant[1] in permission_keys
            }

    expected_legacy_grants = {
        ("admin", permission_key, "global") for permission_key in permission_keys
    }
    try:
        assert ordinary_admin_asset_grants() == set()

        command.downgrade(config, "0047_self_referral_codes")
        assert ordinary_admin_asset_grants() == expected_legacy_grants

        command.upgrade(config, CURRENT_HEAD)
        assert ordinary_admin_asset_grants() == set()
    finally:
        command.upgrade(config, CURRENT_HEAD)


def test_0050_asset_warranty_service_roundtrip(postgres_engine: Engine) -> None:
    config = _alembic_config(postgres_engine)
    columns = {
        "warranty_channel", "warranty_service_location", "warranty_contact_name",
        "warranty_contact_phone", "warranty_product_url", "warranty_order_number",
    }
    try:
        assert columns <= {c["name"] for c in inspect(postgres_engine).get_columns("assets")}
        assert "ck_assets_warranty_channel" in {
            c["name"] for c in inspect(postgres_engine).get_check_constraints("assets")
        }
        command.downgrade(config, "0049_project_coach_selection")
        assert columns.isdisjoint(c["name"] for c in inspect(postgres_engine).get_columns("assets"))
        command.upgrade(config, CURRENT_HEAD)
        assert columns <= {c["name"] for c in inspect(postgres_engine).get_columns("assets")}
    finally:
        command.upgrade(config, CURRENT_HEAD)


def test_0051_keeps_historical_settlements_and_reserves_duplicate_references(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    person_id, user_id, first_id, second_id = (uuid4() for _ in range(4))
    try:
        command.downgrade(config, "0050_asset_warranty_service")
        with postgres_engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO people (id,full_name,identity_ciphertext,identity_digest,sex,date_of_birth) "
                "VALUES (:id,'迁移验证','test',:digest,'male','1990-01-01')"
            ), {"id": person_id, "digest": person_id.hex})
            connection.execute(text(
                "INSERT INTO user_accounts (id,person_id,password_hash,status,"
                "force_password_change,failed_login_count) "
                "VALUES (:id,:person,'test','active',false,0)"
            ), {"id": user_id, "person": person_id})
            for row_id, reference, created in (
                (first_id, " bank-001 ", "2026-09-01"),
                (second_id, "BANK-001", "2026-09-02"),
            ):
                connection.execute(text(
                    "INSERT INTO revenue_share_settlements "
                    "(id,beneficiary_kind,beneficiary_person_id,amount_cents,settled_on,"
                    "reference,created_by_user_id,created_at) "
                    "VALUES (:id,'person',:person,10000,'2026-09-01',:reference,:actor,:created)"
                ), {"id": row_id, "person": person_id, "reference": reference,
                    "actor": user_id, "created": created})
        command.upgrade(config, CURRENT_HEAD)
        with postgres_engine.connect() as connection:
            rows = connection.execute(text(
                "SELECT id,amount_cents,reference_key,idempotency_key "
                "FROM revenue_share_settlements WHERE created_by_user_id=:actor ORDER BY created_at"
            ), {"actor": user_id}).all()
            assert len(rows) == 2
            assert rows[0][0] == first_id and rows[0][1] == 10000 and rows[0][2]
            assert rows[1][0] == second_id and rows[1][1] == 10000 and rows[1][2] is None
            assert all(row[3] is None for row in rows)
        assert {"uq_revenue_share_settlements_operation", "uq_revenue_share_settlements_reference_key"} <= {
            constraint["name"] for constraint in inspect(postgres_engine).get_unique_constraints(
                "revenue_share_settlements"
            )
        }
    finally:
        command.upgrade(config, CURRENT_HEAD)
        with postgres_engine.begin() as connection:
            connection.execute(text("DELETE FROM revenue_share_settlements WHERE created_by_user_id=:id"), {"id": user_id})
            connection.execute(text("DELETE FROM user_accounts WHERE id=:id"), {"id": user_id})
            connection.execute(text("DELETE FROM people WHERE id=:id"), {"id": person_id})


def test_0052_operations_history_roundtrip(postgres_engine: Engine) -> None:
    config = _alembic_config(postgres_engine)
    tables = {"operations_metric_batches", "operations_request_minutes", "operations_alerts"}
    try:
        assert tables <= set(inspect(postgres_engine).get_table_names())
        command.downgrade(config, "0051_settlement_idempotency")
        assert tables.isdisjoint(inspect(postgres_engine).get_table_names())
        command.upgrade(config, CURRENT_HEAD)
        assert tables <= set(inspect(postgres_engine).get_table_names())
        assert "uq_operations_alert_active_key" in {
            index["name"] for index in inspect(postgres_engine).get_indexes("operations_alerts")
        }
    finally:
        command.upgrade(config, CURRENT_HEAD)


def _insert_queue_test_payment(connection: Connection, payment_id: UUID) -> None:
    connection.execute(text(
        "INSERT INTO payments (id,source_kind,source_system,external_order_id,amount_cents,paid_at) "
        "VALUES (:id,'api','queue-migration',:external,10000,'2026-09-01T00:00:00Z')"
    ), {"id": payment_id, "external": payment_id.hex})


def _insert_queue_test_refund(
    connection: Connection, payment_id: UUID, account_id: UUID, refund_id: UUID,
) -> None:
    connection.execute(text(
        "INSERT INTO payment_refunds (id,payment_id,amount_cents,refunded_at,refund_type,"
        "reason,created_by_user_id) VALUES (:id,:payment,100,'2026-09-02T00:00:00Z',"
        "'manual','queue migration test',:account)"
    ), {"id": refund_id, "payment": payment_id, "account": account_id})


def test_0055_preserves_money_and_enforces_manual_operation_constraints(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    person_id, account_id = _insert_test_account(postgres_engine)
    payment_ids = [uuid4(), uuid4()]
    refund_ids = [uuid4(), uuid4()]

    def financial_snapshot() -> list:
        with postgres_engine.connect() as connection:
            return [connection.execute(text(
                f"SELECT to_jsonb(record) - 'idempotency_key' - 'request_fingerprint' "
                f"FROM {table} record WHERE id = ANY(CAST(:ids AS uuid[])) ORDER BY id"
            ), {"ids": ids}).all() for table, ids in (
                ("payments", payment_ids), ("payment_refunds", refund_ids),
            )]

    try:
        command.downgrade(config, "0054_admission_order_numbers")
        with postgres_engine.begin() as connection:
            for payment_id, refund_id in zip(payment_ids, refund_ids, strict=True):
                connection.execute(text(
                    "INSERT INTO payments (id,source_kind,amount_cents,paid_at,"
                    "created_by_user_id,manual_note) VALUES "
                    "(:id,'manual',10000,'2026-09-01T00:00:00Z',:actor,'migration test')"
                ), {"id": payment_id, "actor": account_id})
                _insert_queue_test_refund(connection, payment_id, account_id, refund_id)
        before = financial_snapshot()
        command.upgrade(config, CURRENT_HEAD)
        assert financial_snapshot() == before
        for table, ids in (("payments", payment_ids), ("payment_refunds", refund_ids)):
            with postgres_engine.connect() as connection:
                assert connection.execute(text(
                    f"SELECT idempotency_key, request_fingerprint FROM {table} "
                    "WHERE id = ANY(CAST(:ids AS uuid[]))"
                ), {"ids": ids}).all() == [(None, None), (None, None)]
            key = uuid4()
            for bad_key, bad_fingerprint in (
                (key, None), (None, "a" * 64), (key, "not-a-fingerprint"),
            ):
                with pytest.raises(DBAPIError, match="operation_fingerprint"), postgres_engine.begin() as connection:
                    connection.execute(text(
                        f"UPDATE {table} SET idempotency_key=:key,request_fingerprint=:fingerprint "
                        "WHERE id=:id"
                    ), {"key": bad_key, "fingerprint": bad_fingerprint, "id": ids[0]})
            with postgres_engine.begin() as connection:
                connection.execute(text(
                    f"UPDATE {table} SET idempotency_key=:key,request_fingerprint=:fingerprint "
                    "WHERE id=:id"
                ), {"key": key, "fingerprint": "a" * 64, "id": ids[0]})
            with pytest.raises(DBAPIError, match="manual_operation"), postgres_engine.begin() as connection:
                connection.execute(text(
                    f"UPDATE {table} SET idempotency_key=:key,request_fingerprint=:fingerprint "
                    "WHERE id=:id"
                ), {"key": key, "fingerprint": "b" * 64, "id": ids[1]})
        assert financial_snapshot() == before
        command.downgrade(config, "0054_admission_order_numbers")
        assert financial_snapshot() == before
        command.upgrade(config, CURRENT_HEAD)
        assert financial_snapshot() == before
    finally:
        command.upgrade(config, CURRENT_HEAD)
        with postgres_engine.begin() as connection:
            connection.execute(text(
                "DELETE FROM payment_refunds WHERE id = ANY(CAST(:ids AS uuid[]))"
            ), {"ids": refund_ids})
            connection.execute(text(
                "DELETE FROM payments WHERE id = ANY(CAST(:ids AS uuid[]))"
            ), {"ids": payment_ids})
        _delete_test_account(postgres_engine, person_id=person_id, account_id=account_id)


def test_0057_preserves_procurement_history_and_enforces_operation_keys(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    person_id, account_id = _insert_test_account(postgres_engine)
    center_id = uuid4()
    rows = {
        "daily_purchase_records": [uuid4(), uuid4()],
        "procurement_reserve_fund_entries": [uuid4(), uuid4()],
    }

    def business_snapshot() -> list:
        with postgres_engine.connect() as connection:
            return [connection.execute(text(
                f"SELECT to_jsonb(record) - 'operation_key' - 'request_fingerprint' "
                f"FROM {table} record WHERE settlement_center_id=:center ORDER BY id"
            ), {"center": center_id}).all() for table in rows]

    try:
        command.downgrade(config, "0056_evaluation_upload_cleanup")
        with postgres_engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO procurement_settlement_centers(id,name,created_by_user_id) "
                "VALUES (:id,:name,:actor)"
            ), {"id": center_id, "name": f"Migration-{center_id}", "actor": account_id})
            for record_id in rows["daily_purchase_records"]:
                connection.execute(text(
                    "INSERT INTO daily_purchase_records(id,purchase_date,item_name,quantity,"
                    "unit_price_cents,total_price_cents,settlement_center_id,created_by_user_id) "
                    "VALUES (:id,'2026-09-14','迁移采购',1,100,100,:center,:actor)"
                ), {"id": record_id, "center": center_id, "actor": account_id})
            for record_id in rows["procurement_reserve_fund_entries"]:
                connection.execute(text(
                    "INSERT INTO procurement_reserve_fund_entries(id,settlement_center_id,"
                    "amount_cents,created_by_user_id) VALUES (:id,:center,1000,:actor)"
                ), {"id": record_id, "center": center_id, "actor": account_id})
        before = business_snapshot()
        command.upgrade(config, CURRENT_HEAD)
        assert business_snapshot() == before
        for table, ids in rows.items():
            with postgres_engine.connect() as connection:
                assert connection.execute(text(
                    f"SELECT operation_key,request_fingerprint FROM {table} "
                    "WHERE settlement_center_id=:center"
                ), {"center": center_id}).all() == [(None, None), (None, None)]
            key = uuid4()
            for bad_key, fingerprint in ((key, None), (None, "a" * 64), (key, "invalid")):
                with pytest.raises(DBAPIError, match="ck_procurement_"), postgres_engine.begin() as connection:
                    connection.execute(text(
                        f"UPDATE {table} SET operation_key=:key,request_fingerprint=:fingerprint WHERE id=:id"
                    ), {"key": bad_key, "fingerprint": fingerprint, "id": ids[0]})
            with postgres_engine.begin() as connection:
                connection.execute(text(
                    f"UPDATE {table} SET operation_key=:key,request_fingerprint=:fingerprint WHERE id=:id"
                ), {"key": key, "fingerprint": "a" * 64, "id": ids[0]})
            with pytest.raises(DBAPIError, match="uq_procurement_"), postgres_engine.begin() as connection:
                connection.execute(text(
                    f"UPDATE {table} SET operation_key=:key,request_fingerprint=:fingerprint WHERE id=:id"
                ), {"key": key, "fingerprint": "b" * 64, "id": ids[1]})
        assert business_snapshot() == before
        command.downgrade(config, "0056_evaluation_upload_cleanup")
        assert business_snapshot() == before
        command.upgrade(config, CURRENT_HEAD)
        assert business_snapshot() == before
    finally:
        command.upgrade(config, CURRENT_HEAD)
        with postgres_engine.begin() as connection:
            for table in rows:
                connection.execute(text(f"DELETE FROM {table} WHERE settlement_center_id=:center"), {"center": center_id})
            connection.execute(text("DELETE FROM procurement_settlement_centers WHERE id=:center"), {"center": center_id})
        _delete_test_account(postgres_engine, person_id=person_id, account_id=account_id)


def test_0056_cleanup_schema_roundtrips_and_preserves_pending_work(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    key = ".owned-" + uuid4().hex
    try:
        for bad_key, attempts in (("../unsafe", 0), (key, -1)):
            with pytest.raises(DBAPIError), postgres_engine.begin() as connection:
                connection.execute(text(
                    "INSERT INTO evaluation_quarantine_cleanup(storage_key,attempts) VALUES (:key,:attempts)"
                ), {"key": bad_key, "attempts": attempts})
        with postgres_engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO evaluation_quarantine_cleanup(storage_key) VALUES (:key)"
            ), {"key": key})
        with pytest.raises(DBAPIError, match="pending quarantine cleanup prevents downgrade"):
            command.downgrade(config, "0055_manual_payment_idempotency")
        with postgres_engine.begin() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == CURRENT_HEAD
            assert connection.scalar(text(
                "SELECT attempts FROM evaluation_quarantine_cleanup WHERE storage_key=:key"
            ), {"key": key}) == 0
            connection.execute(text(
                "DELETE FROM evaluation_quarantine_cleanup WHERE storage_key=:key"
            ), {"key": key})
        command.downgrade(config, "0055_manual_payment_idempotency")
        assert "evaluation_quarantine_cleanup" not in inspect(postgres_engine).get_table_names()
        command.upgrade(config, CURRENT_HEAD)
        assert "evaluation_quarantine_cleanup" in inspect(postgres_engine).get_table_names()
        assert "ix_evaluation_quarantine_cleanup_due" in {
            item["name"] for item in inspect(postgres_engine).get_indexes("evaluation_quarantine_cleanup")
        }
        assert "ix_evaluation_upload_account_open" in {
            item["name"] for item in inspect(postgres_engine).get_indexes("system_evaluation_upload_sessions")
        }
    finally:
        command.upgrade(config, CURRENT_HEAD)
        with postgres_engine.begin() as connection:
            connection.execute(text(
                "DELETE FROM evaluation_quarantine_cleanup WHERE storage_key=:key"
            ), {"key": key})


def test_0054_backfills_original_china_dates_without_changing_payments(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0053_revenue_share_processing")
    person_id, account_id = _insert_test_account(postgres_engine)
    case_ids = [uuid4() for _ in range(3)]
    payment_id = uuid4()

    def business_snapshot(connection: Connection) -> tuple:
        return (
            connection.execute(
                text(
                    "SELECT to_jsonb(c) - 'order_number' FROM admission_cases c "
                    "WHERE id = ANY(CAST(:ids AS uuid[])) ORDER BY id"
                ),
                {"ids": case_ids},
            ).all(),
            connection.execute(
                text("SELECT to_jsonb(p) - 'idempotency_key' - 'request_fingerprint' "
                     "FROM payments p WHERE id = :id"),
                {"id": payment_id},
            ).all(),
            connection.execute(
                text(
                    "SELECT to_jsonb(p) FROM admission_case_payments p "
                    "WHERE payment_id = :id"
                ),
                {"id": payment_id},
            ).all(),
        )

    try:
        with postgres_engine.begin() as connection:
            for index, case_id in enumerate(case_ids):
                connection.execute(
                    text(
                        "INSERT INTO admission_cases "
                        "(id, student_person_id, status, created_by_user_id, "
                        "application_source, selected_project_id, selected_program_id, "
                        "project_name_snapshot, program_name_snapshot, payment_preference, "
                        "agreed_tuition_cents, created_at) VALUES "
                        "(:id, :person, 'ready', :actor, :source, "
                        "CASE WHEN :self_service THEN (SELECT id FROM game_projects "
                        "ORDER BY id LIMIT 1) ELSE NULL END, "
                        "CASE WHEN :self_service THEN (SELECT id FROM enrollment_programs "
                        "ORDER BY id LIMIT 1) ELSE NULL END, "
                        "CASE WHEN :self_service THEN '迁移测试项目' ELSE NULL END, "
                        "CASE WHEN :self_service THEN '迁移测试课程' ELSE NULL END, "
                        "CASE WHEN :self_service THEN 'offline' ELSE NULL END, "
                        "10000, CAST(:created AS timestamptz))"
                    ),
                    {
                        "id": case_id,
                        "person": person_id,
                        "actor": account_id if index != 1 else None,
                        "source": "self_service" if index == 2 else None,
                        "self_service": index == 2,
                        "created": [
                            "2026-08-31T15:59:59Z",
                            "2026-08-31T16:00:00Z",
                            "2026-09-01T16:00:00Z",
                        ][index],
                    },
                )
            _insert_queue_test_payment(connection, payment_id)
            connection.execute(
                text(
                    "INSERT INTO admission_case_payments "
                    "(admission_case_id, payment_id, attached_by_user_id) "
                    "VALUES (:case, :payment, :actor)"
                ),
                {"case": case_ids[0], "payment": payment_id, "actor": account_id},
            )
            before = business_snapshot(connection)

        command.upgrade(config, CURRENT_HEAD)
        with postgres_engine.connect() as connection:
            assert business_snapshot(connection) == before
            numbers = dict(
                connection.execute(
                    text(
                        "SELECT id, order_number FROM admission_cases "
                        "WHERE id = ANY(CAST(:ids AS uuid[]))"
                    ),
                    {"ids": case_ids},
                ).all()
            )
            assert [numbers[case_id][2:10] for case_id in case_ids] == [
                "20260831", "20260901", "20260902",
            ]
            serials = [int(numbers[case_id][10:]) for case_id in case_ids]
            assert serials[0] < serials[1] < serials[2]
            assert len(set(numbers.values())) == 3
            column = next(
                item for item in inspect(connection).get_columns("admission_cases")
                if item["name"] == "order_number"
            )
            assert column["nullable"] is False
            assert column["type"].length == 32
            assert "next_admission_order_number()" in column["default"]
            assert connection.scalar(
                text("SELECT pg_get_serial_sequence('admission_cases', 'order_number')")
            ) == "public.admission_order_number_seq"

        command.downgrade(config, "0053_revenue_share_processing")
        with postgres_engine.connect() as connection:
            assert business_snapshot(connection) == before
            assert "order_number" not in {
                column["name"] for column in inspect(connection).get_columns("admission_cases")
            }
            assert connection.scalar(text("SELECT to_regclass('admission_order_number_seq')")) is None
            assert connection.scalar(text("SELECT to_regprocedure('next_admission_order_number()')")) is None
            assert connection.scalar(text("SELECT to_regprocedure('protect_admission_order_number()')")) is None
        command.upgrade(config, CURRENT_HEAD)
        with postgres_engine.connect() as connection:
            assert business_snapshot(connection) == before
    finally:
        command.upgrade(config, CURRENT_HEAD)
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM admission_case_payments WHERE payment_id = :id"),
                {"id": payment_id},
            )
            connection.execute(text("DELETE FROM payments WHERE id = :id"), {"id": payment_id})
            connection.execute(
                text("DELETE FROM admission_cases WHERE id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": case_ids},
            )
        _delete_test_account(postgres_engine, person_id=person_id, account_id=account_id)


def test_0054_generated_order_numbers_are_unique_concurrent_and_never_truncated(
    postgres_engine: Engine,
) -> None:
    case_ids = [uuid4() for _ in range(24)]
    rolled_back_case_id = uuid4()
    with postgres_engine.begin() as connection:
        # Exercise the six-to-seven-digit boundary without inserting a million rows.
        connection.execute(text("SELECT setval('admission_order_number_seq', 999998, true)"))
        before_date = connection.scalar(
            text("SELECT to_char(clock_timestamp() AT TIME ZONE 'Asia/Shanghai', 'YYYYMMDD')")
        )
    try:
        with postgres_engine.connect() as connection:
            transaction = connection.begin()
            rolled_back_number = connection.scalar(
                text("INSERT INTO admission_cases (id) VALUES (:id) RETURNING order_number"),
                {"id": rolled_back_case_id},
            )
            assert rolled_back_number.endswith("999999")
            transaction.rollback()

        def insert_order(case_id: UUID) -> str:
            with postgres_engine.begin() as connection:
                connection.execute(text("SET LOCAL TIME ZONE 'America/Los_Angeles'"))
                return connection.scalar(
                    text("INSERT INTO admission_cases (id) VALUES (:id) RETURNING order_number"),
                    {"id": case_id},
                )

        with ThreadPoolExecutor(max_workers=8) as executor:
            numbers = list(executor.map(insert_order, case_ids))
        with postgres_engine.connect() as connection:
            after_date = connection.scalar(
                text("SELECT to_char(clock_timestamp() AT TIME ZONE 'Asia/Shanghai', 'YYYYMMDD')")
            )
            assert connection.scalar(
                text("SELECT count(*) FROM admission_cases WHERE id = :id"),
                {"id": rolled_back_case_id},
            ) == 0
        assert len(set(numbers)) == len(case_ids)
        assert all(number.startswith("BM") for number in numbers)
        assert all(number[2:10] in {before_date, after_date} for number in numbers)
        assert all(len(number) == 17 for number in numbers)
        assert {int(number[10:]) for number in numbers} == set(range(1000000, 1000024))
        assert rolled_back_number not in numbers
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM admission_cases WHERE id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": case_ids},
            )


def test_0054_order_numbers_are_populated_by_orm_and_remain_immutable(
    postgres_engine: Engine,
) -> None:
    from app.admissions.models import AdmissionCase

    case_id = uuid4()
    try:
        with Session(postgres_engine) as session:
            admission = AdmissionCase(id=case_id)
            session.add(admission)
            session.flush()
            number = admission.order_number
            assert number.startswith("BM")
            session.commit()

        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE admission_cases SET status = 'closed', closed_reason = '迁移测试关闭', "
                    "created_at = '2020-01-01T00:00:00Z', order_number = order_number WHERE id = :id"
                ),
                {"id": case_id},
            )
            assert connection.scalar(
                text("SELECT order_number FROM admission_cases WHERE id = :id"),
                {"id": case_id},
            ) == number

        for new_number in (None, "BM20200101123456"):
            with postgres_engine.begin() as connection, pytest.raises(DBAPIError) as error:
                connection.execute(
                    text("UPDATE admission_cases SET order_number = :number WHERE id = :id"),
                    {"id": case_id, "number": new_number},
                )
            assert error.value.orig.sqlstate == "23514"
            assert error.value.orig.diag.constraint_name == "admission_order_number_immutable"

        for invalid_number, sqlstate in ((number, "23505"), (None, "23502"), ("", "23514")):
            with postgres_engine.begin() as connection, pytest.raises(DBAPIError) as error:
                connection.execute(
                    text("INSERT INTO admission_cases (id, order_number) VALUES (:id, :number)"),
                    {"id": uuid4(), "number": invalid_number},
                )
            assert error.value.orig.sqlstate == sqlstate
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(text("DELETE FROM admission_cases WHERE id = :id"), {"id": case_id})


def test_0053_queue_backfill_and_downgrade_preserve_financial_records(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    person_id, account_id = _insert_test_account(postgres_engine)
    cases = {kind: uuid4() for kind in ("fresh", "pending", "zero", "complete", "refunded")}
    payment_ids = list(cases.values())

    def financial_snapshot() -> dict[str, list[object]]:
        with postgres_engine.connect() as connection:
            return {table: list(connection.scalars(text(
                f"SELECT to_jsonb(record) - 'idempotency_key' - 'request_fingerprint' "
                f"FROM {table} AS record "
                f"WHERE {key} = ANY(CAST(:ids AS uuid[])) ORDER BY id"
                if key == "id" else
                f"SELECT to_jsonb(record) - 'idempotency_key' - 'request_fingerprint' "
                f"FROM {table} AS record "
                f"WHERE {key} = ANY(CAST(:ids AS uuid[])) ORDER BY payment_id"
            ), {"ids": payment_ids})) for table, key in (
                ("payments", "id"), ("payment_refunds", "payment_id"),
                ("revenue_share_ledger_entries", "payment_id"),
                ("revenue_share_payment_statuses", "payment_id"),
            )}

    try:
        command.downgrade(config, "0052_operations_history")
        with postgres_engine.begin() as connection:
            for kind, payment_id in cases.items():
                _insert_queue_test_payment(connection, payment_id)
                if kind != "fresh":
                    connection.execute(text(
                        "INSERT INTO revenue_share_payment_statuses (payment_id,state,reason_code) "
                        "VALUES (:payment,:state,:reason)"
                    ), {"payment": payment_id, "state": "pending" if kind == "pending" else "allocated",
                        "reason": "enrollment_not_found" if kind == "pending" else None})
                if kind in {"pending", "complete", "refunded"}:
                    connection.execute(text(
                        "INSERT INTO revenue_share_ledger_entries (id,event_key,payment_id,entry_kind,"
                        "role_kind,beneficiary_kind,beneficiary_person_id,calculation_type,"
                        "fixed_amount_cents_snapshot,base_amount_cents,amount_cents,occurred_at) "
                        "VALUES (:id,:event,:payment,'earning','additional','person',:person,'fixed',"
                        "1000,10000,1000,'2026-09-01T00:00:00Z')"
                    ), {"id": uuid4(), "event": "migration-queue-" + payment_id.hex,
                        "payment": payment_id, "person": person_id})
            _insert_queue_test_refund(connection, cases["refunded"], account_id, uuid4())
        expected_records = financial_snapshot()

        command.upgrade(config, CURRENT_HEAD)
        with postgres_engine.connect() as connection:
            queued = set(connection.scalars(text(
                "SELECT payment_id FROM revenue_share_reconcile_queue "
                "WHERE payment_id = ANY(CAST(:ids AS uuid[]))"
            ), {"ids": payment_ids}))
        assert queued == set(payment_ids) - {cases["complete"]}
        assert financial_snapshot() == expected_records
        inspector = inspect(postgres_engine)
        assert {index["name"] for index in inspector.get_indexes("revenue_share_reconcile_queue")} == {
            "ix_revenue_share_reconcile_queue_paid_at",
        }
        assert inspector.get_foreign_keys("revenue_share_reconcile_queue")[0]["options"] == {
            "ondelete": "CASCADE",
        }

        command.downgrade(config, "0052_operations_history")
        assert "revenue_share_reconcile_queue" not in inspect(postgres_engine).get_table_names()
        with postgres_engine.connect() as connection:
            assert connection.scalar(text(
                "SELECT count(*) FROM pg_trigger WHERE tgname IN "
                "('trg_revenue_share_payment_insert','trg_revenue_share_refund_insert',"
                "'trg_revenue_share_payment_paid_at')"
            )) == 0
        assert financial_snapshot() == expected_records
        command.upgrade(config, CURRENT_HEAD)
        assert financial_snapshot() == expected_records
        with postgres_engine.connect() as connection:
            assert connection.scalar(text(
                "SELECT count(*) FROM revenue_share_reconcile_queue "
                "WHERE payment_id = ANY(CAST(:ids AS uuid[]))"
            ), {"ids": payment_ids}) == 4
    finally:
        command.upgrade(config, CURRENT_HEAD)
        with postgres_engine.begin() as connection:
            for table in ("revenue_share_ledger_entries", "revenue_share_payment_statuses", "payment_refunds"):
                connection.execute(text(
                    f"DELETE FROM {table} WHERE payment_id = ANY(CAST(:ids AS uuid[]))"
                ), {"ids": payment_ids})
            connection.execute(text("DELETE FROM payments WHERE id = ANY(CAST(:ids AS uuid[]))"),
                               {"ids": payment_ids})
        _delete_test_account(postgres_engine, person_id=person_id, account_id=account_id)


def test_0053_queue_triggers_merge_refunds_and_share_payment_transaction(
    postgres_engine: Engine,
) -> None:
    person_id, account_id = _insert_test_account(postgres_engine)
    payment_id, rolled_back_payment = uuid4(), uuid4()
    rollback_refund = uuid4()
    try:
        with postgres_engine.begin() as connection:
            started = connection.scalar(text("SELECT clock_timestamp()"))
            _insert_queue_test_payment(connection, payment_id)
            queued_at = connection.scalar(text(
                "SELECT queued_at FROM revenue_share_reconcile_queue WHERE payment_id=:id"
            ), {"id": payment_id})
            assert queued_at is not None and queued_at >= started
            _insert_queue_test_refund(connection, payment_id, account_id, uuid4())
            _insert_queue_test_refund(connection, payment_id, account_id, uuid4())
            assert connection.execute(text(
                "SELECT queued_at,count(*) OVER () FROM revenue_share_reconcile_queue WHERE payment_id=:id"
            ), {"id": payment_id}).one() == (queued_at, 1)
            connection.execute(text("UPDATE payments SET paid_at='2026-09-03T00:00:00Z' WHERE id=:id"),
                               {"id": payment_id})
            assert connection.scalar(text(
                "SELECT q.paid_at=p.paid_at AND q.queued_at=:queued_at "
                "FROM revenue_share_reconcile_queue q JOIN payments p ON p.id=q.payment_id WHERE p.id=:id"
            ), {"queued_at": queued_at, "id": payment_id}) is True
            connection.execute(text("DELETE FROM revenue_share_reconcile_queue WHERE payment_id=:id"),
                               {"id": payment_id})
            connection.execute(text("UPDATE payments SET paid_at='2026-09-04T00:00:00Z' WHERE id=:id"),
                               {"id": payment_id})
            assert connection.scalar(text(
                "SELECT count(*) FROM revenue_share_reconcile_queue WHERE payment_id=:id"
            ), {"id": payment_id}) == 0

        with postgres_engine.connect() as connection:
            transaction = connection.begin()
            _insert_queue_test_refund(connection, payment_id, account_id, rollback_refund)
            _insert_queue_test_payment(connection, rolled_back_payment)
            assert connection.scalar(text(
                "SELECT count(*) FROM revenue_share_reconcile_queue "
                "WHERE payment_id IN (:payment,:rolled_back)"
            ), {"payment": payment_id, "rolled_back": rolled_back_payment}) == 2
            transaction.rollback()
        with postgres_engine.begin() as connection:
            assert connection.scalar(text(
                "SELECT count(*) FROM revenue_share_reconcile_queue "
                "WHERE payment_id IN (:payment,:rolled_back)"
            ), {"payment": payment_id, "rolled_back": rolled_back_payment}) == 0
            assert connection.scalar(text("SELECT count(*) FROM payment_refunds WHERE id=:id"),
                                     {"id": rollback_refund}) == 0
            assert connection.scalar(text("SELECT count(*) FROM payments WHERE id=:id"),
                                     {"id": rolled_back_payment}) == 0
            _insert_queue_test_refund(connection, payment_id, account_id, uuid4())
            assert connection.scalar(text(
                "SELECT q.paid_at=p.paid_at FROM revenue_share_reconcile_queue q "
                "JOIN payments p ON p.id=q.payment_id WHERE p.id=:id"
            ), {"id": payment_id}) is True
            _insert_queue_test_payment(connection, rolled_back_payment)
            connection.execute(text("DELETE FROM payments WHERE id=:id"), {"id": rolled_back_payment})
            assert connection.scalar(text(
                "SELECT count(*) FROM revenue_share_reconcile_queue WHERE payment_id=:id"
            ), {"id": rolled_back_payment}) == 0
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(text("DELETE FROM payment_refunds WHERE payment_id=:id"), {"id": payment_id})
            connection.execute(text("DELETE FROM payments WHERE id IN (:id,:other)"),
                               {"id": payment_id, "other": rolled_back_payment})
        _delete_test_account(postgres_engine, person_id=person_id, account_id=account_id)


def test_0049_project_coach_selection_schema_and_permission_roundtrip_cleanly(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)

    def assert_current_schema() -> None:
        inspector = inspect(postgres_engine)
        assert "project_coach_assignments" in inspector.get_table_names()
        assert {
            "selected_coach_person_id",
            "coach_name_snapshot",
        } <= {
            column["name"] for column in inspector.get_columns("admission_cases")
        }
        assert {
            "ix_project_coach_assignments_project_active",
            "ix_project_coach_assignments_coach_active",
        } <= {
            index["name"]
            for index in inspector.get_indexes("project_coach_assignments")
        }
        with postgres_engine.connect() as connection:
            permission = connection.execute(
                text(
                    "SELECT id, name, sensitive FROM permissions "
                    "WHERE key = 'project_coaches.manage'"
                )
            ).one()
            grants = {
                (role_key, scope_kind)
                for role_key, permission_key, scope_kind in _role_permission_grants(connection)
                if permission_key == "project_coaches.manage"
            }
        assert permission == (
            PROJECT_COACH_PERMISSION_ID,
            "配置项目可报名教练",
            False,
        )
        assert grants == {("super_admin", "global")}

    try:
        assert_current_schema()
        command.downgrade(config, "0048_admin_asset_boundary")
        inspector = inspect(postgres_engine)
        assert "project_coach_assignments" not in inspector.get_table_names()
        assert {
            "selected_coach_person_id",
            "coach_name_snapshot",
        }.isdisjoint(
            column["name"] for column in inspector.get_columns("admission_cases")
        )
        with postgres_engine.connect() as connection:
            assert connection.scalar(
                text("SELECT count(*) FROM permissions WHERE key = 'project_coaches.manage'")
            ) == 0
        command.upgrade(config, CURRENT_HEAD)
        assert_current_schema()
    finally:
        command.upgrade(config, CURRENT_HEAD)


def test_0044_travel_report_migration_roundtrips_cleanly(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    try:
        command.downgrade(config, "0043_operations_monitoring")
        assert "travel_report_conversions" not in inspect(postgres_engine).get_table_names()
        with postgres_engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM permissions WHERE key = ANY(CAST(:keys AS text[]))"),
                    {"keys": list(TRAVEL_REPORT_PERMISSION_IDS)},
                )
                == 0
            )
        command.upgrade(config, CURRENT_HEAD)
        assert "travel_report_conversions" in inspect(postgres_engine).get_table_names()
    finally:
        command.upgrade(config, CURRENT_HEAD)


def test_0037_procurement_invoice_migration_roundtrips_cleanly(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    try:
        command.downgrade(config, "0036_daily_procurement")
        assert {
            "procurement_invoices",
            "procurement_invoice_purchases",
        }.isdisjoint(inspect(postgres_engine).get_table_names())
        with postgres_engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM permissions WHERE key = :key"),
                    {"key": "procurement.invoices.manage"},
                )
                == 0
            )

        command.upgrade(config, CURRENT_HEAD)
        assert {
            "procurement_invoices",
            "procurement_invoice_purchases",
        } <= set(inspect(postgres_engine).get_table_names())
        with postgres_engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM permissions WHERE key = :key"),
                    {"key": "procurement.invoices.manage"},
                )
                == 1
            )
    finally:
        command.upgrade(config, CURRENT_HEAD)


def test_0038_removes_ordinary_admin_financial_grants_and_roundtrips_cleanly(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    restricted_permissions = {
        "revenue_share.finance",
        "revenue_share.manage",
        "revenue_share.export",
        *PROCUREMENT_PERMISSION_IDS,
    }

    def admin_restricted_grants() -> set[str]:
        with postgres_engine.connect() as connection:
            return {
                permission_key
                for role_key, permission_key, scope_kind in _role_permission_grants(connection)
                if role_key == "admin"
                and scope_kind == "global"
                and permission_key in restricted_permissions
            }

    try:
        assert admin_restricted_grants() == set()
        command.downgrade(config, "0037_procurement_invoices")
        assert admin_restricted_grants() == restricted_permissions
        command.upgrade(config, CURRENT_HEAD)
        assert admin_restricted_grants() == set()
    finally:
        command.upgrade(config, CURRENT_HEAD)


def test_0039_backfills_purchase_channels_and_roundtrips_cleanly(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    center_id = uuid4()
    purchase_id = uuid4()
    actor_person_id: UUID | None = None
    actor_id: UUID | None = None
    try:
        command.downgrade(config, "0038_admin_finance_boundary")
        columns_before = {
            column["name"]
            for column in inspect(postgres_engine).get_columns("daily_purchase_records")
        }
        assert {"purchase_channel", "product_url"}.isdisjoint(columns_before)
        actor_person_id, actor_id = _insert_test_account(postgres_engine)
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO procurement_settlement_centers "
                    "(id, name, created_by_user_id) VALUES (:id, :name, :actor_id)"
                ),
                {
                    "id": center_id,
                    "name": f"0039 migration center {center_id}",
                    "actor_id": actor_id,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO daily_purchase_records "
                    "(id, purchase_date, item_name, quantity, unit_price_cents, "
                    "total_price_cents, settlement_center_id, created_by_user_id) "
                    "VALUES (:id, DATE '2026-08-29', '迁移前采购', 2, 100, 200, "
                    ":center_id, :actor_id)"
                ),
                {
                    "id": purchase_id,
                    "center_id": center_id,
                    "actor_id": actor_id,
                },
            )

        command.upgrade(config, CURRENT_HEAD)
        with postgres_engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT purchase_channel, product_url FROM daily_purchase_records "
                    "WHERE id = :id"
                ),
                {"id": purchase_id},
            ).one() == ("offline", None)
    finally:
        command.upgrade(config, CURRENT_HEAD)
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM daily_purchase_records WHERE id = :id"),
                {"id": purchase_id},
            )
            connection.execute(
                text("DELETE FROM procurement_settlement_centers WHERE id = :id"),
                {"id": center_id},
            )
        if actor_person_id is not None and actor_id is not None:
            _delete_test_account(
                postgres_engine,
                person_id=actor_person_id,
                account_id=actor_id,
            )


def test_0040_backfills_reimbursement_status_and_roundtrips_cleanly(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    center_id = uuid4()
    purchase_id = uuid4()
    actor_person_id: UUID | None = None
    actor_id: UUID | None = None
    try:
        command.downgrade(config, "0039_procurement_product_links")
        columns_before = {
            column["name"]
            for column in inspect(postgres_engine).get_columns("daily_purchase_records")
        }
        assert "is_reimbursed" not in columns_before
        actor_person_id, actor_id = _insert_test_account(postgres_engine)
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO procurement_settlement_centers "
                    "(id, name, created_by_user_id) VALUES (:id, :name, :actor_id)"
                ),
                {
                    "id": center_id,
                    "name": f"0040 migration center {center_id}",
                    "actor_id": actor_id,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO daily_purchase_records "
                    "(id, purchase_date, item_name, quantity, unit_price_cents, "
                    "total_price_cents, settlement_center_id, created_by_user_id) "
                    "VALUES (:id, DATE '2026-08-30', '迁移前未报销采购', 2, 100, 200, "
                    ":center_id, :actor_id)"
                ),
                {
                    "id": purchase_id,
                    "center_id": center_id,
                    "actor_id": actor_id,
                },
            )

        command.upgrade(config, CURRENT_HEAD)
        with postgres_engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT is_reimbursed FROM daily_purchase_records WHERE id = :id"),
                    {"id": purchase_id},
                )
                is False
            )
        assert "ix_daily_purchase_records_reimbursed_date" in {
            index["name"]
            for index in inspect(postgres_engine).get_indexes("daily_purchase_records")
        }
    finally:
        command.upgrade(config, CURRENT_HEAD)
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM daily_purchase_records WHERE id = :id"),
                {"id": purchase_id},
            )
            connection.execute(
                text("DELETE FROM procurement_settlement_centers WHERE id = :id"),
                {"id": center_id},
            )
        if actor_person_id is not None and actor_id is not None:
            _delete_test_account(
                postgres_engine,
                person_id=actor_person_id,
                account_id=actor_id,
            )


def test_0041_adds_reserve_funds_and_backfills_purchase_source(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    center_id = uuid4()
    purchase_id = uuid4()
    entry_id = uuid4()
    actor_person_id: UUID | None = None
    actor_id: UUID | None = None
    try:
        command.downgrade(config, "0040_procurement_reimbursement_status")
        inspector = inspect(postgres_engine)
        assert "procurement_reserve_fund_entries" not in inspector.get_table_names()
        assert "uses_reserve_fund" not in {
            column["name"] for column in inspector.get_columns("daily_purchase_records")
        }
        actor_person_id, actor_id = _insert_test_account(postgres_engine)
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO procurement_settlement_centers "
                    "(id, name, created_by_user_id) VALUES (:id, :name, :actor_id)"
                ),
                {
                    "id": center_id,
                    "name": f"0041 migration center {center_id}",
                    "actor_id": actor_id,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO daily_purchase_records "
                    "(id, purchase_date, item_name, quantity, unit_price_cents, "
                    "total_price_cents, settlement_center_id, created_by_user_id) "
                    "VALUES (:id, DATE '2026-08-30', '迁移前普通资金采购', 1, 500, 500, "
                    ":center_id, :actor_id)"
                ),
                {
                    "id": purchase_id,
                    "center_id": center_id,
                    "actor_id": actor_id,
                },
            )

        command.upgrade(config, CURRENT_HEAD)
        with postgres_engine.begin() as connection:
            assert (
                connection.scalar(
                    text("SELECT uses_reserve_fund FROM daily_purchase_records WHERE id = :id"),
                    {"id": purchase_id},
                )
                is False
            )
            connection.execute(
                text(
                    "INSERT INTO procurement_reserve_fund_entries "
                    "(id, settlement_center_id, amount_cents, created_by_user_id) "
                    "VALUES (:id, :center_id, 10000, :actor_id)"
                ),
                {
                    "id": entry_id,
                    "center_id": center_id,
                    "actor_id": actor_id,
                },
            )
        assert "ix_daily_purchase_records_reserve_fund_center" in {
            index["name"]
            for index in inspect(postgres_engine).get_indexes("daily_purchase_records")
        }
    finally:
        command.upgrade(config, CURRENT_HEAD)
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM procurement_reserve_fund_entries WHERE id = :id"),
                {"id": entry_id},
            )
            connection.execute(
                text("DELETE FROM daily_purchase_records WHERE id = :id"),
                {"id": purchase_id},
            )
            connection.execute(
                text("DELETE FROM procurement_settlement_centers WHERE id = :id"),
                {"id": center_id},
            )
        if actor_person_id is not None and actor_id is not None:
            _delete_test_account(
                postgres_engine,
                person_id=actor_person_id,
                account_id=actor_id,
            )


def test_0042_adds_asset_purchase_and_warranty_details(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    try:
        command.downgrade(config, "0041_procurement_reserve_funds")
        before_columns = {
            column["name"] for column in inspect(postgres_engine).get_columns("assets")
        }
        assert "purchase_date" not in before_columns
        assert "warranty_expires_on" not in before_columns
        assert "source_purchase_id" not in before_columns

        command.upgrade(config, CURRENT_HEAD)
        inspector = inspect(postgres_engine)
        columns = {column["name"] for column in inspector.get_columns("assets")}
        assert {"purchase_date", "warranty_expires_on", "source_purchase_id"} <= columns
        assert "ix_assets_source_purchase" in {
            index["name"] for index in inspector.get_indexes("assets")
        }
        assert "ck_assets_warranty_range" in {
            constraint["name"] for constraint in inspector.get_check_constraints("assets")
        }
        source_foreign_key = next(
            constraint
            for constraint in inspector.get_foreign_keys("assets")
            if constraint["constrained_columns"] == ["source_purchase_id"]
        )
        assert source_foreign_key["referred_table"] == "daily_purchase_records"
        assert source_foreign_key["options"].get("ondelete") == "SET NULL"
    finally:
        command.upgrade(config, CURRENT_HEAD)


def test_0043_adds_operations_heartbeat_and_super_admin_permission(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    try:
        command.downgrade(config, "0042_asset_purchase_details")
        assert "operations_service_heartbeats" not in inspect(postgres_engine).get_table_names()
        with postgres_engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT id FROM permissions WHERE key = 'operations.monitor.read'")
                )
                is None
            )

        command.upgrade(config, CURRENT_HEAD)
        inspector = inspect(postgres_engine)
        assert "operations_service_heartbeats" in inspector.get_table_names()
        assert {
            "ck_operations_service_heartbeats_status",
            "ck_operations_service_heartbeats_failures",
            "ck_operations_service_heartbeats_result_count",
            "ck_operations_service_heartbeats_error_code",
        } <= {
            constraint["name"]
            for constraint in inspector.get_check_constraints("operations_service_heartbeats")
        }
        with postgres_engine.connect() as connection:
            permission = connection.execute(
                text("SELECT id, sensitive FROM permissions WHERE key = 'operations.monitor.read'")
            ).one()
            grants = set(
                connection.execute(
                    text(
                        "SELECT roles.key FROM role_permissions "
                        "JOIN roles ON roles.id = role_permissions.role_id "
                        "WHERE permission_id = :permission_id"
                    ),
                    {"permission_id": permission.id},
                ).scalars()
            )
        assert permission.id == UUID("20000000-0000-4000-8000-000000000071")
        assert permission.sensitive is True
        assert grants == {"super_admin"}
    finally:
        command.upgrade(config, CURRENT_HEAD)


def test_0027_promotes_existing_roles_and_roundtrips_to_0026(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0026_verified_login_identifiers")
    admin_person_id, admin_account_id = _insert_test_account(postgres_engine)
    staff_person_id, staff_account_id = _insert_test_account(postgres_engine)
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO staff_profiles (person_id, staff_type) "
                    "VALUES (:person_id, 'administration')"
                ),
                {"person_id": staff_person_id},
            )
            connection.execute(
                text(
                    "INSERT INTO user_roles (user_id, role_id) "
                    "SELECT :account_id, id FROM roles WHERE key = 'admin'"
                ),
                {"account_id": admin_account_id},
            )
            connection.execute(
                text(
                    "INSERT INTO user_roles (user_id, role_id) "
                    "SELECT :account_id, id FROM roles WHERE key = 'staff'"
                ),
                {"account_id": staff_account_id},
            )

        command.upgrade(config, CURRENT_HEAD)
        with postgres_engine.connect() as connection:
            account_roles = {
                account_id: set(
                    connection.scalars(
                        text(
                            "SELECT roles.key FROM user_roles "
                            "JOIN roles ON roles.id = user_roles.role_id "
                            "WHERE user_roles.user_id = :account_id"
                        ),
                        {"account_id": account_id},
                    )
                )
                for account_id in (admin_account_id, staff_account_id)
            }
            assert account_roles[admin_account_id] == {
                "registered_user",
                "super_admin",
            }
            assert account_roles[staff_account_id] == {
                "registered_user",
                "administration",
            }

            grants = _role_permission_grants(connection)
            assert {
                permission_key
                for role_key, permission_key, scope_kind in grants
                if role_key == "super_admin" and scope_kind == "global"
            } == {
                permission_key
                for (permission_key,) in connection.execute(text("SELECT key FROM permissions"))
                if permission_key not in SELF_SCOPE_ONLY_PERMISSIONS
            }
            assert ("super_admin", "assets.read_self", "self") in grants
            assert {
                permission_key
                for role_key, permission_key, scope_kind in grants
                if role_key == "admin" and scope_kind == "global"
            }.isdisjoint({"permissions.manage", "audit.read", "mail.settings.manage"})
            assert ("admin", "accounts.read", "global") in grants
            # CURRENT_HEAD includes 0066's owner-summary housing boundary.
            assert ("coach", "housing.read", "global") not in grants
            assert ("coach", "personnel_intake.housing.read", "global") in grants

        command.downgrade(config, "0026_verified_login_identifiers")
        with postgres_engine.connect() as connection:
            account_roles = {
                account_id: set(
                    connection.scalars(
                        text(
                            "SELECT roles.key FROM user_roles "
                            "JOIN roles ON roles.id = user_roles.role_id "
                            "WHERE user_roles.user_id = :account_id"
                        ),
                        {"account_id": account_id},
                    )
                )
                for account_id in (admin_account_id, staff_account_id)
            }
            assert account_roles[admin_account_id] == {"registered_user", "admin"}
            assert account_roles[staff_account_id] == {"registered_user", "staff"}
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM roles WHERE key IN ('super_admin', 'administration')"
                    )
                )
                == 0
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM permissions "
                        "WHERE key IN ('accounts.read', 'mail.settings.manage')"
                    )
                )
                == 0
            )
            grants = _role_permission_grants(connection)
            assert ("admin", "permissions.manage", "global") in grants
            assert ("admin", "audit.read", "global") in grants
            assert ("coach", "housing.read", "global") not in grants
    finally:
        command.upgrade(config, CURRENT_HEAD)
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM staff_profiles WHERE person_id = :person_id"),
                {"person_id": staff_person_id},
            )
        _delete_test_account(
            postgres_engine,
            person_id=admin_person_id,
            account_id=admin_account_id,
        )
        _delete_test_account(
            postgres_engine,
            person_id=staff_person_id,
            account_id=staff_account_id,
        )


def test_0030_removes_non_super_admin_audit_grants_and_overrides(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0029_staff_approval_optional_reason")
    person_id, account_id = _insert_test_account(postgres_engine)
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO role_permissions (role_id, permission_id, scope_kind) "
                    "SELECT roles.id, permissions.id, 'global' "
                    "FROM roles CROSS JOIN permissions "
                    "WHERE roles.key = 'admin' AND permissions.key = 'audit.read' "
                    "ON CONFLICT DO NOTHING"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO user_permission_overrides "
                    "(id, user_id, permission_id, effect, scope_kind) "
                    "SELECT :id, :account_id, permissions.id, 'allow', 'global' "
                    "FROM permissions WHERE permissions.key = 'audit.read'"
                ),
                {"id": uuid4(), "account_id": account_id},
            )

        command.upgrade(config, CURRENT_HEAD)
        with postgres_engine.connect() as connection:
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM role_permissions "
                        "JOIN roles ON roles.id = role_permissions.role_id "
                        "JOIN permissions ON permissions.id = role_permissions.permission_id "
                        "WHERE roles.key <> 'super_admin' AND permissions.key = 'audit.read'"
                    )
                )
                == 0
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM user_permission_overrides "
                        "JOIN permissions ON permissions.id = "
                        "user_permission_overrides.permission_id "
                        "WHERE permissions.key = 'audit.read'"
                    )
                )
                == 0
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM role_permissions "
                        "JOIN roles ON roles.id = role_permissions.role_id "
                        "JOIN permissions ON permissions.id = role_permissions.permission_id "
                        "WHERE roles.key = 'super_admin' AND permissions.key = 'audit.read'"
                    )
                )
                == 1
            )
    finally:
        command.upgrade(config, CURRENT_HEAD)
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )


def test_registration_application_purpose_constraints_are_enforced(
    postgres_engine: Engine,
) -> None:
    valid_rows = (
        ("draft", "student", None, None, None),
        ("draft", "guardian", None, None, None),
        ("draft", "staff_applicant", None, None, None),
        ("contact_verified", "visitor", None, None, None),
        ("identity_completed", "staff_applicant", "coach", "申请担任教练", None),
        ("identity_completed", "visitor", None, None, "探访学员"),
        ("ready_to_finalize", "student", None, None, None),
        ("finalized", "guardian", None, None, None),
        ("finalized", "staff_applicant", None, None, None),
        ("finalized", "visitor", None, None, None),
        ("ready_to_finalize", "visitor", None, None, "探访学员"),
        ("finalized", "staff_applicant", "coach", "申请担任教练", None),
        ("conflict", "staff_applicant", "coach", "申请担任教练", None),
        ("conflict", "visitor", None, None, "探访学员"),
        ("rejected", "visitor", None, None, None),
        ("expired", "staff_applicant", None, None, None),
    )
    invalid_rows = (
        ("identity_completed", "staff_applicant", None, None, None),
        ("identity_completed", "visitor", None, None, None),
        ("identity_completed", "staff_applicant", "coach", "\t\t", None),
        ("identity_completed", "visitor", None, None, "\t\t"),
        ("ready_to_finalize", "staff_applicant", None, None, None),
        ("ready_to_finalize", "visitor", None, None, None),
        ("conflict", "staff_applicant", None, None, None),
        ("conflict", "visitor", None, None, None),
        ("identity_completed", "staff_applicant", None, "申请担任教练", None),
        ("identity_completed", "staff_applicant", "invalid", "申请担任教练", None),
        ("identity_completed", "staff_applicant", "coach", "短", None),
        ("identity_completed", "staff_applicant", "coach", "原" * 501, None),
        ("identity_completed", "staff_applicant", "coach", " 申请担任教练", None),
        (
            "identity_completed",
            "staff_applicant",
            "coach",
            "申请担任教练",
            "不应存在",
        ),
        ("identity_completed", "visitor", "coach", None, "探访学员"),
        ("identity_completed", "visitor", None, None, "短"),
        ("identity_completed", "visitor", None, None, "访" * 201),
        ("identity_completed", "visitor", None, None, "探访学员 "),
        ("identity_completed", "student", None, None, "不应存在"),
        ("identity_completed", "guardian", "coach", "不应存在", None),
    )
    attempted_ids: list[UUID] = []

    def insert_row(
        connection: Connection,
        values: tuple[str, str, str | None, str | None, str | None],
    ) -> UUID:
        row_id = uuid4()
        attempted_ids.append(row_id)
        state, purpose, staff_type, staff_reason, visit_reason = values
        finalization_match_kind = "new_person" if state == "finalized" else None
        connection.execute(
            text(
                "INSERT INTO registration_applications "
                "(id, primary_purpose, state, requested_staff_type, "
                "staff_application_reason, visit_reason, finalization_match_kind, "
                "expires_at) VALUES "
                "(:id, :purpose, :state, :staff_type, :staff_reason, "
                ":visit_reason, :finalization_match_kind, "
                "CURRENT_TIMESTAMP + INTERVAL '30 days')"
            ),
            {
                "id": row_id,
                "state": state,
                "purpose": purpose,
                "staff_type": staff_type,
                "staff_reason": staff_reason,
                "visit_reason": visit_reason,
                "finalization_match_kind": finalization_match_kind,
            },
        )
        return row_id

    try:
        with postgres_engine.begin() as connection:
            for values in valid_rows:
                insert_row(connection, values)

        for values in invalid_rows:
            with pytest.raises(DBAPIError), postgres_engine.begin() as connection:
                insert_row(connection, values)
    finally:
        if attempted_ids:
            with postgres_engine.begin() as connection:
                connection.execute(
                    text(
                        "DELETE FROM registration_applications WHERE id = ANY(CAST(:ids AS uuid[]))"
                    ),
                    {"ids": attempted_ids},
                )


def test_registration_finalization_match_kind_constraint_is_enforced(
    postgres_engine: Engine,
) -> None:
    invalid_rows = (
        ("finalized", None),
        ("ready_to_finalize", "new_person"),
        ("finalized", "invalid"),
    )
    for state, match_kind in invalid_rows:
        with pytest.raises(DBAPIError), postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO registration_applications "
                    "(id, primary_purpose, state, finalization_match_kind, expires_at) "
                    "VALUES (:id, 'student', :state, :match_kind, "
                    "CURRENT_TIMESTAMP + INTERVAL '30 days')"
                ),
                {
                    "id": uuid4(),
                    "state": state,
                    "match_kind": match_kind,
                },
            )


def test_registration_application_review_constraints_are_enforced(
    postgres_engine: Engine,
) -> None:
    person_id, reviewer_id = _insert_test_account(postgres_engine)
    inserted_ids: list[UUID] = []

    def insert_review(
        connection: Connection,
        *,
        state: str,
        decision: str | None,
        reviewed_by_user_id: UUID | None,
        candidate_person_id: UUID | None,
        reason: str | None,
        reviewed: bool,
    ) -> None:
        application_id = uuid4()
        inserted_ids.append(application_id)
        connection.execute(
            text(
                "INSERT INTO registration_applications "
                "(id, primary_purpose, state, review_decision, "
                "reviewed_by_user_id, reviewed_candidate_person_id, "
                "review_reason, reviewed_at, expires_at) "
                "VALUES (:id, 'student', :state, :decision, :reviewer_id, "
                ":candidate_id, "
                ":reason, CASE WHEN :reviewed THEN CURRENT_TIMESTAMP ELSE NULL END, "
                "CURRENT_TIMESTAMP + INTERVAL '30 days')"
            ),
            {
                "id": application_id,
                "state": state,
                "decision": decision,
                "reviewer_id": reviewed_by_user_id,
                "candidate_id": candidate_person_id,
                "reason": reason,
                "reviewed": reviewed,
            },
        )

    valid_rows = (
        ("draft", None, None, None, None, False),
        (
            "pending_guardian",
            "approved",
            reviewer_id,
            person_id,
            "冲突已核验等待监护确认",
            True,
        ),
        ("ready_to_finalize", "approved", reviewer_id, None, "资料审核通过", True),
        ("conflict", "approved", reviewer_id, None, "冲突已人工核验", True),
        ("rejected", "rejected", reviewer_id, None, "身份资料不一致", True),
    )
    invalid_rows = (
        ("draft", "approved", reviewer_id, None, "资料审核通过", True),
        ("ready_to_finalize", "rejected", reviewer_id, None, "资料审核拒绝", True),
        ("rejected", "approved", reviewer_id, None, "资料审核通过", True),
        ("ready_to_finalize", None, reviewer_id, None, "资料审核通过", True),
        ("ready_to_finalize", "approved", None, None, "资料审核通过", True),
        ("ready_to_finalize", "approved", reviewer_id, None, None, True),
        ("ready_to_finalize", "approved", reviewer_id, None, "资料审核通过", False),
        ("ready_to_finalize", "invalid", reviewer_id, None, "资料审核通过", True),
        ("ready_to_finalize", "approved", reviewer_id, None, "短", True),
        ("ready_to_finalize", "approved", reviewer_id, None, "\t\t", True),
        ("ready_to_finalize", "approved", reviewer_id, None, " 资料审核通过", True),
        ("ready_to_finalize", "approved", reviewer_id, None, "审" * 501, True),
    )

    try:
        with postgres_engine.begin() as connection:
            for state, decision, reviewer, candidate, reason, reviewed in valid_rows:
                insert_review(
                    connection,
                    state=state,
                    decision=decision,
                    reviewed_by_user_id=reviewer,
                    candidate_person_id=candidate,
                    reason=reason,
                    reviewed=reviewed,
                )

        for state, decision, reviewer, candidate, reason, reviewed in invalid_rows:
            with pytest.raises(DBAPIError), postgres_engine.begin() as connection:
                insert_review(
                    connection,
                    state=state,
                    decision=decision,
                    reviewed_by_user_id=reviewer,
                    candidate_person_id=candidate,
                    reason=reason,
                    reviewed=reviewed,
                )
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM registration_applications WHERE id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": inserted_ids},
            )
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=reviewer_id,
        )


def test_registration_application_review_candidate_binding_is_enforced(
    postgres_engine: Engine,
) -> None:
    candidate_person_id, reviewer_id = _insert_test_account(postgres_engine)
    inserted_ids: list[UUID] = []

    def insert_binding(
        connection: Connection,
        *,
        state: str,
        decision: str | None,
        candidate_id: UUID | None,
    ) -> None:
        application_id = uuid4()
        inserted_ids.append(application_id)
        reviewed = decision is not None
        connection.execute(
            text(
                "INSERT INTO registration_applications "
                "(id, primary_purpose, state, review_decision, "
                "reviewed_by_user_id, reviewed_candidate_person_id, "
                "review_reason, reviewed_at, expires_at) VALUES "
                "(:id, 'student', :state, :decision, "
                "CASE WHEN :reviewed THEN :reviewer_id ELSE NULL END, "
                ":candidate_id, "
                "CASE WHEN :reviewed THEN '候选人员审核结果' ELSE NULL END, "
                "CASE WHEN :reviewed THEN CURRENT_TIMESTAMP ELSE NULL END, "
                "CURRENT_TIMESTAMP + INTERVAL '30 days')"
            ),
            {
                "id": application_id,
                "state": state,
                "decision": decision,
                "reviewed": reviewed,
                "reviewer_id": reviewer_id,
                "candidate_id": candidate_id,
            },
        )

    valid_rows = (
        ("pending_guardian", None, None),
        ("pending_guardian", "approved", candidate_person_id),
        ("ready_to_finalize", "approved", candidate_person_id),
        ("ready_to_finalize", "approved", None),
        ("rejected", "rejected", None),
    )
    invalid_rows = (
        ("pending_guardian", "approved", None),
        ("pending_guardian", None, candidate_person_id),
        ("conflict", "approved", candidate_person_id),
        ("rejected", "rejected", candidate_person_id),
        ("pending_guardian", "approved", uuid4()),
    )

    try:
        with postgres_engine.begin() as connection:
            for state, decision, candidate_id in valid_rows:
                insert_binding(
                    connection,
                    state=state,
                    decision=decision,
                    candidate_id=candidate_id,
                )

        for state, decision, candidate_id in invalid_rows:
            with pytest.raises(DBAPIError), postgres_engine.begin() as connection:
                insert_binding(
                    connection,
                    state=state,
                    decision=decision,
                    candidate_id=candidate_id,
                )
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM registration_applications WHERE id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": inserted_ids},
            )
        _delete_test_account(
            postgres_engine,
            person_id=candidate_person_id,
            account_id=reviewer_id,
        )


def test_0018_adds_registration_review_metadata_without_rewriting_0017(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    review_columns = {
        "review_decision",
        "reviewed_by_user_id",
        "review_reason",
        "reviewed_at",
    }
    try:
        command.downgrade(config, "0016_camp_evaluation_surveys")
        command.upgrade(config, "0017_self_service_registration")
        assert review_columns.isdisjoint(
            {
                column["name"]
                for column in inspect(postgres_engine).get_columns("registration_applications")
            }
        )

        command.upgrade(config, "0018_registration_review_metadata")
        assert review_columns <= {
            column["name"]
            for column in inspect(postgres_engine).get_columns("registration_applications")
        }

        command.downgrade(config, "0017_self_service_registration")
        assert review_columns.isdisjoint(
            {
                column["name"]
                for column in inspect(postgres_engine).get_columns("registration_applications")
            }
        )
    finally:
        command.upgrade(config, CURRENT_HEAD)


def test_0018_downgrade_refuses_review_history_before_ddl(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    person_id, reviewer_id = _insert_test_account(postgres_engine)
    application_id = uuid4()
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO registration_applications "
                    "(id, primary_purpose, state, review_decision, "
                    "reviewed_by_user_id, review_reason, reviewed_at, expires_at) "
                    "VALUES (:id, 'student', 'rejected', 'rejected', "
                    ":reviewer_id, '人工审核拒绝', CURRENT_TIMESTAMP, "
                    "CURRENT_TIMESTAMP + INTERVAL '30 days')"
                ),
                {"id": application_id, "reviewer_id": reviewer_id},
            )

        with pytest.raises(
            RuntimeError,
            match="cannot downgrade registration review metadata with review history",
        ):
            command.downgrade(config, "0017_self_service_registration")

        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                CURRENT_HEAD
            )
            assert (
                connection.scalar(
                    text("SELECT review_reason FROM registration_applications WHERE id = :id"),
                    {"id": application_id},
                )
                == "人工审核拒绝"
            )
    finally:
        command.upgrade(config, CURRENT_HEAD)
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM registration_applications WHERE id = :id"),
                {"id": application_id},
            )
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=reviewer_id,
        )


def test_0019_invalidates_legacy_pending_links_and_protects_downgrade(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    invitation_id = uuid4()
    person_id: UUID | None = None
    account_id: UUID | None = None
    try:
        command.downgrade(config, "0018_registration_review_metadata")
        person_id, account_id = _insert_test_account(postgres_engine)
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO guardian_registration_invitations "
                    "(id, student_person_id, invited_identifier_kind, "
                    "invited_identifier_digest, invited_identifier_ciphertext, "
                    "relationship, is_primary, status, expires_at) VALUES "
                    "(:id, :student_id, 'phone', :digest, :ciphertext, "
                    "'监护人', false, 'pending', "
                    "CURRENT_TIMESTAMP + INTERVAL '7 days')"
                ),
                {
                    "id": invitation_id,
                    "student_id": person_id,
                    "digest": "legacy-invitation-digest",
                    "ciphertext": "legacy-invitation-ciphertext",
                },
            )

        command.upgrade(config, CURRENT_HEAD)
        with postgres_engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT status, handoff_token_digest, "
                    "invited_identifier_digest, invited_identifier_ciphertext "
                    "FROM guardian_registration_invitations WHERE id = :id"
                ),
                {"id": invitation_id},
            ).one() == (
                "revoked",
                None,
                "redacted:guardian-invitation:v1",
                "redacted:guardian-invitation:v1",
            )

        with pytest.raises(
            RuntimeError,
            match="cannot downgrade guardian invitation token isolation",
        ):
            command.downgrade(config, "0018_registration_review_metadata")

        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM guardian_registration_invitations WHERE id = :id"),
                {"id": invitation_id},
            )
        command.downgrade(config, "0018_registration_review_metadata")
        assert "handoff_token_digest" not in {
            column["name"]
            for column in inspect(postgres_engine).get_columns("guardian_registration_invitations")
        }
    finally:
        command.upgrade(config, CURRENT_HEAD)
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM guardian_registration_invitations WHERE id = :id"),
                {"id": invitation_id},
            )
        if person_id is not None and account_id is not None:
            _delete_test_account(
                postgres_engine,
                person_id=person_id,
                account_id=account_id,
            )


def test_0019_repairs_committed_0018_schema_and_clears_all_invitation_contacts(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    invitation_ids = {status: uuid4() for status in ("pending", "accepted", "revoked", "expired")}
    person_id: UUID | None = None
    account_id: UUID | None = None
    try:
        command.downgrade(config, "0018_registration_review_metadata")
        person_id, account_id = _insert_test_account(postgres_engine)
        with postgres_engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE people DROP CONSTRAINT IF EXISTS ck_people_legacy_email_cleared")
            )
            connection.execute(
                text(
                    "DROP INDEX uq_registration_applications_active_identifier; "
                    "CREATE UNIQUE INDEX "
                    "uq_registration_applications_active_identifier "
                    "ON registration_applications (verified_identifier_digest) "
                    "WHERE verified_identifier_digest IS NOT NULL AND state IN "
                    "('draft', 'contact_verified', 'identity_completed', "
                    "'pending_guardian', 'ready_to_finalize', 'conflict')"
                )
            )
            connection.execute(
                text(
                    """
                    CREATE OR REPLACE FUNCTION enforce_account_login_identifier_primary()
                    RETURNS trigger
                    LANGUAGE plpgsql
                    AS $$
                    DECLARE
                        target_account_id uuid;
                        account_ids uuid[];
                        identifier_count bigint;
                        primary_count bigint;
                    BEGIN
                        IF TG_TABLE_NAME = 'user_accounts' THEN
                            account_ids := ARRAY[NEW.id];
                        ELSIF TG_OP = 'INSERT' THEN
                            account_ids := ARRAY[NEW.account_id];
                        ELSIF TG_OP = 'DELETE' THEN
                            account_ids := ARRAY[OLD.account_id];
                        ELSE
                            account_ids := ARRAY[NEW.account_id, OLD.account_id];
                        END IF;
                        FOREACH target_account_id IN ARRAY account_ids LOOP
                            IF target_account_id IS NULL OR NOT EXISTS (
                                SELECT 1 FROM user_accounts
                                WHERE id = target_account_id
                                  AND (
                                      self_service_primary_purpose IS NOT NULL
                                      OR login_phone_digest IS NULL
                                  )
                            ) THEN
                                CONTINUE;
                            END IF;
                            SELECT count(*), count(*) FILTER (WHERE is_primary)
                            INTO identifier_count, primary_count
                            FROM account_login_identifiers
                            WHERE account_id = target_account_id;
                            IF identifier_count < 1 OR primary_count <> 1 THEN
                                RAISE EXCEPTION USING
                                    ERRCODE = '23514',
                                    CONSTRAINT = 'ck_account_login_identifiers_primary',
                                    MESSAGE = 'account must have exactly one primary login identifier';
                            END IF;
                        END LOOP;
                        RETURN NULL;
                    END;
                    $$
                    """
                )
            )
            for status, invitation_id in invitation_ids.items():
                connection.execute(
                    text(
                        "INSERT INTO guardian_registration_invitations "
                        "(id, student_person_id, invited_identifier_kind, "
                        "invited_identifier_digest, invited_identifier_ciphertext, "
                        "relationship, is_primary, status, expires_at, "
                        "accepted_by_person_id, accepted_at, revoked_at) VALUES "
                        "(:id, :student_id, 'phone', :digest, :ciphertext, "
                        "'监护人', false, CAST(:status AS varchar), "
                        "CURRENT_TIMESTAMP + INTERVAL '7 days', "
                        ":accepted_by, "
                        "CASE WHEN CAST(:status AS varchar) = 'accepted' "
                        "THEN CURRENT_TIMESTAMP ELSE NULL END, "
                        "CASE WHEN CAST(:status AS varchar) = 'revoked' "
                        "THEN CURRENT_TIMESTAMP ELSE NULL END)"
                    ),
                    {
                        "id": invitation_id,
                        "student_id": person_id,
                        "digest": f"legacy-{status}-digest",
                        "ciphertext": f"legacy-{status}-ciphertext",
                        "status": status,
                        "accepted_by": person_id if status == "accepted" else None,
                    },
                )

        command.upgrade(config, "0019_guardian_invitation_tokens")
        with postgres_engine.connect() as connection:
            assert "ck_people_legacy_email_cleared" in {
                constraint["name"]
                for constraint in inspect(connection).get_check_constraints("people")
            }
            index_definition = connection.scalar(
                text(
                    "SELECT pg_get_indexdef("
                    "'uq_registration_applications_active_identifier'::regclass)"
                )
            )
            assert index_definition is not None
            assert "'draft'" not in index_definition
            assert "'contact_verified'" in index_definition
            function_definition = connection.scalar(
                text(
                    "SELECT pg_get_functiondef("
                    "'enforce_account_login_identifier_primary()'::regprocedure)"
                )
            )
            assert function_definition is not None
            assert "self_service_primary_purpose IS NOT NULL" not in function_definition
            rows = connection.execute(
                text(
                    "SELECT id, status, handoff_token_digest, "
                    "invited_identifier_digest, invited_identifier_ciphertext "
                    "FROM guardian_registration_invitations "
                    "WHERE id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": list(invitation_ids.values())},
            ).all()
            assert len(rows) == 4
            rows_by_id = {row.id: row for row in rows}
            for original_status, invitation_id in invitation_ids.items():
                row = rows_by_id[invitation_id]
                assert row.status == (
                    "revoked" if original_status == "pending" else original_status
                )
                assert row.handoff_token_digest is None
                assert row.invited_identifier_digest == "redacted:guardian-invitation:v1"
                assert row.invited_identifier_ciphertext == "redacted:guardian-invitation:v1"
    finally:
        command.upgrade(config, CURRENT_HEAD)
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM guardian_registration_invitations "
                    "WHERE id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": list(invitation_ids.values())},
            )
        if person_id is not None and account_id is not None:
            _delete_test_account(
                postgres_engine,
                person_id=person_id,
                account_id=account_id,
            )


def test_0020_allows_approved_pending_guardian_and_restores_old_constraint(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    application_id = uuid4()
    person_id: UUID | None = None
    reviewer_id: UUID | None = None
    params: dict[str, UUID] = {"id": application_id}
    insert_sql = text(
        "INSERT INTO registration_applications "
        "(id, primary_purpose, state, review_decision, reviewed_by_user_id, "
        "review_reason, reviewed_at, expires_at) VALUES "
        "(:id, 'student', 'pending_guardian', 'approved', :reviewer_id, "
        "'冲突已核验等待监护确认', CURRENT_TIMESTAMP, "
        "CURRENT_TIMESTAMP + INTERVAL '30 days')"
    )
    try:
        command.downgrade(config, "0019_guardian_invitation_tokens")
        command.upgrade(config, "0020_registration_review_pending_guardian")
        person_id, reviewer_id = _insert_test_account(postgres_engine)
        params["reviewer_id"] = reviewer_id

        with postgres_engine.begin() as connection:
            connection.execute(insert_sql, params)
        with postgres_engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT state, review_decision, reviewed_by_user_id, "
                    "review_reason, reviewed_at IS NOT NULL "
                    "FROM registration_applications WHERE id = :id"
                ),
                {"id": application_id},
            ).one() == (
                "pending_guardian",
                "approved",
                reviewer_id,
                "冲突已核验等待监护确认",
                True,
            )

        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM registration_applications WHERE id = :id"),
                {"id": application_id},
            )
        command.downgrade(config, "0019_guardian_invitation_tokens")
        with pytest.raises(DBAPIError), postgres_engine.begin() as connection:
            connection.execute(insert_sql, params)
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM registration_applications WHERE id = :id"),
                {"id": application_id},
            )
        command.upgrade(config, "head")
        if person_id is not None and reviewer_id is not None:
            _delete_test_account(
                postgres_engine,
                person_id=person_id,
                account_id=reviewer_id,
            )


def test_0020_downgrade_refuses_approved_pending_guardian_before_ddl(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    application_id = uuid4()
    person_id: UUID | None = None
    reviewer_id: UUID | None = None
    try:
        command.downgrade(config, "0020_registration_review_pending_guardian")
        person_id, reviewer_id = _insert_test_account(postgres_engine)
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO registration_applications "
                    "(id, primary_purpose, state, review_decision, "
                    "reviewed_by_user_id, review_reason, reviewed_at, expires_at) "
                    "VALUES (:id, 'student', 'pending_guardian', 'approved', "
                    ":reviewer_id, '冲突已核验等待监护确认', CURRENT_TIMESTAMP, "
                    "CURRENT_TIMESTAMP + INTERVAL '30 days')"
                ),
                {"id": application_id, "reviewer_id": reviewer_id},
            )

        with pytest.raises(
            RuntimeError,
            match=(
                "cannot downgrade registration review guardian gate with "
                "approved pending guardian applications"
            ),
        ):
            command.downgrade(config, "0019_guardian_invitation_tokens")

        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0020_registration_review_pending_guardian"
            )
            assert connection.execute(
                text(
                    "SELECT state, review_decision, reviewed_by_user_id, "
                    "review_reason, reviewed_at IS NOT NULL "
                    "FROM registration_applications WHERE id = :id"
                ),
                {"id": application_id},
            ).one() == (
                "pending_guardian",
                "approved",
                reviewer_id,
                "冲突已核验等待监护确认",
                True,
            )
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM registration_applications WHERE id = :id"),
                {"id": application_id},
            )
        command.upgrade(config, "head")
        if person_id is not None and reviewer_id is not None:
            _delete_test_account(
                postgres_engine,
                person_id=person_id,
                account_id=reviewer_id,
            )


def test_0021_adds_review_candidate_binding_and_restores_0020(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    try:
        command.downgrade(config, "0020_registration_review_pending_guardian")
        inspector = inspect(postgres_engine)
        assert "reviewed_candidate_person_id" not in {
            column["name"] for column in inspector.get_columns("registration_applications")
        }

        command.upgrade(config, CURRENT_HEAD)
        inspector = inspect(postgres_engine)
        assert "reviewed_candidate_person_id" in {
            column["name"] for column in inspector.get_columns("registration_applications")
        }
        assert "ck_registration_applications_review_candidate" in {
            constraint["name"]
            for constraint in inspector.get_check_constraints("registration_applications")
        }
        candidate_foreign_keys = [
            foreign_key
            for foreign_key in inspector.get_foreign_keys("registration_applications")
            if foreign_key["constrained_columns"] == ["reviewed_candidate_person_id"]
        ]
        assert len(candidate_foreign_keys) == 1
        assert candidate_foreign_keys[0]["referred_table"] == "people"
        assert candidate_foreign_keys[0]["referred_columns"] == ["id"]
        assert candidate_foreign_keys[0]["options"] == {"ondelete": "RESTRICT"}

        command.downgrade(config, "0020_registration_review_pending_guardian")
        assert "reviewed_candidate_person_id" not in {
            column["name"]
            for column in inspect(postgres_engine).get_columns("registration_applications")
        }
    finally:
        command.upgrade(config, "head")


def test_0021_upgrade_refuses_unbound_approved_pending_guardian_before_ddl(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    application_id = uuid4()
    person_id: UUID | None = None
    reviewer_id: UUID | None = None
    try:
        command.downgrade(config, "0020_registration_review_pending_guardian")
        person_id, reviewer_id = _insert_test_account(postgres_engine)
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO registration_applications "
                    "(id, primary_purpose, state, review_decision, "
                    "reviewed_by_user_id, review_reason, reviewed_at, expires_at) "
                    "VALUES (:id, 'student', 'pending_guardian', 'approved', "
                    ":reviewer_id, '待补充审核候选绑定', CURRENT_TIMESTAMP, "
                    "CURRENT_TIMESTAMP + INTERVAL '30 days')"
                ),
                {"id": application_id, "reviewer_id": reviewer_id},
            )

        with pytest.raises(
            RuntimeError,
            match=(
                "cannot add registration review candidate binding with "
                "unbound approved pending guardian applications"
            ),
        ):
            command.upgrade(config, CURRENT_HEAD)

        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0020_registration_review_pending_guardian"
            )
            assert "reviewed_candidate_person_id" not in {
                column["name"]
                for column in inspect(connection).get_columns("registration_applications")
            }
            assert connection.execute(
                text(
                    "SELECT state, review_decision, reviewed_by_user_id, "
                    "review_reason FROM registration_applications WHERE id = :id"
                ),
                {"id": application_id},
            ).one() == (
                "pending_guardian",
                "approved",
                reviewer_id,
                "待补充审核候选绑定",
            )
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM registration_applications WHERE id = :id"),
                {"id": application_id},
            )
        command.upgrade(config, "head")
        if person_id is not None and reviewer_id is not None:
            _delete_test_account(
                postgres_engine,
                person_id=person_id,
                account_id=reviewer_id,
            )


def test_0021_downgrade_refuses_active_review_candidate_binding_before_ddl(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    application_id = uuid4()
    person_id: UUID | None = None
    reviewer_id: UUID | None = None
    try:
        command.upgrade(config, CURRENT_HEAD)
        person_id, reviewer_id = _insert_test_account(postgres_engine)
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO registration_applications "
                    "(id, primary_purpose, state, review_decision, "
                    "reviewed_by_user_id, reviewed_candidate_person_id, "
                    "review_reason, reviewed_at, expires_at) VALUES "
                    "(:id, 'student', 'pending_guardian', 'approved', "
                    ":reviewer_id, :candidate_id, '候选绑定降级保护', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP + INTERVAL '30 days')"
                ),
                {
                    "id": application_id,
                    "reviewer_id": reviewer_id,
                    "candidate_id": person_id,
                },
            )

        with pytest.raises(
            RuntimeError,
            match=(
                "cannot downgrade registration review candidate binding with "
                "active candidate bindings"
            ),
        ):
            command.downgrade(
                config,
                "0020_registration_review_pending_guardian",
            )

        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                CURRENT_HEAD
            )
            assert connection.execute(
                text(
                    "SELECT state, review_decision, reviewed_by_user_id, "
                    "reviewed_candidate_person_id, review_reason "
                    "FROM registration_applications WHERE id = :id"
                ),
                {"id": application_id},
            ).one() == (
                "pending_guardian",
                "approved",
                reviewer_id,
                person_id,
                "候选绑定降级保护",
            )
    finally:
        command.upgrade(config, "head")
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM registration_applications WHERE id = :id"),
                {"id": application_id},
            )
        if person_id is not None and reviewer_id is not None:
            _delete_test_account(
                postgres_engine,
                person_id=person_id,
                account_id=reviewer_id,
            )


def test_0021_downgrade_locks_review_candidate_binding_against_concurrent_write(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from collections.abc import Callable
    from os import PathLike
    from types import ModuleType
    from typing import cast

    from alembic import util

    config = _alembic_config(postgres_engine)
    candidate_person_id, reviewer_id = _insert_test_account(postgres_engine)
    application_id = uuid4()
    preflight_locked = Event()
    release_downgrade = Event()
    original_load_python_file = util.load_python_file

    def pausing_load_python_file(
        dir_: str | PathLike[str],
        filename: str | PathLike[str],
    ) -> ModuleType:
        module = original_load_python_file(dir_, filename)
        if Path(filename).name != "0021_registration_review_candidate_binding.py":
            return module
        original_preflight = cast(
            Callable[[], None],
            module.__dict__["_preflight_downgrade"],
        )

        def paused_preflight() -> None:
            original_preflight()
            preflight_locked.set()
            if not release_downgrade.wait(timeout=5):
                raise TimeoutError("candidate binding downgrade was not released")

        module.__dict__["_preflight_downgrade"] = paused_preflight
        return module

    monkeypatch.setattr(util, "load_python_file", pausing_load_python_file)
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(
        command.downgrade,
        config,
        "0020_registration_review_pending_guardian",
    )
    try:
        assert preflight_locked.wait(timeout=5), "downgrade did not lock the table"
        with pytest.raises(DBAPIError, match="lock timeout"), postgres_engine.begin() as connection:
            connection.execute(text("SET LOCAL lock_timeout = '200ms'"))
            connection.execute(
                text(
                    "INSERT INTO registration_applications "
                    "(id, primary_purpose, state, review_decision, "
                    "reviewed_by_user_id, reviewed_candidate_person_id, "
                    "review_reason, reviewed_at, expires_at) VALUES "
                    "(:id, 'student', 'pending_guardian', 'approved', "
                    ":reviewer_id, :candidate_id, '并发候选绑定', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP + INTERVAL '30 days')"
                ),
                {
                    "id": application_id,
                    "reviewer_id": reviewer_id,
                    "candidate_id": candidate_person_id,
                },
            )
        release_downgrade.set()
        future.result(timeout=10)
        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0020_registration_review_pending_guardian"
            )
            assert (
                connection.scalar(
                    text("SELECT EXISTS (SELECT 1 FROM registration_applications WHERE id = :id)"),
                    {"id": application_id},
                )
                is False
            )
    finally:
        release_downgrade.set()
        with suppress(DBAPIError, AssertionError, TimeoutError):
            future.result(timeout=10)
        pool.shutdown(wait=True)
        command.upgrade(config, "head")
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM registration_applications WHERE id = :id"),
                {"id": application_id},
            )
        _delete_test_account(
            postgres_engine,
            person_id=candidate_person_id,
            account_id=reviewer_id,
        )


def test_0022_uses_partial_standard_cuisine_uniqueness_and_protects_downgrade(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    person_id: UUID | None = None
    account_id: UUID | None = None
    other_option_id = UUID("60000000-0000-4000-8000-000000000001")
    try:
        with postgres_engine.connect() as connection:
            index_definition = connection.scalar(
                text(
                    "SELECT pg_get_indexdef(to_regclass("
                    "'uq_person_food_preference_cuisines_standard_option'))"
                )
            )
            assert index_definition is not None
            assert "custom_name IS NULL" in index_definition

        person_id, account_id = _insert_test_account(postgres_engine)
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO person_food_preferences "
                    "(person_id, version, source, updated_by_user_id) "
                    "VALUES (:person_id, 1, 'profile_self', :account_id)"
                ),
                {"person_id": person_id, "account_id": account_id},
            )
            for position, name in enumerate(("川菜", "粤菜"), start=1):
                connection.execute(
                    text(
                        "INSERT INTO person_food_preference_cuisines "
                        "(person_id, position, cuisine_option_id, "
                        "cuisine_name_snapshot, custom_name, "
                        "custom_normalized_name) VALUES "
                        "(:person_id, :position, :option_id, '其他', :name, :name)"
                    ),
                    {
                        "person_id": person_id,
                        "position": position,
                        "option_id": other_option_id,
                        "name": name,
                    },
                )

        with pytest.raises(
            RuntimeError,
            match="cannot downgrade multiple custom cuisines with repeated options",
        ):
            command.downgrade(
                config,
                "0021_registration_review_candidate_binding",
            )

        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                CURRENT_HEAD
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM person_food_preference_cuisines "
                        "WHERE person_id = :person_id"
                    ),
                    {"person_id": person_id},
                )
                == 2
            )
    finally:
        command.upgrade(config, "head")
        if person_id is not None:
            with postgres_engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM person_food_preferences WHERE person_id = :person_id"),
                    {"person_id": person_id},
                )
        if person_id is not None and account_id is not None:
            _delete_test_account(
                postgres_engine,
                person_id=person_id,
                account_id=account_id,
            )


def test_staff_identity_application_reason_constraints_are_enforced(
    postgres_engine: Engine,
) -> None:
    person_id, account_id = _insert_test_account(postgres_engine)
    application_id = uuid4()
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO staff_identity_applications "
                    "(id, person_id, requested_staff_type, application_reason) "
                    "VALUES (:id, :person_id, 'coach', '申请担任教练')"
                ),
                {"id": application_id, "person_id": person_id},
            )
        invalid_reasons = (
            ("\t\t", "life_teacher"),
            ("x" * 501, "customer_service"),
            (" 申请担任教练", "administration"),
        )
        for reason, staff_type in invalid_reasons:
            with pytest.raises(DBAPIError), postgres_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO staff_identity_applications "
                        "(id, person_id, requested_staff_type, application_reason) "
                        "VALUES (:id, :person_id, :staff_type, :reason)"
                    ),
                    {
                        "id": uuid4(),
                        "person_id": person_id,
                        "staff_type": staff_type,
                        "reason": reason,
                    },
                )
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM staff_identity_applications WHERE id = :id"),
                {"id": application_id},
            )
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )


def test_0029_allows_approval_without_reason_but_keeps_rejection_reason_required(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0028_registration_channel_settings")
    person_id, account_id = _insert_test_account(postgres_engine)
    approved_id = uuid4()
    try:
        command.upgrade(config, CURRENT_HEAD)
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO staff_identity_applications "
                    "(id, person_id, requested_staff_type, application_reason, status, "
                    "reviewed_by_user_id, reviewed_at) VALUES "
                    "(:id, :person_id, 'coach', '申请担任教练', 'approved', "
                    ":account_id, CURRENT_TIMESTAMP)"
                ),
                {
                    "id": approved_id,
                    "person_id": person_id,
                    "account_id": account_id,
                },
            )
        with pytest.raises(DBAPIError), postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO staff_identity_applications "
                    "(id, person_id, requested_staff_type, application_reason, status, "
                    "reviewed_by_user_id, reviewed_at) VALUES "
                    "(:id, :person_id, 'life_teacher', '申请担任生活老师', 'rejected', "
                    ":account_id, CURRENT_TIMESTAMP)"
                ),
                {
                    "id": uuid4(),
                    "person_id": person_id,
                    "account_id": account_id,
                },
            )

        command.downgrade(config, "0028_registration_channel_settings")
        with postgres_engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT review_reason FROM staff_identity_applications WHERE id = :id"),
                    {"id": approved_id},
                )
                == "审核通过"
            )
    finally:
        command.upgrade(config, "head")
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM staff_identity_applications WHERE id = :id"),
                {"id": approved_id},
            )
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )


def test_0026_removes_primary_login_invariant(postgres_engine: Engine) -> None:
    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0025_password_reset_mail_template")
    person_id, account_id = _insert_test_account(postgres_engine)
    email_identifier_id = uuid4()
    try:
        command.upgrade(config, CURRENT_HEAD)
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE account_login_identifiers SET is_primary = false "
                    "WHERE account_id = :account_id"
                ),
                {"account_id": account_id},
            )
            connection.execute(
                text(
                    "INSERT INTO account_login_identifiers "
                    "(id, account_id, kind, identifier_digest, verified_at, "
                    "verification_source, is_primary) VALUES "
                    "(:id, :account_id, 'email', :digest, CURRENT_TIMESTAMP, "
                    "'test_verified', false)"
                ),
                {
                    "id": email_identifier_id,
                    "account_id": account_id,
                    "digest": f"verified-email-{account_id}",
                },
            )

        with postgres_engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT count(*), count(*) FILTER (WHERE is_primary) "
                    "FROM account_login_identifiers WHERE account_id = :account_id"
                ),
                {"account_id": account_id},
            ).one() == (2, 0)
            assert (
                connection.scalar(
                    text("SELECT to_regclass('uq_account_login_identifiers_primary_account')")
                )
                is None
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_trigger WHERE tgname IN "
                        "('trg_account_login_identifiers_primary_account', "
                        "'trg_user_accounts_primary_identifier')"
                    )
                )
                == 0
            )
    finally:
        command.upgrade(config, CURRENT_HEAD)
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )


def test_0026_downgrade_restores_deterministic_primary_identifier(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    person_id, account_id = _insert_test_account(postgres_engine)
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE account_login_identifiers SET is_primary = false "
                    "WHERE account_id = :account_id"
                ),
                {"account_id": account_id},
            )
            connection.execute(
                text(
                    "INSERT INTO account_login_identifiers "
                    "(id, account_id, kind, identifier_digest, verified_at, "
                    "verification_source, is_primary) VALUES "
                    "(:id, :account_id, 'email', :digest, CURRENT_TIMESTAMP, "
                    "'test_verified', false)"
                ),
                {
                    "id": uuid4(),
                    "account_id": account_id,
                    "digest": f"verified-email-{account_id}",
                },
            )

        command.downgrade(config, "0025_password_reset_mail_template")
        with postgres_engine.connect() as connection:
            rows = list(
                connection.execute(
                    text(
                        "SELECT kind, is_primary FROM account_login_identifiers "
                        "WHERE account_id = :account_id ORDER BY kind"
                    ),
                    {"account_id": account_id},
                ).tuples()
            )
            assert rows == [("email", False), ("phone", True)]
            assert (
                connection.scalar(
                    text("SELECT to_regclass('uq_account_login_identifiers_primary_account')")
                )
                is not None
            )
    finally:
        command.upgrade(config, CURRENT_HEAD)
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )


def test_account_identifier_cascade_delete(postgres_engine: Engine) -> None:
    person_id, account_id = _insert_test_account(postgres_engine)
    try:
        with postgres_engine.connect() as connection:
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM account_login_identifiers "
                        "WHERE account_id = :account_id"
                    ),
                    {"account_id": account_id},
                )
                == 1
            )

        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM user_roles WHERE user_id = :account_id"),
                {"account_id": account_id},
            )
            result = connection.execute(
                text("DELETE FROM user_accounts WHERE id = :account_id"),
                {"account_id": account_id},
            )
            assert result.rowcount == 1

        with postgres_engine.connect() as connection:
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM account_login_identifiers "
                        "WHERE account_id = :account_id"
                    ),
                    {"account_id": account_id},
                )
                == 0
            )
    finally:
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )


def test_food_preference_migration_round_trip_restores_schema_and_grants(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0014_teaching_reports")
    try:
        inspector = inspect(postgres_engine)
        assert FOOD_PREFERENCE_TABLES.isdisjoint(inspector.get_table_names())
        assert "food_preferences_onboarding_completed_at" not in {
            column["name"] for column in inspector.get_columns("user_accounts")
        }
        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0014_teaching_reports"
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM permissions WHERE id = ANY(CAST(:ids AS uuid[]))"),
                    {"ids": list(FOOD_PREFERENCE_PERMISSION_IDS.values())},
                )
                == 0
            )
        command.upgrade(config, "head")
        with postgres_engine.connect() as connection:
            assert _role_permission_grants(connection) >= FOOD_PREFERENCE_GRANTS
    finally:
        command.upgrade(config, "head")


def test_camp_survey_migration_round_trip_restores_schema_and_grants(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0015_food_preferences")
    try:
        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0015_food_preferences"
            )
            assert CAMP_SURVEY_TABLES.isdisjoint(inspect(connection).get_table_names())
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM permissions WHERE id = ANY(CAST(:ids AS uuid[]))"),
                    {"ids": list(CAMP_SURVEY_PERMISSION_IDS.values())},
                )
                == 0
            )
        command.upgrade(config, "head")
        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                CURRENT_HEAD
            )
            assert _role_permission_grants(connection) >= CAMP_SURVEY_GRANTS
    finally:
        command.upgrade(config, "head")


def test_0017_backfills_only_legacy_phone_login(postgres_engine: Engine) -> None:
    from app.security.encryption import EnvelopeCipher, LookupDigest

    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0016_camp_evaluation_surveys")
    person_id, account_id = _insert_test_account(postgres_engine)
    normalized_email = f"operator-{account_id}@example.com"
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text("UPDATE people SET email = :email WHERE id = :person_id"),
                {"person_id": person_id, "email": f"  {normalized_email.upper()}  "},
            )

        command.upgrade(config, CURRENT_HEAD)

        with postgres_engine.connect() as connection:
            identifiers = list(
                connection.execute(
                    text(
                        "SELECT kind, verification_source, is_primary "
                        "FROM account_login_identifiers "
                        "WHERE account_id = :account_id ORDER BY kind"
                    ),
                    {"account_id": account_id},
                ).tuples()
            )
            assert identifiers == [("phone", "legacy_phone_login", True)]
            phone_ciphertext, person_phone_digest, legacy_phone_digest = connection.execute(
                text(
                    "SELECT people.phone_ciphertext, people.phone_digest, "
                    "user_accounts.login_phone_digest FROM people "
                    "JOIN user_accounts ON user_accounts.person_id = people.id "
                    "WHERE user_accounts.id = :account_id"
                ),
                {"account_id": account_id},
            ).one()
            normalized_phone = EnvelopeCipher(MIGRATION_TEST_KEY_BYTES).decrypt(
                phone_ciphertext,
                field_name="phone",
            )
            expected_legacy_phone_digest = LookupDigest(MIGRATION_TEST_KEY_BYTES).digest(
                normalized_phone
            )
            expected_login_identifier_digest = LookupDigest(MIGRATION_TEST_KEY_BYTES).digest_domain(
                "account-login-phone", normalized_phone
            )
            assert person_phone_digest == expected_legacy_phone_digest
            assert legacy_phone_digest == expected_legacy_phone_digest
            assert expected_login_identifier_digest != expected_legacy_phone_digest
            assert (
                connection.scalar(
                    text(
                        "SELECT identifier_digest FROM account_login_identifiers "
                        "WHERE account_id = :account_id"
                    ),
                    {"account_id": account_id},
                )
                == expected_login_identifier_digest
            )
            assert (
                connection.scalar(
                    text("SELECT email_normalized FROM user_accounts WHERE id = :account_id"),
                    {"account_id": account_id},
                )
                is None
            )
            email_ciphertext, email_digest, legacy_email = connection.execute(
                text(
                    "SELECT email_ciphertext, email_digest, email FROM people WHERE id = :person_id"
                ),
                {"person_id": person_id},
            ).one()
            assert legacy_email is None
            assert (
                EnvelopeCipher(MIGRATION_TEST_KEY_BYTES).decrypt(
                    email_ciphertext,
                    field_name="email",
                )
                == normalized_email
            )
            assert email_digest == LookupDigest(MIGRATION_TEST_KEY_BYTES).digest_domain(
                "person-email",
                normalized_email,
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT EXISTS ("
                        "SELECT 1 FROM user_roles ur JOIN roles r ON r.id = ur.role_id "
                        "WHERE ur.user_id = :account_id AND r.key = 'registered_user')"
                    ),
                    {"account_id": account_id},
                )
                is True
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT legacy_backfilled FROM guardian_profiles "
                        "WHERE person_id = :person_id"
                    ),
                    {"person_id": person_id},
                )
                is True
            )
    finally:
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )
        command.upgrade(config, "head")


def test_0017_preserves_multi_profile_relationship_guardians(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0016_camp_evaluation_surveys")
    guardian_id = uuid4()
    student_id = uuid4()
    relationship_id = uuid4()
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO people "
                    "(id, full_name, identity_ciphertext, identity_digest, sex, date_of_birth) "
                    "VALUES "
                    "(:guardian_id, '迁移多身份监护人', 'guardian-cipher', "
                    ":guardian_digest, 'female', DATE '1980-01-02'), "
                    "(:student_id, '迁移受监护学员', 'student-cipher', "
                    ":student_digest, 'male', DATE '2012-01-02')"
                ),
                {
                    "guardian_id": guardian_id,
                    "student_id": student_id,
                    "guardian_digest": f"guardian-{guardian_id}",
                    "student_digest": f"student-{student_id}",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO staff_profiles (person_id, staff_type) "
                    "VALUES (:guardian_id, 'coach')"
                ),
                {"guardian_id": guardian_id},
            )
            connection.execute(
                text(
                    "INSERT INTO student_profiles (person_id, student_number, status) "
                    "VALUES (:student_id, :student_number, 'active')"
                ),
                {
                    "student_id": student_id,
                    "student_number": f"migration-{student_id.hex[:16]}",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO guardian_relationships "
                    "(id, guardian_person_id, student_person_id, relationship, "
                    "is_primary, valid_from) "
                    "VALUES (:relationship_id, :guardian_id, :student_id, "
                    "'mother', true, DATE '2026-01-01')"
                ),
                {
                    "relationship_id": relationship_id,
                    "guardian_id": guardian_id,
                    "student_id": student_id,
                },
            )

        command.upgrade(config, CURRENT_HEAD)
        with postgres_engine.connect() as connection:
            assert (
                connection.scalar(
                    text(
                        "SELECT legacy_backfilled FROM guardian_profiles "
                        "WHERE person_id = :guardian_id"
                    ),
                    {"guardian_id": guardian_id},
                )
                is True
            )

        command.downgrade(config, "0016_camp_evaluation_surveys")
    finally:
        with postgres_engine.connect() as connection:
            current_revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        if current_revision == CURRENT_HEAD:
            command.downgrade(config, "0016_camp_evaluation_surveys")
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM guardian_relationships WHERE id = :id"),
                {"id": relationship_id},
            )
            connection.execute(
                text("DELETE FROM student_profiles WHERE person_id = :id"),
                {"id": student_id},
            )
            connection.execute(
                text("DELETE FROM staff_profiles WHERE person_id = :id"),
                {"id": guardian_id},
            )
            connection.execute(
                text("DELETE FROM people WHERE id IN (:guardian_id, :student_id)"),
                {"guardian_id": guardian_id, "student_id": student_id},
            )
        command.upgrade(config, "head")


def test_0017_normalizes_legacy_phone_before_domain_digest(
    postgres_engine: Engine,
) -> None:
    from app.security.encryption import EnvelopeCipher, LookupDigest

    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0016_camp_evaluation_surveys")
    legacy_phone = " 13800138000 "
    normalized_phone = "13800138000"
    person_id: UUID | None = None
    account_id: UUID | None = None
    try:
        person_id, account_id = _insert_test_account(
            postgres_engine,
            legacy_phone=legacy_phone,
        )
        with postgres_engine.connect() as connection:
            phone_ciphertext, person_digest, account_digest = connection.execute(
                text(
                    "SELECT people.phone_ciphertext, people.phone_digest, "
                    "user_accounts.login_phone_digest FROM people "
                    "JOIN user_accounts ON user_accounts.person_id = people.id "
                    "WHERE user_accounts.id = :account_id"
                ),
                {"account_id": account_id},
            ).one()
        assert (
            EnvelopeCipher(MIGRATION_TEST_KEY_BYTES).decrypt(
                phone_ciphertext,
                field_name="phone",
            )
            == legacy_phone
        )
        expected_legacy_digest = LookupDigest(MIGRATION_TEST_KEY_BYTES).digest(normalized_phone)
        assert person_digest == expected_legacy_digest
        assert account_digest == expected_legacy_digest

        command.upgrade(config, CURRENT_HEAD)

        with postgres_engine.connect() as connection:
            assert connection.scalar(
                text(
                    "SELECT identifier_digest FROM account_login_identifiers "
                    "WHERE account_id = :account_id"
                ),
                {"account_id": account_id},
            ) == LookupDigest(MIGRATION_TEST_KEY_BYTES).digest_domain(
                "account-login-phone",
                normalized_phone,
            )
    finally:
        if person_id is not None and account_id is not None:
            _delete_test_account(
                postgres_engine,
                person_id=person_id,
                account_id=account_id,
            )
        command.upgrade(config, "head")


@pytest.mark.parametrize(
    "corruption",
    (
        "missing_ciphertext",
        "invalid_ciphertext",
        "invalid_plaintext",
        "person_digest_mismatch",
        "account_digest_mismatch",
    ),
)
def test_registration_upgrade_rejects_invalid_legacy_phone_before_ddl(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    from app.security.encryption import EnvelopeCipher

    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0016_camp_evaluation_surveys")
    person_id, account_id = _insert_test_account(postgres_engine)
    try:
        with postgres_engine.begin() as connection:
            if corruption == "missing_ciphertext":
                connection.execute(
                    text("UPDATE people SET phone_ciphertext = NULL WHERE id = :id"),
                    {"id": person_id},
                )
            elif corruption == "invalid_ciphertext":
                connection.execute(
                    text("UPDATE people SET phone_ciphertext = 'invalid' WHERE id = :id"),
                    {"id": person_id},
                )
            elif corruption == "invalid_plaintext":
                connection.execute(
                    text("UPDATE people SET phone_ciphertext = :value WHERE id = :id"),
                    {
                        "id": person_id,
                        "value": EnvelopeCipher(MIGRATION_TEST_KEY_BYTES).encrypt(
                            "not-a-phone",
                            field_name="phone",
                        ),
                    },
                )
            elif corruption == "person_digest_mismatch":
                connection.execute(
                    text("UPDATE people SET phone_digest = :digest WHERE id = :id"),
                    {"id": person_id, "digest": "0" * 64},
                )
            elif corruption == "account_digest_mismatch":
                connection.execute(
                    text("UPDATE user_accounts SET login_phone_digest = :digest WHERE id = :id"),
                    {"id": account_id, "digest": "1" * 64},
                )
            else:  # pragma: no cover - parametrization is closed above
                raise AssertionError(corruption)

        schema_calls = _record_registration_schema_calls(monkeypatch)
        with pytest.raises(RuntimeError, match="legacy phone"):
            command.upgrade(config, CURRENT_HEAD)
        assert "_create_schema" not in schema_calls
        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0016_camp_evaluation_surveys"
            )
            assert REGISTRATION_TABLES.isdisjoint(inspect(connection).get_table_names())
    finally:
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )
        command.upgrade(config, "head")


def test_registration_upgrade_rejects_new_phone_domain_collision_before_ddl(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.security.encryption import LookupDigest

    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0016_camp_evaluation_surveys")
    first_person_id, first_account_id = _insert_test_account(postgres_engine)
    second_person_id, second_account_id = _insert_test_account(postgres_engine)
    original_digest_domain = LookupDigest.digest_domain
    monkeypatch.setattr(
        LookupDigest,
        "digest_domain",
        lambda self, domain, normalized: "f" * 64,
    )
    schema_calls = _record_registration_schema_calls(monkeypatch)
    try:
        with pytest.raises(RuntimeError, match="legacy phone.*domain"):
            command.upgrade(config, CURRENT_HEAD)
        assert "_create_schema" not in schema_calls
        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0016_camp_evaluation_surveys"
            )
            assert REGISTRATION_TABLES.isdisjoint(inspect(connection).get_table_names())
    finally:
        monkeypatch.setattr(LookupDigest, "digest_domain", original_digest_domain)
        _delete_test_account(
            postgres_engine,
            person_id=first_person_id,
            account_id=first_account_id,
        )
        _delete_test_account(
            postgres_engine,
            person_id=second_person_id,
            account_id=second_account_id,
        )
        command.upgrade(config, "head")


def test_registration_migration_round_trip_restores_schema_and_grants(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0016_camp_evaluation_surveys")
    person_id, account_id = _insert_test_account(postgres_engine)
    normalized_email = f"operator-{account_id}@example.com"
    try:
        with postgres_engine.begin() as connection:
            baseline_grants = _role_permission_grants(connection)
            connection.execute(
                text("UPDATE people SET email = :email WHERE id = :person_id"),
                {"person_id": person_id, "email": normalized_email.upper()},
            )

        schema_calls = _record_registration_schema_calls(monkeypatch)
        command.upgrade(config, CURRENT_HEAD)
        assert schema_calls == ["_create_schema"]
        command.downgrade(config, "0016_camp_evaluation_surveys")
        assert schema_calls == ["_create_schema", "_drop_schema"]

        inspector = inspect(postgres_engine)
        assert REGISTRATION_TABLES.isdisjoint(inspector.get_table_names())
        assert "self_service_primary_purpose" not in {
            column["name"] for column in inspector.get_columns("user_accounts")
        }
        assert {"email_ciphertext", "email_digest"}.isdisjoint(
            column["name"] for column in inspector.get_columns("people")
        )
        account_columns = {
            column["name"]: column for column in inspector.get_columns("user_accounts")
        }
        assert account_columns["login_phone_digest"]["nullable"] is False
        assert account_columns["email_normalized"]["nullable"] is False
        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0016_camp_evaluation_surveys"
            )
            assert connection.execute(
                text(
                    "SELECT people.email, user_accounts.email_normalized "
                    "FROM people JOIN user_accounts ON user_accounts.person_id = people.id "
                    "WHERE people.id = :person_id"
                ),
                {"person_id": person_id},
            ).one() == (normalized_email, normalized_email)
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM roles WHERE id = :id"),
                    {"id": REGISTERED_USER_ROLE_ID},
                )
                == 0
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM permissions WHERE id = ANY(CAST(:ids AS uuid[]))"),
                    {"ids": list(REGISTRATION_PERMISSION_IDS.values())},
                )
                == 0
            )
            assert _role_permission_grants(connection) == baseline_grants
    finally:
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )
        command.upgrade(config, "head")


def test_registration_upgrade_rejects_invalid_key_before_ddl(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0016_camp_evaluation_surveys")
    monkeypatch.setenv("FIELD_ENCRYPTION_MASTER_KEY", "not valid base64!")
    schema_calls = _record_registration_schema_calls(monkeypatch)
    try:
        with pytest.raises(ValueError, match="FIELD_ENCRYPTION_MASTER_KEY"):
            command.upgrade(config, CURRENT_HEAD)
        assert "_create_schema" not in schema_calls
        inspector = inspect(postgres_engine)
        assert REGISTRATION_TABLES.isdisjoint(inspector.get_table_names())
        assert "self_service_primary_purpose" not in {
            column["name"] for column in inspector.get_columns("user_accounts")
        }
        assert {"email_ciphertext", "email_digest"}.isdisjoint(
            column["name"] for column in inspector.get_columns("people")
        )
        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0016_camp_evaluation_surveys"
            )
    finally:
        monkeypatch.setenv("FIELD_ENCRYPTION_MASTER_KEY", MIGRATION_TEST_KEY)
        command.upgrade(config, "head")


@pytest.mark.parametrize(
    "secret_name",
    ("FIELD_ENCRYPTION_MASTER_KEY", "FIELD_LOOKUP_KEY"),
)
def test_registration_migration_reads_secret_file_fallback(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    secret_name: str,
) -> None:
    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0016_camp_evaluation_surveys")
    key_file = tmp_path / secret_name.casefold()
    key_file.write_text(MIGRATION_TEST_KEY + "\r\n", encoding="utf-8")
    monkeypatch.delenv(secret_name, raising=False)
    monkeypatch.setenv(f"{secret_name}_FILE", str(key_file))
    try:
        command.upgrade(config, CURRENT_HEAD)
    finally:
        monkeypatch.setenv(secret_name, MIGRATION_TEST_KEY)
        command.upgrade(config, "head")


@pytest.mark.parametrize(
    "secret_name",
    ("FIELD_ENCRYPTION_MASTER_KEY", "FIELD_LOOKUP_KEY"),
)
def test_registration_migration_prefers_direct_secret_over_file(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    secret_name: str,
) -> None:
    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0016_camp_evaluation_surveys")
    key_file = tmp_path / f"invalid-{secret_name.casefold()}"
    key_file.write_text("not valid base64!\r\n", encoding="utf-8")
    monkeypatch.setenv(secret_name, MIGRATION_TEST_KEY)
    monkeypatch.setenv(f"{secret_name}_FILE", str(key_file))
    try:
        command.upgrade(config, CURRENT_HEAD)
    finally:
        monkeypatch.setenv(secret_name, MIGRATION_TEST_KEY)
        command.upgrade(config, "head")


def test_registration_migration_secret_file_read_error_is_stable(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0016_camp_evaluation_surveys")
    missing_file = tmp_path / "private-field-key"
    monkeypatch.delenv("FIELD_ENCRYPTION_MASTER_KEY", raising=False)
    monkeypatch.setenv("FIELD_ENCRYPTION_MASTER_KEY_FILE", str(missing_file))
    try:
        with pytest.raises(ValueError) as error:
            command.upgrade(config, CURRENT_HEAD)
        assert str(error.value) == "unable to read FIELD_ENCRYPTION_MASTER_KEY_FILE"
        assert str(missing_file) not in str(error.value)
        assert error.value.__cause__ is None
    finally:
        monkeypatch.setenv("FIELD_ENCRYPTION_MASTER_KEY", MIGRATION_TEST_KEY)
        command.upgrade(config, "head")


def test_registration_upgrade_rejects_mismatched_legacy_emails_atomically(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0016_camp_evaluation_surveys")
    person_id, account_id = _insert_test_account(postgres_engine)
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text("UPDATE people SET email = 'different@example.com' WHERE id = :id"),
                {"id": person_id},
            )
        schema_calls = _record_registration_schema_calls(monkeypatch)
        with pytest.raises(RuntimeError, match="legacy email"):
            command.upgrade(config, CURRENT_HEAD)
        assert "_create_schema" not in schema_calls

        inspector = inspect(postgres_engine)
        assert REGISTRATION_TABLES.isdisjoint(inspector.get_table_names())
        assert {"email_ciphertext", "email_digest"}.isdisjoint(
            column["name"] for column in inspector.get_columns("people")
        )
        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0016_camp_evaluation_surveys"
            )
            assert (
                connection.scalar(
                    text("SELECT email FROM people WHERE id = :id"),
                    {"id": person_id},
                )
                == "different@example.com"
            )
    finally:
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )
        command.upgrade(config, "head")


def test_registration_upgrade_rejects_missing_legacy_phone_atomically(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0016_camp_evaluation_surveys")
    with postgres_engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE user_accounts ALTER COLUMN login_phone_digest DROP NOT NULL")
        )
    person_id, account_id = _insert_test_account(postgres_engine)
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text("UPDATE user_accounts SET login_phone_digest = NULL WHERE id = :id"),
                {"id": account_id},
            )
        schema_calls = _record_registration_schema_calls(monkeypatch)
        with pytest.raises(RuntimeError, match="legacy phone"):
            command.upgrade(config, CURRENT_HEAD)
        assert "_create_schema" not in schema_calls
        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0016_camp_evaluation_surveys"
            )
            assert REGISTRATION_TABLES.isdisjoint(inspect(connection).get_table_names())
    finally:
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )
        with postgres_engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE user_accounts ALTER COLUMN login_phone_digest SET NOT NULL")
            )
        command.upgrade(config, "head")


def test_registration_upgrade_rejects_duplicate_legacy_phone_atomically(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0016_camp_evaluation_surveys")
    with postgres_engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE user_accounts DROP CONSTRAINT user_accounts_login_phone_digest_key")
        )
    first_person_id, first_account_id = _insert_test_account(postgres_engine)
    second_person_id, second_account_id = _insert_test_account(postgres_engine)
    try:
        with postgres_engine.begin() as connection:
            digest = connection.scalar(
                text("SELECT login_phone_digest FROM user_accounts WHERE id = :id"),
                {"id": first_account_id},
            )
            connection.execute(
                text("UPDATE user_accounts SET login_phone_digest = :digest WHERE id = :id"),
                {"digest": digest, "id": second_account_id},
            )
        schema_calls = _record_registration_schema_calls(monkeypatch)
        with pytest.raises(RuntimeError, match="legacy phone"):
            command.upgrade(config, CURRENT_HEAD)
        assert "_create_schema" not in schema_calls
        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0016_camp_evaluation_surveys"
            )
            assert REGISTRATION_TABLES.isdisjoint(inspect(connection).get_table_names())
    finally:
        _delete_test_account(
            postgres_engine,
            person_id=first_person_id,
            account_id=first_account_id,
        )
        _delete_test_account(
            postgres_engine,
            person_id=second_person_id,
            account_id=second_account_id,
        )
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE user_accounts ADD CONSTRAINT "
                    "user_accounts_login_phone_digest_key UNIQUE (login_phone_digest)"
                )
            )
        command.upgrade(config, "head")


@pytest.mark.parametrize(
    "mutation",
    (
        "business_row",
        "email_only_account",
        "new_identifier",
        "missing_registered_assignment",
        "permission_override",
        "custom_permission_grant",
        "custom_registered_grant",
        "modified_registered_role",
    ),
)
def test_registration_downgrade_refuses_nonbaseline_state_before_ddl(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0016_camp_evaluation_surveys")
    person_id, account_id = _insert_test_account(postgres_engine)
    command.upgrade(config, CURRENT_HEAD)

    mutation_id = uuid4()
    extra_person_id: UUID | None = None
    extra_account_id: UUID | None = None
    try:
        with postgres_engine.begin() as connection:
            if mutation == "business_row":
                connection.execute(
                    text(
                        "INSERT INTO registration_applications "
                        "(id, primary_purpose, state, expires_at) "
                        "VALUES (:id, 'guardian', 'draft', "
                        "CURRENT_TIMESTAMP + INTERVAL '30 days')"
                    ),
                    {"id": mutation_id},
                )
            elif mutation == "email_only_account":
                extra_person_id = uuid4()
                extra_account_id = uuid4()
                connection.execute(
                    text(
                        "INSERT INTO people "
                        "(id, full_name, identity_ciphertext, identity_digest, "
                        "sex, date_of_birth) VALUES "
                        "(:id, '迁移后邮箱账号', 'new-account-ciphertext', "
                        ":digest, 'female', DATE '1990-01-02')"
                    ),
                    {"id": extra_person_id, "digest": f"new-{extra_person_id}"},
                )
                connection.execute(
                    text(
                        "INSERT INTO user_accounts "
                        "(id, person_id, login_phone_digest, email_normalized, "
                        "password_hash, status, force_password_change, failed_login_count) "
                        "VALUES (:id, :person_id, NULL, NULL, 'new-password-hash', "
                        "'active', false, 0)"
                    ),
                    {"id": extra_account_id, "person_id": extra_person_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO account_login_identifiers "
                        "(id, account_id, kind, identifier_digest, verified_at, "
                        "verification_source, is_primary) VALUES "
                        "(:id, :account_id, 'email', :digest, CURRENT_TIMESTAMP, "
                        "'self_service_registration', true)"
                    ),
                    {
                        "id": mutation_id,
                        "account_id": extra_account_id,
                        "digest": f"email-{extra_account_id}",
                    },
                )
            elif mutation == "new_identifier":
                connection.execute(
                    text(
                        "INSERT INTO account_login_identifiers "
                        "(id, account_id, kind, identifier_digest, verified_at, "
                        "verification_source, is_primary) VALUES "
                        "(:id, :account_id, 'email', :digest, CURRENT_TIMESTAMP, "
                        "'add_login_identifier', false)"
                    ),
                    {
                        "id": mutation_id,
                        "account_id": account_id,
                        "digest": f"new-email-{account_id}",
                    },
                )
            elif mutation == "missing_registered_assignment":
                connection.execute(
                    text(
                        "DELETE FROM user_roles WHERE user_id = :account_id AND role_id = :role_id"
                    ),
                    {"account_id": account_id, "role_id": REGISTERED_USER_ROLE_ID},
                )
            elif mutation == "permission_override":
                connection.execute(
                    text(
                        "INSERT INTO user_permission_overrides "
                        "(id, user_id, permission_id, effect, scope_kind) "
                        "VALUES (:id, :user_id, :permission_id, 'allow', 'global')"
                    ),
                    {
                        "id": mutation_id,
                        "user_id": account_id,
                        "permission_id": REGISTRATION_PERMISSION_IDS["registration.review"],
                    },
                )
            elif mutation == "custom_permission_grant":
                connection.execute(
                    text(
                        "INSERT INTO role_permissions "
                        "(role_id, permission_id, scope_kind) "
                        "SELECT id, :permission_id, 'global' FROM roles "
                        "WHERE key = 'student'"
                    ),
                    {"permission_id": REGISTRATION_PERMISSION_IDS["registration.review"]},
                )
            elif mutation == "custom_registered_grant":
                connection.execute(
                    text(
                        "INSERT INTO role_permissions "
                        "(role_id, permission_id, scope_kind) "
                        "SELECT :role_id, id, 'self' FROM permissions "
                        "WHERE key = 'people.read'"
                    ),
                    {"role_id": REGISTERED_USER_ROLE_ID},
                )
            elif mutation == "modified_registered_role":
                connection.execute(
                    text("UPDATE roles SET name = '已定制注册用户' WHERE id = :id"),
                    {"id": REGISTERED_USER_ROLE_ID},
                )
            else:  # pragma: no cover - parametrization is closed above
                raise AssertionError(f"unknown mutation {mutation}")

        schema_calls = _record_registration_schema_calls(monkeypatch)
        with pytest.raises(RuntimeError, match="cannot downgrade self-service registration"):
            command.downgrade(config, "0016_camp_evaluation_surveys")
        assert "_drop_schema" not in schema_calls

        with postgres_engine.connect() as connection:
            _assert_registration_ddl_is_intact(connection)
    finally:
        with postgres_engine.begin() as connection:
            if mutation == "business_row":
                connection.execute(
                    text("DELETE FROM registration_applications WHERE id = :id"),
                    {"id": mutation_id},
                )
            elif mutation == "custom_permission_grant":
                connection.execute(
                    text(
                        "DELETE FROM role_permissions WHERE permission_id = :permission_id "
                        "AND role_id = (SELECT id FROM roles WHERE key = 'student') "
                        "AND scope_kind = 'global'"
                    ),
                    {"permission_id": REGISTRATION_PERMISSION_IDS["registration.review"]},
                )
            elif mutation == "custom_registered_grant":
                connection.execute(
                    text(
                        "DELETE FROM role_permissions WHERE role_id = :role_id "
                        "AND permission_id = ("
                        "SELECT id FROM permissions WHERE key = 'people.read') "
                        "AND scope_kind = 'self'"
                    ),
                    {"role_id": REGISTERED_USER_ROLE_ID},
                )
            elif mutation == "modified_registered_role":
                connection.execute(
                    text("UPDATE roles SET name = '注册用户' WHERE id = :id"),
                    {"id": REGISTERED_USER_ROLE_ID},
                )
        if extra_person_id is not None and extra_account_id is not None:
            _delete_test_account(
                postgres_engine,
                person_id=extra_person_id,
                account_id=extra_account_id,
            )
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )


def test_registration_downgrade_refuses_nonbaseline_guardian_profile_before_ddl(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0016_camp_evaluation_surveys")
    person_id, account_id = _insert_test_account(postgres_engine)
    command.upgrade(config, CURRENT_HEAD)
    student_id = uuid4()
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO people "
                    "(id, full_name, identity_ciphertext, identity_digest, "
                    "sex, date_of_birth) VALUES "
                    "(:id, '新增显式监护人身份学员', 'student-ciphertext', "
                    ":digest, 'female', DATE '2012-01-02')"
                ),
                {"id": student_id, "digest": f"student-{student_id}"},
            )
            connection.execute(
                text(
                    "INSERT INTO student_profiles "
                    "(person_id, student_number, status) "
                    "VALUES (:id, :number, 'active')"
                ),
                {"id": student_id, "number": f"registration-{student_id.hex[:16]}"},
            )
            connection.execute(
                text("INSERT INTO guardian_profiles (person_id) VALUES (:id)"),
                {"id": student_id},
            )

        schema_calls = _record_registration_schema_calls(monkeypatch)
        with pytest.raises(
            RuntimeError,
            match="cannot downgrade self-service registration with changed guardian profile",
        ):
            command.downgrade(config, "0016_camp_evaluation_surveys")
        assert "_drop_schema" not in schema_calls

        with postgres_engine.connect() as connection:
            _assert_registration_ddl_is_intact(connection)
            assert (
                connection.scalar(
                    text("SELECT EXISTS (SELECT 1 FROM guardian_profiles WHERE person_id = :id)"),
                    {"id": student_id},
                )
                is True
            )
    finally:
        with postgres_engine.connect() as connection:
            current_revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        if current_revision != CURRENT_HEAD:
            command.upgrade(config, CURRENT_HEAD)
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM guardian_profiles WHERE person_id = :id"),
                {"id": student_id},
            )
            connection.execute(
                text("DELETE FROM student_profiles WHERE person_id = :id"),
                {"id": student_id},
            )
            connection.execute(
                text("DELETE FROM people WHERE id = :id"),
                {"id": student_id},
            )
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )


def test_registration_downgrade_refuses_new_standalone_guardian_before_ddl(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0016_camp_evaluation_surveys")
    person_id, account_id = _insert_test_account(postgres_engine)
    command.upgrade(config, CURRENT_HEAD)
    new_guardian_id = uuid4()
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO people "
                    "(id, full_name, identity_ciphertext, identity_digest, "
                    "sex, date_of_birth) VALUES "
                    "(:id, '新增独立监护人', 'guardian-ciphertext', "
                    ":digest, 'female', DATE '1988-01-02')"
                ),
                {"id": new_guardian_id, "digest": f"guardian-{new_guardian_id}"},
            )
            connection.execute(
                text("INSERT INTO guardian_profiles (person_id) VALUES (:id)"),
                {"id": new_guardian_id},
            )

        schema_calls = _record_registration_schema_calls(monkeypatch)
        with pytest.raises(
            RuntimeError,
            match="cannot downgrade self-service registration with changed guardian profile",
        ):
            command.downgrade(config, "0016_camp_evaluation_surveys")
        assert "_drop_schema" not in schema_calls

        with postgres_engine.connect() as connection:
            _assert_registration_ddl_is_intact(connection)
            assert (
                connection.scalar(
                    text("SELECT legacy_backfilled FROM guardian_profiles WHERE person_id = :id"),
                    {"id": new_guardian_id},
                )
                is False
            )
    finally:
        with postgres_engine.connect() as connection:
            current_revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        if current_revision != CURRENT_HEAD:
            command.upgrade(config, CURRENT_HEAD)
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM guardian_profiles WHERE person_id = :id"),
                {"id": new_guardian_id},
            )
            connection.execute(
                text("DELETE FROM people WHERE id = :id"),
                {"id": new_guardian_id},
            )
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )


def test_food_preference_downgrade_refuses_completed_onboarding_before_ddl(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    person_id, account_id = _insert_test_account(postgres_engine)
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE user_accounts SET "
                    "food_preferences_onboarding_completed_at = CURRENT_TIMESTAMP "
                    "WHERE id = :account_id"
                ),
                {"account_id": account_id},
            )

        with pytest.raises(
            RuntimeError,
            match="cannot downgrade food preferences with completed onboarding data",
        ):
            command.downgrade(config, "0014_teaching_reports")

        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                CURRENT_HEAD
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT food_preferences_onboarding_completed_at IS NOT NULL "
                        "FROM user_accounts WHERE id = :account_id"
                    ),
                    {"account_id": account_id},
                )
                is True
            )
    finally:
        command.upgrade(config, "head")
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE user_accounts SET "
                    "food_preferences_onboarding_completed_at = NULL "
                    "WHERE id = :account_id"
                ),
                {"account_id": account_id},
            )
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )


def test_food_preference_downgrade_refuses_account_override_before_ddl(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    person_id, account_id = _insert_test_account(postgres_engine)
    override_id = uuid4()
    permission_id = FOOD_PREFERENCE_PERMISSION_IDS["food_preferences.read"]
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO user_permission_overrides ("
                    "id, user_id, permission_id, effect, scope_kind, scope_id"
                    ") VALUES ("
                    ":id, :account_id, :permission_id, 'deny', 'self', NULL)"
                ),
                {
                    "id": override_id,
                    "account_id": account_id,
                    "permission_id": permission_id,
                },
            )

        with pytest.raises(
            RuntimeError,
            match="cannot downgrade food preferences with non-seed permission references",
        ):
            command.downgrade(config, "0014_teaching_reports")

        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                CURRENT_HEAD
            )
            assert (
                connection.scalar(
                    text("SELECT EXISTS (SELECT 1 FROM user_permission_overrides WHERE id = :id)"),
                    {"id": override_id},
                )
                is True
            )
    finally:
        command.upgrade(config, "head")
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM user_permission_overrides WHERE id = :id"),
                {"id": override_id},
            )
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )


def test_housing_foundation_migration_seeds_catalog_and_permissions(
    postgres_engine: Engine,
) -> None:
    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (CURRENT_HEAD)
        assert connection.scalar(text("SELECT count(*) FROM housing_buildings")) == 4
        assert connection.scalar(text("SELECT count(*) FROM housing_floors")) == 11
        assert connection.scalar(text("SELECT count(*) FROM housing_rooms")) == 188
        assert (
            connection.scalar(
                text("SELECT count(*) FROM housing_rooms WHERE room_type = 'functional'")
            )
            == 17
        )
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM housing_rooms "
                    "WHERE room_type <> 'functional' "
                    "AND verification_status = 'pending_verification'"
                )
            )
            == 171
        )
        assert (
            connection.scalar(text("SELECT COALESCE(sum(capacity), 0) FROM housing_rooms")) == 223
        )
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
                | {"housing.history.void": UUID("20000000-0000-4000-8000-000000000090")}
            ).items()
        }
        assert connection.execute(
            text(
                "SELECT r.key,rp.scope_kind FROM role_permissions rp "
                "JOIN roles r ON r.id=rp.role_id "
                "JOIN permissions p ON p.id=rp.permission_id "
                "WHERE p.key='housing.history.void'"
            )
        ).all() == [("super_admin", "global")]
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
            assert HOUSING_REPORTING_TABLES.isdisjoint(set(inspect(connection).get_table_names()))
            assert (
                connection.execute(
                    text(
                        "SELECT id, room_number, building_id, floor_id "
                        "FROM housing_rooms WHERE id = :id"
                    ),
                    {"id": before.id},
                ).one()
                == before
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM permissions WHERE id = :id"),
                    {"id": HOUSING_REPORT_PERMISSION_ID},
                )
                == 0
            )
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
            "voided_at",
            "voided_by_user_id",
            "void_reason_ciphertext",
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
            "ck_housing_stays_void_fact",
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
            "ix_housing_stays_person_latest_checkout",
        },
        "housing_transfers": set(),
    }
    models = (HousingRoomIssue, HousingStay, HousingTransfer)

    assert HOUSING_OCCUPANCY_TABLES <= set(inspector.get_table_names())
    for model in models:
        table_name = model.__tablename__
        assert set(model.__table__.columns.keys()) == expected_columns[table_name]
        assert {column["name"] for column in inspector.get_columns(table_name)} == expected_columns[
            table_name
        ]
        model_check_names = {
            constraint.name
            for constraint in model.__table__.constraints
            if constraint.__class__.__name__ == "CheckConstraint"
        }
        database_check_names = {
            constraint["name"] for constraint in inspector.get_check_constraints(table_name)
        }
        assert model_check_names == expected_checks[table_name]
        assert database_check_names == expected_checks[table_name]
        model_index_names = {index.name for index in model.__table__.indexes}
        database_index_names = {index["name"] for index in inspector.get_indexes(table_name)}
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
        constraint["name"] for constraint in inspector.get_unique_constraints("housing_transfers")
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

            with (
                pytest.raises(
                    DBAPIError,
                    match="uq_housing_room_issues_open_room",
                ),
                connection.begin_nested(),
            ):
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

            with (
                pytest.raises(
                    DBAPIError,
                    match="ck_housing_room_issues_resolution",
                ),
                connection.begin_nested(),
            ):
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

            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM housing_room_issues "
                        "WHERE id = ANY(CAST(:ids AS uuid[]))"
                    ),
                    {"ids": [issue_id, duplicate_id, invalid_id]},
                )
                == 1
            )
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

            with (
                pytest.raises(
                    DBAPIError,
                    match="excl_housing_stays_person_active_overlap",
                ),
                connection.begin_nested(),
            ):
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
                with (
                    pytest.raises(
                        DBAPIError,
                        match=constraint_name,
                    ),
                    connection.begin_nested(),
                ):
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

            assert (
                connection.scalar(
                    text("SELECT count(*) FROM housing_stays WHERE id = ANY(CAST(:ids AS uuid[]))"),
                    {"ids": stay_ids},
                )
                == 4
            )
            connection.execute(
                text("DELETE FROM housing_stays WHERE id = ANY(CAST(:ids AS uuid[]))"),
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
            backend_pids.put(int(connection.scalar(text("SELECT pg_backend_pid()")) or 0))
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
                            "SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = :pid"
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
                text("DELETE FROM housing_stays WHERE id = ANY(CAST(:ids AS uuid[]))"),
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
                with (
                    pytest.raises(
                        DBAPIError,
                        match=constraint_name,
                    ),
                    connection.begin_nested(),
                ):
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

            assert (
                connection.scalar(
                    text("SELECT count(*) FROM housing_transfers WHERE id = :id"),
                    {"id": transfer_id},
                )
                == 1
            )
            connection.execute(
                text("DELETE FROM housing_transfers WHERE id = :id"),
                {"id": transfer_id},
            )
            connection.execute(
                text("DELETE FROM housing_stays WHERE id = ANY(CAST(:ids AS uuid[]))"),
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
        item["name"]: item for item in inspector.get_columns("evaluation_bridge_requests")
    }
    ticket_columns = {
        item["name"]: item for item in inspector.get_columns("evaluation_bridge_tickets")
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
        item["name"]: item for item in inspector.get_indexes("evaluation_bridge_requests")
    }
    ticket_indexes = {
        item["name"]: item for item in inspector.get_indexes("evaluation_bridge_tickets")
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
    assert inspector.get_pk_constraint("evaluation_bridge_requests")["constrained_columns"] == [
        "id"
    ]
    assert inspector.get_pk_constraint("evaluation_bridge_tickets")["constrained_columns"] == ["id"]
    assert request_checks["ck_evaluation_bridge_requests_expiry"] == ("expires_at > created_at")
    mode_check = request_checks["ck_evaluation_bridge_requests_mode"]
    assert "standalone" in mode_check and "embedded" in mode_check
    assert ticket_checks == {"ck_evaluation_bridge_tickets_expiry": "expires_at > created_at"}
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
        column["name"]: column for column in inspector.get_columns("user_permission_overrides")
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
            )
            .tuples()
            .all()
        )
    expected_history_indexes = {
        "ix_student_game_accounts_student_history": "student_game_accounts",
        "ix_student_auxiliary_phones_student_history": "student_auxiliary_phones",
    }
    assert history_index_definitions.keys() == expected_history_indexes.keys()
    for index_name, table_name in expected_history_indexes.items():
        definition = " ".join(history_index_definitions[index_name].lower().split())
        assert (
            f"on public.{table_name} using btree (student_person_id, valid_from desc, id desc)"
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
                text("UPDATE evaluation_staff_roles SET name = '覆盖历史名称' WHERE id = :id"),
                {"id": role_id},
            )

        with postgres_engine.begin() as connection:
            connection.execute(
                text("UPDATE evaluation_staff_roles SET is_active = false WHERE id = :id"),
                {"id": role_id},
            )
            assert (
                connection.scalar(
                    text("SELECT is_active FROM evaluation_staff_roles WHERE id = :id"),
                    {"id": role_id},
                )
                is False
            )

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
    class_checks = {item["name"] for item in inspector.get_check_constraints("teaching_classes")}
    cohort_checks = {item["name"] for item in inspector.get_check_constraints("cohorts")}
    event_checks = {
        item["name"] for item in inspector.get_check_constraints("cohort_status_events")
    }
    assignment_checks = {
        item["name"] for item in inspector.get_check_constraints("coach_assignments")
    }
    class_uniques = {
        tuple(item["column_names"]) for item in inspector.get_unique_constraints("teaching_classes")
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
        tuple(row["column_names"]) for row in inspector.get_unique_constraints("service_accounts")
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
        tuple(row["column_names"]) for row in inspector.get_unique_constraints("enrollments")
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
    case_foreign_keys = {row["name"]: row for row in inspector.get_foreign_keys("admission_cases")}
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

    assert (
        import_foreign_keys["fk_payment_import_events_conflict"]["referred_table"]
        == "payment_conflicts"
    )
    assert (
        conflict_foreign_keys["fk_payment_conflicts_original_event"]["referred_table"]
        == "payment_import_events"
    )

    with postgres_engine.connect() as connection:
        indexes = {
            row.indexname: row.indexdef
            for row in connection.execute(
                text("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'")
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
        assert inspector.get_pk_constraint("guardian_relationships")["constrained_columns"] == [
            "guardian_person_id",
            "student_person_id",
        ]
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
            column["name"] for column in inspector.get_columns("guardian_relationships")
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
        assert (
            connection.scalar(
                text("SELECT count(*) FROM audit_events WHERE id = :id"),
                {"id": event_id},
            )
            == 0
        )


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
                text("SELECT count(*) FROM permissions WHERE id = ANY(CAST(:ids AS uuid[]))"),
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
        assert revision == CURRENT_HEAD
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
                text("SELECT name, sort_order, is_active FROM game_projects WHERE id = :id"),
                {"id": project_id},
            ).one()
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        assert project == ("管理员修改后的项目", 999, False)
        assert revision == CURRENT_HEAD
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
        assert revision == CURRENT_HEAD
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
                text("SELECT count(*) FROM permissions WHERE id = ANY(CAST(:ids AS uuid[]))"),
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
        assert ADMISSIONS_TABLES - {"enrollment_programs"} <= set(
            inspect(postgres_engine).get_table_names()
        )
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
        assert revision == CURRENT_HEAD
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
                text("SELECT EXISTS (SELECT 1 FROM user_permission_overrides WHERE id = :id)"),
                {"id": override_id},
            )
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        assert exists is True
        assert revision == CURRENT_HEAD
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
        assert revision == CURRENT_HEAD
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
            )
            .tuples()
            .all()
        )

    assert set(indexes) == PORTAL_INDEX_NAMES
    assert "(status, starts_at, id)" in indexes["ix_cohorts_portal_next"].lower()
    coach_index = indexes["ix_coach_assignments_portal_current"].lower()
    assert "(coach_person_id, valid_from, valid_until, class_id)" in coach_index
    assert "where (ended_at is null)" in coach_index
    assert (
        "(student_person_id, status, cohort_id, id)"
        in indexes["ix_enrollments_student_status_cohort"].lower()
    )


def test_portal_migration_upgrades_from_and_downgrades_to_0005(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0005_admissions_enrollment")
    try:
        with postgres_engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "0005_admissions_enrollment"
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM permissions "
                        "WHERE id = :permission_id OR key = 'coach_roster.read'"
                    ),
                    {"permission_id": PORTAL_PERMISSION_ID},
                )
                == 0
            )
            assert _portal_index_names(connection) == set()
            assert _role_permission_grants(connection).isdisjoint(PORTAL_GRANTS)

        command.upgrade(config, "0006_coach_family_portal")
        with postgres_engine.connect() as connection:
            _assert_portal_ddl_is_intact(connection, "0006_coach_family_portal")
            assert (
                connection.scalar(
                    text("SELECT id FROM permissions WHERE key = 'coach_roster.read'")
                )
                == PORTAL_PERMISSION_ID
            )
            assert PORTAL_GRANTS <= _role_permission_grants(connection)

        command.downgrade(config, "0005_admissions_enrollment")
        with postgres_engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "0005_admissions_enrollment"
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM permissions "
                        "WHERE id = :permission_id OR key = 'coach_roster.read'"
                    ),
                    {"permission_id": PORTAL_PERMISSION_ID},
                )
                == 0
            )
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
            assert EVALUATION_TABLES.isdisjoint(set(inspect(connection).get_table_names()))
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM permissions "
                        "WHERE id = :permission_id OR key = 'evaluation_center.access'"
                    ),
                    {"permission_id": EVALUATION_ACCESS_PERMISSION_ID},
                )
                == 0
            )
            assert _role_permission_grants(connection).isdisjoint(EVALUATION_ACCESS_GRANTS)

        command.upgrade(config, "0007_evaluation_page_integration")
        with postgres_engine.connect() as connection:
            assert EVALUATION_TABLES <= set(inspect(connection).get_table_names())
            assert (
                connection.scalar(
                    text("SELECT id FROM permissions WHERE key = 'evaluation_center.access'")
                )
                == EVALUATION_ACCESS_PERMISSION_ID
            )
            assert {
                grant
                for grant in _role_permission_grants(connection)
                if grant[1] == "evaluation_center.access"
            } == LEGACY_EVALUATION_ACCESS_GRANTS

        command.downgrade(config, "0006_coach_family_portal")
        with postgres_engine.connect() as connection:
            assert EVALUATION_TABLES.isdisjoint(set(inspect(connection).get_table_names()))
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM permissions "
                        "WHERE id = :permission_id OR key = 'evaluation_center.access'"
                    ),
                    {"permission_id": EVALUATION_ACCESS_PERMISSION_ID},
                )
                == 0
            )

        command.upgrade(config, "0007_evaluation_page_integration")
        with postgres_engine.connect() as connection:
            assert EVALUATION_TABLES <= set(inspect(connection).get_table_names())
            assert {
                grant
                for grant in _role_permission_grants(connection)
                if grant[1] == "evaluation_center.access"
            } == LEGACY_EVALUATION_ACCESS_GRANTS
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
            match=("cannot upgrade evaluation page with preexisting evaluation access permission"),
        ):
            command.upgrade(config, "0007_evaluation_page_integration")

        with postgres_engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "0006_coach_family_portal"
            )
            assert connection.execute(
                text("SELECT key, name, sensitive FROM permissions WHERE id = :permission_id"),
                params,
            ).one() == (permission_key, params["permission_name"], True)
            assert EVALUATION_TABLES.isdisjoint(set(inspect(connection).get_table_names()))
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
                text("SELECT id, key, name, sensitive FROM permissions WHERE id = :permission_id"),
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
            expected_grants.add(("student", "evaluation_center.access", replacement_scope))
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
        ("UPDATE permissions SET name = '并发修改后的评价权限' WHERE id = :permission_id"),
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


def test_0058_talent_upgrade_downgrade_preserves_existing_rows_and_grants(
    postgres_engine: Engine,
) -> None:
    from app.rbac.seed import seed_roles_and_permissions

    config = _alembic_config(postgres_engine)
    person_id, account_id = _insert_test_account(postgres_engine)
    sentinel_permission_id = uuid4()
    sentinel_permission_key = f"migration.sentinel.{sentinel_permission_id.hex}"
    sentinel_override_id = uuid4()
    talent_override_id = uuid4()
    excluded_permission_keys = (
        set(TALENT_TEST_PERMISSION_IDS)
        | set(ESPORTS_ASSESSMENT_PERMISSION_IDS)
        | TEAM_MANAGEMENT_PERMISSION_KEYS
        | {"referrals.read_invited_housing"}
        | {"housing.history.void"}
        | set(ASSISTED_PERSONNEL_PERMISSION_IDS)
    )

    def preserved_row_counts() -> dict[str, int]:
        inspector = inspect(postgres_engine)
        with postgres_engine.connect() as connection:
            counts: dict[str, int] = {}
            for table_name in inspector.get_table_names():
                if table_name == "alembic_version" or table_name in (
                    TALENT_TEST_TABLES | ESPORTS_ASSESSMENT_TABLES | TEAM_MANAGEMENT_TABLES
                    | CLOUD_BACKUP_TABLES | CLOUD_AUTH_TABLES | INVITED_HOUSING_TABLES | ASSISTED_PERSONNEL_TABLES
                    | HOUSING_HISTORY_VOID_TABLES | HOUSING_ROOM_CARE_TABLES
                ):
                    continue
                if table_name == "permissions":
                    statement = text(
                        "SELECT count(*) FROM permissions "
                        "WHERE key <> ALL(CAST(:keys AS text[]))"
                    )
                elif table_name == "role_permissions":
                    statement = text(
                        "SELECT count(*) FROM role_permissions rp "
                        "JOIN permissions p ON p.id = rp.permission_id "
                        "WHERE p.key <> ALL(CAST(:keys AS text[]))"
                    )
                elif table_name == "user_permission_overrides":
                    statement = text(
                        "SELECT count(*) FROM user_permission_overrides upo "
                        "JOIN permissions p ON p.id = upo.permission_id "
                        "WHERE p.key <> ALL(CAST(:keys AS text[]))"
                    )
                elif table_name == "roles":
                    statement = text(
                        "SELECT count(*) FROM roles "
                        "WHERE key NOT IN ('team_member', 'team_manager')"
                    )
                elif table_name == "game_projects":
                    statement = text(
                        "SELECT count(*) FROM game_projects WHERE id <> "
                        "'30000000-0000-4000-8000-000000000005'::uuid"
                    )
                else:
                    statement = text(f'SELECT count(*) FROM "{table_name}"')
                counts[table_name] = int(
                    connection.scalar(
                        statement,
                        {
                            "keys": sorted(excluded_permission_keys)
                        },
                    )
                    or 0
                )
            return counts

    command.upgrade(config, CURRENT_HEAD)
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO permissions (id, key, name, sensitive) "
                    "VALUES (:id, :key, '迁移保留哨兵权限', false)"
                ),
                {"id": sentinel_permission_id, "key": sentinel_permission_key},
            )
            connection.execute(
                text(
                    "INSERT INTO role_permissions "
                    "(role_id, permission_id, scope_kind) "
                    "VALUES (:role_id, :permission_id, 'self')"
                ),
                {
                    "role_id": REGISTERED_USER_ROLE_ID,
                    "permission_id": sentinel_permission_id,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO user_permission_overrides "
                    "(id, user_id, permission_id, effect, scope_kind, "
                    "created_by_user_id, grant_reason) VALUES "
                    "(:sentinel_id, :user_id, :sentinel_permission_id, "
                    "'allow', 'self', :user_id, '迁移保留哨兵'), "
                    "(:talent_id, :user_id, :talent_permission_id, "
                    "'deny', 'self', :user_id, '天赋权限降级清理哨兵')"
                ),
                {
                    "sentinel_id": sentinel_override_id,
                    "talent_id": talent_override_id,
                    "user_id": account_id,
                    "sentinel_permission_id": sentinel_permission_id,
                    "talent_permission_id": TALENT_TEST_PERMISSION_IDS[
                        "talent_tests.read"
                    ],
                },
            )
        before = preserved_row_counts()
        with postgres_engine.connect() as connection:
            _assert_assisted_rbac_transition(connection, installed=True)
            before_rbac = _unrelated_rbac_snapshot(connection, excluded_permission_keys)

        command.downgrade(config, "0057_procurement_idempotency")
        with postgres_engine.connect() as connection:
            _assert_assisted_rbac_transition(connection, installed=False)
            assert _unrelated_rbac_snapshot(connection, excluded_permission_keys) == before_rbac
            assert TALENT_TEST_TABLES.isdisjoint(
                set(inspect(connection).get_table_names())
            )
            assert connection.scalar(
                text(
                    "SELECT count(*) FROM permissions "
                    "WHERE key = ANY(CAST(:keys AS text[]))"
                ),
                {"keys": sorted(TALENT_TEST_PERMISSION_IDS)},
            ) == 0
            assert connection.execute(
                text(
                    "SELECT key, name, sensitive FROM permissions WHERE id = :id"
                ),
                {"id": sentinel_permission_id},
            ).one() == (sentinel_permission_key, "迁移保留哨兵权限", False)
            assert connection.execute(
                text(
                    "SELECT role_id, permission_id, scope_kind "
                    "FROM role_permissions WHERE permission_id = :permission_id"
                ),
                {"permission_id": sentinel_permission_id},
            ).one() == (
                REGISTERED_USER_ROLE_ID,
                sentinel_permission_id,
                "self",
            )
            assert connection.execute(
                text(
                    "SELECT id, user_id, permission_id, effect, scope_kind "
                    "FROM user_permission_overrides WHERE id = :id"
                ),
                {"id": sentinel_override_id},
            ).one() == (
                sentinel_override_id,
                account_id,
                sentinel_permission_id,
                "allow",
                "self",
            )
            assert connection.scalar(
                text(
                    "SELECT count(*) FROM user_permission_overrides "
                    "WHERE id = :id"
                ),
                {"id": talent_override_id},
            ) == 0
        assert preserved_row_counts() == before

        command.upgrade(config, CURRENT_HEAD)
        assert preserved_row_counts() == before
        with postgres_engine.connect() as connection:
            _assert_assisted_rbac_transition(connection, installed=True)
            assert _unrelated_rbac_snapshot(connection, excluded_permission_keys) == before_rbac
            assert TALENT_TEST_TABLES <= set(inspect(connection).get_table_names())
            assert connection.execute(
                text(
                    "SELECT key, id, sensitive FROM permissions "
                    "WHERE key = ANY(CAST(:keys AS text[])) ORDER BY key"
                ),
                {"keys": sorted(TALENT_TEST_PERMISSION_IDS)},
            ).all() == [
                (
                    key,
                    TALENT_TEST_PERMISSION_IDS[key],
                    key == "talent_tests.read",
                )
                for key in sorted(TALENT_TEST_PERMISSION_IDS)
            ]
            assert {
                grant
                for grant in _role_permission_grants(connection)
                if grant[1] in TALENT_TEST_PERMISSION_IDS
            } == TALENT_TEST_GRANTS
            assert connection.scalar(
                text(
                    "SELECT count(*) FROM user_permission_overrides "
                    "WHERE id = :id"
                ),
                {"id": talent_override_id},
            ) == 0
            assert connection.scalar(
                text(
                    "SELECT count(*) FROM user_permission_overrides "
                    "WHERE id = :id"
                ),
                {"id": sentinel_override_id},
            ) == 1

        with Session(postgres_engine) as session:
            seed_roles_and_permissions(session)
            session.commit()
        with postgres_engine.connect() as connection:
            assert {
                grant
                for grant in _role_permission_grants(connection)
                if grant[1] in TALENT_TEST_PERMISSION_IDS
            } == TALENT_TEST_GRANTS
    finally:
        command.upgrade(config, CURRENT_HEAD)
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM role_permissions WHERE permission_id = :permission_id"
                ),
                {"permission_id": sentinel_permission_id},
            )
            connection.execute(
                text("DELETE FROM permissions WHERE id = :permission_id"),
                {"permission_id": sentinel_permission_id},
            )


def _insert_talent_migration_session(
    connection: Connection,
    *,
    person_id: UUID,
    account_id: UUID,
    catalog_version: str,
    status: str = "in_progress",
) -> UUID:
    session_id = uuid4()
    scoring_version = catalog_version.replace("talent-training-", "talent-score-")
    report_version = catalog_version.replace("talent-training-", "talent-report-")
    connection.execute(
        text(
            "INSERT INTO talent_test_sessions ("
            "id, person_id, created_by_user_id, creation_key, creation_request_digest, "
            "catalog_version, scoring_version, report_version, rules_snapshot, status, "
            "completed_at) VALUES ("
            ":id, :person_id, :account_id, :creation_key, :digest, :catalog_version, "
            ":scoring_version, :report_version, CAST(:rules_snapshot AS jsonb), "
            "CAST(:status AS varchar), CASE WHEN CAST(:status AS varchar) = 'completed' "
            "THEN CURRENT_TIMESTAMP ELSE NULL END)"
        ),
        {
            "id": session_id,
            "person_id": person_id,
            "account_id": account_id,
            "creation_key": uuid4(),
            "digest": "a" * 64,
            "catalog_version": catalog_version,
            "scoring_version": scoring_version,
            "report_version": report_version,
            "rules_snapshot": (
                '{"catalog_version":"talent-training-v1",'
                '"test_ids":["reactiontime","aim","sequence","number-memory",'
                '"verbal-memory","chimp","memory"]}'
                if catalog_version == "talent-training-v1"
                else json.dumps({"catalog_version": catalog_version})
            ),
            "status": status,
        },
    )
    return session_id


def _insert_talent_migration_result(
    connection: Connection,
    *,
    session_id: UUID,
    test_id: str,
) -> UUID:
    result_id = uuid4()
    connection.execute(
        text(
            "INSERT INTO talent_test_results ("
            "id, session_id, test_id, engine_version, locale, raw_result, device_snapshot, "
            "normalized_result, score, quality_flags, duration_ms, background_interrupted, "
            "submission_id, submission_digest) VALUES ("
            ":id, :session_id, :test_id, :engine_version, 'zh-CN', CAST('{}' AS jsonb), "
            "CAST('{}' AS jsonb), CAST('{}' AS jsonb), 0, CAST('[]' AS jsonb), 1, false, "
            ":submission_id, :digest)"
        ),
        {
            "id": result_id,
            "session_id": session_id,
            "test_id": test_id,
            "engine_version": (
                "dynamic-vision-v1" if test_id == "dynamic-vision" else f"{test_id}-clone-v1"
            ),
            "submission_id": uuid4(),
            "digest": "b" * 64,
        },
    )
    return result_id


def _delete_talent_migration_rows(connection: Connection, person_id: UUID) -> None:
    sessions = "SELECT id FROM talent_test_sessions WHERE person_id = :person_id"
    connection.execute(
        text(f"DELETE FROM talent_test_reports WHERE session_id IN ({sessions})"),
        {"person_id": person_id},
    )
    connection.execute(
        text(f"DELETE FROM talent_test_results WHERE session_id IN ({sessions})"),
        {"person_id": person_id},
    )
    connection.execute(
        text("DELETE FROM talent_test_sessions WHERE person_id = :person_id"),
        {"person_id": person_id},
    )


def _insert_concurrent_talent_v2_data(
    connection: Connection,
    *,
    kind: str,
    person_id: UUID,
    account_id: UUID,
    session_id: UUID,
) -> None:
    if kind == "session":
        _insert_talent_migration_session(
            connection,
            person_id=person_id,
            account_id=account_id,
            catalog_version="talent-training-v2",
        )
    elif kind == "result":
        _insert_talent_migration_result(connection, session_id=session_id, test_id="dynamic-vision")
    else:
        connection.execute(
            text(
                "INSERT INTO talent_test_reports (id, session_id, report_snapshot) "
                "VALUES (:id, :session_id, CAST(:snapshot AS jsonb))"
            ),
            {
                "id": uuid4(),
                "session_id": session_id,
                "snapshot": '{"rules":{"catalog_version":"talent-training-v2"}}',
            },
        )


@pytest.mark.parametrize("kind", ["session", "result", "report"])
def test_0059_downgrade_blocks_concurrent_insert_after_guard_through_ddl(
    postgres_engine: Engine,
    kind: str,
) -> None:
    """Pause after the real EXISTS executes; a separate writer must time out.

    The writer transaction is rolled back even on RED, then the downgrade may
    finish. New-version writers must remain stopped after the DDL commits.
    """
    config = _alembic_config(postgres_engine)
    person_id, account_id = _insert_test_account(postgres_engine)
    guard_checked = Event()
    release_downgrade = Event()

    def pause_after_guard(
        connection: Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        if not statement.startswith("SELECT EXISTS(SELECT 1 FROM talent_test_sessions"):
            return
        guard_checked.set()
        if not release_downgrade.wait(timeout=10):
            raise TimeoutError("talent downgrade guard was not released")

    with postgres_engine.begin() as connection:
        session_id = _insert_talent_migration_session(
            connection,
            person_id=person_id,
            account_id=account_id,
            catalog_version="talent-training-v1",
            status="completed",
        )
        tables_before = set(inspect(connection).get_table_names())
    pool = ThreadPoolExecutor(max_workers=1)
    event.listen(Engine, "after_cursor_execute", pause_after_guard)
    future = pool.submit(command.downgrade, config, "0058_talent_tests")
    try:
        assert guard_checked.wait(timeout=10), "downgrade did not reach its guard"
        with postgres_engine.connect() as writer:
            transaction = writer.begin()
            try:
                writer.execute(text("SET LOCAL lock_timeout = '200ms'"))
                if kind == "session":
                    # Service submission reads FOR UPDATE, then selectinloads
                    # results before writing. Letting those read locks through
                    # could deadlock a later result-table DDL lock upgrade.
                    with pytest.raises(DBAPIError, match="lock timeout"), writer.begin_nested():
                        writer.execute(
                            text("SELECT id FROM talent_test_sessions WHERE id = :id FOR UPDATE"),
                            {"id": session_id},
                        )
                with pytest.raises(DBAPIError, match="lock timeout"):
                    _insert_concurrent_talent_v2_data(
                        writer,
                        kind=kind,
                        person_id=person_id,
                        account_id=account_id,
                        session_id=session_id,
                    )
            finally:
                transaction.rollback()
        release_downgrade.set()
        future.result(timeout=10)
        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0058_talent_tests"
            )
            assert set(inspect(connection).get_table_names()) == (
                tables_before - ESPORTS_ASSESSMENT_TABLES - TEAM_MANAGEMENT_TABLES
                - CLOUD_BACKUP_TABLES - CLOUD_AUTH_TABLES - INVITED_HOUSING_TABLES - ASSISTED_PERSONNEL_TABLES
                - HOUSING_HISTORY_VOID_TABLES - HOUSING_ROOM_CARE_TABLES
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM talent_test_sessions "
                        "WHERE catalog_version = 'talent-training-v2'"
                    )
                )
                == 0
            )
            assert connection.scalar(text("SELECT count(*) FROM talent_test_results")) == 0
            assert connection.scalar(text("SELECT count(*) FROM talent_test_reports")) == 0
            with pytest.raises(DBAPIError), connection.begin_nested():
                _insert_talent_migration_result(
                    connection, session_id=session_id, test_id="dynamic-vision"
                )
    finally:
        release_downgrade.set()
        with suppress(DBAPIError, RuntimeError, AssertionError, TimeoutError):
            future.result(timeout=10)
        pool.shutdown(wait=True)
        event.remove(Engine, "after_cursor_execute", pause_after_guard)
        command.upgrade(config, "head")
        with postgres_engine.begin() as connection:
            _delete_talent_migration_rows(connection, person_id)
        _delete_test_account(postgres_engine, person_id=person_id, account_id=account_id)


@pytest.mark.parametrize("kind", ["session", "result", "report"])
def test_0059_downgrade_waits_for_prior_writer_then_refuses_without_ddl(
    postgres_engine: Engine,
    kind: str,
) -> None:
    """An uncommitted v2 writer owns the table before downgrade starts.

    Observe the actual PostgreSQL blocker PID, then commit the writer. The
    migration must re-check under its locks and refuse without revision/DDL loss.
    """
    config = _alembic_config(postgres_engine)
    person_id, account_id = _insert_test_account(postgres_engine)
    with postgres_engine.begin() as connection:
        session_id = _insert_talent_migration_session(
            connection,
            person_id=person_id,
            account_id=account_id,
            catalog_version="talent-training-v1",
            status="completed",
        )
        constraints_before = inspect(connection).get_check_constraints("talent_test_results")
    pool = ThreadPoolExecutor(max_workers=1)
    future = None
    try:
        with postgres_engine.connect() as writer:
            transaction = writer.begin()
            try:
                writer_pid = writer.scalar(text("SELECT pg_backend_pid()"))
                _insert_concurrent_talent_v2_data(
                    writer,
                    kind=kind,
                    person_id=person_id,
                    account_id=account_id,
                    session_id=session_id,
                )
                future = pool.submit(command.downgrade, config, "0058_talent_tests")
                deadline = monotonic() + 10
                blocked = False
                with postgres_engine.connect().execution_options(
                    isolation_level="AUTOCOMMIT"
                ) as observer:
                    while monotonic() < deadline and not future.done():
                        blocked = bool(
                            observer.scalar(
                                text(
                                    "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                                    "WHERE :writer_pid = ANY(pg_blocking_pids(pid)))"
                                ),
                                {"writer_pid": writer_pid},
                            )
                        )
                        if blocked:
                            break
                        sleep(0.01)
                assert blocked, "downgrade did not wait for the in-flight v2 writer"
                transaction.commit()
            finally:
                if transaction.is_active:
                    transaction.rollback()
        with pytest.raises(RuntimeError, match="存在八项天赋测试数据，拒绝降级"):
            future.result(timeout=10)
        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                CURRENT_HEAD
            )
            assert (
                inspect(connection).get_check_constraints("talent_test_results")
                == constraints_before
            )
            table = {
                "session": "talent_test_sessions",
                "result": "talent_test_results",
                "report": "talent_test_reports",
            }[kind]
            expected = 2 if kind == "session" else 1
            assert connection.scalar(text(f"SELECT count(*) FROM {table}")) == expected
    finally:
        if future is not None:
            with suppress(DBAPIError, RuntimeError, AssertionError, TimeoutError):
                future.result(timeout=10)
        pool.shutdown(wait=True)
        command.upgrade(config, "head")
        with postgres_engine.begin() as connection:
            _delete_talent_migration_rows(connection, person_id)
        _delete_test_account(postgres_engine, person_id=person_id, account_id=account_id)


def test_0059_talent_dynamic_vision_upgrade_downgrade_preserves_legacy_rows(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    person_id, account_id = _insert_test_account(postgres_engine)
    old_snapshot = {
        "catalog_version": "talent-training-v1",
        "test_ids": [
            "reactiontime",
            "aim",
            "sequence",
            "number-memory",
            "verbal-memory",
            "chimp",
            "memory",
        ],
    }
    try:
        command.downgrade(config, "0058_talent_tests")
        with postgres_engine.begin() as connection:
            tables_before = set(inspect(connection).get_table_names())
            session_id = _insert_talent_migration_session(
                connection,
                person_id=person_id,
                account_id=account_id,
                catalog_version="talent-training-v1",
            )
            legacy_result_id = _insert_talent_migration_result(
                connection,
                session_id=session_id,
                test_id="reactiontime",
            )

        command.upgrade(config, "0059_talent_dynamic_vision")
        with postgres_engine.begin() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0059_talent_dynamic_vision"
            )
            assert set(inspect(connection).get_table_names()) == tables_before
            assert connection.scalar(
                text("SELECT rules_snapshot FROM talent_test_sessions WHERE id = :id"),
                {"id": session_id},
            ) == old_snapshot
            dynamic_result_id = _insert_talent_migration_result(
                connection,
                session_id=session_id,
                test_id="dynamic-vision",
            )
            assert connection.scalar(
                text("SELECT count(*) FROM talent_test_results WHERE session_id = :id"),
                {"id": session_id},
            ) == 2
            connection.execute(
                text("DELETE FROM talent_test_results WHERE id = :id"),
                {"id": dynamic_result_id},
            )

        command.downgrade(config, "0058_talent_tests")
        with postgres_engine.begin() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0058_talent_tests"
            )
            assert connection.execute(
                text(
                    "SELECT id, rules_snapshot FROM talent_test_sessions WHERE id = :id"
                ),
                {"id": session_id},
            ).one() == (session_id, old_snapshot)
            assert connection.scalar(
                text("SELECT count(*) FROM talent_test_results WHERE id = :id"),
                {"id": legacy_result_id},
            ) == 1
            with pytest.raises(DBAPIError), connection.begin_nested():
                _insert_talent_migration_result(
                    connection,
                    session_id=session_id,
                    test_id="dynamic-vision",
                )
    finally:
        command.upgrade(config, "head")
        with postgres_engine.begin() as connection:
            _delete_talent_migration_rows(connection, person_id)
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )


@pytest.mark.parametrize(
    "new_data", ["v2_session", "v3_session", "dynamic_result", "v2_report", "v3_report"]
)
def test_0059_talent_dynamic_vision_downgrade_rejects_new_data_without_deleting(
    postgres_engine: Engine,
    new_data: str,
) -> None:
    config = _alembic_config(postgres_engine)
    person_id, account_id = _insert_test_account(postgres_engine)
    try:
        command.downgrade(config, "0059_talent_dynamic_vision")
        with postgres_engine.begin() as connection:
            version = "talent-training-v3" if new_data.startswith("v3_") else "talent-training-v2"
            session_id = _insert_talent_migration_session(
                connection,
                person_id=person_id,
                account_id=account_id,
                catalog_version=(
                    version if new_data.endswith("_session") else "talent-training-v1"
                ),
                status="completed" if new_data.endswith("_report") else "in_progress",
            )
            if new_data == "dynamic_result":
                _insert_talent_migration_result(
                    connection,
                    session_id=session_id,
                    test_id="dynamic-vision",
                )
            elif new_data.endswith("_report"):
                connection.execute(
                    text(
                        "INSERT INTO talent_test_reports "
                        "(id, session_id, report_snapshot) VALUES "
                        "(:id, :session_id, CAST(:snapshot AS jsonb))"
                    ),
                    {
                        "id": uuid4(),
                        "session_id": session_id,
                        "snapshot": json.dumps({"rules": {"catalog_version": version}}),
                    },
                )
            before = {
                table: connection.scalar(text(f'SELECT count(*) FROM "{table}"'))
                for table in TALENT_TEST_TABLES
            }
            snapshots_before = {
                table: connection.execute(text(f'SELECT * FROM "{table}" ORDER BY id')).all()
                for table in TALENT_TEST_TABLES
            }
            if new_data.startswith("v3_"):
                assert before["talent_test_results"] == 0

        with pytest.raises(
            RuntimeError,
            match="存在八项天赋测试数据，拒绝降级；未删除任何记录",
        ):
            command.downgrade(config, "0058_talent_tests")

        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0059_talent_dynamic_vision"
            )
            after = {
                table: connection.scalar(text(f'SELECT count(*) FROM "{table}"'))
                for table in TALENT_TEST_TABLES
            }
            assert after == before
            assert {
                table: connection.execute(text(f'SELECT * FROM "{table}" ORDER BY id')).all()
                for table in TALENT_TEST_TABLES
            } == snapshots_before
    finally:
        command.upgrade(config, "head")
        with postgres_engine.begin() as connection:
            _delete_talent_migration_rows(connection, person_id)
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )


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
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "0005_admissions_enrollment"
            )
            assert connection.execute(
                text("SELECT key, name, sensitive FROM permissions WHERE id = :permission_id"),
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
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "0005_admissions_enrollment"
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM role_permissions "
                        "WHERE role_id = :role_id AND permission_id = :permission_id "
                        "AND scope_kind = 'assigned_class'"
                    ),
                    params,
                )
                == 1
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM permissions WHERE id = :permission_id"),
                    {"permission_id": PORTAL_PERMISSION_ID},
                )
                == 0
            )
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
            assert (
                connection.scalar(
                    text("SELECT EXISTS (SELECT 1 FROM user_permission_overrides WHERE id = :id)"),
                    {"id": override_id},
                )
                is True
            )
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
                text("UPDATE permissions SET name = '已修改名单权限' WHERE id = :permission_id"),
                {"permission_id": PORTAL_PERMISSION_ID},
            )

        with pytest.raises(
            RuntimeError,
            match="cannot downgrade portal with modified permission seed",
        ):
            command.downgrade(config, "0005_admissions_enrollment")

        with postgres_engine.connect() as connection:
            _assert_portal_ddl_is_intact(connection)
            assert (
                connection.scalar(
                    text("SELECT name FROM permissions WHERE id = :permission_id"),
                    {"permission_id": PORTAL_PERMISSION_ID},
                )
                == "已修改名单权限"
            )
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE permissions SET name = '查看教练下一期名单' WHERE id = :permission_id"
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
            assert (
                connection.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM role_permissions "
                        "WHERE role_id = :role_id AND permission_id = :permission_id "
                        "AND scope_kind = 'family')"
                    ),
                    params,
                )
                is True
            )
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
            assert (
                connection.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM role_permissions "
                        "WHERE role_id = :role_id AND permission_id = :permission_id "
                        "AND scope_kind = 'global')"
                    ),
                    params,
                )
                is True
            )
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
            assert EVALUATION_FOUNDATION_TABLES.isdisjoint(set(inspector.get_table_names()))
            assert "game_account_limit" not in {
                column["name"] for column in inspector.get_columns("student_profiles")
            }
            assert "valid_from" not in {
                column["name"] for column in inspector.get_columns("user_permission_overrides")
            }
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM permissions WHERE id = ANY(CAST(:ids AS uuid[]))"),
                    {"ids": list(EVALUATION_FOUNDATION_PERMISSION_IDS.values())},
                )
                == 0
            )

        command.upgrade(config, "0008_evaluation_data_media_foundation")
        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0008_evaluation_data_media_foundation"
            )
            assert EVALUATION_FOUNDATION_TABLES <= set(inspect(connection).get_table_names())
            assert connection.scalar(text("SELECT count(*) FROM project_analysis_profiles")) == len(
                INITIAL_PROJECT_IDS
            )
    finally:
        command.upgrade(config, "head")


def test_system_evaluation_upgrade_downgrade_upgrade_restores_clean_schema(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0011_housing_real_name_reporting")
    try:
        with postgres_engine.connect() as connection:
            inspector = inspect(connection)
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0011_housing_real_name_reporting"
            )
            assert SYSTEM_EVALUATION_TABLES.isdisjoint(set(inspector.get_table_names()))
            assert {"media_slots", "result_schema_version"}.isdisjoint(
                {column["name"] for column in inspector.get_columns("project_analysis_profiles")}
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM permissions WHERE id = ANY(CAST(:ids AS uuid[]))"),
                    {"ids": list(SYSTEM_EVALUATION_PERMISSION_IDS.values())},
                )
                == 0
            )

        command.upgrade(config, "0012_system_evaluation_processing")
        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0012_system_evaluation_processing"
            )
            tables = set(inspect(connection).get_table_names())
            later_tables = {"system_evaluation_manual_drafts", "evaluation_quarantine_cleanup"}
            assert (SYSTEM_EVALUATION_TABLES - later_tables) <= tables
            assert later_tables.isdisjoint(tables)
    finally:
        command.upgrade(config, "head")
    assert SYSTEM_EVALUATION_TABLES <= set(inspect(postgres_engine).get_table_names())


def test_system_evaluation_manual_review_upgrade_downgrade_roundtrip(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0012_system_evaluation_processing")
    try:
        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0012_system_evaluation_processing"
            )
            assert "system_evaluation_manual_drafts" not in inspect(connection).get_table_names()

        command.upgrade(config, "head")
        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                CURRENT_HEAD
            )
            assert "system_evaluation_manual_drafts" in inspect(connection).get_table_names()
    finally:
        command.upgrade(config, "head")


def test_teaching_reports_upgrade_downgrade_upgrade_restores_clean_schema(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0013_system_evaluation_manual_review")
    try:
        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0013_system_evaluation_manual_review"
            )
            assert TEACHING_REPORT_TABLES.isdisjoint(set(inspect(connection).get_table_names()))
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM permissions WHERE id = ANY(CAST(:ids AS uuid[]))"),
                    {"ids": list(TEACHING_REPORT_PERMISSION_IDS.values())},
                )
                == 0
            )

        command.upgrade(config, "head")
        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                CURRENT_HEAD
            )
            assert TEACHING_REPORT_TABLES <= set(inspect(connection).get_table_names())
    finally:
        command.upgrade(config, "head")


def test_teaching_reports_downgrade_refuses_business_rows_before_ddl(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    author_person_id, author_account_id = _insert_test_account(postgres_engine)
    ids = _insert_teaching_report_root(
        postgres_engine,
        author_person_id=author_person_id,
        author_account_id=author_account_id,
    )
    try:
        with pytest.raises(
            RuntimeError,
            match="cannot downgrade teaching reports with business data",
        ):
            command.downgrade(config, "0013_system_evaluation_manual_review")

        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                CURRENT_HEAD
            )
            assert (
                connection.scalar(
                    text("SELECT EXISTS (SELECT 1 FROM student_reports WHERE id = :id)"),
                    {"id": ids["report_id"]},
                )
                is True
            )
    finally:
        command.upgrade(config, "head")
        _delete_teaching_report_root(
            postgres_engine,
            ids=ids,
            author_person_id=author_person_id,
        )
        _delete_test_account(
            postgres_engine,
            person_id=author_person_id,
            account_id=author_account_id,
        )


def test_teaching_reports_downgrade_refuses_extra_role_grant_before_ddl(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    permission_id = TEACHING_REPORT_PERMISSION_IDS["student_reports.write"]
    with postgres_engine.connect() as connection:
        coach_role_id = connection.scalar(text("SELECT id FROM roles WHERE key = 'coach'"))
    assert coach_role_id is not None
    params = {"role_id": coach_role_id, "permission_id": permission_id}
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
            match=("cannot downgrade teaching reports with customized permissions or grants"),
        ):
            command.downgrade(config, "0013_system_evaluation_manual_review")

        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                CURRENT_HEAD
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM role_permissions "
                        "WHERE role_id = :role_id AND permission_id = :permission_id "
                        "AND scope_kind = 'assigned_class')"
                    ),
                    params,
                )
                is True
            )
    finally:
        command.upgrade(config, "head")
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM role_permissions "
                    "WHERE role_id = :role_id AND permission_id = :permission_id "
                    "AND scope_kind = 'assigned_class'"
                ),
                params,
            )


def test_teaching_report_history_rows_are_immutable(
    postgres_engine: Engine,
) -> None:
    author_person_id, author_account_id = _insert_test_account(postgres_engine)
    ids = _insert_teaching_report_root(
        postgres_engine,
        author_person_id=author_person_id,
        author_account_id=author_account_id,
    )
    version_id = uuid4()
    review_id = uuid4()
    grant_id = uuid4()
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO student_report_versions "
                    "(id, report_id, version, source, body, dimension_values, "
                    "submitted_by_user_id, submitted_at) VALUES "
                    "(:id, :report_id, 1, 'initial', '首份日报', '{}'::jsonb, "
                    ":actor_id, TIMESTAMPTZ '2026-08-01 20:00:00+08')"
                ),
                {
                    "id": version_id,
                    "report_id": ids["report_id"],
                    "actor_id": author_account_id,
                },
            )
            connection.execute(
                text(
                    "UPDATE student_reports SET status = 'pending_review', "
                    "current_version_id = :version_id, "
                    "submitted_at = TIMESTAMPTZ '2026-08-01 20:00:00+08' "
                    "WHERE id = :report_id"
                ),
                {"version_id": version_id, "report_id": ids["report_id"]},
            )
            connection.execute(
                text(
                    "INSERT INTO student_report_reviews "
                    "(id, report_id, version_id, decision, reviewed_by_user_id, "
                    "reviewed_at) VALUES "
                    "(:id, :report_id, :version_id, 'approved', :actor_id, "
                    "TIMESTAMPTZ '2026-08-02 09:00:00+08')"
                ),
                {
                    "id": review_id,
                    "report_id": ids["report_id"],
                    "version_id": version_id,
                    "actor_id": author_account_id,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO student_report_window_grants "
                    "(id, report_id, grant_kind, reason, opens_at, expires_at, "
                    "granted_by_user_id) VALUES "
                    "(:id, :report_id, 'revision', '迁移不可变测试', "
                    "TIMESTAMPTZ '2026-08-25 00:00:00+08', "
                    "TIMESTAMPTZ '2026-08-28 23:59:59+08', :actor_id)"
                ),
                {
                    "id": grant_id,
                    "report_id": ids["report_id"],
                    "actor_id": author_account_id,
                },
            )

            mutations = (
                (
                    ("UPDATE student_report_versions SET body = '被篡改' WHERE id = :id"),
                    version_id,
                ),
                (
                    "DELETE FROM student_report_reviews WHERE id = :id",
                    review_id,
                ),
                (
                    ("UPDATE student_report_window_grants SET reason = '被篡改' WHERE id = :id"),
                    grant_id,
                ),
            )
            for statement, resource_id in mutations:
                with (
                    pytest.raises(
                        DBAPIError,
                        match="teaching report history is immutable",
                    ),
                    connection.begin_nested(),
                ):
                    connection.execute(text(statement), {"id": resource_id})
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE student_reports SET status = 'draft', "
                    "current_version_id = NULL, submitted_at = NULL "
                    "WHERE id = :report_id"
                ),
                {"report_id": ids["report_id"]},
            )
            for table_name, trigger_name in (
                ("student_report_versions", "student_report_versions_immutable"),
                ("student_report_reviews", "student_report_reviews_immutable"),
                (
                    "student_report_window_grants",
                    "student_report_window_grants_immutable",
                ),
            ):
                connection.execute(text(f"ALTER TABLE {table_name} DISABLE TRIGGER {trigger_name}"))
            try:
                connection.execute(
                    text("DELETE FROM student_report_reviews WHERE id = :id"),
                    {"id": review_id},
                )
                connection.execute(
                    text("DELETE FROM student_report_window_grants WHERE id = :id"),
                    {"id": grant_id},
                )
                connection.execute(
                    text("DELETE FROM student_report_versions WHERE id = :id"),
                    {"id": version_id},
                )
            finally:
                for table_name, trigger_name in (
                    ("student_report_versions", "student_report_versions_immutable"),
                    ("student_report_reviews", "student_report_reviews_immutable"),
                    (
                        "student_report_window_grants",
                        "student_report_window_grants_immutable",
                    ),
                ):
                    connection.execute(
                        text(f"ALTER TABLE {table_name} ENABLE TRIGGER {trigger_name}")
                    )
        _delete_teaching_report_root(
            postgres_engine,
            ids=ids,
            author_person_id=author_person_id,
        )
        _delete_test_account(
            postgres_engine,
            person_id=author_person_id,
            account_id=author_account_id,
        )


def test_system_evaluation_manual_review_downgrade_refuses_draft_before_ddl(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    person_id, account_id = _insert_test_account(postgres_engine)
    upload_session_id, task_id = _insert_system_evaluation_task(postgres_engine, account_id)
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO system_evaluation_manual_drafts "
                    "(task_id, last_edited_by_user_id, fields) "
                    "VALUES (:task_id, :account_id, '{\"partial\": true}'::jsonb)"
                ),
                {"task_id": task_id, "account_id": account_id},
            )

        with pytest.raises(
            RuntimeError,
            match="cannot downgrade system evaluation review with manual drafts",
        ):
            command.downgrade(config, "0012_system_evaluation_processing")

        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                CURRENT_HEAD
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM system_evaluation_manual_drafts "
                        "WHERE task_id = :task_id)"
                    ),
                    {"task_id": task_id},
                )
                is True
            )
    finally:
        _delete_system_evaluation_task(
            postgres_engine,
            upload_session_id=upload_session_id,
            task_id=task_id,
        )
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )
        command.upgrade(config, "head")


def test_reviewed_result_survives_manual_review_downgrade_with_old_guard(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    person_id, account_id = _insert_test_account(postgres_engine)
    upload_session_id, task_id = _insert_system_evaluation_task(postgres_engine, account_id)
    result_id = uuid4()
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO system_evaluation_result_versions "
                    "(id, task_id, version, source, fields, confidences, warnings, "
                    "components, missing_fields, score, grade, advice, "
                    "result_schema_version) VALUES (:id, :task_id, 1, 'automatic', "
                    "'{}'::jsonb, '{}'::jsonb, '[]'::jsonb, '[]'::jsonb, "
                    "'[]'::jsonb, 88, 'A', 'keep going', '1')"
                ),
                {"id": result_id, "task_id": task_id},
            )
            connection.execute(
                text(
                    "UPDATE system_evaluation_result_versions SET "
                    "review_decision = 'approved', reviewed_by_user_id = :account_id, "
                    "reviewed_at = CURRENT_TIMESTAMP, published_at = CURRENT_TIMESTAMP "
                    "WHERE id = :result_id"
                ),
                {"account_id": account_id, "result_id": result_id},
            )

        command.downgrade(config, "0012_system_evaluation_processing")
        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0012_system_evaluation_processing"
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT review_decision FROM system_evaluation_result_versions "
                        "WHERE id = :result_id"
                    ),
                    {"result_id": result_id},
                )
                == "approved"
            )
            savepoint = connection.begin_nested()
            try:
                with pytest.raises(DBAPIError, match="system evaluation result .*immutable"):
                    connection.execute(
                        text(
                            "UPDATE system_evaluation_result_versions SET advice = 'smuggled' "
                            "WHERE id = :result_id"
                        ),
                        {"result_id": result_id},
                    )
            finally:
                savepoint.rollback()
            assert connection.scalar(text("SELECT 1")) == 1
    finally:
        command.upgrade(config, "head")
        _delete_system_evaluation_task(
            postgres_engine,
            upload_session_id=upload_session_id,
            task_id=task_id,
            result_id=result_id,
        )
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )


def test_system_evaluation_downgrade_refuses_business_rows_before_ddl(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    person_id, account_id = _insert_test_account(postgres_engine)
    upload_session_id = uuid4()
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO system_evaluation_upload_sessions "
                    "(id, created_by_user_id, project_id, status, expires_at) "
                    "VALUES (:id, :account_id, :project_id, 'open', "
                    "CURRENT_TIMESTAMP + INTERVAL '1 hour')"
                ),
                {
                    "id": upload_session_id,
                    "account_id": account_id,
                    "project_id": INITIAL_PROJECT_IDS[0],
                },
            )

        with pytest.raises(
            RuntimeError,
            match="cannot downgrade system evaluation with business data",
        ):
            command.downgrade(config, "0011_housing_real_name_reporting")

        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                CURRENT_HEAD
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM "
                        "system_evaluation_upload_sessions WHERE id = :id)"
                    ),
                    {"id": upload_session_id},
                )
                is True
            )
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM system_evaluation_upload_sessions WHERE id = :id"),
                {"id": upload_session_id},
            )
        command.upgrade(config, "head")
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )


def test_system_evaluation_downgrade_refuses_changed_analysis_profile(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    person_id, account_id = _insert_test_account(postgres_engine)
    profile_id = uuid4()
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO project_analysis_profiles ("
                    "id, project_id, version, mode, required_fields, media_slots, "
                    "result_schema_version, confidence_thresholds, "
                    "auto_publish_warning_levels, publish_mode, effective_from, "
                    "configured_by_user_id, change_reason) VALUES ("
                    ":id, :project_id, 2, 'manual_only', '[\"score\"]'::jsonb, "
                    '\'[{"key": "result", "label": "result", '
                    "\"required\": true}]'::jsonb, '1', '{}'::jsonb, "
                    "'[]'::jsonb, 'review_required', CURRENT_TIMESTAMP, "
                    ":account_id, 'migration downgrade guard')"
                ),
                {
                    "id": profile_id,
                    "project_id": INITIAL_PROJECT_IDS[0],
                    "account_id": account_id,
                },
            )

        with pytest.raises(
            RuntimeError,
            match="cannot downgrade system evaluation with changed analysis profiles",
        ):
            command.downgrade(config, "0011_housing_real_name_reporting")

        with postgres_engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT EXISTS (SELECT 1 FROM project_analysis_profiles WHERE id = :id)"),
                    {"id": profile_id},
                )
                is True
            )
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                CURRENT_HEAD
            )
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM project_analysis_profiles WHERE id = :id"),
                {"id": profile_id},
            )
        command.upgrade(config, "head")
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )


def test_system_evaluation_downgrade_refuses_modified_permission_before_ddl(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    permission_id = SYSTEM_EVALUATION_PERMISSION_IDS["evaluation_jobs.review"]
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text("UPDATE permissions SET name = '管理员自定义评价审核' WHERE id = :id"),
                {"id": permission_id},
            )

        with pytest.raises(
            RuntimeError,
            match="cannot downgrade system evaluation with customized permission",
        ):
            command.downgrade(config, "0011_housing_real_name_reporting")

        with postgres_engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT name FROM permissions WHERE id = :id"),
                    {"id": permission_id},
                )
                == "管理员自定义评价审核"
            )
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                CURRENT_HEAD
            )
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text("UPDATE permissions SET name = '审核系统评价' WHERE id = :id"),
                {"id": permission_id},
            )
        command.upgrade(config, "head")


def test_system_evaluation_downgrade_refuses_extra_role_grant_before_ddl(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    role_id = UUID("10000000-0000-4000-8000-000000000002")
    permission_id = SYSTEM_EVALUATION_PERMISSION_IDS["evaluation_jobs.review"]
    params = {"role_id": role_id, "permission_id": permission_id}
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO role_permissions (role_id, permission_id, scope_kind) "
                    "VALUES (:role_id, :permission_id, 'assigned_class')"
                ),
                params,
            )

        with pytest.raises(
            RuntimeError,
            match="cannot downgrade system evaluation with customized grant",
        ):
            command.downgrade(config, "0011_housing_real_name_reporting")

        with postgres_engine.connect() as connection:
            assert (
                connection.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM role_permissions "
                        "WHERE role_id = :role_id AND permission_id = :permission_id "
                        "AND scope_kind = 'assigned_class')"
                    ),
                    params,
                )
                is True
            )
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                CURRENT_HEAD
            )
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM role_permissions WHERE role_id = :role_id "
                    "AND permission_id = :permission_id "
                    "AND scope_kind = 'assigned_class'"
                ),
                params,
            )
        command.upgrade(config, "head")


def test_evaluation_foundation_downgrade_refuses_business_rows_before_ddl(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0007_evaluation_page_integration")
    command.upgrade(config, "0008_evaluation_data_media_foundation")
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
                "0008_evaluation_data_media_foundation"
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
                assert (
                    connection.scalar(
                        text(f"SELECT EXISTS (SELECT 1 FROM {table_name} WHERE id = :id)"),
                        {"id": row_id},
                    )
                    is True
                )
            assert (
                connection.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM "
                        "cohort_staff_assignment_permissions "
                        "WHERE assignment_id = :assignment_id)"
                    ),
                    {"assignment_id": assignment_id},
                )
                is True
            )
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
                text("DELETE FROM cohort_staff_assignment_permissions WHERE assignment_id = :id"),
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
    expected_revision: str = CURRENT_HEAD,
) -> None:
    assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (expected_revision)
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
            assert HOUSING_FOUNDATION_TABLES.isdisjoint(set(inspect(connection).get_table_names()))
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM permissions WHERE id = ANY(CAST(:ids AS uuid[]))"),
                    {"ids": list(HOUSING_PERMISSION_IDS.values())},
                )
                == 0
            )

        command.upgrade(config, "0009_housing_room_status_foundation")
        with postgres_engine.connect() as connection:
            _assert_housing_downgrade_refusal_preserved(
                connection,
                "0009_housing_room_status_foundation",
            )
            assert connection.scalar(text("SELECT count(*) FROM housing_rooms")) == 188
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM permissions WHERE id = ANY(CAST(:ids AS uuid[]))"),
                    {"ids": list(HOUSING_PERMISSION_IDS.values())},
                )
                == 2
            )
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
                text("UPDATE housing_rooms SET notes = :notes WHERE room_number = :room_number"),
                {"notes": custom_notes, "room_number": room_number},
            )

        with pytest.raises(
            RuntimeError,
            match="cannot downgrade housing foundation with customized housing data",
        ):
            command.downgrade(config, "0008_evaluation_data_media_foundation")

        with postgres_engine.connect() as connection:
            _assert_housing_downgrade_refusal_preserved(connection)
            assert (
                connection.scalar(
                    text("SELECT notes FROM housing_rooms WHERE room_number = :room_number"),
                    {"room_number": room_number},
                )
                == custom_notes
            )
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text("UPDATE housing_rooms SET notes = NULL WHERE room_number = :room_number"),
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
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM housing_rooms WHERE id = :id"),
                    {"id": room_id},
                )
                == 1
            )
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
            assert (
                connection.scalar(
                    text("SELECT name FROM permissions WHERE id = :id"),
                    {"id": permission_id},
                )
                == custom_name
            )
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
            assert (
                connection.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM role_permissions "
                        "WHERE role_id = :role_id AND permission_id = :permission_id "
                        "AND scope_kind = 'global')"
                    ),
                    params,
                )
                is False
            )
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
                text("SELECT scope_kind, scope_id FROM user_permission_overrides WHERE id = :id"),
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
        role_id = connection.scalar(text("SELECT id FROM roles WHERE key = 'customer_service'"))
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
            assert (
                connection.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM role_permissions "
                        "WHERE role_id = :role_id AND permission_id = :permission_id "
                        "AND scope_kind = 'building')"
                    ),
                    params,
                )
                is True
            )
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
    expected_revision: str = CURRENT_HEAD,
) -> None:
    assert connection.scalar(text("SELECT version_num FROM alembic_version")) == expected_revision
    assert HOUSING_OCCUPANCY_TABLES <= set(inspect(connection).get_table_names())
    assert connection.scalar(
        text("SELECT count(*) FROM permissions WHERE id = ANY(CAST(:ids AS uuid[]))"),
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
            assert HOUSING_OCCUPANCY_TABLES.isdisjoint(set(inspect(connection).get_table_names()))
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM permissions WHERE id = ANY(CAST(:ids AS uuid[]))"),
                    {"ids": list(HOUSING_OCCUPANCY_PERMISSION_IDS.values())},
                )
                == 0
            )
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
            assert (
                connection.scalar(
                    text("SELECT EXISTS (SELECT 1 FROM housing_stays WHERE id = :id)"),
                    {"id": stay_id},
                )
                is True
            )
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
            assert (
                connection.scalar(
                    text("SELECT name FROM permissions WHERE id = :id"),
                    {"id": permission_id},
                )
                == custom_name
            )
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
        student_role_id = connection.scalar(text("SELECT id FROM roles WHERE key = 'student'"))
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
            assert (
                connection.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM role_permissions "
                        "WHERE role_id = :role_id AND permission_id = :permission_id "
                        "AND scope_kind = 'self')"
                    ),
                    params,
                )
                is False
            )
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
            assert (
                connection.scalar(
                    text("SELECT EXISTS (SELECT 1 FROM user_permission_overrides WHERE id = :id)"),
                    {"id": override_id},
                )
                is True
            )
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
        role_id = connection.scalar(text("SELECT id FROM roles WHERE key = 'customer_service'"))
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
            assert (
                connection.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM role_permissions "
                        "WHERE role_id = :role_id AND permission_id = :permission_id "
                        "AND scope_kind = 'global')"
                    ),
                    params,
                )
                is True
            )
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
            assert (
                connection.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM "
                        "cohort_staff_assignment_permissions "
                        "WHERE assignment_id = :assignment_id "
                        "AND permission_id = :permission_id)"
                    ),
                    {"assignment_id": assignment_id, "permission_id": permission_id},
                )
                is True
            )
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
            text("SELECT id FROM housing_rooms WHERE room_type <> 'functional' ORDER BY id LIMIT 1")
        )
        customer_service_role_id = connection.scalar(
            text("SELECT id FROM roles WHERE key = 'customer_service'")
        )
    assert room_id is not None
    assert customer_service_role_id is not None

    mutation_sql = {
        "permission": (
            "UPDATE permissions SET name = '并发修改住宿权限' WHERE id = :permission_id"
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
            assert HOUSING_OCCUPANCY_TABLES.isdisjoint(set(inspect(connection).get_table_names()))
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
                text("UPDATE permissions SET name = '管理住宿与预约' WHERE id = :permission_id"),
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
