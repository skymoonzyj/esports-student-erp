"""Transactional submission and one-revision service for camp surveys.

The service keeps respondent identity outside the payload digest and resolves
all names from the immutable roster.  It is intentionally framework-neutral so
the API and background completion workflow can share the same transaction.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.auth.models import UserAccount
from app.core.time import business_date_at
from app.core.transactions import write_transaction
from app.evaluations.errors import (
    CampSurveyResourceNotFound,
    CampSurveyRevisionConflict,
    CampSurveySnapshotConflict,
    CampSurveySupplementConflict,
    CampSurveyValidationError,
    CampSurveyWindowClosed,
)
from app.evaluations.idempotency import EvaluationIdempotencyService
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
from app.evaluations.survey_schemas import (
    CampSurveyRevisionInput,
    CampSurveyStaffRatingInput,
    CampSurveySubmissionInput,
    CampSurveySupplementInput,
)
from app.food_preferences.errors import (
    FoodPreferenceNotFound,
    FoodPreferenceValidationFailed,
    FoodPreferenceVersionChanged,
)
from app.food_preferences.preference_service import (
    FoodPreferenceProjection,
    FoodPreferenceService,
    ReplaceFoodPreferenceCommand,
)
from app.food_preferences.schemas import CuisineSelectionInput
from app.identity.models import GuardianRelationship, Person, StudentProfile
from app.identity.readiness import GuardianReadinessPolicy
from app.rbac.models import Role, UserRole


@dataclass(frozen=True)
class CampSurveySubmitted:
    submission: CampSurveySubmission
    version: CampSurveyVersion
    idempotent: bool = False


@dataclass(frozen=True)
class CampSurveySupplementSubmitted:
    supplement: CampSurveySupplement
    idempotent: bool = False


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("occurred_at must be timezone-aware")


def _canonical_submission_payload(
    input: CampSurveySubmissionInput,
    *,
    cycle_id: UUID | None = None,
) -> dict[str, object]:
    """Return only user-controlled content suitable for idempotency hashing."""

    payload: dict[str, object] = {
        "expected_snapshot_version": input.expected_snapshot_version,
        "expected_food_preference_version": input.expected_food_preference_version,
        "favorite_cuisines": [
            {
                "cuisine_option_id": str(item.cuisine_option_id),
                "name": item.name,
                "custom_name": item.custom_name,
            }
            for item in input.favorite_cuisines
        ],
        "favorite_dishes": list(input.favorite_dishes),
        "course_training_score": input.course_training_score,
        "course_training_reason": input.course_training_reason,
        "life_service_score": input.life_service_score,
        "life_service_reason": input.life_service_reason,
        "service_management_score": input.service_management_score,
        "service_management_reason": input.service_management_reason,
        "overall_suggestion": input.overall_suggestion,
        "staff_ratings": [
            {
                "staff_snapshot_id": str(item.staff_snapshot_id),
                "score": item.score,
                "reason": item.reason,
                "suggestion": item.suggestion,
            }
            for item in input.staff_ratings
        ],
    }
    if cycle_id is not None:
        payload["cycle_id"] = str(cycle_id)
    return payload


def _validate_staff_snapshot_ids(
    ratings: Iterable[CampSurveyStaffRatingInput],
    expected_ids: set[UUID],
) -> None:
    actual_ids = {item.staff_snapshot_id for item in ratings}
    if actual_ids != expected_ids:
        raise CampSurveyValidationError(
            "staff ratings must exactly cover the current survey snapshot"
        )


def _pending_staff_snapshot_ids(
    rows: Iterable[CampSurveyStaffSnapshot],
    *,
    completed_person_ids: set[UUID],
) -> set[UUID]:
    """Return current snapshot row IDs whose stable people are not complete."""

    return {row.id for row in rows if row.staff_person_id not in completed_person_ids}


def _food_snapshot_matches(
    input: CampSurveySubmissionInput,
    stored: dict[str, object] | None,
) -> bool:
    """Compare a legacy persisted food snapshot with submitted content.

    Submission paths no longer use this helper: they replace the profile and
    persist the returned projection.  It remains available for compatibility
    with older contract callers that inspect stored snapshots directly.
    """

    if stored is None or stored.get("version") != input.expected_food_preference_version:
        return False
    if input.expected_food_preference_version == 0:
        return True
    return stored.get("cuisines") == [
        {
            "cuisine_option_id": str(item.cuisine_option_id),
            "name": item.name,
            "custom_name": item.custom_name,
        }
        for item in input.favorite_cuisines
    ] and stored.get("dishes") == list(input.favorite_dishes)


def _canonical_food_payload(
    projection: FoodPreferenceProjection,
) -> tuple[list[dict[str, object]], list[str]]:
    """Return the server-owned cuisine/dish snapshot for persistence."""

    cuisines = projection.cuisines
    dishes = projection.dishes
    canonical_cuisines: list[dict[str, object]] = []
    for cuisine in cuisines:
        option_id = cuisine.cuisine_option_id
        name = cuisine.name
        custom_name = cuisine.custom_name
        if not isinstance(option_id, UUID) or not isinstance(name, str):
            raise CampSurveyValidationError("food preference payload is invalid")
        if custom_name is not None and not isinstance(custom_name, str):
            raise CampSurveyValidationError("food preference payload is invalid")
        canonical_cuisines.append(
            {
                "cuisine_option_id": str(option_id),
                "name": name,
                "custom_name": custom_name,
            }
        )
    if not all(isinstance(dish, str) for dish in dishes):
        raise CampSurveyValidationError("food preference payload is invalid")
    return canonical_cuisines, list(dishes)


def _audit_metadata(*, version: int, item_count: int) -> dict[str, int]:
    """Return the allowlisted, content-free audit metadata for a submission."""

    return {"new_version": version, "item_count": item_count}


def _mutation_request_id(
    request_id: str | None,
    operation: str,
    resource_id: UUID,
) -> str:
    """Use the HTTP request ID while keeping background calls traceable."""

    if request_id is not None and request_id.strip():
        return request_id.strip()
    return f"camp-survey-{operation}-{resource_id}"


class CampSurveyService:
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
        _require_aware(result)
        return result

    def _locked_cycle(self, cycle_id: UUID) -> CampSurveyCycle:
        cycle = self.db.scalar(
            select(CampSurveyCycle)
            .where(CampSurveyCycle.id == cycle_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if cycle is None:
            raise CampSurveyResourceNotFound("survey resource not found")
        return cycle

    def _revalidate_respondent(
        self,
        *,
        cycle: CampSurveyCycle,
        actor_user_id: UUID,
        actor_person_id: UUID,
        student_person_id: UUID | None,
        respondent_kind: str | None,
        guardian_relationship_id: UUID | None,
        occurred_at: datetime,
    ) -> GuardianRelationship | None:
        """Re-authorize the actor after the cycle lock is acquired.

        The router performs an early read for a fast private-404 response, but
        that read is not a write authorization boundary.  Locking the account
        and (for guardians) the exact relationship here makes a concurrent
        relationship end linearize before or after the submission; a stale
        route snapshot can never be written.
        """
        expected_student_id = student_person_id or cycle.student_person_id
        if expected_student_id != cycle.student_person_id:
            raise CampSurveyResourceNotFound("survey resource not found")
        effective_kind = respondent_kind or (
            "self" if actor_person_id == cycle.student_person_id else "guardian"
        )
        # Identity relationship changes acquire the guardian advisory lock,
        # then person rows in UUID order, then the relationship and accounts.
        # Reuse that order here so a stale route snapshot cannot race a
        # relationship-end transaction into a deadlock or a write after the
        # respondent person has been soft-deleted.
        readiness_policy = GuardianReadinessPolicy(self.db)
        if effective_kind == "guardian":
            readiness_policy.lock_guardian_relationships(actor_person_id)
            locked_people = readiness_policy.lock_students(
                (actor_person_id, cycle.student_person_id)
            )
        else:
            locked_people = readiness_policy.lock_students((actor_person_id,))
        locked_people_by_id = {person.id: person for person in locked_people}
        actor_person = locked_people_by_id.get(actor_person_id)
        student_person = locked_people_by_id.get(cycle.student_person_id)
        if (
            actor_person is None
            or actor_person.deleted_at is not None
            or student_person is None
            or student_person.deleted_at is not None
        ):
            raise CampSurveyResourceNotFound("survey resource not found")

        def locked_account() -> UserAccount:
            account = self.db.scalar(
                select(UserAccount)
                .join(Person, Person.id == UserAccount.person_id)
                .where(
                    UserAccount.id == actor_user_id,
                    UserAccount.person_id == actor_person_id,
                    UserAccount.status == "active",
                    Person.deleted_at.is_(None),
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if account is None:
                raise CampSurveyResourceNotFound("survey resource not found")
            return account

        if effective_kind == "self":
            if (
                actor_person_id != cycle.student_person_id
                or self.db.scalar(
                    select(StudentProfile.person_id).where(
                        StudentProfile.person_id == cycle.student_person_id,
                        StudentProfile.status == "active",
                    )
                )
                is None
            ):
                raise CampSurveyResourceNotFound("survey resource not found")
            locked_account()
            CampSurveyAccessService(self.db).require_center_access(
                cycle=cycle,
                account_id=actor_user_id,
                occurred_at=occurred_at,
            )
            return None
        if effective_kind != "guardian":
            raise CampSurveyResourceNotFound("survey resource not found")
        if (
            self.db.scalar(
                select(UserRole.user_id)
                .join(Role, Role.id == UserRole.role_id)
                .where(UserRole.user_id == actor_user_id, Role.key == "guardian")
                .limit(1)
            )
            is None
        ):
            raise CampSurveyResourceNotFound("survey resource not found")
        relationship_query = select(GuardianRelationship).where(
            GuardianRelationship.guardian_person_id == actor_person_id,
            GuardianRelationship.student_person_id == cycle.student_person_id,
            GuardianRelationship.requires_verification.is_(False),
        )
        if guardian_relationship_id is not None:
            relationship_query = relationship_query.where(
                GuardianRelationship.id == guardian_relationship_id
            )
        reference_date = business_date_at(occurred_at)
        relationship = self.db.scalar(
            relationship_query.where(
                GuardianRelationship.valid_from <= reference_date,
                or_(
                    GuardianRelationship.valid_until.is_(None),
                    GuardianRelationship.valid_until >= reference_date,
                ),
                or_(
                    GuardianRelationship.ended_at.is_(None),
                    GuardianRelationship.ended_at > occurred_at,
                ),
            )
            .order_by(GuardianRelationship.is_primary.desc(), GuardianRelationship.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if relationship is None:
            raise CampSurveyResourceNotFound("survey resource not found")
        # The relationship is locked before the account, matching the
        # identity service's relationship -> account order.
        locked_account()
        CampSurveyAccessService(self.db).require_center_access(
            cycle=cycle,
            account_id=actor_user_id,
            occurred_at=occurred_at,
        )
        return relationship

    def _locked_snapshot(
        self, cycle: CampSurveyCycle, expected_version: int
    ) -> CampSurveySnapshotVersion:
        if expected_version != cycle.current_snapshot_version:
            raise CampSurveySnapshotConflict("survey snapshot version changed")
        snapshot = self.db.scalar(
            select(CampSurveySnapshotVersion)
            .where(
                CampSurveySnapshotVersion.cycle_id == cycle.id,
                CampSurveySnapshotVersion.version == expected_version,
            )
            .with_for_update(read=True)
        )
        if snapshot is None:
            raise CampSurveyResourceNotFound("survey resource not found")
        return snapshot

    def _staff_rows(self, snapshot_id: UUID) -> tuple[CampSurveyStaffSnapshot, ...]:
        return tuple(
            self.db.scalars(
                select(CampSurveyStaffSnapshot)
                .where(
                    CampSurveyStaffSnapshot.snapshot_version_id == snapshot_id,
                    CampSurveyStaffSnapshot.include_in_summary.is_(True),
                )
                .order_by(CampSurveyStaffSnapshot.id)
            )
        )

    def _check_window(self, cycle: CampSurveyCycle, occurred_at: datetime) -> None:
        if occurred_at < cycle.opens_at or occurred_at >= cycle.due_at:
            raise CampSurveyWindowClosed("survey window is closed")

    def _prepare_idempotency(
        self,
        *,
        scope: str,
        actor_user_id: UUID,
        key: str,
        payload: dict[str, object],
        occurred_at: datetime,
    ) -> UUID | None:
        if self.idempotency is None:
            return None
        digests = self.idempotency.prepare(
            scope=scope,
            actor_user_id=actor_user_id,
            key=key,
            payload=payload,
        )
        return self.idempotency.existing_resource_id(digests, occurred_at=occurred_at)

    def _record_idempotency(
        self,
        *,
        scope: str,
        actor_user_id: UUID,
        key: str,
        payload: dict[str, object],
        resource_id: UUID,
        occurred_at: datetime,
    ) -> None:
        if self.idempotency is None:
            return
        digests = self.idempotency.prepare(
            scope=scope,
            actor_user_id=actor_user_id,
            key=key,
            payload=payload,
        )
        self.idempotency.record(digests, resource_id=resource_id, occurred_at=occurred_at)

    @staticmethod
    def _submission_from_existing(
        db: Session,
        *,
        version_id: UUID,
        actor_person_id: UUID,
        cycle_id: UUID,
    ) -> CampSurveySubmitted:
        version = db.scalar(
            select(CampSurveyVersion)
            .join(CampSurveySubmission, CampSurveySubmission.id == CampSurveyVersion.submission_id)
            .where(
                CampSurveyVersion.id == version_id,
                CampSurveySubmission.cycle_id == cycle_id,
                CampSurveySubmission.respondent_person_id == actor_person_id,
            )
        )
        submission = db.scalar(
            select(CampSurveySubmission).where(
                CampSurveySubmission.id == (version.submission_id if version else None),
                CampSurveySubmission.cycle_id == cycle_id,
                CampSurveySubmission.respondent_person_id == actor_person_id,
            )
        )
        if version is None or submission is None:
            raise CampSurveyResourceNotFound("survey resource not found")
        return CampSurveySubmitted(submission=submission, version=version, idempotent=True)

    def _write_version(
        self,
        *,
        submission: CampSurveySubmission,
        snapshot: CampSurveySnapshotVersion,
        input: CampSurveySubmissionInput,
        food_projection: FoodPreferenceProjection,
        version_number: int,
        occurred_at: datetime,
    ) -> CampSurveyVersion:
        rows = self._staff_rows(snapshot.id)
        _validate_staff_snapshot_ids(input.staff_ratings, {row.id for row in rows})
        cuisines, dishes = _canonical_food_payload(food_projection)
        version = CampSurveyVersion(
            id=uuid4(),
            submission_id=submission.id,
            snapshot_version_id=snapshot.id,
            cycle_id=submission.cycle_id,
            version=version_number,
            course_training_score=input.course_training_score,
            course_training_reason=input.course_training_reason,
            life_service_score=input.life_service_score,
            life_service_reason=input.life_service_reason,
            service_management_score=input.service_management_score,
            service_management_reason=input.service_management_reason,
            overall_suggestion=input.overall_suggestion,
            favorite_cuisines=cuisines,
            favorite_dishes=dishes,
            food_preference_version=food_projection.version,
            published_at=occurred_at,
        )
        self.db.add(version)
        self.db.flush()
        self.db.add_all(
            CampSurveyStaffRating(
                version_id=version.id,
                staff_snapshot_id=item.staff_snapshot_id,
                snapshot_version_id=snapshot.id,
                score=item.score,
                reason=item.reason,
                suggestion=item.suggestion,
            )
            for item in input.staff_ratings
        )
        self.db.flush()
        return version

    def _sync_food_preference(
        self,
        *,
        student_person_id: UUID,
        input: CampSurveySubmissionInput,
        actor_user_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> FoodPreferenceProjection:
        """Create/update the student's canonical profile in the same transaction.

        The first camp survey is also the registration questionnaire.  A
        non-zero expected version is treated as an optimistic concurrency
        check; version zero creates the profile.  Guardian submissions never
        silently change a different profile after it has advanced.
        """
        service = FoodPreferenceService(self.db)
        command = ReplaceFoodPreferenceCommand(
            expected_version=input.expected_food_preference_version,
            cuisines=tuple(
                CuisineSelectionInput(
                    cuisine_option_id=item.cuisine_option_id,
                    custom_name=item.custom_name,
                )
                for item in input.favorite_cuisines
            ),
            dishes=tuple(input.favorite_dishes),
            actor_user_id=actor_user_id,
            source="camp_survey",
            request_id=request_id,
        )
        try:
            return service.replace_locked(student_person_id, command)
        except FoodPreferenceVersionChanged as exc:
            raise CampSurveySnapshotConflict("food preference version changed") from exc
        except FoodPreferenceValidationFailed as exc:
            raise CampSurveyValidationError("food preference payload is invalid") from exc
        except FoodPreferenceNotFound as exc:
            raise CampSurveyValidationError("food preference payload is invalid") from exc

    def submit(
        self,
        cycle_id: UUID,
        input: CampSurveySubmissionInput,
        *,
        actor_user_id: UUID,
        actor_person_id: UUID,
        student_person_id: UUID,
        respondent_kind: str,
        guardian_relationship_id: UUID | None = None,
        guardian_relationship_snapshot: dict[str, Any] | None = None,
        request_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> CampSurveySubmitted:
        now = self._now(occurred_at)
        payload = _canonical_submission_payload(input, cycle_id=cycle_id)
        with write_transaction(self.db):
            cycle = self._locked_cycle(cycle_id)
            relationship = self._revalidate_respondent(
                cycle=cycle,
                actor_user_id=actor_user_id,
                actor_person_id=actor_person_id,
                student_person_id=student_person_id,
                respondent_kind=respondent_kind,
                guardian_relationship_id=guardian_relationship_id,
                occurred_at=now,
            )
            self._check_window(cycle, now)
            if relationship is not None:
                guardian_relationship_id = relationship.id
                guardian_relationship_snapshot = {
                    "relationship": relationship.relationship,
                    "is_primary": relationship.is_primary,
                    "valid_from": relationship.valid_from.isoformat(),
                    "valid_until": (
                        relationship.valid_until.isoformat()
                        if relationship.valid_until is not None
                        else None
                    ),
                }
            replay_id = self._prepare_idempotency(
                scope="camp_survey.submit",
                actor_user_id=actor_user_id,
                key=input.idempotency_key,
                payload=payload,
                occurred_at=now,
            )
            if replay_id is not None:
                return self._submission_from_existing(
                    self.db,
                    version_id=replay_id,
                    actor_person_id=actor_person_id,
                    cycle_id=cycle_id,
                )
            snapshot = self._locked_snapshot(cycle, input.expected_snapshot_version)
            submission = self.db.scalar(
                select(CampSurveySubmission)
                .where(
                    CampSurveySubmission.cycle_id == cycle.id,
                    CampSurveySubmission.respondent_person_id == actor_person_id,
                )
                .with_for_update()
            )
            if submission is not None:
                raise CampSurveyRevisionConflict("survey submission already exists")
            food_projection = self._sync_food_preference(
                student_person_id=student_person_id,
                input=input,
                actor_user_id=actor_user_id,
                occurred_at=now,
                request_id=_mutation_request_id(request_id, "submit", cycle.id),
            )
            submission = CampSurveySubmission(
                id=uuid4(),
                cycle_id=cycle.id,
                respondent_person_id=actor_person_id,
                student_person_id=student_person_id,
                respondent_kind=respondent_kind,
                guardian_relationship_id=guardian_relationship_id,
                guardian_relationship_snapshot=guardian_relationship_snapshot,
                revision_count=0,
                latest_version=1,
                first_submitted_at=now,
                last_submitted_at=now,
            )
            self.db.add(submission)
            self.db.flush()
            version = self._write_version(
                submission=submission,
                snapshot=snapshot,
                input=input,
                food_projection=food_projection,
                version_number=1,
                occurred_at=now,
            )
            self._record_idempotency(
                scope="camp_survey.submit",
                actor_user_id=actor_user_id,
                key=input.idempotency_key,
                payload=payload,
                resource_id=version.id,
                occurred_at=now,
            )
            self.audit.append(
                actor_user_id=actor_user_id,
                action="submit",
                resource_type="camp_survey_submission",
                resource_id=submission.id,
                purpose=None,
                outcome="allowed",
                request_id=_mutation_request_id(request_id, "submit", version.id),
                metadata=_audit_metadata(
                    version=version.version,
                    item_count=len(input.favorite_cuisines) + len(input.favorite_dishes),
                ),
            )
            return CampSurveySubmitted(submission=submission, version=version)

    def revise(
        self,
        cycle_id: UUID,
        submission_id: UUID,
        input: CampSurveyRevisionInput,
        *,
        actor_user_id: UUID,
        actor_person_id: UUID,
        student_person_id: UUID | None = None,
        respondent_kind: str | None = None,
        guardian_relationship_id: UUID | None = None,
        request_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> CampSurveySubmitted:
        now = self._now(occurred_at)
        payload = _canonical_submission_payload(input, cycle_id=cycle_id)
        with write_transaction(self.db):
            cycle = self._locked_cycle(cycle_id)
            self._revalidate_respondent(
                cycle=cycle,
                actor_user_id=actor_user_id,
                actor_person_id=actor_person_id,
                student_person_id=student_person_id,
                respondent_kind=respondent_kind,
                guardian_relationship_id=guardian_relationship_id,
                occurred_at=now,
            )
            self._check_window(cycle, now)
            replay_id = self._prepare_idempotency(
                scope="camp_survey.revise",
                actor_user_id=actor_user_id,
                key=input.idempotency_key,
                payload={"submission_id": str(submission_id), **payload},
                occurred_at=now,
            )
            if replay_id is not None:
                return self._submission_from_existing(
                    self.db,
                    version_id=replay_id,
                    actor_person_id=actor_person_id,
                    cycle_id=cycle_id,
                )
            submission = self.db.scalar(
                select(CampSurveySubmission)
                .where(
                    CampSurveySubmission.id == submission_id,
                    CampSurveySubmission.cycle_id == cycle.id,
                    CampSurveySubmission.respondent_person_id == actor_person_id,
                )
                .with_for_update()
            )
            if submission is None:
                raise CampSurveyResourceNotFound("survey resource not found")
            if submission.latest_version != 1 or submission.revision_count >= 1:
                raise CampSurveyRevisionConflict("survey revision is already used")
            snapshot = self._locked_snapshot(cycle, input.expected_snapshot_version)
            food_projection = self._sync_food_preference(
                student_person_id=submission.student_person_id,
                input=input,
                actor_user_id=actor_user_id,
                occurred_at=now,
                request_id=_mutation_request_id(request_id, "revise", submission.id),
            )
            version = self._write_version(
                submission=submission,
                snapshot=snapshot,
                input=input,
                food_projection=food_projection,
                version_number=2,
                occurred_at=now,
            )
            submission.latest_version = 2
            submission.revision_count = 1
            submission.last_submitted_at = now
            self.db.flush()
            self._record_idempotency(
                scope="camp_survey.revise",
                actor_user_id=actor_user_id,
                key=input.idempotency_key,
                payload={"submission_id": str(submission_id), **payload},
                resource_id=version.id,
                occurred_at=now,
            )
            self.audit.append(
                actor_user_id=actor_user_id,
                action="submit",
                resource_type="camp_survey_submission",
                resource_id=submission.id,
                purpose=None,
                outcome="allowed",
                request_id=_mutation_request_id(request_id, "revise", version.id),
                metadata=_audit_metadata(
                    version=version.version,
                    item_count=len(input.favorite_cuisines) + len(input.favorite_dishes),
                ),
            )
            return CampSurveySubmitted(submission=submission, version=version)

    def submit_supplement(
        self,
        cycle_id: UUID,
        submission_id: UUID,
        input: CampSurveySupplementInput,
        *,
        actor_user_id: UUID,
        actor_person_id: UUID,
        student_person_id: UUID | None = None,
        respondent_kind: str | None = None,
        guardian_relationship_id: UUID | None = None,
        request_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> CampSurveySupplementSubmitted:
        now = self._now(occurred_at)
        payload = {
            "cycle_id": str(cycle_id),
            "submission_id": str(submission_id),
            "expected_snapshot_version": input.expected_snapshot_version,
            "staff_ratings": [
                {
                    "staff_snapshot_id": str(item.staff_snapshot_id),
                    "score": item.score,
                    "reason": item.reason,
                    "suggestion": item.suggestion,
                }
                for item in input.staff_ratings
            ],
            "change_reason": input.change_reason,
        }
        with write_transaction(self.db):
            cycle = self._locked_cycle(cycle_id)
            self._revalidate_respondent(
                cycle=cycle,
                actor_user_id=actor_user_id,
                actor_person_id=actor_person_id,
                student_person_id=student_person_id,
                respondent_kind=respondent_kind,
                guardian_relationship_id=guardian_relationship_id,
                occurred_at=now,
            )
            self._check_window(cycle, now)
            replay_id = self._prepare_idempotency(
                scope="camp_survey.supplement",
                actor_user_id=actor_user_id,
                key=input.idempotency_key,
                payload=payload,
                occurred_at=now,
            )
            if replay_id is not None:
                replay = self.db.scalar(
                    select(CampSurveySupplement).where(
                        CampSurveySupplement.id == replay_id,
                        CampSurveySupplement.submission_id == submission_id,
                    )
                )
                if replay is None:
                    raise CampSurveySupplementConflict("idempotent supplement is unavailable")
                return CampSurveySupplementSubmitted(supplement=replay, idempotent=True)
            submission = self.db.scalar(
                select(CampSurveySubmission)
                .where(
                    CampSurveySubmission.id == submission_id,
                    CampSurveySubmission.cycle_id == cycle.id,
                    CampSurveySubmission.respondent_person_id == actor_person_id,
                )
                .with_for_update()
            )
            if submission is None:
                raise CampSurveyResourceNotFound("survey resource not found")
            snapshot = self._locked_snapshot(cycle, input.expected_snapshot_version)
            rows = self._staff_rows(snapshot.id)
            main_version_ids = tuple(
                self.db.scalars(
                    select(CampSurveyVersion.id).where(
                        CampSurveyVersion.submission_id == submission.id
                    )
                )
            )
            # Snapshot rows are immutable and therefore receive a new UUID
            # when the roster is corrected.  Match previously rated staff by
            # their stable person identity, not by the version-local snapshot
            # UUID, so a supplement contains only genuinely new personnel.
            existing_main_person_ids = (
                set(
                    self.db.scalars(
                        select(CampSurveyStaffSnapshot.staff_person_id)
                        .join(
                            CampSurveyStaffRating,
                            CampSurveyStaffRating.staff_snapshot_id == CampSurveyStaffSnapshot.id,
                        )
                        .where(CampSurveyStaffRating.version_id.in_(main_version_ids))
                    )
                )
                if main_version_ids
                else set()
            )
            historical_supplement_person_ids = set(
                self.db.scalars(
                    select(CampSurveyStaffSnapshot.staff_person_id)
                    .join(
                        CampSurveySupplementRating,
                        CampSurveySupplementRating.staff_snapshot_id == CampSurveyStaffSnapshot.id,
                    )
                    .join(
                        CampSurveySupplement,
                        CampSurveySupplement.id == CampSurveySupplementRating.supplement_id,
                    )
                    .where(
                        CampSurveySupplement.submission_id == submission.id,
                        CampSurveySupplement.submitted_at.is_not(None),
                    )
                    .distinct()
                )
            )
            completed_person_ids = existing_main_person_ids | historical_supplement_person_ids
            supplement = self.db.scalar(
                select(CampSurveySupplement)
                .where(
                    CampSurveySupplement.submission_id == submission.id,
                    CampSurveySupplement.snapshot_version_id == snapshot.id,
                )
                .with_for_update()
            )
            if supplement is not None:
                raise CampSurveySupplementConflict("supplement has already been submitted")
            new_ids = _pending_staff_snapshot_ids(
                rows,
                completed_person_ids=completed_person_ids,
            )
            _validate_staff_snapshot_ids(input.staff_ratings, new_ids)
            supplement = CampSurveySupplement(
                id=uuid4(),
                submission_id=submission.id,
                snapshot_version_id=snapshot.id,
                cycle_id=submission.cycle_id,
                change_reason=input.change_reason,
                created_by_user_id=actor_user_id,
                submitted_at=now,
            )
            self.db.add(supplement)
            self.db.flush()
            self.db.add_all(
                CampSurveySupplementRating(
                    supplement_id=supplement.id,
                    staff_snapshot_id=item.staff_snapshot_id,
                    snapshot_version_id=snapshot.id,
                    score=item.score,
                    reason=item.reason,
                    suggestion=item.suggestion,
                )
                for item in input.staff_ratings
            )
            self.db.flush()
            self._record_idempotency(
                scope="camp_survey.supplement",
                actor_user_id=actor_user_id,
                key=input.idempotency_key,
                payload=payload,
                resource_id=supplement.id,
                occurred_at=now,
            )
            self.audit.append(
                actor_user_id=actor_user_id,
                action="submit",
                resource_type="camp_survey_supplement",
                resource_id=supplement.id,
                purpose=None,
                outcome="allowed",
                request_id=_mutation_request_id(request_id, "supplement", supplement.id),
                metadata=_audit_metadata(
                    version=snapshot.version,
                    item_count=len(input.staff_ratings),
                ),
            )
            return CampSurveySupplementSubmitted(supplement=supplement)
