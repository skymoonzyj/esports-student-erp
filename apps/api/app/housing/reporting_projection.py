from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any, cast
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from app.auth.contact_readiness import verified_phone_identifier_statement
from app.auth.models import UserAccount
from app.housing.history_validity import effective_stay_clause
from app.housing.models import HousingBuilding, HousingFloor, HousingRoom, HousingStay
from app.housing.reporting_types import (
    GuardianReportRow,
    HousingReportProjectionV1,
    ProjectionBuildResult,
    ProjectionIssue,
    ReportSex,
    ReportStayPurpose,
    ReportStayStatus,
    ResidentPersonType,
    ResidentReportRow,
)
from app.housing.sorting import building_code_sort_key, building_order_expressions
from app.identity.models import (
    GuardianRelationship,
    Person,
    StaffProfile,
    StudentProfile,
    VisitorProfile,
)
from app.identity.normalization import IdentityValidationError, normalize_cn_id, normalize_phone
from app.identity.readiness import is_minor
from app.personnel_intake.guardian_records import (
    ApprovalEvidenceError,
    authenticated_approval_facts,
    person_facts,
)
from app.personnel_intake.models import (
    AssistedApproval,
    AssistedPersonProfile,
    PersonnelIntakeRequest,
)
from app.security.encryption import EncryptedFieldError, EnvelopeCipher, LookupDigest
from app.teaching.models import Cohort

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_ACTUAL_STAY_STATUSES = frozenset({"checked_in", "checkout_pending", "checked_out"})
_STAFF_TYPES = frozenset({
    "coach", "life_teacher", "customer_service", "administration", "other",
    "team_manager", "assistant_coach",
})


@dataclass(frozen=True, slots=True)
class StayCandidate:
    status: str
    actual_check_in_at: datetime | None
    actual_check_out_at: datetime | None
    checkout_requested_actual_at: datetime | None


@dataclass(frozen=True, slots=True)
class ResidentCandidateRecord:
    stay_id: UUID
    person_id: UUID
    full_name: str
    phone_ciphertext: str | None
    identity_ciphertext: str
    sex: str | None
    date_of_birth: date | None
    is_student: bool
    staff_type: str | None
    is_visitor: bool
    has_verified_phone: bool
    building_id: UUID
    building_name: str
    building_order: int
    floor_id: UUID
    floor_name: str
    floor_order: int
    room_id: UUID
    room_number: str
    stay_purpose: str
    actual_check_in_at: datetime
    actual_check_out_at: datetime | None
    stay_status: str
    checkout_requested_actual_at: datetime | None
    cohort_id: UUID | None
    cohort_name: str | None
    visit_reason: str | None
    building_code: str | None = None
    assisted_kind: str | None = None
    has_approved_phone: bool = False
    document_type: str = "cn_id"


@dataclass(frozen=True, slots=True)
class GuardianCandidateRecord:
    relationship_id: UUID
    student_id: UUID
    guardian_id: UUID
    valid_from: date
    valid_until: date | None
    ended_at: datetime | None
    guardian_name: str
    guardian_phone_ciphertext: str | None
    guardian_identity_ciphertext: str
    guardian_sex: str | None
    guardian_date_of_birth: date | None
    guardian_document_type: str = "cn_id"


@dataclass(frozen=True, slots=True)
class ProjectionRequest:
    generated_at: datetime
    range_start: datetime
    range_end: datetime
    floor_ids: tuple[UUID, ...]
    cohort_ids: tuple[UUID, ...] = ()
    person_types: tuple[ResidentPersonType, ...] = ()


def _require_aware(value: datetime) -> None:
    if value.utcoffset() is None:
        raise ValueError("report timestamps must be timezone-aware")


def overlaps_actual_stay(
    stay: StayCandidate,
    *,
    start: datetime,
    end: datetime,
) -> bool:
    _require_aware(start)
    _require_aware(end)
    if start >= end:
        raise ValueError("report range must be non-empty")
    if stay.status not in _ACTUAL_STAY_STATUSES or stay.actual_check_in_at is None:
        return False
    _require_aware(stay.actual_check_in_at)
    effective_end = stay.actual_check_out_at if stay.status == "checked_out" else None
    if effective_end is not None:
        _require_aware(effective_end)
    return stay.actual_check_in_at < end and (effective_end is None or effective_end > start)


def classify_person_type(
    *,
    is_student: bool,
    staff_type: str | None,
    is_visitor: bool,
    assisted_kind: str | None = None,
) -> ResidentPersonType | None:
    if assisted_kind in {"player", "both"} and staff_type is None and not is_visitor:
        return "student_player" if is_student else "player"
    profile_count = int(is_student) + int(staff_type is not None) + int(is_visitor)
    if profile_count != 1:
        return None
    if is_student:
        return "student"
    if is_visitor:
        return "visitor"
    if staff_type not in _STAFF_TYPES:
        return None
    return cast(ResidentPersonType, staff_type)


def effective_guardian_relationship(
    *,
    valid_from: date,
    valid_until: date | None,
    ended_at: datetime | None,
    check_in_at: datetime,
) -> bool:
    _require_aware(check_in_at)
    reference_date = check_in_at.astimezone(_SHANGHAI).date()
    if valid_from > reference_date:
        return False
    if valid_until is not None and valid_until < reference_date:
        return False
    if ended_at is not None:
        _require_aware(ended_at)
        if ended_at <= check_in_at:
            return False
    return True


def mask_report_phone(phone: str | None) -> str | None:
    if phone is None or len(phone) < 7:
        return None
    return f"{phone[:3]}****{phone[-4:]}"


def projection_sort_key(
    row: ResidentReportRow,
) -> tuple[int, str, int, str, str, datetime, str, str]:
    return (
        row.building_order,
        row.building_name,
        row.floor_order,
        row.floor_name,
        row.room_number,
        row.actual_check_in_at,
        row.full_name,
        row.row_id,
    )


def reporting_candidate_statement(
    *,
    floor_ids: Sequence[UUID],
    range_start: datetime,
    range_end: datetime,
) -> Select[tuple[Any, ...]]:
    _require_aware(range_start)
    _require_aware(range_end)
    if range_start >= range_end:
        raise ValueError("report range must be non-empty")
    if not floor_ids:
        raise ValueError("at least one authorized floor is required")
    return (
        select(
            HousingStay,
            Person,
            HousingRoom,
            HousingFloor,
            HousingBuilding,
            StudentProfile.person_id.label("student_person_id"),
            StaffProfile.staff_type,
            VisitorProfile.person_id.label("visitor_person_id"),
            Cohort,
            verified_phone_identifier_statement(Person.id).exists().label("has_verified_phone"),
        )
        .join(Person, Person.id == HousingStay.person_id)
        .join(HousingRoom, HousingRoom.id == HousingStay.room_id)
        .join(HousingFloor, HousingFloor.id == HousingRoom.floor_id)
        .join(HousingBuilding, HousingBuilding.id == HousingRoom.building_id)
        .outerjoin(StudentProfile, StudentProfile.person_id == Person.id)
        .outerjoin(StaffProfile, StaffProfile.person_id == Person.id)
        .outerjoin(VisitorProfile, VisitorProfile.person_id == Person.id)
        .outerjoin(Cohort, Cohort.id == HousingStay.cohort_id)
        .where(
            HousingFloor.id.in_(tuple(floor_ids)),
            effective_stay_clause(),
            HousingStay.status.in_(tuple(sorted(_ACTUAL_STAY_STATUSES))),
            HousingStay.actual_check_in_at.is_not(None),
            HousingStay.actual_check_in_at < range_end,
            or_(
                HousingStay.status != "checked_out",
                HousingStay.actual_check_out_at > range_start,
            ),
        )
        .order_by(
            *building_order_expressions(),
            HousingBuilding.name,
            HousingFloor.display_order,
            HousingFloor.name,
            HousingRoom.room_number,
            HousingStay.actual_check_in_at,
            Person.full_name,
            HousingStay.id,
        )
    )


def _person_fields(
    *,
    cipher: EnvelopeCipher,
    phone_ciphertext: str | None,
    identity_ciphertext: str,
    date_of_birth: date | None,
    sex: str | None,
    prefix: str = "",
    document_type: str = "cn_id",
) -> tuple[str | None, str | None, set[str]]:
    missing: set[str] = set()
    phone: str | None = None
    identity_number: str | None = None
    if phone_ciphertext is None:
        missing.add(f"{prefix}phone")
    else:
        try:
            phone = normalize_phone(cipher.decrypt(phone_ciphertext, field_name="phone"))
        except (EncryptedFieldError, IdentityValidationError):
            missing.add(f"{prefix}phone")
    if document_type != "cn_id":
        missing.add(f"{prefix}identity_number")
    else:
        try:
            identity = normalize_cn_id(
                cipher.decrypt(identity_ciphertext, field_name="identity_number"),
                expected_birth_date=date_of_birth,
            )
            identity_number = identity.number
            if identity.sex != sex:
                missing.add(f"{prefix}sex")
        except (EncryptedFieldError, IdentityValidationError):
            missing.add(f"{prefix}identity_number")
    if sex not in {"male", "female"}:
        missing.add(f"{prefix}sex")
    if date_of_birth is None:
        missing.add(f"{prefix}date_of_birth")
    return phone, identity_number, missing


def _resident_issue(
    candidate: ResidentCandidateRecord,
    *,
    phone: str | None,
    missing: Iterable[str],
) -> ProjectionIssue:
    return ProjectionIssue(
        person_id=candidate.person_id,
        person_name=candidate.full_name.strip() or "未命名人员",
        masked_phone=mask_report_phone(phone),
        stay_id=candidate.stay_id,
        missing_fields=tuple(sorted(set(missing))),
    )


def _candidate_stay(candidate: ResidentCandidateRecord) -> StayCandidate:
    return StayCandidate(
        status=candidate.stay_status,
        actual_check_in_at=candidate.actual_check_in_at,
        actual_check_out_at=candidate.actual_check_out_at,
        checkout_requested_actual_at=candidate.checkout_requested_actual_at,
    )


def build_projection_from_candidates(
    *,
    residents: Sequence[ResidentCandidateRecord],
    guardians: Sequence[GuardianCandidateRecord],
    cipher: EnvelopeCipher,
    generated_at: datetime,
    range_start: datetime,
    range_end: datetime,
    cohort_ids: Sequence[UUID],
    person_types: Sequence[ResidentPersonType],
    row_id_factory: Callable[[], str] = lambda: uuid4().hex,
) -> ProjectionBuildResult:
    cohort_filter = frozenset(cohort_ids)
    person_type_filter = frozenset(person_types)
    guardian_candidates: dict[UUID, list[GuardianCandidateRecord]] = {}
    for guardian in guardians:
        guardian_candidates.setdefault(guardian.student_id, []).append(guardian)

    ordered_building_ids = {candidate.building_id: candidate for candidate in residents}
    natural_building_order = {
        building_id: index
        for index, (building_id, candidate) in enumerate(
            sorted(
                ordered_building_ids.items(),
                key=lambda item: building_code_sort_key(
                    item[1].building_code or item[1].building_name,
                    display_order=item[1].building_order,
                    stable_id=str(item[0]),
                ),
            )
        )
    }
    projected_residents: list[ResidentReportRow] = []
    projected_guardians: list[GuardianReportRow] = []
    issues: list[ProjectionIssue] = []

    for candidate in residents:
        if not overlaps_actual_stay(
            _candidate_stay(candidate),
            start=range_start,
            end=range_end,
        ):
            continue
        if cohort_filter and candidate.cohort_id not in cohort_filter:
            continue

        person_type = classify_person_type(
            is_student=candidate.is_student,
            staff_type=candidate.staff_type,
            is_visitor=candidate.is_visitor,
            assisted_kind=candidate.assisted_kind,
        )
        if person_type is not None and person_type_filter and person_type not in person_type_filter:
            continue

        phone, identity_number, missing = _person_fields(
            cipher=cipher,
            phone_ciphertext=candidate.phone_ciphertext,
            identity_ciphertext=candidate.identity_ciphertext,
            date_of_birth=candidate.date_of_birth,
            sex=candidate.sex,
            document_type=candidate.document_type,
        )
        if not candidate.has_verified_phone and not candidate.has_approved_phone:
            missing.add("phone")
        if person_type is None:
            missing.add("person_type")
        if not candidate.full_name.strip():
            missing.add("full_name")
        if not candidate.building_name.strip():
            missing.add("building")
        if not candidate.floor_name.strip():
            missing.add("floor")
        if not candidate.room_number.strip():
            missing.add("room")
        if candidate.stay_purpose not in {"permanent", "camp", "temporary"}:
            missing.add("stay_purpose")
        if candidate.stay_purpose == "camp" and candidate.cohort_id is None:
            missing.add("cohort")
        if candidate.stay_purpose == "temporary" and not (candidate.visit_reason or "").strip():
            missing.add("visit_reason")
        if candidate.stay_status == "checked_out" and candidate.actual_check_out_at is None:
            missing.add("actual_check_out_at")
        if candidate.stay_status not in _ACTUAL_STAY_STATUSES:
            missing.add("stay_status")
        if (
            missing
            or phone is None
            or identity_number is None
            or person_type is None
            or candidate.date_of_birth is None
        ):
            issues.append(_resident_issue(candidate, phone=phone, missing=missing))
            continue

        check_in_date = candidate.actual_check_in_at.astimezone(_SHANGHAI).date()
        minor = person_type in {"student", "player", "student_player"} and is_minor(
            candidate.date_of_birth,
            check_in_date,
        )
        resident_row = ResidentReportRow(
            row_id=row_id_factory(),
            person_id=candidate.person_id,
            full_name=candidate.full_name.strip(),
            phone=phone,
            identity_number=identity_number,
            sex=cast(ReportSex, candidate.sex),
            date_of_birth=candidate.date_of_birth,
            person_type=person_type,
            building_id=candidate.building_id,
            building_name=candidate.building_name.strip(),
            building_order=natural_building_order[candidate.building_id],
            floor_id=candidate.floor_id,
            floor_name=candidate.floor_name.strip(),
            floor_order=candidate.floor_order,
            room_id=candidate.room_id,
            room_number=candidate.room_number.strip(),
            stay_id=candidate.stay_id,
            stay_purpose=cast(ReportStayPurpose, candidate.stay_purpose),
            actual_check_in_at=candidate.actual_check_in_at,
            actual_check_out_at=candidate.actual_check_out_at,
            stay_status=cast(ReportStayStatus, candidate.stay_status),
            cohort_id=candidate.cohort_id,
            cohort_name=(candidate.cohort_name or "").strip() or None,
            visit_reason=(candidate.visit_reason or "").strip() or None,
            is_minor_at_check_in=minor,
            guardian_count=0,
            phone_verified=candidate.has_verified_phone,
            phone_evidence=(
                "verified_identifier" if candidate.has_verified_phone else "approved_profile"
            ),
        )

        guardian_rows: list[GuardianReportRow] = []
        guardian_missing: set[str] = set()
        if minor:
            effective = [
                guardian
                for guardian in guardian_candidates.get(candidate.person_id, ())
                if effective_guardian_relationship(
                    valid_from=guardian.valid_from,
                    valid_until=guardian.valid_until,
                    ended_at=guardian.ended_at,
                    check_in_at=candidate.actual_check_in_at,
                )
            ]
            unique_effective = {guardian.guardian_id: guardian for guardian in effective}
            if not unique_effective:
                guardian_missing.add("guardian_relationship")
            for guardian in unique_effective.values():
                guardian_phone, guardian_identity, current_missing = _person_fields(
                    cipher=cipher,
                    phone_ciphertext=guardian.guardian_phone_ciphertext,
                    identity_ciphertext=guardian.guardian_identity_ciphertext,
                    date_of_birth=guardian.guardian_date_of_birth,
                    sex=guardian.guardian_sex,
                    document_type=guardian.guardian_document_type,
                    prefix="guardian.",
                )
                guardian_missing.update(current_missing)
                if not guardian.guardian_name.strip():
                    guardian_missing.add("guardian.full_name")
                guardian_minor = guardian.guardian_date_of_birth is not None and is_minor(
                    guardian.guardian_date_of_birth, check_in_date
                )
                if guardian_minor:
                    guardian_missing.add("guardian.age")
                if (
                    current_missing
                    or guardian_phone is None
                    or guardian_identity is None
                    or not guardian.guardian_name.strip()
                    or guardian_minor
                ):
                    continue
                guardian_rows.append(
                    GuardianReportRow(
                        resident_row_id=resident_row.row_id,
                        student_id=candidate.person_id,
                        student_name=candidate.full_name.strip(),
                        guardian_id=guardian.guardian_id,
                        guardian_name=guardian.guardian_name.strip(),
                        guardian_phone=guardian_phone,
                        guardian_identity_number=guardian_identity,
                    )
                )
        if guardian_missing:
            issues.append(_resident_issue(candidate, phone=phone, missing=guardian_missing))
            continue

        guardian_rows.sort(key=lambda row: (row.guardian_name, str(row.guardian_id)))
        resident_row = resident_row.model_copy(update={"guardian_count": len(guardian_rows)})
        projected_residents.append(resident_row)
        projected_guardians.extend(guardian_rows)

    if issues:
        issues.sort(key=lambda issue: (issue.person_name, str(issue.stay_id)))
        return ProjectionBuildResult(projection=None, issues=tuple(issues))

    projected_residents.sort(key=projection_sort_key)
    resident_order = {resident.row_id: index for index, resident in enumerate(projected_residents)}
    projected_guardians.sort(
        key=lambda row: (
            resident_order[row.resident_row_id],
            row.guardian_name,
            str(row.guardian_id),
        )
    )
    return ProjectionBuildResult(
        projection=HousingReportProjectionV1(
            generated_at=generated_at,
            range_start=range_start,
            range_end=range_end,
            residents=tuple(projected_residents),
            guardians=tuple(projected_guardians),
        )
    )


class HousingReportProjectionBuilder:
    def __init__(
        self, db: Session, cipher: EnvelopeCipher, *, lookup: LookupDigest | None = None
    ) -> None:
        self.db = db
        self.cipher = cipher
        self.lookup = lookup

    def build(self, request: ProjectionRequest) -> ProjectionBuildResult:
        rows = self.db.execute(
            reporting_candidate_statement(
                floor_ids=request.floor_ids,
                range_start=request.range_start,
                range_end=request.range_end,
            )
        ).all()
        residents = [self._resident_candidate(row) for row in rows]
        # Classification is operational metadata, not today's eligibility. Historical
        # actual stays remain visible even after a guardian relationship ends.
        evidence = self._approved_evidence({candidate.person_id for candidate in residents})
        residents = [
            replace(
                candidate,
                assisted_kind=evidence.get(candidate.person_id, (None, False))[0],
                has_approved_phone=evidence.get(candidate.person_id, (None, False))[1],
            )
            for candidate in residents
        ]
        student_ids = tuple(
            sorted(
                {
                    candidate.person_id
                    for candidate in residents
                    if candidate.is_student or candidate.assisted_kind in {"player", "both"}
                },
                key=str,
            )
        )
        guardians = self._guardian_candidates(student_ids)
        return build_projection_from_candidates(
            residents=residents,
            guardians=guardians,
            cipher=self.cipher,
            generated_at=request.generated_at,
            range_start=request.range_start,
            range_end=request.range_end,
            cohort_ids=request.cohort_ids,
            person_types=request.person_types,
        )

    def _approved_evidence(self, person_ids: set[UUID]) -> dict[UUID, tuple[str, bool]]:
        if not person_ids:
            return {}
        rows = self.db.execute(
            select(
                Person,
                UserAccount.account_access_origin,
                AssistedApproval,
                PersonnelIntakeRequest.kind,
            )
            .join(AssistedPersonProfile, AssistedPersonProfile.person_id == Person.id)
            .join(
                AssistedApproval, AssistedApproval.id == AssistedPersonProfile.current_approval_id
            )
            .join(PersonnelIntakeRequest, PersonnelIntakeRequest.id == AssistedApproval.request_id)
            .outerjoin(UserAccount, UserAccount.person_id == Person.id)
            .where(Person.id.in_(person_ids))
        ).all()
        result: dict[UUID, tuple[str, bool]] = {}
        for person, origin, approval, kind in rows:
            approved_phone = False
            if origin != "legacy":
                if self.lookup is None:
                    raise ApprovalEvidenceError()
                approved, _ = authenticated_approval_facts(
                    approval, cipher=self.cipher, lookup=self.lookup
                )
                try:
                    # Historical guardian readiness is evaluated separately at check-in.
                    approved_phone = person_facts(person, self.cipher) == approved
                except EncryptedFieldError as exc:
                    raise ApprovalEvidenceError() from exc
            result[person.id] = (kind, approved_phone)
        return result

    @staticmethod
    def _resident_candidate(row: Any) -> ResidentCandidateRecord:
        (
            stay,
            person,
            room,
            floor,
            building,
            student_person_id,
            staff_type,
            visitor_person_id,
            cohort,
            has_verified_phone,
        ) = row
        return ResidentCandidateRecord(
            stay_id=stay.id,
            person_id=person.id,
            full_name=person.full_name,
            phone_ciphertext=person.phone_ciphertext,
            identity_ciphertext=person.identity_ciphertext,
            sex=person.sex,
            date_of_birth=person.date_of_birth,
            is_student=student_person_id is not None,
            staff_type=staff_type,
            is_visitor=visitor_person_id is not None,
            building_id=building.id,
            building_name=stay.building_name_snapshot,
            building_order=building.display_order,
            floor_id=floor.id,
            floor_name=stay.floor_name_snapshot,
            floor_order=floor.display_order,
            room_id=room.id,
            room_number=stay.room_name_snapshot,
            stay_purpose=stay.stay_purpose,
            actual_check_in_at=stay.actual_check_in_at,
            actual_check_out_at=stay.actual_check_out_at,
            stay_status=stay.status,
            checkout_requested_actual_at=stay.checkout_requested_actual_at,
            cohort_id=stay.cohort_id,
            cohort_name=cohort.name if cohort is not None else None,
            visit_reason=stay.visit_reason,
            has_verified_phone=bool(has_verified_phone),
            building_code=building.code,
            document_type=person.document_type,
        )

    def _guardian_candidates(
        self,
        student_ids: Sequence[UUID],
    ) -> list[GuardianCandidateRecord]:
        if not student_ids:
            return []
        rows = self.db.execute(
            select(GuardianRelationship, Person)
            .join(Person, Person.id == GuardianRelationship.guardian_person_id)
            .where(GuardianRelationship.student_person_id.in_(tuple(student_ids)))
            .order_by(
                GuardianRelationship.student_person_id,
                Person.full_name,
                GuardianRelationship.guardian_person_id,
                GuardianRelationship.id,
            )
        ).all()
        return [
            GuardianCandidateRecord(
                relationship_id=relationship.id,
                student_id=relationship.student_person_id,
                guardian_id=guardian.id,
                valid_from=relationship.valid_from,
                valid_until=relationship.valid_until,
                ended_at=relationship.ended_at,
                guardian_name=guardian.full_name,
                guardian_phone_ciphertext=guardian.phone_ciphertext,
                guardian_identity_ciphertext=guardian.identity_ciphertext,
                guardian_sex=guardian.sex,
                guardian_date_of_birth=guardian.date_of_birth,
                guardian_document_type=guardian.document_type,
            )
            for relationship, guardian in rows
        ]
