from __future__ import annotations

from sqlalchemy import Engine, Table, inspect, text

from app.evaluations import models as evaluation_models
from app.evaluations.report_models import (
    StudentReport,
    StudentReportDraft,
    StudentReportReview,
    StudentReportVersion,
    StudentReportWindowGrant,
)

REPORT_TABLES = {
    "student_reports",
    "student_report_drafts",
    "student_report_versions",
    "student_report_reviews",
    "student_report_window_grants",
}

EXPECTED_MODEL_EXPORTS = {
    "StudentReport",
    "StudentReportDraft",
    "StudentReportReview",
    "StudentReportVersion",
    "StudentReportWindowGrant",
}

EXPECTED_CHECKS = {
    "student_reports": {
        "ck_student_reports_kind",
        "ck_student_reports_period",
        "ck_student_reports_status",
        "ck_student_reports_lock_version",
        "ck_student_reports_snapshots",
        "ck_student_reports_assignment_source",
        "ck_student_reports_windows",
        "ck_student_reports_version_pointers",
        "ck_student_reports_state_times",
    },
    "student_report_drafts": {
        "ck_student_report_drafts_body",
        "ck_student_report_drafts_dimensions",
        "ck_student_report_drafts_kind",
        "ck_student_report_drafts_revision",
    },
    "student_report_versions": {
        "ck_student_report_versions_body",
        "ck_student_report_versions_dimensions",
        "ck_student_report_versions_source",
        "ck_student_report_versions_version",
    },
    "student_report_reviews": {"ck_student_report_reviews_decision"},
    "student_report_window_grants": {
        "ck_student_report_window_grants_kind",
        "ck_student_report_window_grants_reason",
        "ck_student_report_window_grants_window",
    },
}

EXPECTED_UNIQUES = {
    "student_reports": {
        "uq_student_reports_author_period",
        "uq_student_reports_id_enrollment",
    },
    "student_report_versions": {
        "uq_student_report_versions_report_version",
        "uq_student_report_versions_id_report",
    },
    "student_report_reviews": {"uq_student_report_reviews_version"},
    "student_report_window_grants": {
        "uq_student_report_window_grants_id_report"
    },
}

EXPECTED_TRIGGERS = {
    "student_report_versions": "student_report_versions_immutable",
    "student_report_reviews": "student_report_reviews_immutable",
    "student_report_window_grants": "student_report_window_grants_immutable",
}


def _constraint_names(table: Table, kind: str) -> set[str]:
    constraints = table.constraints
    return {
        constraint.name
        for constraint in constraints
        if constraint.__class__.__name__ == kind and constraint.name is not None
    }


def test_report_models_are_registered_and_exported() -> None:
    assert REPORT_TABLES <= set(StudentReport.metadata.tables)
    assert EXPECTED_MODEL_EXPORTS <= set(evaluation_models.__all__)
    assert StudentReport.__table__.name == "student_reports"
    assert StudentReportDraft.__table__.name == "student_report_drafts"
    assert StudentReportVersion.__table__.name == "student_report_versions"
    assert StudentReportReview.__table__.name == "student_report_reviews"
    assert StudentReportWindowGrant.__table__.name == "student_report_window_grants"


def test_report_model_constraints_freeze_the_domain_contract() -> None:
    tables = StudentReport.metadata.tables
    for table_name, expected in EXPECTED_CHECKS.items():
        assert expected <= _constraint_names(tables[table_name], "CheckConstraint")
    for table_name, expected in EXPECTED_UNIQUES.items():
        assert expected <= _constraint_names(tables[table_name], "UniqueConstraint")


def test_all_report_business_foreign_keys_are_restrictive() -> None:
    for table_name in REPORT_TABLES:
        table = StudentReport.metadata.tables[table_name]
        for constraint in table.foreign_key_constraints:
            assert constraint.ondelete == "RESTRICT", (table_name, constraint.name)


def test_report_root_uses_composite_resource_and_version_foreign_keys() -> None:
    foreign_keys = {
        constraint.name: (
            tuple(element.parent.name for element in constraint.elements),
            tuple(element.target_fullname for element in constraint.elements),
        )
        for constraint in StudentReport.__table__.foreign_key_constraints
    }

    assert foreign_keys["fk_student_reports_enrollment_target"] == (
        ("enrollment_id", "student_person_id", "cohort_id", "class_id"),
        (
            "enrollments.id",
            "enrollments.student_person_id",
            "enrollments.cohort_id",
            "enrollments.class_id",
        ),
    )
    assert foreign_keys["fk_student_reports_class_target"] == (
        ("class_id", "cohort_id", "project_id"),
        (
            "teaching_classes.id",
            "teaching_classes.cohort_id",
            "teaching_classes.project_id",
        ),
    )
    for name, column in (
        ("fk_student_reports_current_version", "current_version_id"),
        ("fk_student_reports_published_version", "published_version_id"),
    ):
        assert foreign_keys[name] == (
            (column, "id"),
            ("student_report_versions.id", "student_report_versions.report_id"),
        )
    assert foreign_keys["fk_student_reports_window_grant"] == (
        ("current_window_grant_id", "id"),
        (
            "student_report_window_grants.id",
            "student_report_window_grants.report_id",
        ),
    )


def test_migration_creates_report_constraints_and_immutability_triggers(
    postgres_engine: Engine,
) -> None:
    inspector = inspect(postgres_engine)
    assert REPORT_TABLES <= set(inspector.get_table_names())

    for table_name, expected in EXPECTED_CHECKS.items():
        assert expected <= {
            item["name"] for item in inspector.get_check_constraints(table_name)
        }
    for table_name, expected in EXPECTED_UNIQUES.items():
        assert expected <= {
            item["name"] for item in inspector.get_unique_constraints(table_name)
        }
    with postgres_engine.connect() as connection:
        triggers = {
            table_name: set(
                connection.scalars(
                    text(
                        "SELECT trigger_name FROM information_schema.triggers "
                        "WHERE event_object_schema = 'public' "
                        "AND event_object_table = :table_name"
                    ),
                    {"table_name": table_name},
                )
            )
            for table_name in EXPECTED_TRIGGERS
        }
    for table_name, trigger_name in EXPECTED_TRIGGERS.items():
        assert trigger_name in triggers[table_name]
