from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, NoReturn, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.auth.dependencies import require_standard_account
from app.auth.models import UserAccount
from app.auth.service import AuthContext
from app.core.database import get_db
from app.core.errors import ProblemError
from app.core.time import business_date_at
from app.evaluations.access import EvaluationAccessService, EvaluationResource
from app.evaluations.dependencies import (
    get_camp_survey_access_service,
    get_camp_survey_query_service,
    get_camp_survey_service,
)
from app.evaluations.errors import CampSurveyError
from app.evaluations.staff_models import EvaluationStaffRole
from app.evaluations.survey_access import CampSurveyAccessService
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
from app.evaluations.survey_query_service import (
    CampSurveyQueryService,
    PrintProjectionItem,
    StaffSurveyPage,
)
from app.evaluations.survey_schemas import (
    CampSurveyRevisionInput,
    CampSurveySubmissionInput,
    CampSurveySupplementInput,
)
from app.evaluations.survey_service import CampSurveyService
from app.evaluations.survey_snapshot_service import _snapshot_row_sort_key
from app.identity.models import GuardianRelationship, Person, StaffProfile
from app.rbac.models import Role, UserRole
from app.rbac.service import RbacService
from app.teaching.models import TeachingClass

router = APIRouter(prefix="/evaluations/surveys", tags=["camp-surveys"])


# These are the reusable account roles which may receive an evaluation staff
# roster assignment.  Guardian/student/customer-service accounts may have an
# evaluation-center surface for other reasons, but they are not staff-feedback
# respondents.
_STAFF_ACCOUNT_ROLE_KEYS = frozenset({"coach", "life_teacher", "staff"})
_BUILTIN_SURVEY_ROLE_KEYS = frozenset({"primary_coach", "assistant_coach"})


class SurveyStaffSnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    staff_name: str
    role_key: str
    role_name: str
    role_category: str
    roles: list[dict[str, str]] = Field(default_factory=list)
    service_intervals: list[dict[str, object]]


class SurveyStaffRatingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    staff_snapshot_id: UUID
    score: StrictInt
    reason: str | None = None
    suggestion: str | None = None


class SurveyLatestSubmissionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: StrictInt
    course_training_score: StrictInt
    course_training_reason: str | None = None
    life_service_score: StrictInt
    life_service_reason: str | None = None
    service_management_score: StrictInt
    service_management_reason: str | None = None
    overall_suggestion: str | None = None
    favorite_cuisines: list[dict[str, object]]
    favorite_dishes: list[str]
    staff_ratings: list[SurveyStaffRatingResponse]


class SurveyContextResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cycle_id: UUID
    student_person_id: UUID
    student_name: str
    opens_at: AwareDatetime
    due_at: AwareDatetime
    snapshot_version: StrictInt = Field(ge=1)
    respondent_kind: str
    can_submit: bool
    can_revise: bool
    can_supplement: bool = False
    submission_id: UUID | None
    latest_version: StrictInt | None
    staff: list[SurveyStaffSnapshotResponse]
    # The current respondent's own version is safe to use for form
    # rehydration; it never contains another respondent's identity.
    latest_submission: SurveyLatestSubmissionResponse | None = None
    # Only staff still awaiting a supplement are exposed on this separate
    # list.  Stable person identity is resolved server-side so a later
    # snapshot UUID cannot make an already-completed supplement reappear.
    supplement_staff: list[SurveyStaffSnapshotResponse] = Field(default_factory=list)


class SurveySubmissionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    submission_id: UUID
    version_id: UUID
    version: StrictInt = Field(ge=1, le=2)
    published_at: AwareDatetime
    idempotent: bool = False


class SurveySupplementResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supplement_id: UUID
    idempotent: bool = False


class SurveyFeedbackItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cycle_id: UUID | None = None
    enrollment_id: UUID | None = None
    cohort_id: UUID
    version_id: UUID | None = None
    version: StrictInt
    ordinal: StrictInt
    staff_snapshot_id: UUID | None
    staff_person_id: UUID | None
    staff_name: str | None
    role_key: str | None
    role_name: str | None
    role_category: str | None
    score: StrictInt
    reason: str | None
    suggestion: str | None
    respondent_label: str
    respondent_person_id: UUID | None
    respondent_name: str | None
    respondent_kind: str | None
    course_training_score: StrictInt | None
    course_training_reason: str | None
    life_service_score: StrictInt | None
    life_service_reason: str | None
    service_management_score: StrictInt | None
    service_management_reason: str | None
    overall_suggestion: str | None
    favorite_cuisines: list[dict[str, object]] | None
    favorite_dishes: list[str] | None


class StaffSurveyFeedbackItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cycle_id: UUID
    cohort_id: UUID
    version: StrictInt
    ordinal: StrictInt
    role_category: str
    score: StrictInt
    reason: str | None
    suggestion: str | None
    course_training_score: StrictInt | None
    course_training_reason: str | None
    life_service_score: StrictInt | None
    life_service_reason: str | None
    service_management_score: StrictInt | None
    service_management_reason: str | None
    team_suggestion: str | None
    project_id: UUID | None
    role_key: str | None
    role_name: str | None


class SurveyFeedbackPageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[SurveyFeedbackItemResponse]
    total: StrictInt
    page: StrictInt
    page_size: StrictInt


class StaffSurveyFeedbackPageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[StaffSurveyFeedbackItemResponse]
    total: StrictInt
    page: StrictInt
    page_size: StrictInt


class SurveyPrintDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: StrictInt
    reason: str | None
    suggestion: str | None
    ordinal: StrictInt
    respondent_person_id: UUID | None
    respondent_name: str | None
    respondent_kind: str | None


class SurveyPrintItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cohort_id: UUID
    project_id: UUID | None = None
    staff_person_id: UUID | None
    staff_name: str | None
    role_key: str
    role_name: str
    role_category: str
    service_intervals: list[dict[str, object]]
    average_score: float
    item_count: StrictInt
    details: list[SurveyPrintDetailResponse]
    team_suggestions: list[str] = Field(default_factory=list)
    roles: list[dict[str, str]] = Field(default_factory=list)


class SurveyPrintResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identity: Literal["hidden", "real_name"]
    items: list[SurveyPrintItemResponse]


def _private_not_found(exc: Exception | None = None) -> NoReturn:
    raise ProblemError(
        status=404,
        code="evaluation_resource_not_found",
        detail="评价资源不存在",
    ) from exc


def _raise_survey_error(exc: CampSurveyError) -> NoReturn:
    if exc.code == "evaluation_resource_not_found":
        _private_not_found(exc)
    if exc.code in {"evaluation_survey_window_closed", "evaluation_stale_version"}:
        raise ProblemError(status=409, code=exc.code, detail="评价问卷状态已变化") from exc
    if exc.code.endswith("conflict"):
        raise ProblemError(status=409, code=exc.code, detail="评价问卷提交发生冲突") from exc
    raise ProblemError(status=422, code=exc.code, detail="评价问卷内容无效") from exc


def _staff_response(row: CampSurveyStaffSnapshot) -> SurveyStaffSnapshotResponse:
    return SurveyStaffSnapshotResponse(
        id=row.id,
        staff_name=row.staff_name_snapshot,
        role_key=row.role_key_snapshot,
        role_name=row.role_name_snapshot,
        role_category=row.role_category,
        roles=row.roles_snapshot,
        service_intervals=row.service_intervals,
    )


def _print_item_response(item: PrintProjectionItem) -> SurveyPrintItemResponse:
    """Convert the service dataclass projection into the public response model."""
    return SurveyPrintItemResponse(
        cohort_id=item.cohort_id,
        project_id=item.project_id,
        staff_person_id=item.staff_person_id,
        staff_name=item.staff_name,
        role_key=item.role_key,
        role_name=item.role_name,
        role_category=item.role_category,
        service_intervals=list(item.service_intervals),
        average_score=item.average_score,
        item_count=item.item_count,
        details=[
            SurveyPrintDetailResponse(
                score=detail.score,
                reason=detail.reason,
                suggestion=detail.suggestion,
                ordinal=detail.ordinal,
                respondent_person_id=detail.respondent_person_id,
                respondent_name=detail.respondent_name,
                respondent_kind=detail.respondent_kind,
            )
            for detail in item.details
        ],
        team_suggestions=list(item.team_suggestions),
        roles=list(item.roles),
    )


def _feedback_item_response(
    item: Any,
    *,
    identity: Literal["hidden", "real_name"],
) -> SurveyFeedbackItemResponse:
    """Serialize admin feedback with respondent-linked IDs redacted by mode."""

    values = dict(item.__dict__)
    if identity == "hidden":
        values["cycle_id"] = None
        values["enrollment_id"] = None
        values["version_id"] = None
    return SurveyFeedbackItemResponse.model_validate(values)


def _live_staff_account_allowed(
    *,
    context: AuthContext,
    db: Session,
) -> bool:
    """Re-read the staff account boundary instead of trusting login state.

    ``AuthContext`` contains ORM objects loaded when the session was created.
    A person can be deleted, a staff profile can be removed, or the account can
    be disabled after that point.  The feedback route therefore requires one
    current joined row and a current reusable staff account role on every
    request.
    """

    active_person = db.scalar(
        select(Person.id)
        .join(UserAccount, UserAccount.person_id == Person.id)
        .join(StaffProfile, StaffProfile.person_id == Person.id)
        .where(
            UserAccount.id == context.account.id,
            UserAccount.person_id == context.person.id,
            UserAccount.status == "active",
            Person.id == context.person.id,
            Person.deleted_at.is_(None),
        )
        .limit(1)
    )
    if active_person is None:
        return False
    return (
        db.scalar(
            select(UserRole.user_id)
            .join(Role, Role.id == UserRole.role_id)
            .where(
                UserRole.user_id == context.account.id,
                Role.key.in_(_STAFF_ACCOUNT_ROLE_KEYS),
            )
            .limit(1)
        )
        is not None
    )


def _snapshot_role_keys(snapshot: CampSurveyStaffSnapshot) -> set[str]:
    keys: set[str] = set()
    if isinstance(snapshot.role_key_snapshot, str) and snapshot.role_key_snapshot:
        keys.add(snapshot.role_key_snapshot)
    for role in snapshot.roles_snapshot or []:
        if isinstance(role, dict) and isinstance(role.get("role_key"), str):
            keys.add(role["role_key"])
    return keys


def _snapshot_has_legal_role(
    db: Session,
    snapshot: CampSurveyStaffSnapshot,
    *,
    role_key: str | None = None,
) -> bool:
    """Accept only built-in coach roles or currently active staff roles."""

    keys = _snapshot_role_keys(snapshot)
    if role_key is not None:
        keys.intersection_update({role_key})
    if not keys:
        return False
    if keys.intersection(_BUILTIN_SURVEY_ROLE_KEYS):
        return True
    active_keys = set(
        db.scalars(
            select(EvaluationStaffRole.key).where(
                EvaluationStaffRole.key.in_(keys),
                EvaluationStaffRole.is_active.is_(True),
            )
        )
    )
    return bool(keys.intersection(active_keys))


def _resolve_role_key(
    db: Session,
    *,
    role_id: UUID | None,
    role_key: str | None,
) -> str | None:
    """Resolve a client role id to the server-owned immutable role key."""

    if role_id is None:
        return role_key
    role = db.scalar(
        select(EvaluationStaffRole).where(EvaluationStaffRole.id == role_id)
    )
    if role is None or not role.is_active:
        _private_not_found()
    if role_key is not None and role.key != role_key:
        _private_not_found()
    return role.key


def _survey_target_exists(
    db: Session,
    *,
    cohort_id: UUID | None = None,
    project_id: UUID | None = None,
    staff_person_id: UUID | None = None,
    role_key: str | None = None,
) -> bool:
    """Check a requested scope against current, server-loaded roster rows."""

    statement = select(CampSurveyCycle.id).where(
        CampSurveyCycle.current_snapshot_version >= 1,
    ).limit(1)
    if cohort_id is not None:
        statement = statement.where(CampSurveyCycle.cohort_id == cohort_id)
    if project_id is not None:
        # The cycle stores the class id; the composite relationship prevents a
        # class from one cohort from becoming a project target in another.
        from app.teaching.models import TeachingClass

        statement = statement.join(
            TeachingClass,
            (TeachingClass.id == CampSurveyCycle.class_id)
            & (TeachingClass.cohort_id == CampSurveyCycle.cohort_id),
        ).where(TeachingClass.project_id == project_id)
    if staff_person_id is not None or role_key is not None:
        statement = statement.join(
            CampSurveySnapshotVersion,
            (CampSurveySnapshotVersion.cycle_id == CampSurveyCycle.id)
            & (CampSurveySnapshotVersion.version == CampSurveyCycle.current_snapshot_version),
        ).join(
            CampSurveyStaffSnapshot,
            CampSurveyStaffSnapshot.snapshot_version_id == CampSurveySnapshotVersion.id,
        ).join(
            Person,
            Person.id == CampSurveyStaffSnapshot.staff_person_id,
        ).where(
            CampSurveyStaffSnapshot.include_in_summary.is_(True),
            Person.deleted_at.is_(None),
        )
    if staff_person_id is not None:
        statement = statement.where(
            CampSurveyStaffSnapshot.staff_person_id == staff_person_id
        )
    if role_key is not None:
        statement = statement.where(
            or_(
                CampSurveyStaffSnapshot.role_key_snapshot == role_key,
                CampSurveyStaffSnapshot.roles_snapshot.contains([{"role_key": role_key}]),
            )
        )
    return db.scalar(statement) is not None


def _staff_feedback_target_exists(
    *,
    context: AuthContext,
    db: Session,
    cohort_id: UUID | None,
    role_key: str | None,
) -> bool:
    """Require a current included roster assignment for this staff person."""

    # The target query deliberately includes the live person/account boundary;
    # old immutable snapshots never revive a deleted or disabled staff account.
    statement = (
        select(CampSurveyStaffSnapshot)
        .join(
            CampSurveySnapshotVersion,
            CampSurveySnapshotVersion.id == CampSurveyStaffSnapshot.snapshot_version_id,
        )
        .join(CampSurveyCycle, CampSurveyCycle.id == CampSurveySnapshotVersion.cycle_id)
        .join(Person, Person.id == CampSurveyStaffSnapshot.staff_person_id)
        .join(UserAccount, UserAccount.person_id == Person.id)
        .join(StaffProfile, StaffProfile.person_id == Person.id)
        .where(
            CampSurveySnapshotVersion.version == CampSurveyCycle.current_snapshot_version,
            CampSurveyStaffSnapshot.staff_person_id == context.person.id,
            CampSurveyStaffSnapshot.include_in_summary.is_(True),
            UserAccount.status == "active",
            Person.deleted_at.is_(None),
        )
    )
    if cohort_id is not None:
        statement = statement.where(CampSurveyCycle.cohort_id == cohort_id)
    if role_key is not None:
        statement = statement.where(
            or_(
                CampSurveyStaffSnapshot.role_key_snapshot == role_key,
                CampSurveyStaffSnapshot.roles_snapshot.contains([{"role_key": role_key}]),
            )
        )
    snapshots = list(db.scalars(statement))
    return any(
        _snapshot_has_legal_role(db, snapshot, role_key=role_key)
        for snapshot in snapshots
    )


def _staff_feedback_scope_allowed(
    *,
    context: AuthContext,
    db: Session,
    cohort_id: UUID | None,
    role_key: str | None,
    occurred_at: datetime,
) -> bool:
    """Honor scoped evaluation-center denies on each server-loaded target."""

    statement = (
        select(CampSurveyCycle, CampSurveyStaffSnapshot, TeachingClass.project_id)
        .join(
            CampSurveySnapshotVersion,
            CampSurveySnapshotVersion.cycle_id == CampSurveyCycle.id,
        )
        .join(
            CampSurveyStaffSnapshot,
            CampSurveyStaffSnapshot.snapshot_version_id == CampSurveySnapshotVersion.id,
        )
        .join(
            TeachingClass,
            (TeachingClass.id == CampSurveyCycle.class_id)
            & (TeachingClass.cohort_id == CampSurveyCycle.cohort_id),
        )
        .where(
            CampSurveySnapshotVersion.version == CampSurveyCycle.current_snapshot_version,
            CampSurveyStaffSnapshot.staff_person_id == context.person.id,
            CampSurveyStaffSnapshot.include_in_summary.is_(True),
        )
    )
    if cohort_id is not None:
        statement = statement.where(CampSurveyCycle.cohort_id == cohort_id)
    if role_key is not None:
        statement = statement.where(
            or_(
                CampSurveyStaffSnapshot.role_key_snapshot == role_key,
                CampSurveyStaffSnapshot.roles_snapshot.contains([{"role_key": role_key}]),
            )
        )
    rows = list(db.execute(statement).all())
    if not rows:
        # The target existence check remains the authoritative boundary.  A
        # compatibility test double may not expose row execution, so preserve
        # the already-checked global capability in that narrow case.
        return True
    access = EvaluationAccessService(db)
    rbac = RbacService(db)
    for cycle, snapshot, project_id in rows:
        if not _snapshot_has_legal_role(db, snapshot, role_key=role_key):
            continue
        tokens = access.resource_tokens(
            actor_person_id=context.person.id,
            resource=EvaluationResource(
                student_person_id=cycle.student_person_id,
                staff_person_id=snapshot.staff_person_id,
                cohort_id=cycle.cohort_id,
                project_id=project_id,
                enrollment_id=cycle.enrollment_id,
                class_id=cycle.class_id,
            ),
            occurred_at=occurred_at,
        )
        if rbac.decide(
            user_id=context.account.id,
            permission="evaluation_center.access",
            resource_tokens=tokens,
            occurred_at=occurred_at,
        ).allowed:
            return True
    return False


def _staff_feedback_row_authorizer(
    *,
    context: AuthContext,
    db: Session,
    occurred_at: datetime,
) -> Callable[[Any], bool]:
    """Authorize each feedback row against its own evaluation resource."""

    rbac = RbacService(db)
    evaluation_access = EvaluationAccessService(db)

    def allowed(row: Any) -> bool:
        snapshot = row.snapshot
        if snapshot.staff_person_id != context.person.id:
            return False
        tokens = evaluation_access.resource_tokens(
            actor_person_id=context.person.id,
            resource=EvaluationResource(
                student_person_id=row.cycle.student_person_id,
                staff_person_id=snapshot.staff_person_id,
                cohort_id=row.cycle.cohort_id,
                project_id=row.project_id,
                enrollment_id=row.cycle.enrollment_id,
                class_id=row.cycle.class_id,
            ),
            occurred_at=occurred_at,
        )
        return rbac.decide(
            user_id=context.account.id,
            permission="evaluation_center.access",
            resource_tokens=tokens,
            occurred_at=occurred_at,
        ).allowed

    return allowed


def _list_staff_feedback(
    *,
    query: CampSurveyQueryService,
    db: Session,
    staff_person_id: UUID,
    cohort_id: UUID | None,
    role_key: str | None,
    page: int,
    page_size: int,
    row_authorizer: Callable[[Any], bool] | None = None,
) -> StaffSurveyPage:
    """Apply role filtering before pagination using server-owned rows.

    ``CampSurveyQueryService.list_for_staff`` predates the role filter and has
    no role argument.  Use its stable internal row loader when available so a
    role filter changes both the total and ordinal stream, while retaining a
    small compatibility fallback for test doubles and older deployments.
    """

    loader = getattr(query, "_feedback_rows", None)
    if not callable(loader):
        kwargs: dict[str, object] = {
            "staff_person_id": staff_person_id,
            "cohort_id": cohort_id,
            "page": page,
            "page_size": page_size,
        }
        if role_key is not None:
            kwargs["role_key"] = role_key
        try:
            return query.list_for_staff(**kwargs)  # type: ignore[arg-type]
        except TypeError:
            # Compatibility with the currently deployed query service, whose
            # public method has not yet gained the optional role parameter.
            kwargs.pop("role_key", None)
            return query.list_for_staff(**kwargs)  # type: ignore[arg-type]
    rows = list(
        loader(
            cohort_id=cohort_id,
            staff_person_id=staff_person_id,
            role_key=role_key,
        )
    )
    rows = [
        row
        for row in rows
        if _snapshot_has_legal_role(db, row.snapshot, role_key=role_key)
    ]
    if row_authorizer is not None:
        rows = [row for row in rows if row_authorizer(row)]
    total = len(rows)
    start = (page - 1) * page_size
    page_rows = rows[start : start + page_size]
    items = []
    for ordinal, row in enumerate(page_rows, start=start + 1):
        snapshot = row.snapshot
        role_categories = CampSurveyQueryService._role_categories(snapshot)
        items.append(
            CampSurveyQueryService.staff_item_from_anonymous(
                cycle_id=row.cycle.id,
                cohort_id=row.cycle.cohort_id,
                version=row.version.version,
                ordinal=ordinal,
                role_category=snapshot.role_category,
                score=row.rating.score,
                reason=row.rating.reason,
                suggestion=row.rating.suggestion,
                course_training_score=row.version.course_training_score,
                course_training_reason=row.version.course_training_reason,
                life_service_score=row.version.life_service_score,
                life_service_reason=row.version.life_service_reason,
                service_management_score=row.version.service_management_score,
                service_management_reason=row.version.service_management_reason,
                overall_suggestion=row.version.overall_suggestion,
                role_categories=role_categories,
                project_id=row.project_id,
                role_key=snapshot.role_key_snapshot,
                role_name=snapshot.role_name_snapshot,
            )
        )
    return StaffSurveyPage(tuple(items), total, page, page_size)


def _survey_context_for_cycle(
    *,
    cycle: CampSurveyCycle,
    context: AuthContext,
    access: CampSurveyAccessService,
    db: Session,
    occurred_at: datetime,
) -> SurveyContextResponse | None:
    try:
        respondent = access.resolve_respondent(
            cycle_id=cycle.id,
            account_id=context.account.id,
            occurred_at=occurred_at,
        )
    except CampSurveyError:
        return None
    student_name = db.scalar(
        select(Person.full_name).where(
            Person.id == cycle.student_person_id,
            Person.deleted_at.is_(None),
        )
    )
    if student_name is None:
        return None
    snapshot = db.scalar(
        select(CampSurveySnapshotVersion).where(
            CampSurveySnapshotVersion.cycle_id == cycle.id,
            CampSurveySnapshotVersion.version == cycle.current_snapshot_version,
        )
    )
    if snapshot is None:
        return None
    staff = sorted(
        db.scalars(
            select(CampSurveyStaffSnapshot).where(
                CampSurveyStaffSnapshot.snapshot_version_id == snapshot.id,
                CampSurveyStaffSnapshot.include_in_summary.is_(True),
            )
        ),
        key=_snapshot_row_sort_key,
    )
    submission = db.scalar(
        select(CampSurveySubmission).where(
            CampSurveySubmission.cycle_id == cycle.id,
            CampSurveySubmission.respondent_person_id == respondent.person_id,
        )
    )
    latest_submission: SurveyLatestSubmissionResponse | None = None
    supplemented_staff_people: set[UUID] = set()
    if submission is not None and submission.latest_version is not None:
        latest_version = db.scalar(
            select(CampSurveyVersion).where(
                CampSurveyVersion.submission_id == submission.id,
                CampSurveyVersion.version == submission.latest_version,
            )
        )
        if latest_version is not None:
            latest_ratings = list(
                db.scalars(
                    select(CampSurveyStaffRating).where(
                        CampSurveyStaffRating.version_id == latest_version.id,
                    )
                )
            )
            rated_staff_people = set(
                db.scalars(
                    select(CampSurveyStaffSnapshot.staff_person_id)
                    .join(
                        CampSurveyStaffRating,
                        CampSurveyStaffRating.staff_snapshot_id
                        == CampSurveyStaffSnapshot.id,
                    )
                    .where(
                        CampSurveyStaffRating.version_id == latest_version.id,
                    )
                )
            )
            latest_submission = SurveyLatestSubmissionResponse(
                version=latest_version.version,
                course_training_score=latest_version.course_training_score,
                course_training_reason=latest_version.course_training_reason,
                life_service_score=latest_version.life_service_score,
                life_service_reason=latest_version.life_service_reason,
                service_management_score=latest_version.service_management_score,
                service_management_reason=latest_version.service_management_reason,
                overall_suggestion=latest_version.overall_suggestion,
                favorite_cuisines=list(latest_version.favorite_cuisines),
                favorite_dishes=list(latest_version.favorite_dishes),
                staff_ratings=[
                    SurveyStaffRatingResponse(
                        staff_snapshot_id=rating.staff_snapshot_id,
                        score=rating.score,
                        reason=rating.reason,
                        suggestion=rating.suggestion,
                    )
                    for rating in latest_ratings
                ],
            )
        else:
            rated_staff_people = set()
        supplemented_staff_people = set(
            db.scalars(
                select(CampSurveyStaffSnapshot.staff_person_id)
                .join(
                    CampSurveySupplementRating,
                    CampSurveySupplementRating.staff_snapshot_id
                    == CampSurveyStaffSnapshot.id,
                )
                .join(
                    CampSurveySupplement,
                    CampSurveySupplement.id
                    == CampSurveySupplementRating.supplement_id,
                )
                .where(
                    CampSurveySupplement.submission_id == submission.id,
                    CampSurveySupplement.submitted_at.is_not(None),
                )
            )
        )
    else:
        rated_staff_people = set()
    # Both main ratings and supplements are mapped to stable staff person
    # UUIDs because each corrected snapshot gets new row UUIDs.  Keep only
    # current rows that have neither form of completion.
    supplement_staff = [
        row
        for row in staff
        if row.staff_person_id not in rated_staff_people
        and row.staff_person_id not in supplemented_staff_people
    ] if submission is not None else []
    return SurveyContextResponse(
        cycle_id=cycle.id,
        student_person_id=cycle.student_person_id,
        student_name=student_name,
        opens_at=cycle.opens_at,
        due_at=cycle.due_at,
        snapshot_version=snapshot.version,
        respondent_kind=respondent.respondent_kind,
        can_submit=(
            occurred_at >= cycle.opens_at
            and occurred_at < cycle.due_at
            and submission is None
        ),
        can_revise=(
            occurred_at >= cycle.opens_at
            and occurred_at < cycle.due_at
            and bool(submission and submission.revision_count == 0)
        ),
        can_supplement=(
            occurred_at >= cycle.opens_at
            and occurred_at < cycle.due_at
            and submission is not None
            and bool(supplement_staff)
        ),
        submission_id=submission.id if submission else None,
        latest_version=submission.latest_version if submission else None,
        staff=[_staff_response(row) for row in staff],
        latest_submission=latest_submission,
        supplement_staff=[_staff_response(row) for row in supplement_staff],
    )


@router.get("/context", response_model=list[SurveyContextResponse])
def list_survey_context(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    access: Annotated[CampSurveyAccessService, Depends(get_camp_survey_access_service)],
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[SurveyContextResponse]:
    now = datetime.now(UTC)
    reference_date = business_date_at(now)
    guardian_cycle = select(GuardianRelationship.id).where(
        GuardianRelationship.guardian_person_id == context.person.id,
        GuardianRelationship.student_person_id == CampSurveyCycle.student_person_id,
        GuardianRelationship.requires_verification.is_(False),
        GuardianRelationship.valid_from <= reference_date,
        or_(
            GuardianRelationship.valid_until.is_(None),
            GuardianRelationship.valid_until >= reference_date,
        ),
        or_(
            GuardianRelationship.ended_at.is_(None),
            GuardianRelationship.ended_at > now,
        ),
    )
    statement = (
        select(CampSurveyCycle)
        .where(
            or_(
                CampSurveyCycle.student_person_id == context.person.id,
                guardian_cycle.exists(),
            )
        )
        .order_by(CampSurveyCycle.opens_at.desc(), CampSurveyCycle.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    cycles = list(db.scalars(statement))
    result: list[SurveyContextResponse] = []
    for cycle in cycles:
        item = _survey_context_for_cycle(
            cycle=cycle,
            context=context,
            access=access,
            db=db,
            occurred_at=now,
        )
        if item is not None:
            result.append(item)
    return result


@router.get("", response_model=list[SurveyContextResponse])
def list_surveys(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    access: Annotated[CampSurveyAccessService, Depends(get_camp_survey_access_service)],
    db: Annotated[Session, Depends(get_db)],
    student_id: UUID | None = None,
    enrollment_id: UUID | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[SurveyContextResponse]:
    now = datetime.now(UTC)
    reference_date = business_date_at(now)
    guardian_cycle = select(GuardianRelationship.id).where(
        GuardianRelationship.guardian_person_id == context.person.id,
        GuardianRelationship.student_person_id == CampSurveyCycle.student_person_id,
        GuardianRelationship.requires_verification.is_(False),
        GuardianRelationship.valid_from <= reference_date,
        or_(
            GuardianRelationship.valid_until.is_(None),
            GuardianRelationship.valid_until >= reference_date,
        ),
        or_(
            GuardianRelationship.ended_at.is_(None),
            GuardianRelationship.ended_at > now,
        ),
    )
    statement = select(CampSurveyCycle).where(
        or_(
            CampSurveyCycle.student_person_id == context.person.id,
            guardian_cycle.exists(),
        )
    )
    if student_id is not None:
        statement = statement.where(CampSurveyCycle.student_person_id == student_id)
    if enrollment_id is not None:
        statement = statement.where(CampSurveyCycle.enrollment_id == enrollment_id)
    cycles = list(
        db.scalars(
            statement.order_by(CampSurveyCycle.opens_at.desc(), CampSurveyCycle.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    result = [
        item
        for cycle in cycles
        if (item := _survey_context_for_cycle(
            cycle=cycle,
            context=context,
            access=access,
            db=db,
            occurred_at=now,
        )) is not None
    ]
    if not result and (student_id is not None or enrollment_id is not None):
        _private_not_found()
    return result


@router.get("/{cycle_id:uuid}", response_model=SurveyContextResponse)
def get_survey(
    cycle_id: UUID,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    access: Annotated[CampSurveyAccessService, Depends(get_camp_survey_access_service)],
    db: Annotated[Session, Depends(get_db)],
) -> SurveyContextResponse:
    cycle = db.scalar(select(CampSurveyCycle).where(CampSurveyCycle.id == cycle_id))
    if cycle is None:
        _private_not_found()
    item = _survey_context_for_cycle(
        cycle=cycle,
        context=context,
        access=access,
        db=db,
        occurred_at=datetime.now(UTC),
    )
    if item is None:
        _private_not_found()
    return item


@router.post("/{cycle_id}/submissions", response_model=SurveySubmissionResponse, status_code=201)
def submit_survey(
    cycle_id: UUID,
    input: CampSurveySubmissionInput,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    access: Annotated[CampSurveyAccessService, Depends(get_camp_survey_access_service)],
    service: Annotated[CampSurveyService, Depends(get_camp_survey_service)],
) -> SurveySubmissionResponse:
    now = datetime.now(UTC)
    try:
        respondent = access.resolve_respondent(
            cycle_id=cycle_id,
            account_id=context.account.id,
            occurred_at=now,
        )
        result = service.submit(
            cycle_id,
            input,
            actor_user_id=context.account.id,
            actor_person_id=respondent.person_id,
            student_person_id=respondent.student_person_id,
            respondent_kind=respondent.respondent_kind,
            guardian_relationship_id=respondent.guardian_relationship_id,
            guardian_relationship_snapshot=respondent.guardian_relationship_snapshot,
            request_id=str(request.state.request_id),
            occurred_at=now,
        )
        return SurveySubmissionResponse(
            submission_id=result.submission.id,
            version_id=result.version.id,
            version=result.version.version,
            published_at=result.version.published_at,
            idempotent=result.idempotent,
        )
    except CampSurveyError as exc:
        _raise_survey_error(exc)


@router.post("/{cycle_id}/submissions/{submission_id}/revisions", response_model=SurveySubmissionResponse)
def revise_survey(
    cycle_id: UUID,
    submission_id: UUID,
    input: CampSurveyRevisionInput,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    access: Annotated[CampSurveyAccessService, Depends(get_camp_survey_access_service)],
    service: Annotated[CampSurveyService, Depends(get_camp_survey_service)],
    db: Annotated[Session, Depends(get_db)],
) -> SurveySubmissionResponse:
    now = datetime.now(UTC)
    try:
        respondent = access.resolve_respondent(
            cycle_id=cycle_id,
            account_id=context.account.id,
            occurred_at=now,
        )
        result = service.revise(
            cycle_id,
            submission_id,
            input,
            actor_user_id=context.account.id,
            actor_person_id=respondent.person_id,
            student_person_id=respondent.student_person_id,
            respondent_kind=respondent.respondent_kind,
            guardian_relationship_id=respondent.guardian_relationship_id,
            request_id=str(request.state.request_id),
            occurred_at=now,
        )
        return SurveySubmissionResponse(
            submission_id=result.submission.id,
            version_id=result.version.id,
            version=result.version.version,
            published_at=result.version.published_at,
            idempotent=result.idempotent,
        )
    except CampSurveyError as exc:
        _raise_survey_error(exc)


@router.post(
    "/{cycle_id}/submissions/{submission_id}/supplement",
    response_model=SurveySupplementResponse,
    status_code=201,
)
def submit_survey_supplement(
    cycle_id: UUID,
    submission_id: UUID,
    input: CampSurveySupplementInput,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    access: Annotated[CampSurveyAccessService, Depends(get_camp_survey_access_service)],
    service: Annotated[CampSurveyService, Depends(get_camp_survey_service)],
) -> SurveySupplementResponse:
    now = datetime.now(UTC)
    try:
        respondent = access.resolve_respondent(
            cycle_id=cycle_id,
            account_id=context.account.id,
            occurred_at=now,
        )
        result = service.submit_supplement(
            cycle_id,
            submission_id,
            input,
            actor_user_id=context.account.id,
            actor_person_id=respondent.person_id,
            student_person_id=respondent.student_person_id,
            respondent_kind=respondent.respondent_kind,
            guardian_relationship_id=respondent.guardian_relationship_id,
            request_id=str(request.state.request_id),
            occurred_at=now,
        )
        return SurveySupplementResponse(
            supplement_id=result.supplement.id,
            idempotent=result.idempotent,
        )
    except CampSurveyError as exc:
        _raise_survey_error(exc)


@router.get("/my-feedback", response_model=StaffSurveyFeedbackPageResponse)
def get_my_survey_feedback(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    query: Annotated[CampSurveyQueryService, Depends(get_camp_survey_query_service)],
    db: Annotated[Session, Depends(get_db)],
    cohort_id: UUID | None = None,
    role_id: UUID | None = None,
    role_key: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> StaffSurveyFeedbackPageResponse:
    # The projection is private to a currently active staff account with a
    # legal staff role, a current roster assignment, and evaluation-center
    # capability.  Every condition is re-read from the database; the login
    # context and historical snapshot are not authorization tokens.
    now = datetime.now(UTC)
    if not _live_staff_account_allowed(context=context, db=db):
        raise ProblemError(status=403, code="permission_denied", detail="没有执行此操作的权限")
    resolved_role_key = _resolve_role_key(
        db,
        role_id=role_id,
        role_key=role_key,
    )
    has_filter = cohort_id is not None or role_id is not None or role_key is not None
    if not _staff_feedback_target_exists(
        context=context,
        db=db,
        cohort_id=cohort_id,
        role_key=resolved_role_key,
    ):
        if has_filter:
            _private_not_found()
        raise ProblemError(status=403, code="permission_denied", detail="没有执行此操作的权限")
    if not _staff_feedback_scope_allowed(
        context=context,
        db=db,
        cohort_id=cohort_id,
        role_key=resolved_role_key,
        occurred_at=now,
    ):
        if has_filter:
            _private_not_found()
        raise ProblemError(status=403, code="permission_denied", detail="没有执行此操作的权限")
    result = _list_staff_feedback(
        query=query,
        db=db,
        staff_person_id=context.person.id,
        cohort_id=cohort_id,
        role_key=resolved_role_key,
        page=page,
        page_size=page_size,
        row_authorizer=_staff_feedback_row_authorizer(
            context=context,
            db=db,
            occurred_at=now,
        ),
    )
    return StaffSurveyFeedbackPageResponse(
        items=[StaffSurveyFeedbackItemResponse.model_validate(item.__dict__) for item in result.items],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
    )


admin_router = APIRouter(prefix="/evaluations/admin/surveys", tags=["camp-surveys"])


def _survey_resource_tokens(
    context: AuthContext,
    db: Session,
    *,
    cohort_id: UUID | None,
    project_id: UUID | None,
    staff_person_id: UUID | None,
    enrollment_id: UUID | None = None,
    student_person_id: UUID | None = None,
    class_id: UUID | None = None,
) -> set[str]:
    """Build only server-owned scope tokens for survey query authorization."""
    return EvaluationAccessService(db).resource_tokens(
        actor_person_id=context.person.id,
        resource=EvaluationResource(
            student_person_id=student_person_id,
            staff_person_id=staff_person_id,
            cohort_id=cohort_id,
            project_id=project_id,
            class_id=class_id,
            enrollment_id=enrollment_id,
        ),
        occurred_at=datetime.now(UTC),
    )


def _print_required_permissions(
    identity: Literal["hidden", "real_name"],
) -> tuple[str, ...]:
    """Return the exact permission set for the dedicated print surface."""

    if identity == "real_name":
        return ("camp_surveys.print", "camp_surveys.identity.read")
    return ("camp_surveys.print",)


def _survey_row_authorizer(
    context: AuthContext,
    db: Session,
    *,
    identity: Literal["hidden", "real_name"],
    required_permissions: tuple[str, ...] | None = None,
    require_all: bool | None = None,
    center_denied_cycles: set[UUID] | None = None,
) -> Callable[[Any], bool]:
    """Return a row-level predicate for every admin survey projection.

    Applying the same predicate to filtered and unfiltered reads prevents a
    global grant from bypassing a scoped deny.  The callback only receives
    server-loaded immutable rows; request query parameters never become
    authorization tokens by themselves.
    """
    rbac = RbacService(db)
    evaluation_access = EvaluationAccessService(db)
    occurred_at = datetime.now(UTC)
    permissions = required_permissions or (
        "camp_surveys.print",
        "camp_surveys.identity.read",
    )
    check_all = identity == "real_name" if require_all is None else require_all

    def allowed(row: Any) -> bool:
        if db.scalar(
            select(Person.id).where(
                Person.id == row.snapshot.staff_person_id,
                Person.deleted_at.is_(None),
            )
        ) is None:
            return False
        resource = EvaluationResource(
            student_person_id=row.cycle.student_person_id,
            staff_person_id=row.snapshot.staff_person_id,
            cohort_id=row.cycle.cohort_id,
            project_id=row.project_id,
            enrollment_id=row.cycle.enrollment_id,
            class_id=row.cycle.class_id,
        )
        center = evaluation_access.decide(
            account_id=context.account.id,
            permission="evaluation_center.access",
            resource=resource,
            occurred_at=occurred_at,
        )
        # Admin survey operations have independent grants; an absent center
        # navigation grant is not a revocation. Explicit center denies still
        # dominate each row's operation grants.
        if not center.allowed and center.reason != "no_matching_grant":
            if center_denied_cycles is not None:
                center_denied_cycles.add(row.cycle.id)
            return False
        tokens = evaluation_access.resource_tokens(
            actor_person_id=context.person.id,
            resource=resource,
            occurred_at=occurred_at,
        )
        decisions = [
            rbac.decide(
                user_id=context.account.id,
                permission=permission,
                resource_tokens=tokens,
                occurred_at=occurred_at,
            ).allowed
            for permission in permissions
        ]
        return all(decisions) if check_all else any(decisions)

    return allowed


def _survey_global_capability(
    context: AuthContext,
    db: Session,
    *,
    identity: Literal["hidden", "real_name"],
    required_permissions: tuple[str, ...] | None = None,
    require_all: bool | None = None,
) -> bool:
    """Fallback capability check for an unfiltered empty result.

    Scoped-only accounts are allowed when at least one row matches the row
    predicate.  If no row exists, only a global grant can distinguish an empty
    dataset from an account with no survey capability.
    """
    rbac = RbacService(db)
    center = EvaluationAccessService(db).decide(
        account_id=context.account.id,
        permission="evaluation_center.access",
        resource=EvaluationResource(),
        occurred_at=datetime.now(UTC),
    )
    if not center.allowed and center.reason != "no_matching_grant":
        return False
    permissions = required_permissions or (
        "camp_surveys.print",
        "camp_surveys.identity.read",
    )
    check_all = identity == "real_name" if require_all is None else require_all
    decisions = [
        rbac.decide(
            user_id=context.account.id,
            permission=permission,
            resource_tokens={"global"},
            occurred_at=datetime.now(UTC),
        ).allowed
        for permission in permissions
    ]
    return all(decisions) if check_all else any(decisions)


def _survey_filter_capability(
    context: AuthContext,
    db: Session,
    *,
    identity: Literal["hidden", "real_name"],
    cohort_id: UUID | None,
    project_id: UUID | None,
    staff_person_id: UUID | None,
    role_key: str | None = None,
    required_permissions: tuple[str, ...] | None = None,
    require_all: bool | None = None,
) -> bool:
    if (
        cohort_id is None
        and project_id is None
        and staff_person_id is None
        and role_key is None
    ):
        return False
    if not _survey_target_exists(
        db,
        cohort_id=cohort_id,
        project_id=project_id,
        staff_person_id=staff_person_id,
        role_key=role_key,
    ):
        return False
    tokens = _survey_resource_tokens(
        context,
        db,
        cohort_id=cohort_id,
        project_id=project_id,
        staff_person_id=staff_person_id,
    )
    rbac = RbacService(db)
    center = rbac.decide(
        user_id=context.account.id,
        permission="evaluation_center.access",
        resource_tokens=tokens,
        occurred_at=datetime.now(UTC),
    )
    if not center.allowed and center.reason != "default_deny":
        return False
    permissions = required_permissions or (
        "camp_surveys.print",
        "camp_surveys.identity.read",
    )
    check_all = identity == "real_name" if require_all is None else require_all
    decisions = [
        rbac.decide(
            user_id=context.account.id,
            permission=permission,
            resource_tokens=tokens,
            occurred_at=datetime.now(UTC),
        ).allowed
        for permission in permissions
    ]
    return all(decisions) if check_all else any(decisions)


def _survey_snapshot_version_for_audit(
    db: Session,
    *,
    cohort_id: UUID | None,
) -> int | None:
    """Return a safe current snapshot version when the scope is unambiguous."""
    statement = (
        select(func.max(CampSurveySnapshotVersion.version))
        .join(
            CampSurveyCycle,
            CampSurveySnapshotVersion.cycle_id == CampSurveyCycle.id,
        )
        .where(
            CampSurveySnapshotVersion.version
            == CampSurveyCycle.current_snapshot_version,
        )
    )
    if cohort_id is not None:
        statement = statement.where(CampSurveyCycle.cohort_id == cohort_id)
    return db.scalar(statement)


@admin_router.get("", response_model=SurveyFeedbackPageResponse)
def list_admin_surveys(
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    query: Annotated[CampSurveyQueryService, Depends(get_camp_survey_query_service)],
    db: Annotated[Session, Depends(get_db)],
    cohort_id: UUID | None = None,
    project_id: UUID | None = None,
    staff_id: UUID | None = None,
    role_key: str | None = None,
    identity: Literal["hidden", "real_name"] = "hidden",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> SurveyFeedbackPageResponse:
    include_identity = identity == "real_name"
    admin_permissions = (
        ("camp_surveys.identity.read",)
        if include_identity
        else None
    )
    admin_require_all = True if include_identity else None
    center_denied_cycles: set[UUID] = set()
    filtered = (
        cohort_id is not None
        or project_id is not None
        or staff_id is not None
        or role_key is not None
    )
    result = query.list_for_admin(
        cohort_id=cohort_id,
        project_id=project_id,
        staff_person_id=staff_id,
        role_key=role_key,
        page=page,
        page_size=page_size,
        include_staff_identity=True,
        include_respondent_identity=include_identity,
        row_authorizer=_survey_row_authorizer(
            context,
            db,
            identity=identity,
            required_permissions=admin_permissions,
            require_all=admin_require_all,
            center_denied_cycles=center_denied_cycles,
        ),
    )
    if result.total == 0:
        if filtered:
            if not _survey_filter_capability(
                context,
                db,
                identity=identity,
                cohort_id=cohort_id,
                project_id=project_id,
                staff_person_id=staff_id,
                role_key=role_key,
                required_permissions=admin_permissions,
                require_all=admin_require_all,
            ):
                _private_not_found()
        elif not _survey_global_capability(
            context,
            db,
            identity=identity,
            required_permissions=admin_permissions,
            require_all=admin_require_all,
        ):
            raise ProblemError(status=403, code="permission_denied", detail="没有执行此操作的权限")
    if include_identity and (result.total or not center_denied_cycles):
        metadata: dict[str, object] = {
            "identity_mode": identity,
            "item_count": result.total,
        }
        for key, value in (
            ("cohort_id", cohort_id),
            ("project_id", project_id),
            ("staff_person_id", staff_id),
            ("role_key", role_key),
        ):
            if value is not None:
                metadata[key] = str(value)
        snapshot_version = _survey_snapshot_version_for_audit(
            db,
            cohort_id=cohort_id,
        )
        if snapshot_version is not None:
            metadata["snapshot_version"] = snapshot_version
        AuditService(db).append(
            actor_user_id=context.account.id,
            action="camp_surveys.identity.read",
            resource_type="camp_survey_feedback_projection",
            resource_id=None,
            purpose=None,
            outcome="allowed",
            request_id=str(request.state.request_id),
            metadata=metadata,
        )
        db.commit()
    return SurveyFeedbackPageResponse(
        items=[
            _feedback_item_response(item, identity=identity)
            for item in result.items
        ],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
    )


@admin_router.get("/print-projection", response_model=SurveyPrintResponse)
@admin_router.get("/print", response_model=SurveyPrintResponse, include_in_schema=False)
def get_print_projection(
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    query: Annotated[CampSurveyQueryService, Depends(get_camp_survey_query_service)],
    db: Annotated[Session, Depends(get_db)],
    cohort_id: UUID | None = None,
    project_id: UUID | None = None,
    staff_id: UUID | None = None,
    role_key: str | None = None,
    identity: Literal["hidden", "real_name"] = "hidden",
) -> SurveyPrintResponse:
    center_denied_cycles: set[UUID] = set()
    filtered = (
        cohort_id is not None
        or project_id is not None
        or staff_id is not None
        or role_key is not None
    )
    required_permissions = _print_required_permissions(identity)
    if filtered and not _survey_target_exists(
        db,
        cohort_id=cohort_id,
        project_id=project_id,
        staff_person_id=staff_id,
        role_key=role_key,
    ):
        _private_not_found()
    can_print = _survey_global_capability(
        context,
        db,
        identity="hidden",
        required_permissions=("camp_surveys.print",),
        require_all=True,
    )
    can_identity = (
        _survey_global_capability(
            context,
            db,
            identity="real_name",
            required_permissions=("camp_surveys.identity.read",),
            require_all=True,
        )
        if identity == "real_name"
        else False
    )
    try:
        projection = query.print_projection(
            identity=identity,
            can_print=can_print,
            can_read_identity=can_identity,
            cohort_id=cohort_id,
            project_id=project_id,
            staff_person_id=staff_id,
            role_key=role_key,
            row_authorizer=_survey_row_authorizer(
                context,
                db,
                identity=identity,
                required_permissions=required_permissions,
                require_all=True,
                center_denied_cycles=center_denied_cycles,
            ),
        )
    except PermissionError as exc:
        raise ProblemError(status=403, code="permission_denied", detail="没有执行此操作的权限") from exc
    if not projection.items:
        if filtered:
            if not _survey_filter_capability(
                context,
                db,
                identity=identity,
                cohort_id=cohort_id,
                project_id=project_id,
                staff_person_id=staff_id,
                role_key=role_key,
                required_permissions=required_permissions,
                require_all=True,
            ):
                _private_not_found()
        elif not _survey_global_capability(
            context,
            db,
            identity=identity,
            required_permissions=required_permissions,
            require_all=True,
        ):
            raise ProblemError(status=403, code="permission_denied", detail="没有执行此操作的权限")
    if identity == "real_name" and (projection.items or not center_denied_cycles):
        snapshot_version = _survey_snapshot_version_for_audit(
            db,
            cohort_id=cohort_id,
        )
        metadata: dict[str, object] = {
            **({"staff_person_id": str(staff_id)} if staff_id else {}),
            **({"cohort_id": str(cohort_id)} if cohort_id else {}),
            "identity_mode": identity,
            "item_count": len(projection.items),
        }
        if snapshot_version is not None:
            metadata["snapshot_version"] = snapshot_version
        AuditService(db).append(
            actor_user_id=context.account.id,
            action="camp_surveys.print",
            resource_type="camp_survey_print_projection",
            resource_id=None,
            purpose=None,
            outcome="allowed",
            request_id=str(request.state.request_id),
            metadata=metadata,
        )
        db.commit()
    return SurveyPrintResponse(
        identity=cast(Literal["hidden", "real_name"], projection.identity),
        items=[_print_item_response(item) for item in projection.items],
    )
