from datetime import date, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from app.admissions.models import Enrollment
from app.identity.models import Person, StudentProfile
from app.portal.learning_service import EnrollmentRow, PortalLearningService
from app.portal.schemas import PortalAccessSource
from app.teaching.models import Cohort, GameProject, TeachingClass


def test_students_keep_priority_aware_start_and_missing_name_id_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subjects: dict[UUID, tuple[Person, StudentProfile, PortalAccessSource]] = {}
    rows: dict[UUID, EnrollmentRow] = {}
    specs = [
        (6, "A", None, None),
        (2, "A", "active", "2026-10-25T01:00:00+00:00"),
        (1, "Z", None, None),
        (8, "Z", "active", "2026-10-25T00:00:00+00:00"),
        (3, "A", "upcoming", "2026-10-24T01:00:00+00:00"),
        (7, "A", "active", "2026-10-25T00:00:00+00:00"),
        (5, "A", None, None),
        (4, "A", "active", "2026-10-25T08:00:00+08:00"),
    ]
    for number, name, status, timestamp in specs:
        person_id = UUID(int=number)
        subjects[person_id] = (
            Person(id=person_id, full_name=name, date_of_birth=date(2000, 1, 1)),
            StudentProfile(person_id=person_id, student_number=f"S-{number}"),
            "family",
        )
        if timestamp is None:
            continue
        starts_at = datetime.fromisoformat(timestamp)
        rows[person_id] = (
            Enrollment(
                id=person_id,
                student_person_id=person_id,
                status="active",
                confirmed_at=starts_at - timedelta(days=1),
            ),
            Cohort(
                id=person_id,
                name=f"C-{number}",
                status=status,
                starts_at=starts_at,
                ends_at=starts_at + timedelta(days=20),
            ),
            TeachingClass(id=person_id, name="Standard", class_type="standard"),
            GameProject(id=person_id, name="Game"),
            None,
        )

    # Only the two database/read-authorization boundaries are replaced. The
    # enrollment projection, age projection and public list sorting stay real.
    with Session() as session:
        service = PortalLearningService(session)
        monkeypatch.setattr(service, "authorized_student_people", lambda **_: subjects)
        monkeypatch.setattr(service, "_current_rows", lambda _: rows)
        response = service.list_students(
            user_id=UUID(int=100),
            actor_person_id=UUID(int=101),
            reference_date=date(2026, 10, 25),
        )

    assert [student.student_person_id.int for student in response.items] == [
        4, 7, 8, 2, 3, 5, 6, 1
    ]
    assert response.total == 8
    assert [student.current_enrollment is None for student in response.items] == [
        False, False, False, False, False, True, True, True
    ]
