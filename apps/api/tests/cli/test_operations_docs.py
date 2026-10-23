from __future__ import annotations

import ast
import re
import shlex
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
REGISTRATION_OPERATIONS = REPOSITORY_ROOT / "docs/operations/self-service-registration-local.md"
STAGE2_OPERATIONS = REPOSITORY_ROOT / "docs/operations/stage2-local-acceptance.md"
LOCAL_CONTAINER_OPERATIONS = REPOSITORY_ROOT / "docs/operations/local-container-acceptance.md"
TASK2_TASK7_COMPLETION_PLAN = (
    REPOSITORY_ROOT / "docs/superpowers/plans/2026-08-17-task2-task7-completion.md"
)
HEADING_PATTERN = re.compile(r"^(?P<marks>#{1,6})\s+(?P<title>.+?)\s*$")
NON_CURRENT_PREFIX_PATTERN = re.compile(
    r"(?:"
    r"(?:尚未|仍未|并未|未|并非|不是|没有)"
    r"(?:声称|宣称|写成|描述为|视为|认为)?[‘’“”\"'`：:\s]*|"
    r"(?:不得|禁止|不能|不可|不应)(?:据此)?"
    r"(?:声称|宣称|写成|描述为|视为|认为)?[‘’“”\"'`：:\s]*|"
    r"不表示[‘’“”\"'`：:\s]*|"
    r"(?:历史|旧版|此前)(?:记录|文档|版本)?(?:曾经?)?"
    r"(?:误写|写作|声称|描述为|称为)?[‘’“”\"'`：:\s]*|"
    r"曾(?:经)?(?:误写|写作|声称|描述为)?[‘’“”\"'`：:\s]*|"
    r"误写(?:为)?[‘’“”\"'`：:\s]*|"
    r"无(?:任何|可维护的|可用的)?\s*"
    r")$"
)
NON_CURRENT_SUFFIX_PATTERN = re.compile(
    r"^[‘’“”\"'`]*[\s，,:：]*(?:"
    r"(?:的)?(?:这一?|该)?(?:说法|表述|描述)(?:已)?(?:被)?(?:禁止|否定|纠正)|"
    r"(?:已)?(?:被)?(?:否定|纠正)|"
    r"(?:仍)?(?:延期|待后续)[^。；！？]{0,12}"
    r"(?:实现|开发|开放|完成|上线)(?=[。；！？]|$)|"
    r"(?:是|为|系|属于|来自)(?:历史|旧版|此前)(?:记录|版本|误写|表述)|"
    r"(?:并非|不是)(?:当前|现有|本单元)?(?:状态|事实|实现|结论)|"
    r"(?:现)?已纠正"
    r")"
)


def _text(path: str) -> str:
    return (REPOSITORY_ROOT / path).read_text(encoding="utf-8")


def _imported_app_packages(*paths: str) -> set[str]:
    packages: set[str] = set()
    for path in paths:
        tree = ast.parse(_text(path), filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                modules = (node.module,) if node.module is not None else ()
            elif isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            else:
                continue
            for module in modules:
                parts = module.split(".")
                if len(parts) >= 2 and parts[0] == "app":
                    packages.add(parts[1])
    return packages


def _shell_array(document: str, name: str) -> list[str]:
    match = re.search(
        rf"{re.escape(name)}=\(\n(?P<body>.*?)\n?\)",
        document,
        re.DOTALL,
    )
    assert match is not None, f"missing shell array: {name}"
    return shlex.split(match.group("body"))


def _markdown_section(document: str, title: str) -> str:
    lines = document.splitlines()
    start: int | None = None
    level: int | None = None
    for index, line in enumerate(lines):
        match = HEADING_PATTERN.match(line)
        if match is not None and match.group("title").strip(" #") == title:
            start = index
            level = len(match.group("marks"))
            break

    assert start is not None and level is not None, f"missing Markdown section: {title}"
    end = len(lines)
    for index in range(start + 1, len(lines)):
        match = HEADING_PATTERN.match(lines[index])
        if match is not None and len(match.group("marks")) <= level:
            end = index
            break
    return "\n".join(lines[start:end])


def _markdown_lead(document: str) -> str:
    lines = document.splitlines()
    end = next(
        (index for index, line in enumerate(lines) if line.startswith("## ")),
        len(lines),
    )
    return "\n".join(lines[:end])


def _line_asserts_claim(line: str, claim: str) -> bool:
    for match in re.finditer(re.escape(claim), line):
        prefix = line[: match.start()]
        suffix = line[match.end() :]
        if NON_CURRENT_PREFIX_PATTERN.search(prefix) is not None:
            continue
        if NON_CURRENT_SUFFIX_PATTERN.search(suffix) is not None:
            continue
        return True
    return False


def test_self_service_registration_operations_document_is_truthful() -> None:
    assert REGISTRATION_OPERATIONS.is_file()
    text = REGISTRATION_OPERATIONS.read_text(encoding="utf-8")
    for required in (
        "scripts/run-self-service-registration-e2e.sh",
        "本地测试发送器",
        "REGISTRATION_TEST_MAILBOX_DIR",
        "0017_self_service_registration",
        "0019_guardian_invitation_tokens",
        "0020_registration_review_pending_guardian",
        "0021_registration_review_candidate_binding",
        "项目选择与支付仍未开放",
        "Docker/NAS 仍未验证",
        "30 天",
        "7 天",
        "注册会话 Cookie",
        "正式会话 Cookie",
        "TRUSTED_PROXY_CIDRS",
        "app.cli.cleanup_registration_applications --limit 500",
        "固定 SHA-256 测试别名",
        "不作为密码学不可逆或长期匿名化保证",
    ):
        assert required in text
    for forbidden in (
        "真实短信已接入",
        "真实邮件已接入",
        "生产可用",
        "支付已完成",
        "Docker/NAS 已完成",
        "不可逆别名",
    ):
        assert forbidden not in text

    readme = _text("README.md")
    assert "## 自助注册本地验收（已完成）" in readme
    assert "自助注册本地实现与完整门禁已完成" in readme
    assert "scripts/run-self-service-registration-e2e.sh" in readme
    design = _text("docs/superpowers/specs/2026-08-14-self-service-registration-design.md")
    assert "- **状态：** 本地实现与完整门禁已完成" in design
    assert "0019_guardian_invitation_tokens" in design


def test_schema_manifest_tracks_current_head_and_all_required_tables() -> None:
    manifest = _text("scripts/db-schema-manifest.sh")
    assert "EXPECTED_ALEMBIC_HEAD=0071_housing_room_care" in manifest
    assert "0071_housing_room_care" in _shell_array(manifest, "MIGRATION_CHAIN")
    assert "housing_room_photos" in _shell_array(manifest, "REQUIRED_TABLES")
    assert "housing_room_photos" in _shell_array(manifest, "ROW_COUNT_TABLES")
    assert "0070_housing_history_void" in _shell_array(manifest, "MIGRATION_CHAIN")
    assert "0068_deferred_email_claims" in _shell_array(manifest, "MIGRATION_CHAIN")
    assert "0067_person_profile_notes" in _shell_array(manifest, "MIGRATION_CHAIN")
    assert "0066_assisted_personnel" in _shell_array(manifest, "MIGRATION_CHAIN")
    assert "0064_cloud_auth_builtin_import" in _shell_array(manifest, "MIGRATION_CHAIN")
    assert "0065_invited_housing_visibility" in _shell_array(manifest, "MIGRATION_CHAIN")
    assert "0062_cloud_backups" in _shell_array(manifest, "MIGRATION_CHAIN")
    assert "0058_talent_tests" in _shell_array(manifest, "MIGRATION_CHAIN")
    assert "0059_talent_dynamic_vision" in _shell_array(manifest, "MIGRATION_CHAIN")
    assert "0060_esports_assessments" in _shell_array(manifest, "MIGRATION_CHAIN")
    assert "0061_team_management" in _shell_array(manifest, "MIGRATION_CHAIN")
    assert "0057_procurement_idempotency" in _shell_array(manifest, "MIGRATION_CHAIN")
    assert "0056_evaluation_upload_cleanup" in _shell_array(manifest, "MIGRATION_CHAIN")
    assert "0055_manual_payment_idempotency" in _shell_array(manifest, "MIGRATION_CHAIN")
    assert "0054_admission_order_numbers" in _shell_array(manifest, "MIGRATION_CHAIN")
    assert "0053_revenue_share_processing" in _shell_array(manifest, "MIGRATION_CHAIN")
    assert "0026_verified_login_identifiers" in _shell_array(
        manifest,
        "MIGRATION_CHAIN",
    )
    assert "0027_super_admin_administration_roles" in _shell_array(
        manifest,
        "MIGRATION_CHAIN",
    )
    assert "0028_registration_channel_settings" in _shell_array(
        manifest,
        "MIGRATION_CHAIN",
    )
    assert "0029_staff_approval_optional_reason" in _shell_array(
        manifest,
        "MIGRATION_CHAIN",
    )
    assert "0034_person_nicknames" in _shell_array(
        manifest,
        "MIGRATION_CHAIN",
    )
    assert "0039_procurement_product_links" in _shell_array(
        manifest,
        "MIGRATION_CHAIN",
    )
    assert "0040_procurement_reimbursement_status" in _shell_array(
        manifest,
        "MIGRATION_CHAIN",
    )
    assert "0042_asset_purchase_details" in _shell_array(
        manifest,
        "MIGRATION_CHAIN",
    )
    assert "0043_operations_monitoring" in _shell_array(
        manifest,
        "MIGRATION_CHAIN",
    )
    assert "0044_travel_report_conversions" in _shell_array(
        manifest,
        "MIGRATION_CHAIN",
    )
    assert "0045_enrollment_program_catalog" in _shell_array(
        manifest,
        "MIGRATION_CHAIN",
    )
    assert "0047_self_referral_codes" in _shell_array(
        manifest,
        "MIGRATION_CHAIN",
    )
    assert "0048_admin_asset_boundary" in _shell_array(
        manifest,
        "MIGRATION_CHAIN",
    )
    assert "0049_project_coach_selection" in _shell_array(
        manifest,
        "MIGRATION_CHAIN",
    )
    required_tables = _shell_array(manifest, "REQUIRED_TABLES")
    assert len(required_tables) == 148
    assert "housing_stay_void_operations" in required_tables
    assert "housing_stay_void_operations" in _shell_array(manifest, "ROW_COUNT_TABLES")
    claim_tables = {"profile_claim_sessions", "profile_claims", "profile_claim_bindings", "profile_claim_decisions"}
    assert claim_tables <= set(required_tables)
    assert claim_tables <= set(_shell_array(manifest, "ROW_COUNT_TABLES"))
    assert "revenue_share_organization_manager_assignments" in required_tables
    assert {"cloud_auth_applications", "cloud_auth_sessions", "cloud_auth_candidates", "cloud_auth_operations", "cloud_backup_target_history", "cloud_connector_state"} <= set(required_tables)
    assert {"cloud_backup_targets", "cloud_backup_jobs"} <= set(required_tables)
    assert {"team_memberships", "team_manager_assignments", "team_operations"} <= set(
        required_tables
    )
    assert "evaluation_quarantine_cleanup" in required_tables
    assert "evaluation_quarantine_cleanup" in _shell_array(manifest, "ROW_COUNT_TABLES")
    assert {"operations_metric_batches", "operations_request_minutes", "operations_alerts"} <= set(required_tables)
    assert {
        "service_accounts",
        "payments",
        "payment_import_events",
        "payment_conflicts",
        "payment_refunds",
        "procurement_reserve_fund_entries",
        "admission_cases",
        "admission_case_payments",
        "enrollments",
        "enrollment_events",
        "enrollment_withdrawals",
        "evaluation_bridge_requests",
        "evaluation_bridge_tickets",
        "student_reports",
        "student_report_drafts",
        "student_report_versions",
        "student_report_reviews",
        "student_report_window_grants",
        "evaluation_staff_roles",
        "cohort_staff_assignments",
        "cohort_staff_assignment_permissions",
        "student_game_accounts",
        "student_auxiliary_phones",
        "project_analysis_profiles",
        "evaluation_media_blobs",
        "evaluation_media_assets",
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
        "housing_buildings",
        "housing_floors",
        "housing_rooms",
        "housing_import_batches",
        "housing_room_issues",
        "housing_stays",
        "housing_transfers",
        "housing_report_batches",
        "housing_report_batch_scopes",
        "food_cuisine_options",
        "person_food_preferences",
        "person_food_preference_cuisines",
        "person_food_preference_dishes",
        "person_food_preference_versions",
        "account_login_identifiers",
        "guardian_profiles",
        "registration_applications",
        "registration_verification_challenges",
        "registration_sessions",
        "guardian_registration_invitations",
        "staff_identity_applications",
        "registration_consent_acceptances",
        "registration_channel_settings",
        "mail_delivery_settings",
        "mail_templates",
        "assets",
        "asset_assignments",
        "asset_photos",
        "revenue_share_ledger_entries",
        "daily_purchase_records",
        "procurement_invoices",
        "procurement_invoice_purchases",
        "travel_report_conversions",
        "enrollment_programs",
        "project_coach_assignments",
        "revenue_share_reconcile_queue",
        "talent_test_sessions",
        "talent_test_results",
        "talent_test_reports",
        "esports_assessment_sessions",
        "esports_assessment_attempts",
        "esports_assessment_results",
        "esports_assessment_reports",
        "esports_assessment_practice_runs",
    } <= set(required_tables)
    assert {
        "ix_evaluation_quarantine_cleanup_due",
        "ix_evaluation_upload_account_open",
        "uq_housing_room_issues_open_room",
        "uq_account_login_identifiers_identifier_digest",
        "uq_registration_applications_active_identifier",
        "uq_registration_applications_active_identity",
        "uq_registration_sessions_token_digest",
        "uq_guardian_registration_invitations_pending_application",
        "uq_guardian_registration_invitations_pending_student",
        "uq_guardian_registration_invitations_accepted_application",
        "uq_guardian_registration_invitations_accepted_student",
        "uq_guardian_registration_invitations_handoff_token",
        "uq_staff_identity_applications_open_person_type",
        "ix_housing_stays_room_status_time",
        "ix_housing_stays_person_status_time",
        "ix_housing_stays_cohort_status",
        "excl_housing_stays_person_active_overlap",
        "uq_housing_transfers_from_stay",
        "uq_housing_transfers_to_stay",
        "uq_person_food_preference_cuisines_standard_option",
        "ix_daily_purchase_records_reimbursed_date",
        "uq_admission_cases_open_self_service_choice",
        "ix_project_coach_assignments_project_active",
        "ix_project_coach_assignments_coach_active",
        "uq_project_coach_assignments_project_coach",
        "ix_revenue_share_reconcile_queue_paid_at",
        "uq_talent_sessions_person_creation",
        "uq_talent_sessions_active_person",
        "ix_talent_sessions_person_created",
        "uq_talent_results_session_test",
        "uq_talent_results_session_submission",
        "uq_talent_reports_session",
        "ix_talent_reports_created",
        "uq_ea_open_person_suite",
        "uq_ea_session_creator_creation",
        "ix_ea_session_person_suite_status_created",
        "uq_ea_attempt_identity",
        "uq_ea_attempt_creator_creation",
        "uq_ea_attempt_submittable_session_test",
        "uq_ea_result_session_test",
        "uq_ea_result_attempt",
        "uq_ea_result_creator_submission",
        "ix_ea_result_comparison_created",
        "uq_ea_report_session",
        "uq_ea_practice_creator_creation",
        "uq_ea_practice_creator_submission",
        "ix_ea_practice_person_suite_state_created",
        "ix_ea_practice_comparison_created",
    } <= set(_shell_array(manifest, "REQUIRED_INDEXES"))
    assert {
        "uq_payments_manual_operation",
        "ck_payments_operation_fingerprint",
        "uq_payment_refunds_manual_operation",
        "ck_payment_refunds_operation_fingerprint",
        "ck_evaluation_quarantine_cleanup_key",
        "ck_evaluation_quarantine_cleanup_attempts",
        "ck_housing_room_issues_description",
        "ck_housing_room_issues_previous_service_status",
        "ck_housing_room_issues_status",
        "ck_housing_room_issues_resolution",
        "ck_housing_room_issues_resolved_at",
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
        "excl_housing_stays_person_active_overlap",
        "ck_housing_transfers_distinct_stays",
        "ck_housing_transfers_reason",
        "uq_housing_transfers_from_stay",
        "uq_housing_transfers_to_stay",
        "ck_user_accounts_self_service_primary_purpose",
        "ck_account_login_identifiers_kind",
        "ck_registration_applications_primary_purpose",
        "ck_registration_applications_state",
        "ck_registration_applications_identifier_kind",
        "ck_registration_applications_sex",
        "ck_registration_applications_requested_staff_type",
        "ck_registration_applications_staff_application_reason",
        "ck_registration_applications_visit_reason",
        "ck_registration_applications_purpose_fields",
        "ck_registration_applications_identifier_pair",
        "ck_registration_applications_finalization_match_kind",
        "ck_registration_applications_finalized_at",
        "ck_registration_applications_rejected_at",
        "ck_registration_applications_review_reason",
        "ck_registration_applications_review_state",
        "ck_registration_applications_review_candidate",
        "ck_registration_verification_challenges_purpose",
        "ck_registration_verification_challenges_identifier_kind",
        "ck_registration_verification_challenges_attempts",
        "ck_guardian_registration_invitations_target",
        "ck_guardian_registration_invitations_identifier_kind",
        "ck_guardian_registration_invitations_status",
        "ck_guardian_registration_invitations_acceptance",
        "ck_guardian_registration_invitations_handoff_token",
        "ck_staff_identity_applications_requested_type",
        "ck_staff_identity_applications_status",
        "ck_staff_identity_applications_reason",
        "ck_staff_identity_applications_review_reason",
        "ck_staff_identity_applications_review_state",
        "ck_registration_consent_acceptances_subject",
        "ck_registration_consent_acceptances_labels",
        "ck_people_nickname_not_blank",
        "ck_people_profile_notes_length",
        "ck_enrollment_programs_category",
        "ck_enrollment_programs_price",
        "ck_enrollment_programs_sort_order",
        "ck_admission_cases_application_source",
        "ck_admission_cases_payment_preference",
        "ck_admission_cases_self_service_fields",
        "uq_admission_cases_order_number",
        "ck_admission_cases_order_number_format",
        "uq_project_coach_assignments_project_coach",
        "ck_talent_sessions_state_times",
        "ck_talent_sessions_abandon_pair",
        "ck_talent_sessions_creation_digest",
        "ck_talent_results_score",
        "ck_talent_results_test_id",
        "ck_talent_results_submission_digest",
        "ck_ea_session_state_times",
        "ck_ea_session_creation_digest",
        "ck_ea_attempt_state_times",
        "ck_ea_attempt_deadlines",
        "ck_ea_attempt_seed",
        "ck_ea_result_score",
        "ck_ea_result_submission_digest",
        "ck_ea_practice_state_times",
        "ck_ea_practice_deadlines",
        "ck_ea_practice_seed",
    } <= set(_shell_array(manifest, "REQUIRED_CONSTRAINTS"))
    assert set(_shell_array(manifest, "REQUIRED_TRIGGERS")) == {
        "trg_housing_stays_void_fact_immutable",
        "trg_housing_stay_void_operations_immutable",
        "trg_accounts_identity_facts", "trg_people_account_identity_facts",
        "trg_profile_claims_evidence_immutable", "trg_profile_claim_sessions_evidence_immutable",
        "trg_profile_claim_bindings_evidence_immutable", "trg_profile_claim_decisions_immutable",
        "trg_profile_claims_no_delete", "trg_assisted_profile_activation_monotonic",
        "trg_profile_claim_bindings_no_delete",
        "trg_revenue_share_payment_insert", "trg_revenue_share_refund_insert",
        "trg_revenue_share_payment_paid_at",
        "trg_admission_order_number_immutable",
        "trg_assisted_account_origin_immutable",
        "trg_personnel_intake_revisions_immutable",
        "trg_assisted_approvals_immutable",
        "trg_assisted_approval_guardian_references",
    }
    assert {"registration_student_number_seq", "admission_order_number_seq"} <= set(
        _shell_array(manifest, "REQUIRED_SEQUENCES")
    )
    assert {"people.email_ciphertext", "people.email_digest"} <= set(
        _shell_array(manifest, "REQUIRED_ENCRYPTED_COLUMNS")
    )
    assert {
        "people.email",
        "user_accounts.login_phone_digest",
        "user_accounts.email_normalized",
    } <= set(_shell_array(manifest, "REQUIRED_NULLABLE_LEGACY_COLUMNS"))
    assert "registered_user" in _shell_array(manifest, "REQUIRED_ROLE_KEYS")
    assert {
        "registration.review",
        "registration.sensitive.read",
        "staff_identity_applications.review",
        "enrollment_applications.create",
        "project_coaches.manage",
        "talent_tests.take",
        "talent_tests.read",
        "esports_assessments.take",
        "esports_assessments.read",
    } <= set(_shell_array(manifest, "REQUIRED_PERMISSION_KEYS"))


def test_schema_manifest_table_set_matches_runtime_sqlalchemy_metadata() -> None:
    from app.core.models import Base
    from app.esports_assessments import models as esports_assessment_models  # noqa: F401
    from app.housing import history_void_models  # noqa: F401
    from app.talent_tests import models as talent_test_models  # noqa: F401

    manifest_tables = set(_shell_array(_text("scripts/db-schema-manifest.sh"), "REQUIRED_TABLES"))

    assert set(Base.metadata.tables) == manifest_tables


def test_complete_gate_rehearses_backup_and_restore() -> None:
    check_script = _text("scripts/check.sh")
    assert "scripts/backup-db.sh" in check_script
    assert "scripts/restore-db.sh" in check_script
    assert "erp_e2e_restore_test" in check_script


def test_nas_runtime_uses_production_security_defaults() -> None:
    compose = _text("compose.nas-staging.yaml")
    assert compose.count("APP_ENV: production") == 2
    assert "APP_ENV: development" not in compose
    assert compose.count("ERP_USE_DEPENDENCY_IMAGE: ${ERP_USE_DEPENDENCY_IMAGE:-0}") == 2
    assert (
        compose.count(
            "EVALUATION_TOOL_STANDALONE_BASE_URL: "
            "${EVALUATION_TOOL_STANDALONE_BASE_URL:-https://skymoonzyj.l.cd:18080}"
        )
        == 2
    )

    caddy = _text("infra/caddy/Caddyfile")
    assert 'Strict-Transport-Security "max-age=31536000; includeSubDomains"' in caddy


def test_production_image_copies_imported_runtime_packages_but_not_e2e_seed() -> None:
    dockerfile = _text("apps/api/Dockerfile")
    production = dockerfile.split("FROM base AS production", maxsplit=1)[1].split(
        "FROM base AS e2e", maxsplit=1
    )[0]
    required_packages = _imported_app_packages("apps/api/app/main.py", "apps/api/migrations/env.py")
    # Transitive API imports must ship too; CLI remains explicitly allowlisted
    # so the production image never gains test-only fixture seed commands.
    required_packages |= {
        package.name
        for package in (REPOSITORY_ROOT / "apps/api/app").iterdir()
        if (package / "__init__.py").is_file() and package.name != "cli"
    }
    copied_packages = set(
        re.findall(
            r"^COPY app/([a-z][a-z0-9_]*) ./app/\1$",
            production,
            re.MULTILINE,
        )
    )
    assert required_packages <= copied_packages, (
        f"production image omits imported runtime packages: "
        f"{sorted(required_packages - copied_packages)}"
    )
    assert "COPY app ./app" not in production
    assert "COPY app/cli ./app/cli" not in production
    assert "seed_e2e.py" not in production
    assert "seed_system_evaluation_e2e.py" not in production


def test_readme_documents_admissions_operations_and_current_boundaries() -> None:
    readme = _text("README.md")
    required_operator_guidance = (
        "## 招生报名与付款运营",
        "创建、轮换或停用机器凭证",
        "签名规范串",
        "手工登记付款",
        "退款不自动取消报名",
        "按剩余日期退款",
        "退还全部剩余已付款",
        "回滚应用代码",
        "不回退已经提交的付款数据",
    )
    for phrase in required_operator_guidance:
        assert phrase in readme

    required_deferred_boundaries = (
        "截图上传与自动评价",
        "本地实名入住报送",
        "公安在线自动报送",
        "住宿生命周期四视口浏览器旅程已通过",
        "支付平台适配器",
        "录像或 GPU 分析",
    )
    for phrase in required_deferred_boundaries:
        assert phrase in readme


@pytest.mark.parametrize(
    "required_text",
    [
        "教练下一期学员名单",
        "家庭教学与多学员切换",
        "学员本人教学信息",
        "本人付款与退款",
        "已发布的日报、周报可在已授权的家庭教学页面查看",
        "Docker/NAS 打包仍在最终阶段",
    ],
)
def test_readme_documents_portal_operations(required_text: str) -> None:
    assert required_text in _text("README.md")


def test_design_status_does_not_claim_container_gate_before_it_runs() -> None:
    design = _text("docs/superpowers/specs/2026-07-27-admissions-teaching-operations-design.md")
    assert "**状态：** 阶段 1B 第二模块待容器门禁复核" in design


def test_readme_documents_local_evaluation_integration_and_processing_boundaries() -> None:
    readme = _text("README.md")
    required = {
        "评价中心页面整合",
        "game-score-tool",
        "http://localhost:8010",
        "EVALUATION_TOOL_SERVICE_TOKEN",
        "SCORE_TOOL_SESSION_SECRET",
        "scripts/run-evaluation-page-e2e.sh",
        "scripts/run-system-evaluation-e2e.sh",
        "系统评价截图→任务→Worker→审核→发布链路已提供本地隔离 E2E",
        "教学日报、周报、营期问卷和组合工作台的 Stage 2 本地联合验收已通过",
        "生产 Docker/NAS 整合仍延期到全部 ERP 项目完成后",
    }
    assert all(phrase in readme for phrase in required)
    obsolete_boundaries = {
        "`game-score-tool` 的只读页面壳",
        "独立/内嵌只读壳",
        "截图、评分、日报、周报和营期问卷尚未开放",
        "截图上传、评价任务、OCR/评分、审核和发布仍未开放",
        "截图上传、评价任务、OCR/自动评价、支付平台适配、公安在线自动报送和录像/GPU 仍未开放",
        "当前不可用：站内项目选择与支付、微信/支付宝等支付平台适配器、每日/每周评语与学习评价正文、截图上传与自动评价",
    }
    assert all(boundary not in readme for boundary in obsolete_boundaries)

    forbidden_completion_claims = {
        "截图上传已完成",
        "评分处理已完成",
        "录像分析已完成",
        "GPU 服务已完成",
        "Docker/NAS 已完成",
        "NAS 部署已完成",
    }
    assert forbidden_completion_claims.isdisjoint(readme.splitlines())


def test_teaching_report_docs_describe_stage2_local_acceptance_as_passed() -> None:
    readme = _text("README.md")
    contract = _text(
        "docs/superpowers/specs/2026-08-10-teaching-report-workspace-contract-design.md"
    )
    current_unavailable = _markdown_section(readme, "阶段 1B 明确边界")

    assert "教学日报、周报、审核发布与家庭已发布查看已本地实现" in readme
    assert "教学日报、周报、营期问卷和组合工作台的 Stage 2 本地联合验收已通过" in readme
    assert "正等待本轮 Stage 2 联合验收" not in readme
    assert "待 Stage 2 联合验收" not in readme
    assert "每日/每周评语与学习评价正文" not in current_unavailable
    assert "日报、周报和营期问卷继续延期" not in readme
    assert "- **状态：** 本地实现与 Stage 2 本地联合验收已通过" in contract
    assert "Task 7 的四视口 Playwright 旅程；它仍在既定 Task 7 完成。" not in contract
    assert "Task 7 再运行四视口 Playwright" not in contract


def test_readme_documents_local_container_acceptance_evidence_boundary() -> None:
    assert LOCAL_CONTAINER_OPERATIONS.is_file()
    readme = _text("README.md")

    for required in (
        "bash scripts/run-local-container-acceptance.sh",
        "docs/operations/local-container-acceptance.md",
        "本地 disposable Compose 验收已通过",
        "`status=passed`、`cleanup_completed=true`",
        "Docker daemon 未运行时不得声称真实容器验收通过",
        "NAS 部署、外部 TLS、生产密钥轮换、真实备份恢复和生产数据迁移仍延期",
        "Stage 2 本地联合验收通过不代表 Docker/NAS/GPU/录像/生产数据迁移通过",
    ):
        assert required in readme
    assert "尚无可用于宣称真实容器通过的当次摘要" not in readme

    operations = LOCAL_CONTAINER_OPERATIONS.read_text(encoding="utf-8")
    assert "只有 `status=passed`、`cleanup_completed=true`" in operations
    assert "Docker daemon 不可用时" in operations
    assert "本工作树已留存一次符合该标准的本地容器通过记录" in operations
    assert "Docker Compose v2" in operations
    for deferred in (
        "NAS 部署",
        "外部 TLS",
        "生产密钥轮换",
        "真实备份恢复",
        "生产数据迁移",
    ):
        assert deferred in operations


def test_task2_task7_completion_plan_uses_valid_markdown_backticks() -> None:
    assert TASK2_TASK7_COMPLETION_PLAN.is_file()
    assert "\\`" not in TASK2_TASK7_COMPLETION_PLAN.read_text(encoding="utf-8")


def test_stage2_operations_document_freezes_local_only_acceptance_boundary() -> None:
    assert STAGE2_OPERATIONS.is_file()
    text = STAGE2_OPERATIONS.read_text(encoding="utf-8")
    for required in (
        "scripts/run-stage2-local-acceptance.sh",
        "scripts/seed-stage2-acceptance-e2e.py",
        "desktop-1440",
        "desktop-1024",
        "tablet-768",
        "mobile-360",
        "TEST_DATABASE_URL",
        "app.cli.e2e_database_safety",
        "stage2-local-acceptance-summary.json",
        "source_tree_sha256",
        "旧 `data/`、`uploads/`、`runtime/`",
        "Docker/NAS/GPU/录像/生产数据迁移均未执行",
    ):
        assert required in text
    for forbidden in (
        "NAS 部署已完成",
        "生产可用",
        "真实数据迁移已完成",
        "GPU 分析已完成",
    ):
        assert forbidden not in text


def test_readme_documents_evaluation_data_media_and_processing_boundaries() -> None:
    readme = _text("README.md")
    required = {
        "游戏 ID、辅助手机号、工作人员服务历史和手动分析配置已可用",
        "私有媒体存储、校验和读取原语",
        "python -m app.cli.evaluation_media_manifest",
        "系统评价处理的本地 E2E 会覆盖截图上传、任务创建、OCR/评分、审核、发布和人工整改",
        "旧 game-score-tool 的 `data/`、`uploads/`、`runtime/` 和 SQLite 未读取、未迁移、未修改或删除",
        "生产 Docker/NAS 整合仍延期到全部 ERP 项目完成后",
    }
    assert all(phrase in readme for phrase in required)


def test_evaluation_e2e_launcher_runs_both_local_specs_without_docker() -> None:
    launcher = _text("scripts/run-evaluation-page-e2e.sh")
    assert "E2E_PYTHON_BIN" in launcher
    assert 'python_bin" -m app.cli.e2e_database_safety' in launcher
    assert launcher.index("app.cli.e2e_database_safety") < launcher.index("-m alembic upgrade head")
    assert "evaluation-page-integration.spec.ts" in launcher
    assert "evaluation-data-media-foundation.spec.ts" in launcher
    assert "docker " not in launcher.lower()
    assert "docker-compose" not in launcher.lower()


def test_housing_room_status_launcher_runs_four_local_projects_without_docker() -> None:
    path = REPOSITORY_ROOT / "scripts/run-housing-room-status-e2e.sh"
    launcher = path.read_text(encoding="utf-8")

    assert path.stat().st_mode & 0o111
    assert "E2E_API_PORT:-8001" in launcher
    assert "E2E_WEB_PORT:-5176" in launcher
    assert "housing-room-status-foundation.spec.ts" in launcher
    assert 'python_bin" -m app.cli.seed_e2e' in launcher
    assert 'python_bin" -m app.cli.e2e_database_safety' in launcher
    assert launcher.index("app.cli.e2e_database_safety") < launcher.index("-m alembic upgrade head")
    for project in ("desktop-1440", "desktop-1024", "tablet-768", "mobile-360"):
        assert project in launcher
    assert "docker " not in launcher.lower()
    assert "docker-compose" not in launcher.lower()
    readme = _text("README.md")
    assert "有效连接目标的数据库名严格为 `erp_e2e` 或以 `_test` 结尾" in readme
    assert "拒绝数据库目标选择器查询参数" in readme
    assert "凭据中的 `@` 必须写成百分号编码 `%40`" in readme
    assert "无 authority 的本地 PostgreSQL socket URL 仍可使用" in readme


def test_housing_occupancy_launcher_runs_both_specs_for_four_local_projects() -> None:
    path = REPOSITORY_ROOT / "scripts/run-housing-occupancy-e2e.sh"
    launcher = path.read_text(encoding="utf-8")

    assert path.stat().st_mode & 0o111
    assert "E2E_API_PORT:-8002" in launcher
    assert "E2E_WEB_PORT:-5177" in launcher
    assert "housing-occupancy-lifecycle.spec.ts" in launcher
    assert "housing-occupancy-revocation.spec.ts" in launcher
    assert "person-housing.spec.ts" in launcher
    assert '"$python_bin" -m app.cli.seed_e2e' in launcher
    assert "seed_housing_portal_e2e" not in launcher
    assert '"$python_bin" -m app.cli.e2e_database_safety' in launcher
    assert launcher.index("app.cli.e2e_database_safety") < launcher.index("-m alembic upgrade head")
    assert 'E2E_API_URL="$api_origin"' in launcher
    assert 'E2E_BASE_URL="$web_origin"' in launcher
    for project in ("desktop-1440", "desktop-1024", "tablet-768", "mobile-360"):
        assert project in launcher
    assert "docker " not in launcher.lower()
    assert "docker-compose" not in launcher.lower()


def test_housing_reporting_launcher_runs_both_specs_for_four_local_projects() -> None:
    path = REPOSITORY_ROOT / "scripts/run-housing-reporting-e2e.sh"
    launcher = path.read_text(encoding="utf-8")

    assert path.stat().st_mode & 0o111
    assert "E2E_API_PORT:-8004" in launcher
    assert "E2E_WEB_PORT:-5179" in launcher
    assert "housing-reporting-lifecycle.spec.ts" in launcher
    assert "housing-reporting-authorization.spec.ts" in launcher
    assert '"$python_bin" -m app.cli.seed_e2e' in launcher
    assert '"$python_bin" -m app.cli.seed_housing_reporting_e2e' in launcher
    assert launcher.index("-m app.cli.seed_e2e") < launcher.index(
        "-m app.cli.seed_housing_reporting_e2e"
    )
    assert '"$python_bin" -m app.cli.e2e_database_safety' in launcher
    assert launcher.index("app.cli.e2e_database_safety") < launcher.index("-m alembic upgrade head")
    assert 'E2E_PYTHON_BIN="$python_bin"' in launcher
    assert 'E2E_API_URL="$api_origin"' in launcher
    assert 'E2E_BASE_URL="$web_origin"' in launcher
    for project in ("desktop-1440", "desktop-1024", "tablet-768", "mobile-360"):
        assert project in launcher
    assert "docker " not in launcher.lower()
    assert "docker-compose" not in launcher.lower()
    specs = "\n".join(
        _text(path)
        for path in (
            "apps/web/tests/housing-reporting-lifecycle.spec.ts",
            "apps/web/tests/housing-reporting-authorization.spec.ts",
        )
    )
    assert "waitForTimeout" not in specs
    assert "networkidle" not in specs


def test_readme_documents_local_reporting_gate_and_deployment_deferrals() -> None:
    readme = _text("README.md")
    boundary = _markdown_section(readme, "住宿生命周期与个人住宿门户")
    required_local_reporting = {
        "/housing/reports",
        "PostgreSQL 加密不可变快照",
        "Excel 两个工作表",
        "CSV ZIP",
        "楼栋/楼层范围授权",
        "人工报送、作废和更正链",
        "scripts/run-housing-reporting-e2e.sh",
        "API `8004` 和 Web `5179`",
        "实名报送四视口浏览器旅程已通过",
        "共 8 条实名报送浏览器旅程全部成功",
    }
    assert all(phrase in boundary for phrase in required_local_reporting)
    for deferred in (
        "Docker/NAS",
        "公安在线 API",
        "生产密钥轮换",
        "生产备份恢复",
        "录像/GPU",
    ):
        assert deferred in boundary
    assert "实名导出和人工报送仍未完成" not in readme
    assert "实名住宿导出与人工报送或录像分析" not in readme


def test_housing_personal_portal_launcher_runs_four_local_projects_without_docker() -> None:
    path = REPOSITORY_ROOT / "scripts/run-housing-personal-portal-e2e.sh"
    launcher = path.read_text(encoding="utf-8")

    assert path.stat().st_mode & 0o111
    assert "E2E_API_PORT:-8003" in launcher
    assert "E2E_WEB_PORT:-5178" in launcher
    assert "housing-personal-portal.spec.ts" in launcher
    assert 'python_bin" -m app.cli.seed_e2e' in launcher
    assert 'python_bin" -m app.cli.seed_housing_portal_e2e' in launcher
    assert 'python_bin" -m app.cli.e2e_database_safety' in launcher
    assert launcher.index("app.cli.e2e_database_safety") < launcher.index("-m alembic upgrade head")
    for project in ("desktop-1440", "desktop-1024", "tablet-768", "mobile-360"):
        assert project in launcher
    assert "docker " not in launcher.lower()
    assert "docker-compose" not in launcher.lower()


def test_evaluation_docs_mark_foundation_and_processing_units_complete_locally() -> None:
    design = _text("docs/superpowers/specs/2026-07-30-teaching-evaluation-center-design.md")
    readme = _text("README.md")
    assert "第二实施单元本地实现与非 Docker 门禁已完成" in design
    assert "评价中心基础与系统评价处理单元均已提供本地实现和非 Docker 门禁" in readme
    assert "第二实施单元计划已编写，待执行" not in design


def test_housing_docs_record_local_lifecycle_and_personal_portal_boundaries() -> None:
    readme = _text("README.md")
    parent_design = _text("docs/superpowers/specs/2026-08-03-housing-room-status-design.md")
    personal_design = _text("docs/superpowers/specs/2026-08-05-housing-personal-portal-design.md")
    readme_boundary = _markdown_section(readme, "住宿生命周期与个人住宿门户")
    parent_boundary = "\n".join(
        (
            _markdown_lead(parent_design),
            _markdown_section(parent_design, "当前本地实现边界"),
        )
    )
    personal_boundary = _markdown_lead(personal_design)
    current_boundaries = (readme_boundary, parent_boundary)
    required_catalog_facts = {
        "4 幢楼、11 个楼层、188 个房间条目",
        "171 间住宿房需要逐间或批量核对",
        "scripts/run-housing-room-status-e2e.sh",
        "scripts/run-housing-occupancy-e2e.sh",
    }
    assert all(
        text in boundary for boundary in current_boundaries for text in required_catalog_facts
    )

    required_local_implementation = {
        "住宿生命周期后端 API 已在本地实现",
        "学员/监护人个人住宿门户已在本地实现",
        "管理员入住、调房、退房对话框和动态房态管理 UI 已在本地实现",
        "入住生命周期本地实现与非 Docker 门禁已完成",
        "住宿生命周期四视口浏览器旅程已通过",
    }
    assert all(
        text in boundary
        for boundary in current_boundaries
        for text in required_local_implementation
    )
    assert "住宿模块的 Docker/NAS、公安在线接口、生产备份恢复和生产环境均未验证" in parent_boundary
    assert "scripts/run-housing-personal-portal-e2e.sh" in readme_boundary
    assert "默认使用 API `8002` 和 Web `5177`" in readme_boundary
    assert "个人住宿门户本地实现与非 Docker 四视口门禁已完成" in personal_boundary
    assert "Docker/NAS、公安在线报送和生产部署仍未验证" in personal_boundary

    truthful_catalog_boundaries = {
        "楼栋 → 楼层 → 房间",
        "三层目录",
        "房间源自一次初始导入批次",
        "导入批次仅由初始种子写入",
        "保留导入批次元数据",
        "楼栋和房间都没有导入批次外键",
        "管理员可以逐间或批量核对这些房间",
        "没有导入批次 API 或管理界面",
        "管理员不能创建、编辑或删除导入批次",
    }
    assert all(
        text in boundary for boundary in current_boundaries for text in truthful_catalog_boundaries
    )

    forbidden_catalog_overclaims = {
        "导入批次 → 楼栋 → 楼层 → 房间",
        "四级源目录",
        "管理员可以维护导入批次",
        "导入批次 CRUD",
        "管理员可以新增导入批次",
        "管理员可以编辑导入批次",
        "管理员可以删除导入批次",
        "管理员可以维护四级目录",
        "管理员可维护四级目录",
        "系统提供导入批次管理页面",
        "导入批次支持 CRUD",
        "导入批次支持增删改查",
        "导入批次 API 已完成",
        "导入批次 UI 已完成",
        "导入批次 API 已上线",
        "导入批次 UI 已上线",
        "系统提供导入批次 API",
        "系统提供导入批次 UI",
        "导入批次支持 API",
        "导入批次支持 UI",
    }
    for boundary in current_boundaries:
        for claim in forbidden_catalog_overclaims:
            assert not any(_line_asserts_claim(line, claim) for line in boundary.splitlines())

    forbidden_completion_claims = {
        "阶段 3 已完成",
        "住宿模块已完成",
        "入住功能已完成",
        "管理员动态房态 UI 已完成",
        "实名导出已完成",
        "公安接口已完成",
        "Docker/NAS 已完成",
        "生产房态已上线",
    }
    for boundary in current_boundaries:
        for claim in forbidden_completion_claims:
            assert not any(_line_asserts_claim(line, claim) for line in boundary.splitlines())

    stale_unavailable_claims = {
        "当前人数固定为 0",
        "尚未开放入住、预约、问题房、调房、退房和实名导出",
        "管理员完整入住、调房、退房对话框和完整动态房态管理 UI 仍未完成",
        "截图上传与自动评价、住宿房态或录像分析",
        "OCR/自动评价、住宿房态、实名导出",
        "截图上传与自动评价、住宿房态、入住/派出所名单导出",
    }
    assert all(claim not in readme_boundary for claim in stale_unavailable_claims)


def test_housing_designs_mark_lifecycle_gate_complete_without_deployment() -> None:
    parent_design = _text("docs/superpowers/specs/2026-08-03-housing-room-status-design.md")
    personal_design = _text("docs/superpowers/specs/2026-08-05-housing-personal-portal-design.md")
    assert (
        "- **状态：** 入住生命周期本地实现与非 Docker 门禁已完成；"
        "个人住宿门户非 Docker 门禁已完成；实名导出和部署仍待后续单元"
    ) in parent_design
    assert (
        "**状态：** 个人住宿门户本地实现与非 Docker 四视口门禁已完成；"
        "Docker/NAS、公安在线报送和生产部署仍未验证"
    ) in personal_design


def test_readme_restore_verification_follows_current_schema_manifest() -> None:
    restore_section = _markdown_section(_text("README.md"), "数据库备份与恢复演练")

    assert re.search(r"33\s*张", restore_section) is None
    assert "当前 schema manifest" in restore_section
    assert "清单内每张必要表" in restore_section


def test_markdown_section_stops_before_later_housing_scope() -> None:
    document = """\
# 房态设计

## 当前边界

尚未开放入住功能。

### 当前边界细节

没有导入批次 CRUD/API/UI。

## 后续目标

入住功能已完成。
"""

    section = _markdown_section(document, "当前边界")

    assert "尚未开放入住功能" in section
    assert "没有导入批次 CRUD/API/UI" in section
    assert "入住功能已完成" not in section


@pytest.mark.parametrize(
    ("line", "claim", "expected"),
    [
        ("住宿模块已完成。", "住宿模块已完成", True),
        ("入住功能已完成。", "入住功能已完成", True),
        ("公安接口已完成。", "公安接口已完成", True),
        ("Docker/NAS 已完成。", "Docker/NAS 已完成", True),
        ("生产房态已上线。", "生产房态已上线", True),
        ("系统提供导入批次管理页面。", "系统提供导入批次管理页面", True),
        ("导入批次 CRUD。", "导入批次 CRUD", True),
        ("生产房态已上线，Docker/NAS 延期。", "生产房态已上线", True),
        ("无异常，住宿模块已完成。", "住宿模块已完成", True),
        ("住宿模块尚未完成但入住功能已完成。", "入住功能已完成", True),
        ("无异常且住宿模块已完成。", "住宿模块已完成", True),
        ("住宿模块尚未完成同时入住功能已完成。", "入住功能已完成", True),
        ("无异常以及住宿模块已完成。", "住宿模块已完成", True),
        ("导入批次 API 已完成。", "导入批次 API 已完成", True),
        ("系统提供导入批次 UI。", "系统提供导入批次 UI", True),
        ("生产房态已上线，待后续验证。", "生产房态已上线", True),
        ("Docker/NAS 已完成，延期复核。", "Docker/NAS 已完成", True),
        ("公安接口已完成，待后续部署。", "公安接口已完成", True),
        ("住宿模块已完成，属于历史性成果。", "住宿模块已完成", True),
        ("公安接口已完成，已被禁止使用。", "公安接口已完成", True),
        ("生产房态已上线并非问题。", "生产房态已上线", True),
        ("住宿模块已完成不是坏事。", "住宿模块已完成", True),
        ("生产房态已上线，待后续完成公安接口。", "生产房态已上线", True),
        ("Docker/NAS 已完成，待后续开放生产房态。", "Docker/NAS 已完成", True),
        ("住宿模块尚未完成。", "住宿模块已完成", False),
        ("尚未声称住宿模块已完成。", "住宿模块已完成", False),
        ("并未写成入住功能已完成。", "入住功能已完成", False),
        ("不得声称住宿模块已完成。", "住宿模块已完成", False),
        ("旧版误写“入住功能已完成”，现已纠正。", "入住功能已完成", False),
        ("并非公安接口已完成。", "公安接口已完成", False),
        ("Docker/NAS 已完成这一说法被禁止。", "Docker/NAS 已完成", False),
        ("生产房态已上线待后续单元实现。", "生产房态已上线", False),
        ("“入住功能已完成”，这一说法已被禁止。", "入住功能已完成", False),
        ("生产房态已上线，待后续单元实现。", "生产房态已上线", False),
        ("Docker/NAS 已完成，系历史误写。", "Docker/NAS 已完成", False),
        ("住宿模块已完成的说法被禁止。", "住宿模块已完成", False),
        ("生产房态已上线并非当前状态。", "生产房态已上线", False),
        ("入住功能已完成是历史误写。", "入住功能已完成", False),
        ("无导入批次 CRUD/API/UI。", "导入批次 CRUD", False),
        ("系统未提供导入批次管理页面。", "系统提供导入批次管理页面", False),
    ],
)
def test_line_assertion_distinguishes_positive_and_non_current_claims(
    line: str, claim: str, expected: bool
) -> None:
    assert _line_asserts_claim(line, claim) is expected
