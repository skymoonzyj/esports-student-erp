"""Build immutable staff rosters for camp completion surveys.

The service deliberately reads historical assignment ranges instead of the
current class/assignment pointers.  A roster row represents one person and
role; all overlapping service segments for that pair are retained in the
JSON snapshot so later edits cannot change an already submitted survey.
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.admissions.models import Enrollment, EnrollmentEvent
from app.admissions.schedule import effective_schedule
from app.audit.service import AuditService
from app.auth.models import UserAccount
from app.core.transactions import write_transaction
from app.evaluations.idempotency import EvaluationIdempotencyService
from app.evaluations.staff_models import CohortStaffAssignment, EvaluationStaffRole
from app.evaluations.survey_models import (
    CampSurveyCycle,
    CampSurveySnapshotVersion,
    CampSurveyStaffSnapshot,
)
from app.evaluations.survey_window import survey_window_for_end
from app.identity.models import Person, StaffProfile
from app.teaching.models import CoachAssignment, Cohort, TeachingClass


@dataclass(frozen=True)
class ServiceSegment:
    role_key: str
    role_name: str
    role_category: str
    staff_person_id: UUID
    staff_name: str
    valid_from: datetime
    valid_until: datetime
    source: str
    scope: str


@dataclass(frozen=True)
class SnapshotRoster:
    cycle: CampSurveyCycle
    snapshot: CampSurveySnapshotVersion
    staff: tuple[CampSurveyStaffSnapshot, ...]
    idempotent: bool = False


@dataclass(frozen=True)
class StaffSnapshotGroup:
    """One respondent-visible staff member with all roles and service spans."""

    staff_person_id: UUID
    staff_name: str
    roles: tuple[dict[str, str], ...]
    intervals: tuple[dict[str, Any], ...]


def _snapshot_staff_sort_key(
    *,
    role_category: str,
    role_name: str,
    staff_name: str,
    staff_person_id: UUID,
) -> tuple[str, str, str, str]:
    """Return the locale-independent respondent-visible staff order."""

    return role_category, role_name, staff_name, str(staff_person_id)


def _merge_service_segments(
    segments: tuple[ServiceSegment, ...] | list[ServiceSegment],
) -> tuple[ServiceSegment, ...]:
    """Merge overlapping/adjacent segments with the same service source/scope."""

    ordered = sorted(
        segments,
        key=lambda item: (
            item.role_key,
            item.staff_person_id,
            item.valid_from,
            item.valid_until,
            item.source,
            item.scope,
        ),
    )
    merged: list[ServiceSegment] = []
    for item in ordered:
        if not merged:
            merged.append(item)
            continue
        previous = merged[-1]
        same_identity = (
            previous.role_key == item.role_key
            and previous.role_name == item.role_name
            and previous.role_category == item.role_category
            and previous.staff_person_id == item.staff_person_id
            and previous.staff_name == item.staff_name
            and previous.source == item.source
            and previous.scope == item.scope
        )
        if same_identity and item.valid_from <= previous.valid_until:
            merged[-1] = ServiceSegment(
                role_key=previous.role_key,
                role_name=previous.role_name,
                role_category=previous.role_category,
                staff_person_id=previous.staff_person_id,
                staff_name=previous.staff_name,
                valid_from=previous.valid_from,
                valid_until=max(previous.valid_until, item.valid_until),
                source=previous.source,
                scope=previous.scope,
            )
        else:
            merged.append(item)
    return tuple(merged)


def _effective_end(
    *,
    valid_until: datetime | None,
    ended_at: datetime | None,
    enrollment_end: datetime,
) -> datetime:
    values = [enrollment_end]
    if valid_until is not None:
        values.append(valid_until)
    if ended_at is not None:
        values.append(ended_at)
    return min(values)


def _overlap(
    *,
    start: datetime,
    end: datetime,
    lower: datetime,
    upper: datetime,
) -> tuple[datetime, datetime] | None:
    left = max(start, lower)
    right = min(end, upper)
    if left >= right:
        return None
    return left, right


def _segment_json(segment: ServiceSegment) -> dict[str, Any]:
    return {
        "role_key": segment.role_key,
        "role_name": segment.role_name,
        "role_category": segment.role_category,
        "from": segment.valid_from.isoformat(),
        "until": segment.valid_until.isoformat(),
        "source": segment.source,
        "scope": segment.scope,
    }


def _group_service_segments_by_staff(
    segments: tuple[ServiceSegment, ...] | list[ServiceSegment],
) -> tuple[StaffSnapshotGroup, ...]:
    """Collapse role assignments so one person receives one score target.

    Roles remain explicit in the immutable JSON snapshot.  This avoids the
    historical uniqueness conflict where a person serving as both coach and
    life teacher was emitted as two rows, while retaining every role and
    interval for audit and display.
    """

    by_staff: dict[UUID, list[ServiceSegment]] = {}
    for segment in segments:
        by_staff.setdefault(segment.staff_person_id, []).append(segment)
    groups: list[StaffSnapshotGroup] = []
    for staff_id, staff_segments in by_staff.items():
        by_role: dict[tuple[str, str, str], list[ServiceSegment]] = {}
        for segment in staff_segments:
            by_role.setdefault(
                (segment.role_category, segment.role_key, segment.role_name), []
            ).append(segment)
        ordered_roles = sorted(
            by_role,
            key=lambda item: (item[0], item[2], item[1]),
        )
        roles = tuple(
            {
                "role_key": role_key,
                "role_name": role_name,
                "role_category": role_category,
            }
            for role_category, role_key, role_name in ordered_roles
        )
        intervals: list[dict[str, Any]] = []
        for role_category, role_key, role_name in ordered_roles:
            for segment in _merge_service_segments(by_role[(role_category, role_key, role_name)]):
                intervals.append(_segment_json(segment))
        first = min(
            staff_segments,
            key=lambda item: (
                item.role_category,
                item.role_name,
                item.role_key,
                item.valid_from,
                item.valid_until,
            ),
        )
        groups.append(
            StaffSnapshotGroup(
                staff_person_id=staff_id,
                staff_name=first.staff_name,
                roles=roles,
                intervals=tuple(intervals),
            )
        )
    return tuple(
        sorted(
            groups,
            key=lambda item: _snapshot_staff_sort_key(
                role_category=item.roles[0]["role_category"],
                role_name=item.roles[0]["role_name"],
                staff_name=item.staff_name,
                staff_person_id=item.staff_person_id,
            ),
        )
    )


def _snapshot_rows_signature(
    rows: tuple[CampSurveyStaffSnapshot, ...] | list[CampSurveyStaffSnapshot],
) -> tuple[tuple[object, ...], ...]:
    """Return the immutable business content without row/version identities."""

    return tuple(
        sorted(
            (
                str(row.staff_person_id),
                row.staff_name_snapshot,
                row.role_key_snapshot,
                row.role_name_snapshot,
                row.role_category,
                json.dumps(
                    row.roles_snapshot,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                json.dumps(
                    row.service_intervals,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                row.include_in_summary,
            )
            for row in rows
        )
    )


def _snapshot_row_sort_key(
    row: CampSurveyStaffSnapshot,
) -> tuple[str, str, str, str]:
    return _snapshot_staff_sort_key(
        role_category=row.role_category,
        role_name=row.role_name_snapshot,
        staff_name=row.staff_name_snapshot,
        staff_person_id=row.staff_person_id,
    )


class CampSurveySnapshotService:
    """Creates and repairs cycle/snapshot rows with deterministic locking."""

    def __init__(
        self,
        db: Session,
        *,
        idempotency: EvaluationIdempotencyService | None = None,
    ) -> None:
        self.db = db
        self.idempotency = idempotency
        self.audit = AuditService(db)

    @staticmethod
    def _now(value: datetime | None) -> datetime:
        result = value or datetime.now(UTC)
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        return result

    def _roster(
        self,
        *,
        cycle: CampSurveyCycle,
        snapshot: CampSurveySnapshotVersion,
        idempotent: bool = False,
    ) -> SnapshotRoster:
        staff = tuple(
            sorted(
                self.db.scalars(
                    select(CampSurveyStaffSnapshot).where(
                        CampSurveyStaffSnapshot.snapshot_version_id == snapshot.id
                    )
                ),
                key=_snapshot_row_sort_key,
            )
        )
        return SnapshotRoster(
            cycle=cycle,
            snapshot=snapshot,
            staff=staff,
            idempotent=idempotent,
        )

    def _locked_enrollment(self, enrollment_id: UUID) -> Enrollment:
        enrollment = self.db.scalar(
            select(Enrollment)
            .where(Enrollment.id == enrollment_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if enrollment is None:
            raise ValueError("enrollment not found")
        if enrollment.status != "completed" or enrollment.completed_at is None:
            raise ValueError("enrollment is not completed")
        return enrollment

    def _locked_cohort(self, cohort_id: UUID) -> Cohort:
        cohort = self.db.scalar(
            select(Cohort)
            .where(Cohort.id == cohort_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if cohort is None:
            raise ValueError("cohort not found")
        return cohort

    def _lock_snapshot_sources(self, cohort_id: UUID) -> None:
        """Freeze mutable assignment history before locking the cycle pointer."""

        coach_assignments = list(
            self.db.scalars(
                select(CoachAssignment)
                .join(TeachingClass, TeachingClass.id == CoachAssignment.class_id)
                .where(TeachingClass.cohort_id == cohort_id)
                .order_by(CoachAssignment.id)
                .with_for_update(of=CoachAssignment)
                .execution_options(populate_existing=True)
            )
        )
        staff_assignments = list(
            self.db.scalars(
                select(CohortStaffAssignment)
                .where(CohortStaffAssignment.cohort_id == cohort_id)
                .order_by(CohortStaffAssignment.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        role_ids = {assignment.role_id for assignment in staff_assignments}
        if role_ids:
            list(
                self.db.scalars(
                    select(EvaluationStaffRole)
                    .where(EvaluationStaffRole.id.in_(role_ids))
                    .order_by(EvaluationStaffRole.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
        person_ids = {
            assignment.coach_person_id for assignment in coach_assignments
        } | {
            assignment.staff_person_id for assignment in staff_assignments
        }
        if not person_ids:
            return
        list(
            self.db.scalars(
                select(Person)
                .where(Person.id.in_(person_ids))
                .order_by(Person.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        list(
            self.db.scalars(
                select(StaffProfile)
                .where(StaffProfile.person_id.in_(person_ids))
                .order_by(StaffProfile.person_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        list(
            self.db.scalars(
                select(UserAccount)
                .where(UserAccount.person_id.in_(person_ids))
                .order_by(UserAccount.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )

    def _class_history(
        self, enrollment: Enrollment, *, lower: datetime, upper: datetime
    ) -> list[tuple[UUID, datetime, datetime]]:
        events = list(
            self.db.scalars(
                select(EnrollmentEvent)
                .where(EnrollmentEvent.enrollment_id == enrollment.id)
                .order_by(EnrollmentEvent.occurred_at, EnrollmentEvent.id)
            )
        )
        current_class: UUID | None = None
        current_start: datetime | None = None
        rows: list[tuple[UUID, datetime, datetime]] = []
        for event in events:
            if event.event_type == "confirmed" and event.new_class_id is not None:
                current_class = event.new_class_id
                current_start = max(event.occurred_at, lower)
            elif event.event_type == "transferred" and event.new_class_id is not None:
                if current_class is not None and current_start is not None:
                    end = min(event.occurred_at, upper)
                    if current_start < end:
                        rows.append((current_class, current_start, end))
                current_class = event.new_class_id
                current_start = max(event.occurred_at, lower)
            elif event.event_type in {"cancelled", "completed", "withdrawal_refunded"}:
                if current_class is not None and current_start is not None:
                    end = min(event.occurred_at, upper)
                    if current_start < end:
                        rows.append((current_class, current_start, end))
                current_class = None
                current_start = None
        if current_class is not None and current_start is not None:
            end = upper
            if current_start < end:
                rows.append((current_class, current_start, end))
        if not rows and enrollment.class_id:
            rows.append((enrollment.class_id, lower, upper))
        return rows

    def _segments(
        self, enrollment: Enrollment, cohort: Cohort
    ) -> list[ServiceSegment]:
        schedule = effective_schedule(enrollment, cohort)
        lower = max(enrollment.confirmed_at, schedule.starts_at)
        upper = min(enrollment.completed_at or schedule.ends_at, schedule.ends_at)
        class_history = self._class_history(enrollment, lower=lower, upper=upper)
        class_ids = {item[0] for item in class_history}
        classes = {
            row.id: row
            for row in self.db.scalars(select(TeachingClass).where(TeachingClass.id.in_(class_ids)))
        }
        segments: list[ServiceSegment] = []

        def add(
            *,
            staff_id: UUID,
            role_key: str,
            role_name: str,
            category: str,
            start: datetime,
            end: datetime,
            source: str,
            scope: str,
        ) -> None:
            overlap = _overlap(start=start, end=end, lower=lower, upper=upper)
            if overlap is None:
                return
            person = self.db.scalar(
                select(Person)
                .join(StaffProfile, StaffProfile.person_id == Person.id)
                .join(UserAccount, UserAccount.person_id == Person.id)
                .where(
                    Person.id == staff_id,
                    Person.deleted_at.is_(None),
                    UserAccount.status == "active",
                )
                .limit(1)
            )
            if person is None:
                return
            segments.append(
                ServiceSegment(
                    role_key=role_key,
                    role_name=role_name,
                    role_category=category,
                    staff_person_id=staff_id,
                    staff_name=person.full_name,
                    valid_from=overlap[0],
                    valid_until=overlap[1],
                    source=source,
                    scope=scope,
                )
            )

        for class_id, class_start, class_end in class_history:
            teaching_class = classes.get(class_id)
            if teaching_class is None:
                continue
            coach_assignments = self.db.scalars(
                select(CoachAssignment)
                .where(
                    CoachAssignment.class_id == class_id,
                    CoachAssignment.valid_from < class_end,
                    or_(CoachAssignment.valid_until.is_(None), CoachAssignment.valid_until > class_start),
                    or_(CoachAssignment.ended_at.is_(None), CoachAssignment.ended_at > class_start),
                )
            )
            for assignment in coach_assignments:
                end = _effective_end(
                    valid_until=assignment.valid_until,
                    ended_at=assignment.ended_at,
                    enrollment_end=class_end,
                )
                add(
                    staff_id=assignment.coach_person_id,
                    role_key="primary_coach" if assignment.assignment_type == "primary" else "assistant_coach",
                    role_name="主教练" if assignment.assignment_type == "primary" else "助教",
                    category="teaching",
                    start=max(assignment.valid_from, class_start),
                    end=end,
                    source="coach_assignment",
                    scope=f"class:{class_id}",
                )
            staff_assignments = self.db.execute(
                select(CohortStaffAssignment, EvaluationStaffRole)
                .join(EvaluationStaffRole, EvaluationStaffRole.id == CohortStaffAssignment.role_id)
                .where(
                    CohortStaffAssignment.cohort_id == cohort.id,
                    or_(CohortStaffAssignment.class_id == class_id,
                        and_(CohortStaffAssignment.project_id == teaching_class.project_id,
                             CohortStaffAssignment.class_id.is_(None))),
                    EvaluationStaffRole.is_active.is_(True),
                    CohortStaffAssignment.valid_from < class_end,
                    or_(CohortStaffAssignment.valid_until.is_(None), CohortStaffAssignment.valid_until > class_start),
                    or_(CohortStaffAssignment.ended_at.is_(None), CohortStaffAssignment.ended_at > class_start),
                )
            )
            for assignment, role in staff_assignments:
                end = _effective_end(
                    valid_until=assignment.valid_until,
                    ended_at=assignment.ended_at,
                    enrollment_end=class_end,
                )
                add(
                    staff_id=assignment.staff_person_id,
                    role_key=role.key,
                    role_name=assignment.role_name_snapshot,
                    category=assignment.role_category_snapshot,
                    start=max(assignment.valid_from, class_start),
                    end=end,
                    source="cohort_staff_assignment",
                    scope=(f"class:{class_id}" if assignment.class_id else f"project:{teaching_class.project_id}"),
                )
        return segments

    def _build_snapshot_rows(
        self, enrollment: Enrollment, cohort: Cohort, snapshot_id: UUID
    ) -> list[CampSurveyStaffSnapshot]:
        grouped = _group_service_segments_by_staff(self._segments(enrollment, cohort))
        rows: list[CampSurveyStaffSnapshot] = []
        for group in grouped:
            first_role = group.roles[0]
            rows.append(
                CampSurveyStaffSnapshot(
                    snapshot_version_id=snapshot_id,
                    staff_person_id=group.staff_person_id,
                    staff_name_snapshot=group.staff_name,
                    role_key_snapshot=first_role["role_key"],
                    role_name_snapshot=first_role["role_name"],
                    role_category=first_role["role_category"],
                    roles_snapshot=list(group.roles),
                    service_intervals=list(group.intervals),
                    include_in_summary=True,
                )
            )
        rows.sort(key=_snapshot_row_sort_key)
        return rows

    def ensure_cycle(
        self, enrollment_id: UUID, *, actor_user_id: UUID | None = None
    ) -> SnapshotRoster:
        with write_transaction(self.db):
            return self.ensure_cycle_in_transaction(
                enrollment_id, actor_user_id=actor_user_id
            )

    def ensure_cycle_in_transaction(
        self, enrollment_id: UUID, *, actor_user_id: UUID | None = None
    ) -> SnapshotRoster:
        """Ensure a cycle while participating in an already-open write transaction."""
        # All survey mutations use the shared lock order cohort -> enrollment
        # -> coach assignments -> cohort staff assignments -> staff roles -> people
        # -> staff profiles -> user accounts -> cycle so snapshot sources stay frozen.
        cohort_id = self.db.scalar(
            select(Enrollment.cohort_id).where(Enrollment.id == enrollment_id)
        )
        if cohort_id is None:
            raise ValueError("enrollment not found")
        cohort = self._locked_cohort(cohort_id)
        enrollment = self._locked_enrollment(enrollment_id)
        self._lock_snapshot_sources(cohort.id)
        cycle = self.db.scalar(
            select(CampSurveyCycle)
            .where(CampSurveyCycle.enrollment_id == enrollment.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if cycle is None:
            completed_at = enrollment.completed_at
            if completed_at is None:  # guarded by _locked_enrollment
                raise RuntimeError("completed enrollment has no completion time")
            window = survey_window_for_end(completed_at)
            cycle = CampSurveyCycle(
                enrollment_id=enrollment.id,
                student_person_id=enrollment.student_person_id,
                cohort_id=enrollment.cohort_id,
                class_id=enrollment.class_id,
                opens_at=window.opens_at,
                due_at=window.closes_at,
                current_snapshot_version=1,
            )
            self.db.add(cycle)
            self.db.flush()
            snapshot = CampSurveySnapshotVersion(
                cycle_id=cycle.id,
                version=1,
                change_reason="initial roster",
                created_by_user_id=actor_user_id,
            )
            self.db.add(snapshot)
            self.db.flush()
            self.db.add_all(self._build_snapshot_rows(enrollment, cohort, snapshot.id))
            self.db.flush()
        else:
            existing_snapshot = self.db.scalar(
                select(CampSurveySnapshotVersion)
                .where(
                    CampSurveySnapshotVersion.cycle_id == cycle.id,
                    CampSurveySnapshotVersion.version == cycle.current_snapshot_version,
                )
            )
            if existing_snapshot is None:
                raise RuntimeError("survey snapshot pointer is missing")
            snapshot = existing_snapshot
        return self._roster(cycle=cycle, snapshot=snapshot)

    def create_snapshot_version(
        self,
        cycle_id: UUID,
        *,
        actor_user_id: UUID,
        change_reason: str,
        idempotency_key: str,
        request_id: str,
        occurred_at: datetime | None = None,
        excluded_staff_person_ids: set[UUID] | None = None,
    ) -> SnapshotRoster:
        """Rebuild the roster, appending only when immutable content changed.

        Before the first response this is a content-idempotent rebuild of the
        unused current roster.  After responses exist the same comparison
        avoids duplicate correction versions, while a real roster change is
        still appended and becomes the new current snapshot.  Authorization
        is intentionally owned by the future administrator-facing caller.
        """

        reason = change_reason.strip()
        if not reason or len(reason) > 500:
            raise ValueError("change_reason is required")
        if self.idempotency is None:
            raise RuntimeError("snapshot correction idempotency is required")
        now = self._now(occurred_at)
        excluded_ids = excluded_staff_person_ids or set()
        digests = self.idempotency.prepare(
            scope="camp_survey.snapshot.version",
            actor_user_id=actor_user_id,
            key=idempotency_key,
            payload={
                "cycle_id": str(cycle_id),
                "change_reason": reason,
                "excluded_staff_person_ids": sorted(
                    str(person_id) for person_id in excluded_ids
                ),
            },
        )
        with write_transaction(self.db):
            cycle_identity = self.db.execute(
                select(CampSurveyCycle.enrollment_id, CampSurveyCycle.cohort_id).where(
                    CampSurveyCycle.id == cycle_id
                )
            ).one_or_none()
            if cycle_identity is None:
                raise ValueError("survey cycle not found")
            enrollment_id, cohort_id = cycle_identity
            cohort = self._locked_cohort(cohort_id)
            enrollment = self._locked_enrollment(enrollment_id)
            self._lock_snapshot_sources(cohort.id)
            cycle = self.db.scalar(
                select(CampSurveyCycle)
                .where(CampSurveyCycle.id == cycle_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if cycle is None:
                raise ValueError("survey cycle not found")
            replay_id = self.idempotency.existing_resource_id(
                digests,
                occurred_at=now,
            )
            if replay_id is not None:
                replay = self.db.scalar(
                    select(CampSurveySnapshotVersion).where(
                        CampSurveySnapshotVersion.id == replay_id,
                        CampSurveySnapshotVersion.cycle_id == cycle.id,
                    )
                )
                if replay is None:
                    raise RuntimeError("idempotent survey snapshot is unavailable")
                return self._roster(
                    cycle=cycle,
                    snapshot=replay,
                    idempotent=True,
                )

            current_snapshot = self.db.scalar(
                select(CampSurveySnapshotVersion).where(
                    CampSurveySnapshotVersion.cycle_id == cycle.id,
                    CampSurveySnapshotVersion.version
                    == cycle.current_snapshot_version,
                )
            )
            if current_snapshot is None:
                raise RuntimeError("survey snapshot pointer is missing")
            current_rows = list(
                self.db.scalars(
                    select(CampSurveyStaffSnapshot).where(
                        CampSurveyStaffSnapshot.snapshot_version_id
                        == current_snapshot.id
                    )
                )
            )

            next_version = cycle.current_snapshot_version + 1
            snapshot_id = uuid4()
            rows = self._build_snapshot_rows(enrollment, cohort, snapshot_id)
            rows_by_staff = {row.staff_person_id: row for row in rows}
            current_by_staff = {
                row.staff_person_id: row for row in current_rows
            }
            unknown_exclusions = excluded_ids - rows_by_staff.keys() - current_by_staff.keys()
            if unknown_exclusions:
                raise ValueError("excluded staff is not in the survey roster")
            for staff_person_id, snapshot_row in rows_by_staff.items():
                previous = current_by_staff.get(staff_person_id)
                if previous is not None:
                    snapshot_row.include_in_summary = previous.include_in_summary
            for staff_person_id in excluded_ids:
                row = rows_by_staff.get(staff_person_id)
                if row is None:
                    previous = current_by_staff[staff_person_id]
                    row = CampSurveyStaffSnapshot(
                        snapshot_version_id=snapshot_id,
                        staff_person_id=previous.staff_person_id,
                        staff_name_snapshot=previous.staff_name_snapshot,
                        role_key_snapshot=previous.role_key_snapshot,
                        role_name_snapshot=previous.role_name_snapshot,
                        role_category=previous.role_category,
                        roles_snapshot=deepcopy(previous.roles_snapshot),
                        service_intervals=deepcopy(previous.service_intervals),
                        include_in_summary=False,
                    )
                    rows.append(row)
                    rows_by_staff[staff_person_id] = row
                else:
                    row.include_in_summary = False
            rows.sort(key=_snapshot_row_sort_key)
            if _snapshot_rows_signature(rows) == _snapshot_rows_signature(current_rows):
                self.idempotency.record(
                    digests,
                    resource_id=current_snapshot.id,
                    occurred_at=now,
                )
                return self._roster(
                    cycle=cycle,
                    snapshot=current_snapshot,
                    idempotent=True,
                )

            snapshot = CampSurveySnapshotVersion(
                id=snapshot_id,
                cycle_id=cycle.id,
                version=next_version,
                change_reason=reason,
                created_by_user_id=actor_user_id,
            )
            self.db.add(snapshot)
            self.db.flush()
            self.db.add_all(rows)
            cycle.current_snapshot_version = next_version
            self.db.flush()
            self.idempotency.record(
                digests,
                resource_id=snapshot.id,
                occurred_at=now,
            )
            self.audit.append(
                actor_user_id=actor_user_id,
                action="camp_survey.snapshot.version.create",
                resource_type="camp_survey_snapshot_version",
                resource_id=snapshot.id,
                purpose=None,
                outcome="allowed",
                request_id=request_id,
                metadata={
                    "cohort_id": str(cycle.cohort_id),
                    "enrollment_id": str(cycle.enrollment_id),
                    "snapshot_version": snapshot.version,
                    "item_count": len(rows),
                },
            )
            return SnapshotRoster(
                cycle=cycle,
                snapshot=snapshot,
                staff=tuple(rows),
            )
