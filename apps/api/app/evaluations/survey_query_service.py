"""Read-only, privacy-safe projections for camp survey feedback."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, aliased

from app.evaluations.survey_models import (
    CampSurveyCycle,
    CampSurveySnapshotVersion,
    CampSurveyStaffRating,
    CampSurveyStaffSnapshot,
    CampSurveySubmission,
    CampSurveySupplement,
    CampSurveySupplementRating,
    CampSurveyVersion,
)
from app.identity.models import Person
from app.teaching.models import TeachingClass


@dataclass(frozen=True)
class AnonymousSurveyItem:
    cycle_id: UUID | None
    enrollment_id: UUID | None
    cohort_id: UUID
    version_id: UUID | None
    version: int
    ordinal: int
    staff_snapshot_id: UUID | None
    staff_person_id: UUID | None
    staff_name: str | None
    role_key: str | None
    role_name: str | None
    role_category: str | None
    score: int
    reason: str | None
    suggestion: str | None
    respondent_label: str
    respondent_person_id: UUID | None = None
    respondent_name: str | None = None
    respondent_kind: str | None = None
    course_training_score: int | None = None
    course_training_reason: str | None = None
    life_service_score: int | None = None
    life_service_reason: str | None = None
    service_management_score: int | None = None
    service_management_reason: str | None = None
    overall_suggestion: str | None = None
    favorite_cuisines: list[dict[str, object]] | None = None
    favorite_dishes: list[str] | None = None


@dataclass(frozen=True)
class AnonymousSurveyPage:
    items: tuple[AnonymousSurveyItem, ...]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True)
class StaffSurveyItem:
    """Role-filtered feedback.  Deliberately has no respondent or staff identity."""

    cycle_id: UUID
    cohort_id: UUID
    version: int
    ordinal: int
    role_category: str
    score: int
    reason: str | None
    suggestion: str | None
    course_training_score: int | None = None
    course_training_reason: str | None = None
    life_service_score: int | None = None
    life_service_reason: str | None = None
    service_management_score: int | None = None
    service_management_reason: str | None = None
    team_suggestion: str | None = None
    project_id: UUID | None = None
    role_key: str | None = None
    role_name: str | None = None


@dataclass(frozen=True)
class AdminSurveyPage:
    items: tuple[object, ...]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True)
class PrintDetail:
    score: int
    reason: str | None
    suggestion: str | None
    ordinal: int
    respondent_person_id: UUID | None = None
    respondent_name: str | None = None
    respondent_kind: str | None = None


@dataclass(frozen=True)
class PrintProjectionItem:
    cohort_id: UUID
    project_id: UUID | None
    staff_person_id: UUID | None
    staff_name: str | None
    role_key: str
    role_name: str
    role_category: str
    service_intervals: tuple[dict[str, object], ...]
    average_score: float
    item_count: int
    details: tuple[PrintDetail, ...]
    team_suggestions: tuple[str, ...] = ()
    roles: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class PrintProjection:
    identity: str
    items: tuple[PrintProjectionItem, ...]


@dataclass(frozen=True)
class StaffSurveyPage:
    items: tuple[StaffSurveyItem, ...]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True)
class _FeedbackRow:
    cycle: CampSurveyCycle
    submission: CampSurveySubmission
    version: CampSurveyVersion
    rating: CampSurveyStaffRating | CampSurveySupplementRating
    snapshot: CampSurveyStaffSnapshot
    project_id: UUID | None
    is_supplement: bool = False


FeedbackRowAuthorizer = Callable[[_FeedbackRow], bool]


class CampSurveyQueryService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def has_staff_snapshot(self, *, staff_person_id: UUID) -> bool:
        """Return whether this person has any immutable survey staff snapshot.

        A snapshot is the server-owned capability boundary for the staff
        feedback projection.  Historical snapshots intentionally remain
        eligible after an assignment ends, while the router separately checks
        the account's live evaluation-center permission.
        """
        return (
            self.db.scalar(
                select(CampSurveyStaffSnapshot.id)
                .where(CampSurveyStaffSnapshot.staff_person_id == staff_person_id)
                .limit(1)
            )
            is not None
        )

    @staticmethod
    def _role_categories(snapshot: CampSurveyStaffSnapshot) -> tuple[str, ...]:
        categories: list[str] = []
        for role in snapshot.roles_snapshot or []:
            category = role.get("role_category")
            if isinstance(category, str) and category not in categories:
                categories.append(category)
        if snapshot.role_category not in categories:
            categories.append(snapshot.role_category)
        return tuple(categories)

    def _base_statement(
        self,
        *,
        current_snapshot: Any,
        current_staff: Any,
    ) -> Any:
        return (
            select(
                CampSurveyCycle,
                CampSurveySubmission,
                CampSurveyVersion,
                CampSurveyStaffRating,
                CampSurveyStaffSnapshot,
                current_staff,
            )
            .join(
                CampSurveySubmission,
                CampSurveySubmission.cycle_id == CampSurveyCycle.id,
            )
            .join(
                CampSurveyVersion,
                CampSurveyVersion.submission_id == CampSurveySubmission.id,
            )
            .join(
                CampSurveyStaffRating,
                CampSurveyStaffRating.version_id == CampSurveyVersion.id,
            )
            .join(
                CampSurveyStaffSnapshot,
                CampSurveyStaffSnapshot.id == CampSurveyStaffRating.staff_snapshot_id,
            )
            .join(
                current_snapshot,
                (current_snapshot.cycle_id == CampSurveyCycle.id)
                & (current_snapshot.version == CampSurveyCycle.current_snapshot_version),
            )
            .join(
                current_staff,
                (current_staff.snapshot_version_id == current_snapshot.id)
                & (
                    current_staff.staff_person_id
                    == CampSurveyStaffSnapshot.staff_person_id
                ),
            )
        )

    @staticmethod
    def _validate_pagination(page: int, page_size: int) -> None:
        if page < 1 or page_size < 1 or page_size > 200:
            raise ValueError("invalid pagination")

    @staticmethod
    def _apply_filters(
        statement: Any,
        *,
        cohort_id: UUID | None,
        project_id: UUID | None = None,
        staff_person_id: UUID | None,
        role_key: str | None = None,
        staff_snapshot_model: Any = CampSurveyStaffSnapshot,
    ) -> Any:
        if cohort_id is not None:
            statement = statement.where(CampSurveyCycle.cohort_id == cohort_id)
        if project_id is not None:
            statement = statement.join(
                TeachingClass,
                (TeachingClass.id == CampSurveyCycle.class_id)
                & (TeachingClass.cohort_id == CampSurveyCycle.cohort_id),
            ).where(TeachingClass.project_id == project_id)
        if staff_person_id is not None:
            statement = statement.where(
                staff_snapshot_model.staff_person_id == staff_person_id
            )
        if role_key is not None:
            statement = statement.where(
                or_(
                    staff_snapshot_model.role_key_snapshot == role_key,
                    staff_snapshot_model.roles_snapshot.contains(
                        [{"role_key": role_key}]
                    ),
                )
            )
        return statement

    def _main_statement(
        self,
        *,
        cohort_id: UUID | None = None,
        project_id: UUID | None = None,
        staff_person_id: UUID | None = None,
        role_key: str | None = None,
    ) -> Any:
        current_snapshot = aliased(CampSurveySnapshotVersion)
        current_staff = aliased(CampSurveyStaffSnapshot)
        statement = self._base_statement(
            current_snapshot=current_snapshot,
            current_staff=current_staff,
        ).where(
            CampSurveyVersion.version == CampSurveySubmission.latest_version,
            current_staff.include_in_summary.is_(True),
        )
        return self._apply_filters(
            statement,
            cohort_id=cohort_id,
            project_id=project_id,
            staff_person_id=staff_person_id,
            role_key=role_key,
            staff_snapshot_model=current_staff,
        )

    def _feedback_rows(
        self,
        *,
        cohort_id: UUID | None = None,
        project_id: UUID | None = None,
        staff_person_id: UUID | None = None,
        role_key: str | None = None,
    ) -> list[_FeedbackRow]:
        """Load current main ratings plus submitted supplement ratings.

        The union is intentionally assembled in Python: supplements are an
        append-only table and have no survey version of their own, while the
        public projection still needs the respondent's latest version and
        overall fields.  This keeps both sources under the same filters and
        makes one anonymous ordinal stream for callers.
        """
        main_rows = self.db.execute(
            self._main_statement(
                cohort_id=cohort_id,
                project_id=project_id,
                staff_person_id=staff_person_id,
                role_key=role_key,
            ).order_by(
                CampSurveyCycle.id,
                CampSurveySubmission.id,
                CampSurveyStaffSnapshot.id,
            )
        ).all()
        supplement_statement = self._supplement_statement(
            cohort_id=cohort_id,
            project_id=project_id,
            staff_person_id=staff_person_id,
            role_key=role_key,
        )
        supplement_rows = self.db.execute(
            supplement_statement.order_by(
                CampSurveyCycle.id,
                CampSurveySubmission.id,
                CampSurveyStaffSnapshot.id,
            )
        ).all()
        raw_rows: list[
            tuple[
                CampSurveyCycle,
                CampSurveySubmission,
                CampSurveyVersion,
                CampSurveyStaffRating | CampSurveySupplementRating,
                CampSurveyStaffSnapshot,
                bool,
            ]
        ] = []
        for main_row in main_rows:
            if len(main_row) == 6:
                cycle, submission, version, rating, _historical_snapshot, snapshot = main_row
            else:
                cycle, submission, version, rating, snapshot = main_row
            raw_rows.append((cycle, submission, version, rating, snapshot, False))
        for supplement_row in supplement_rows:
            if len(supplement_row) == 7:
                (
                    cycle,
                    submission,
                    _supplement,
                    rating,
                    _historical_snapshot,
                    version,
                    snapshot,
                ) = supplement_row
            else:
                cycle, submission, _supplement, rating, snapshot, version = supplement_row
            raw_rows.append((cycle, submission, version, rating, snapshot, True))
        class_ids = {row[0].class_id for row in raw_rows}
        project_by_class = {
            class_id: project
            for class_id, project in self.db.execute(
                select(TeachingClass.id, TeachingClass.project_id).where(
                    TeachingClass.id.in_(class_ids)
                )
            )
        } if class_ids else {}
        rows: list[_FeedbackRow] = [
            _FeedbackRow(
                cycle=cycle,
                submission=submission,
                version=version,
                rating=rating,
                snapshot=snapshot,
                project_id=project_by_class.get(cycle.class_id),
                is_supplement=is_supplement,
            )
            for cycle, submission, version, rating, snapshot, is_supplement
            in raw_rows
        ]
        # A supplement is created from a later roster snapshot, so its staff
        # snapshot UUID is different from the UUID used by the main rating.
        # The stable person identity is the actual subject of the feedback,
        # however.  Keep the main row first (``raw_rows`` is assembled in main
        # then supplement order), and only append a supplement when that person
        # has not already contributed to this respondent's cycle.  This also
        # prevents repeated supplements across successive snapshot revisions
        # from inflating aggregates.  The SQL predicates above still decide
        # whether historical rows are currently included, preserving the
        # exclusion/re-inclusion semantics of the current roster snapshot.
        seen_feedback_keys: set[tuple[UUID, UUID, UUID]] = set()
        deduplicated_rows: list[_FeedbackRow] = []

        def append_if_new(row: _FeedbackRow) -> None:
            key = (
                row.cycle.id,
                row.submission.id,
                row.snapshot.staff_person_id,
            )
            if key in seen_feedback_keys:
                return
            seen_feedback_keys.add(key)
            deduplicated_rows.append(row)

        # Process both sources explicitly so this preference remains true even
        # if the database ordering or raw-row assembly changes later.
        for feedback_row in rows:
            if not feedback_row.is_supplement:
                append_if_new(feedback_row)
        for feedback_row in rows:
            if feedback_row.is_supplement:
                append_if_new(feedback_row)
        rows = deduplicated_rows
        rows.sort(
            key=lambda row: (
                str(row.cycle.id),
                str(row.submission.id),
                row.snapshot.role_category,
                row.snapshot.role_name_snapshot,
                row.snapshot.staff_name_snapshot,
                str(row.snapshot.id),
                row.is_supplement,
            )
        )
        return rows

    def _supplement_statement(
        self,
        *,
        cohort_id: UUID | None = None,
        project_id: UUID | None = None,
        staff_person_id: UUID | None = None,
        role_key: str | None = None,
    ) -> Any:
        supplement_snapshot = aliased(CampSurveySnapshotVersion)
        current_snapshot = aliased(CampSurveySnapshotVersion)
        current_staff = aliased(CampSurveyStaffSnapshot)
        statement = (
            select(
                CampSurveyCycle,
                CampSurveySubmission,
                CampSurveySupplement,
                CampSurveySupplementRating,
                CampSurveyStaffSnapshot,
                CampSurveyVersion,
                current_staff,
            )
            .join(
                CampSurveySubmission,
                CampSurveySubmission.cycle_id == CampSurveyCycle.id,
            )
            .join(
                CampSurveySupplement,
                CampSurveySupplement.submission_id == CampSurveySubmission.id,
            )
            .join(
                CampSurveySupplementRating,
                CampSurveySupplementRating.supplement_id == CampSurveySupplement.id,
            )
            .join(
                CampSurveyStaffSnapshot,
                CampSurveyStaffSnapshot.id
                == CampSurveySupplementRating.staff_snapshot_id,
            )
            .join(
                supplement_snapshot,
                supplement_snapshot.id == CampSurveySupplement.snapshot_version_id,
            )
            .join(
                CampSurveyVersion,
                (CampSurveyVersion.submission_id == CampSurveySubmission.id)
                & (CampSurveyVersion.version == CampSurveySubmission.latest_version),
            )
            .join(
                current_snapshot,
                (current_snapshot.cycle_id == CampSurveyCycle.id)
                & (current_snapshot.version == CampSurveyCycle.current_snapshot_version),
            )
            .join(
                current_staff,
                (current_staff.snapshot_version_id == current_snapshot.id)
                & (
                    current_staff.staff_person_id
                    == CampSurveyStaffSnapshot.staff_person_id
                ),
            )
            .where(
                current_staff.include_in_summary.is_(True),
            )
        )
        return self._apply_filters(
            statement,
            cohort_id=cohort_id,
            project_id=project_id,
            staff_person_id=staff_person_id,
            role_key=role_key,
            staff_snapshot_model=current_staff,
        )

    def list_anonymous(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        cohort_id: UUID | None = None,
        project_id: UUID | None = None,
        staff_person_id: UUID | None = None,
        role_key: str | None = None,
        include_staff_identity: bool = False,
        include_respondent_identity: bool = False,
        include_staff_snapshot_id: bool = False,
        row_authorizer: FeedbackRowAuthorizer | None = None,
    ) -> AnonymousSurveyPage:
        self._validate_pagination(page, page_size)
        all_rows = self._feedback_rows(
            cohort_id=cohort_id,
            project_id=project_id,
            staff_person_id=staff_person_id,
            role_key=role_key,
        )
        if row_authorizer is not None:
            all_rows = [row for row in all_rows if row_authorizer(row)]
        respondent_names: dict[UUID, str] = {}
        if include_respondent_identity and all_rows:
            respondent_ids = {row.submission.respondent_person_id for row in all_rows}
            respondent_names = {
                person.id: person.full_name
                for person in self.db.scalars(
                    select(Person).where(Person.id.in_(respondent_ids))
                )
            }
        total = len(all_rows)
        rows = all_rows[(page - 1) * page_size : page * page_size]
        items: list[AnonymousSurveyItem] = []
        for ordinal, row in enumerate(
            rows, start=(page - 1) * page_size + 1
        ):
            cycle, _submission, version, rating, snapshot = (
                row.cycle,
                row.submission,
                row.version,
                row.rating,
                row.snapshot,
            )
            items.append(
                AnonymousSurveyItem(
                    cycle_id=cycle.id if include_respondent_identity else None,
                    enrollment_id=(
                        cycle.enrollment_id if include_respondent_identity else None
                    ),
                    cohort_id=cycle.cohort_id,
                    version_id=version.id if include_respondent_identity else None,
                    version=version.version,
                    ordinal=ordinal,
                    staff_snapshot_id=snapshot.id if include_staff_snapshot_id else None,
                    staff_person_id=snapshot.staff_person_id if include_staff_identity else None,
                    staff_name=snapshot.staff_name_snapshot if include_staff_identity else None,
                    role_key=snapshot.role_key_snapshot if include_staff_identity else None,
                    role_name=snapshot.role_name_snapshot if include_staff_identity else None,
                    role_category=snapshot.role_category if include_staff_identity else None,
                    score=rating.score,
                    reason=rating.reason,
                    suggestion=rating.suggestion,
                    respondent_label=f"匿名评价 #{ordinal}",
                    respondent_person_id=(
                        row.submission.respondent_person_id
                        if include_respondent_identity
                        else None
                    ),
                    respondent_name=(
                        respondent_names.get(row.submission.respondent_person_id)
                        if include_respondent_identity
                        else None
                    ),
                    respondent_kind=(
                        row.submission.respondent_kind
                        if include_respondent_identity
                        else None
                    ),
                    course_training_score=(
                        version.course_training_score
                        if include_respondent_identity
                        else None
                    ),
                    course_training_reason=(
                        version.course_training_reason
                        if include_respondent_identity
                        else None
                    ),
                    life_service_score=(
                        version.life_service_score
                        if include_respondent_identity
                        else None
                    ),
                    life_service_reason=(
                        version.life_service_reason
                        if include_respondent_identity
                        else None
                    ),
                    service_management_score=(
                        version.service_management_score
                        if include_respondent_identity
                        else None
                    ),
                    service_management_reason=(
                        version.service_management_reason
                        if include_respondent_identity
                        else None
                    ),
                    overall_suggestion=(
                        version.overall_suggestion
                        if include_respondent_identity
                        else None
                    ),
                    favorite_cuisines=(
                        list(version.favorite_cuisines)
                        if include_respondent_identity
                        else None
                    ),
                    favorite_dishes=(
                        list(version.favorite_dishes)
                        if include_respondent_identity
                        else None
                    ),
                )
            )
        return AnonymousSurveyPage(
            items=tuple(items), total=total, page=page, page_size=page_size
        )

    @staticmethod
    def staff_item_from_anonymous(
        *, cycle_id: UUID, cohort_id: UUID, version: int, ordinal: int,
        role_category: str, score: int, reason: str | None,
        suggestion: str | None, course_training_score: int | None = None,
        course_training_reason: str | None = None,
        life_service_score: int | None = None,
        life_service_reason: str | None = None,
        service_management_score: int | None = None,
        service_management_reason: str | None = None,
        overall_suggestion: str | None = None,
        role_categories: tuple[str, ...] | None = None,
        project_id: UUID | None = None,
        role_key: str | None = None,
        role_name: str | None = None,
    ) -> StaffSurveyItem:
        """Build a closed staff projection from server-owned values."""
        categories = set(role_categories or (role_category,))
        has_teaching = "teaching" in categories
        has_life_service = "life_service" in categories
        has_operations = "operations" in categories
        return StaffSurveyItem(
            cycle_id=cycle_id,
            cohort_id=cohort_id,
            version=version,
            ordinal=ordinal,
            role_category=role_category,
            score=score,
            reason=reason,
            suggestion=suggestion,
            course_training_score=(course_training_score if has_teaching else None),
            course_training_reason=(course_training_reason if has_teaching else None),
            life_service_score=(life_service_score if has_life_service else None),
            life_service_reason=(life_service_reason if has_life_service else None),
            service_management_score=(service_management_score if has_operations else None),
            service_management_reason=(service_management_reason if has_operations else None),
            team_suggestion=(overall_suggestion if has_teaching else None),
            project_id=project_id,
            role_key=role_key,
            role_name=role_name,
        )

    def list_for_staff(
        self,
        *,
        staff_person_id: UUID,
        cohort_id: UUID | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> StaffSurveyPage:
        self._validate_pagination(page, page_size)
        all_rows = self._feedback_rows(
            cohort_id=cohort_id,
            staff_person_id=staff_person_id,
        )
        total = len(all_rows)
        rows = all_rows[(page - 1) * page_size : page * page_size]
        items: list[StaffSurveyItem] = []
        for ordinal, row in enumerate(
            rows, start=(page - 1) * page_size + 1
        ):
            cycle, version, rating, snapshot = (
                row.cycle,
                row.version,
                row.rating,
                row.snapshot,
            )
            role_categories = self._role_categories(snapshot)
            items.append(
                self.staff_item_from_anonymous(
                    cycle_id=cycle.id,
                    cohort_id=cycle.cohort_id,
                    version=version.version,
                    ordinal=ordinal,
                    role_category=snapshot.role_category,
                    score=rating.score,
                    reason=rating.reason,
                    suggestion=rating.suggestion,
                    course_training_score=version.course_training_score,
                    course_training_reason=version.course_training_reason,
                    life_service_score=version.life_service_score,
                    life_service_reason=version.life_service_reason,
                    service_management_score=version.service_management_score,
                    service_management_reason=version.service_management_reason,
                    overall_suggestion=version.overall_suggestion,
                    role_categories=role_categories,
                    project_id=row.project_id,
                    role_key=snapshot.role_key_snapshot,
                    role_name=snapshot.role_name_snapshot,
                )
            )
        return StaffSurveyPage(tuple(items), total, page, page_size)

    def list_for_admin(
        self,
        *,
        cohort_id: UUID | None = None,
        project_id: UUID | None = None,
        staff_person_id: UUID | None = None,
        role_key: str | None = None,
        page: int = 1,
        page_size: int = 50,
        include_staff_identity: bool = False,
        include_respondent_identity: bool = False,
        include_staff_snapshot_id: bool = False,
        row_authorizer: FeedbackRowAuthorizer | None = None,
    ) -> AdminSurveyPage:
        result = self.list_anonymous(
            page=page,
            page_size=page_size,
            cohort_id=cohort_id,
            project_id=project_id,
            staff_person_id=staff_person_id,
            role_key=role_key,
            include_staff_identity=include_staff_identity,
            include_respondent_identity=include_respondent_identity,
            include_staff_snapshot_id=include_staff_snapshot_id,
            row_authorizer=row_authorizer,
        )
        return AdminSurveyPage(items=result.items, total=result.total, page=result.page, page_size=result.page_size)

    def print_projection(
        self,
        *,
        identity: str = "hidden",
        can_print: bool = True,
        can_read_identity: bool = False,
        cohort_id: UUID | None = None,
        project_id: UUID | None = None,
        staff_person_id: UUID | None = None,
        role_key: str | None = None,
        row_authorizer: FeedbackRowAuthorizer | None = None,
    ) -> PrintProjection:
        if identity not in {"hidden", "real_name"}:
            raise ValueError("identity must be hidden or real_name")
        # A route may provide a row authorizer for scoped permissions.  In
        # that case a missing global grant is not a reason to reject before
        # the authorized rows are projected; the caller still passes the
        # live permission result and the callback filters every row.  Without
        # a callback, the service keeps its standalone permission gate.
        if (
            not can_print
            and row_authorizer is None
        ) or (
            identity == "real_name"
            and not can_read_identity
            and row_authorizer is None
        ):
            raise PermissionError("survey print permission denied")
        rows = self._feedback_rows(
            cohort_id=cohort_id,
            project_id=project_id,
            staff_person_id=staff_person_id,
            role_key=role_key,
        )
        if row_authorizer is not None:
            rows = [row for row in rows if row_authorizer(row)]
        respondent_names: dict[UUID, str] = {}
        if identity == "real_name" and rows:
            respondent_ids = {row.submission.respondent_person_id for row in rows}
            respondent_names = {
                person.id: person.full_name
                for person in self.db.scalars(
                    select(Person).where(Person.id.in_(respondent_ids))
                )
            }
        grouped: dict[tuple[UUID, UUID | None, UUID], list[_FeedbackRow]] = {}
        for row in rows:
            key = (
                row.cycle.cohort_id,
                row.project_id,
                row.snapshot.staff_person_id,
            )
            grouped.setdefault(key, []).append(row)
        items: list[PrintProjectionItem] = []
        for (_cohort, _project, _staff), group in sorted(
            grouped.items(),
            key=lambda item: (
                str(item[0][0]),
                str(item[0][1]),
                str(item[0][2]),
            ),
        ):
            snapshot = group[0].snapshot
            details = tuple(
                PrintDetail(
                    score=row.rating.score,
                    reason=row.rating.reason,
                    suggestion=row.rating.suggestion,
                    ordinal=index,
                    respondent_person_id=(
                        row.submission.respondent_person_id
                        if identity == "real_name"
                        else None
                    ),
                    respondent_name=(
                        respondent_names.get(row.submission.respondent_person_id)
                        if identity == "real_name"
                        else None
                    ),
                    respondent_kind=(
                        row.submission.respondent_kind
                        if identity == "real_name"
                        else None
                    ),
                )
                for index, row in enumerate(group, start=1)
            )
            roles_by_key: dict[tuple[str, str, str], dict[str, str]] = {
                (str(role["role_category"]), str(role["role_key"]), str(role["role_name"])): {
                    "role_category": str(role["role_category"]),
                    "role_key": str(role["role_key"]),
                    "role_name": str(role["role_name"]),
                }
                for row in group
                for role in (row.snapshot.roles_snapshot or [])
            }
            intervals_by_key: dict[str, dict[str, object]] = {}
            for row in group:
                for interval in row.snapshot.service_intervals or []:
                    interval_key = repr(sorted(interval.items()))
                    intervals_by_key[interval_key] = interval
            role_categories = {
                category
                for row in group
                for category in self._role_categories(row.snapshot)
            }
            team_suggestions = tuple(
                dict.fromkeys(
                    row.version.overall_suggestion
                    for row in group
                    if "teaching" in role_categories
                    and row.version.overall_suggestion
                )
            )
            items.append(
                PrintProjectionItem(
                    cohort_id=_cohort,
                    project_id=group[0].project_id,
                    staff_person_id=snapshot.staff_person_id,
                    staff_name=snapshot.staff_name_snapshot,
                    role_key=snapshot.role_key_snapshot,
                    role_name=snapshot.role_name_snapshot,
                    role_category=snapshot.role_category,
                    service_intervals=tuple(intervals_by_key.values()),
                    average_score=round(sum(item.score for item in details) / len(details), 2),
                    item_count=len(details),
                    details=details,
                    team_suggestions=team_suggestions,
                    roles=tuple(roles_by_key.values()),
                )
            )
        return PrintProjection(identity=identity, items=tuple(items))
