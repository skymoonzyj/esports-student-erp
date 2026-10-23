from __future__ import annotations

from sqlalchemy import Engine, inspect, text

from app.evaluations import models as evaluation_models
from app.evaluations.survey_models import (
    CampSurveyCycle,
    CampSurveyStaffRating,
    CampSurveySupplement,
    CampSurveySupplementRating,
    CampSurveyVersion,
)

SURVEY_TABLES = {
    "camp_survey_cycles",
    "camp_survey_snapshot_versions",
    "camp_survey_staff_snapshots",
    "camp_survey_submissions",
    "camp_survey_versions",
    "camp_survey_staff_ratings",
    "camp_survey_supplements",
    "camp_survey_supplement_ratings",
}

EXPECTED_CHECKS = {
    "camp_survey_cycles": {
        "ck_camp_survey_cycles_window",
        "ck_camp_survey_cycles_snapshot_version",
    },
    "camp_survey_snapshot_versions": {
        "ck_camp_survey_snapshot_versions_version",
        "ck_camp_survey_snapshot_versions_reason",
    },
    "camp_survey_staff_snapshots": {
        "ck_camp_survey_staff_snapshots_name",
        "ck_camp_survey_staff_snapshots_category",
        "ck_camp_survey_staff_snapshots_service_intervals",
        "ck_camp_survey_staff_snapshots_roles_snapshot",
    },
    "camp_survey_submissions": {
        "ck_camp_survey_submissions_kind",
        "ck_camp_survey_submissions_revision_count",
    },
    "camp_survey_versions": {
        "ck_camp_survey_versions_version",
        "ck_camp_survey_versions_scores",
        "ck_camp_survey_versions_food_snapshot",
    },
    "camp_survey_staff_ratings": {
        "ck_camp_survey_staff_ratings_score",
        "ck_camp_survey_staff_ratings_reason",
        "ck_camp_survey_staff_ratings_suggestion",
    },
    "camp_survey_supplements": {
        "ck_camp_survey_supplements_reason",
    },
    "camp_survey_supplement_ratings": {
        "ck_camp_survey_supplement_ratings_score",
        "ck_camp_survey_supplement_ratings_reason",
        "ck_camp_survey_supplement_ratings_suggestion",
    },
}

EXPECTED_UNIQUES = {
    "camp_survey_cycles": {"uq_camp_survey_cycles_enrollment"},
    "camp_survey_snapshot_versions": {"uq_camp_survey_snapshot_versions_cycle_version"},
    "camp_survey_staff_snapshots": {"uq_camp_survey_staff_snapshots_staff"},
    "camp_survey_submissions": {
        "uq_camp_survey_submissions_cycle_respondent",
        "uq_camp_survey_submissions_id_cycle",
    },
    "camp_survey_versions": {"uq_camp_survey_versions_submission_version"},
    "camp_survey_staff_ratings": {"uq_camp_survey_staff_ratings_version_staff"},
    "camp_survey_supplements": {
        "uq_camp_survey_supplements_submission_snapshot",
        "uq_camp_survey_supplements_id_snapshot",
    },
    "camp_survey_supplement_ratings": {
        "uq_camp_survey_supplement_ratings_supplement_staff"
    },
}

EXPECTED_TRIGGERS = {
    "camp_survey_snapshot_versions": "camp_survey_snapshot_versions_immutable",
    "camp_survey_staff_snapshots": "camp_survey_staff_snapshots_immutable",
    "camp_survey_versions": "camp_survey_versions_immutable",
    "camp_survey_staff_ratings": "camp_survey_staff_ratings_immutable",
    "camp_survey_supplements": "camp_survey_supplements_immutable",
    "camp_survey_supplement_ratings": "camp_survey_supplement_ratings_immutable",
}


def _constraint_names(table, kind: str) -> set[str]:
    return {
        constraint.name
        for constraint in table.constraints
        if constraint.__class__.__name__ == kind and constraint.name is not None
    }


def test_survey_models_are_registered_and_exported() -> None:
    assert SURVEY_TABLES <= set(CampSurveyCycle.metadata.tables)
    assert {
        "CampSurveyCycle",
        "CampSurveySnapshotVersion",
        "CampSurveyStaffSnapshot",
        "CampSurveySubmission",
        "CampSurveyVersion",
        "CampSurveyStaffRating",
        "CampSurveySupplement",
        "CampSurveySupplementRating",
    } <= set(evaluation_models.__all__)


def test_survey_model_constraints_freeze_contract() -> None:
    tables = CampSurveyCycle.metadata.tables
    for table_name, expected in EXPECTED_CHECKS.items():
        assert expected <= _constraint_names(tables[table_name], "CheckConstraint")
    for table_name, expected in EXPECTED_UNIQUES.items():
        assert expected <= _constraint_names(tables[table_name], "UniqueConstraint")


def test_survey_food_snapshot_constraint_requires_one_to_three_items() -> None:
    constraint = next(
        item
        for item in CampSurveyCycle.metadata.tables["camp_survey_versions"].constraints
        if item.name == "ck_camp_survey_versions_food_snapshot"
    )
    sqltext = str(constraint.sqltext)
    assert "jsonb_array_length(favorite_cuisines) BETWEEN 1 AND 3" in sqltext
    assert "jsonb_array_length(favorite_dishes) BETWEEN 1 AND 3" in sqltext


def test_all_survey_foreign_keys_are_restrictive() -> None:
    for table_name in SURVEY_TABLES:
        table = CampSurveyCycle.metadata.tables[table_name]
        for constraint in table.foreign_key_constraints:
            assert constraint.ondelete == "RESTRICT", (table_name, constraint.name)


def test_survey_models_use_composite_identity_foreign_keys() -> None:
    cycle_fks = {
        constraint.name: tuple(element.target_fullname for element in constraint.elements)
        for constraint in CampSurveyCycle.__table__.foreign_key_constraints
    }
    assert cycle_fks["fk_camp_survey_cycles_enrollment_target"] == (
        "enrollments.id",
        "enrollments.student_person_id",
        "enrollments.cohort_id",
        "enrollments.class_id",
    )
    rating_fks = {
        constraint.name: {element.parent.name for element in constraint.elements}
        for constraint in CampSurveyStaffRating.__table__.foreign_key_constraints
    }
    assert {"version_id", "snapshot_version_id"} <= rating_fks[
        "fk_camp_survey_staff_ratings_version"
    ]
    version_fks = {
        constraint.name: tuple(element.parent.name for element in constraint.elements)
        for constraint in CampSurveyVersion.__table__.foreign_key_constraints
    }
    assert version_fks["fk_camp_survey_versions_submission"] == (
        "submission_id",
        "cycle_id",
    )
    assert version_fks["fk_camp_survey_versions_snapshot"] == (
        "snapshot_version_id",
        "cycle_id",
    )
    supplement_fks = {
        constraint.name: tuple(element.parent.name for element in constraint.elements)
        for constraint in CampSurveySupplement.__table__.foreign_key_constraints
    }
    assert supplement_fks["fk_camp_survey_supplements_submission"] == (
        "submission_id",
        "cycle_id",
    )
    assert supplement_fks["fk_camp_survey_supplements_snapshot"] == (
        "snapshot_version_id",
        "cycle_id",
    )
    supplement_rating_fks = {
        constraint.name: tuple(element.parent.name for element in constraint.elements)
        for constraint in CampSurveySupplementRating.__table__.foreign_key_constraints
    }
    assert supplement_rating_fks[
        "fk_camp_survey_supplement_ratings_supplement"
    ] == ("supplement_id", "snapshot_version_id")
    assert CampSurveyVersion.__table__.c.cycle_id.nullable is False
    assert CampSurveySupplement.__table__.c.cycle_id.nullable is False


def test_survey_migration_creates_tables_constraints_and_history_triggers(
    postgres_engine: Engine,
) -> None:
    inspector = inspect(postgres_engine)
    assert SURVEY_TABLES <= set(inspector.get_table_names())
    for table_name, expected in EXPECTED_CHECKS.items():
        assert expected <= {
            item["name"] for item in inspector.get_check_constraints(table_name)
        }
    for table_name, expected in EXPECTED_UNIQUES.items():
        assert expected <= {
            item["name"] for item in inspector.get_unique_constraints(table_name)
        }
    for table_name in SURVEY_TABLES:
        for foreign_key in inspector.get_foreign_keys(table_name):
            assert foreign_key["options"].get("ondelete") == "RESTRICT"
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
