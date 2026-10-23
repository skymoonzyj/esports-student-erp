"""Database guards against mixing immutable survey history across cycles."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.evaluations.survey_models import (
    CampSurveySnapshotVersion,
    CampSurveyStaffSnapshot,
    CampSurveySubmission,
    CampSurveySupplement,
    CampSurveySupplementRating,
    CampSurveyVersion,
)
from tests.api.test_camp_surveys import CampApiScenario

pytest_plugins = ("tests.api.test_camp_surveys",)


def _submission(db: Session, scenario: CampApiScenario) -> CampSurveySubmission:
    row = CampSurveySubmission(
        cycle_id=scenario.cycle.id,
        respondent_person_id=scenario.cycle.student_person_id,
        student_person_id=scenario.cycle.student_person_id,
        respondent_kind="self",
        guardian_relationship_id=None,
        guardian_relationship_snapshot=None,
    )
    db.add(row)
    db.flush()
    return row


def _snapshot(
    db: Session,
    cycle_id,
) -> CampSurveySnapshotVersion:
    row = db.scalar(
        select(CampSurveySnapshotVersion).where(
            CampSurveySnapshotVersion.cycle_id == cycle_id,
            CampSurveySnapshotVersion.version == 1,
        )
    )
    assert row is not None
    return row


def _version(
    submission: CampSurveySubmission,
    snapshot: CampSurveySnapshotVersion,
) -> CampSurveyVersion:
    return CampSurveyVersion(
        submission_id=submission.id,
        snapshot_version_id=snapshot.id,
        cycle_id=submission.cycle_id,
        version=1,
        course_training_score=8,
        life_service_score=8,
        service_management_score=8,
        favorite_cuisines=[
            {
                "cuisine_option_id": str(uuid4()),
                "name": "川菜",
                "custom_name": None,
            }
        ],
        favorite_dishes=["鱼香肉丝"],
        published_at=datetime.now(UTC),
    )


def test_version_rejects_snapshot_from_another_cycle(
    db: Session,
    camp_api_scenario: CampApiScenario,
) -> None:
    submission = _submission(db, camp_api_scenario)
    foreign_snapshot = _snapshot(db, camp_api_scenario.second_cycle.id)

    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(_version(submission, foreign_snapshot))
        db.flush()


def test_supplement_rejects_snapshot_from_another_cycle(
    db: Session,
    camp_api_scenario: CampApiScenario,
) -> None:
    submission = _submission(db, camp_api_scenario)
    foreign_snapshot = _snapshot(db, camp_api_scenario.second_cycle.id)

    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(
            CampSurveySupplement(
                submission_id=submission.id,
                snapshot_version_id=foreign_snapshot.id,
                cycle_id=submission.cycle_id,
                change_reason="跨周期负例",
                created_by_user_id=camp_api_scenario.accounts["admin"].id,
                submitted_at=datetime.now(UTC),
            )
        )
        db.flush()


def test_supplement_rating_must_use_its_supplement_snapshot(
    db: Session,
    camp_api_scenario: CampApiScenario,
) -> None:
    submission = _submission(db, camp_api_scenario)
    own_snapshot = _snapshot(db, camp_api_scenario.cycle.id)
    foreign_snapshot = _snapshot(db, camp_api_scenario.second_cycle.id)
    foreign_staff = db.scalar(
        select(CampSurveyStaffSnapshot).where(
            CampSurveyStaffSnapshot.snapshot_version_id == foreign_snapshot.id
        )
    )
    assert foreign_staff is not None
    supplement = CampSurveySupplement(
        submission_id=submission.id,
        snapshot_version_id=own_snapshot.id,
        cycle_id=submission.cycle_id,
        change_reason="合法补充项",
        created_by_user_id=camp_api_scenario.accounts["admin"].id,
        submitted_at=datetime.now(UTC),
    )
    db.add(supplement)
    db.flush()

    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(
            CampSurveySupplementRating(
                supplement_id=supplement.id,
                staff_snapshot_id=foreign_staff.id,
                snapshot_version_id=foreign_snapshot.id,
                score=8,
            )
        )
        db.flush()
