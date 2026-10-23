from __future__ import annotations

from datetime import date
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.admissions.errors import (
    DuplicateEnrollmentApplication,
    EnrollmentApplicationForbidden,
    EnrollmentCoachNotAvailable,
    EnrollmentCoachRequired,
    EnrollmentProgramNotFound,
    EnrollmentProjectNotFound,
)
from app.admissions.models import AdmissionCase, EnrollmentProgram
from app.admissions.payment_service import PaymentService
from app.admissions.schemas import (
    AdmissionCaseStatus,
    EnrollmentApplicantStudentResponse,
    EnrollmentApplicationCreate,
    EnrollmentApplicationResponse,
    EnrollmentCatalogResponse,
    EnrollmentCoachResponse,
    EnrollmentPaymentPreference,
    EnrollmentProgramCategory,
    EnrollmentProgramResponse,
    EnrollmentProgramScheduleItem,
    EnrollmentProjectResponse,
)
from app.audit.service import AuditService
from app.core.transactions import write_transaction
from app.identity.models import Person
from app.portal.errors import PortalPermissionDenied
from app.portal.learning_service import PortalLearningService
from app.portal.schemas import LearningStudentSummary
from app.rbac.service import RbacService
from app.teaching.coach_eligibility import CoachEligibility
from app.teaching.models import GameProject, ProjectCoachAssignment


class EnrollmentApplicationService:
    permission = "enrollment_applications.create"

    def __init__(self, db: Session, *, payment_service: PaymentService) -> None:
        self.db = db
        self.payment_service = payment_service
        self.rbac = RbacService(db)

    def _authorized_students(
        self,
        *,
        user_id: UUID,
        actor_person_id: UUID,
        reference_date: date,
    ) -> list[LearningStudentSummary]:
        try:
            candidates = (
                PortalLearningService(self.db)
                .list_students(
                    user_id=user_id,
                    actor_person_id=actor_person_id,
                    reference_date=reference_date,
                )
                .items
            )
        except PortalPermissionDenied as exc:
            raise EnrollmentApplicationForbidden("student or guardian access is required") from exc
        tokens_by_student = self.rbac.person_resource_tokens_many(
            actor_person_id=actor_person_id,
            target_person_ids={item.student_person_id for item in candidates},
            reference_date=reference_date,
        )
        decisions = self.rbac.decision_context(user_id)
        allowed = [
            item
            for item in candidates
            if decisions.decide(
                self.permission,
                tokens_by_student[item.student_person_id],
            ).allowed
        ]
        if not allowed:
            raise EnrollmentApplicationForbidden(
                "no authorized student is available for enrollment"
            )
        return allowed

    @staticmethod
    def _student_response(
        student: LearningStudentSummary,
    ) -> EnrollmentApplicantStudentResponse:
        return EnrollmentApplicantStudentResponse(
            student_person_id=student.student_person_id,
            student_name=student.student_name,
            student_number=student.student_number,
            age_status=student.age_status,
            access_source=student.access_source,
        )

    @staticmethod
    def _program_response(program: EnrollmentProgram) -> EnrollmentProgramResponse:
        return EnrollmentProgramResponse(
            id=program.id,
            code=program.code,
            name=program.name,
            category=cast(EnrollmentProgramCategory, program.category),
            duration_label=program.duration_label,
            price_cents=program.price_cents,
            price_note=program.price_note,
            inclusion_note=program.inclusion_note,
            summary=program.summary,
            target_audience=program.target_audience,
            highlights=list(program.highlights),
            schedule=[
                EnrollmentProgramScheduleItem.model_validate(item) for item in program.schedule
            ],
            eligibility_note=program.eligibility_note,
        )

    def _application_response(
        self,
        admission_case: AdmissionCase,
        *,
        student_name: str,
    ) -> EnrollmentApplicationResponse:
        if (
            admission_case.student_person_id is None
            or admission_case.selected_project_id is None
            or admission_case.selected_program_id is None
            or admission_case.project_name_snapshot is None
            or admission_case.program_name_snapshot is None
            or admission_case.payment_preference is None
            or admission_case.agreed_tuition_cents is None
        ):
            raise RuntimeError("self-service enrollment application is incomplete")
        payment = self.payment_service.payment_summary(admission_case.id)
        return EnrollmentApplicationResponse(
            id=admission_case.id,
            order_number=admission_case.order_number,
            student_person_id=admission_case.student_person_id,
            student_name=student_name,
            status=cast(AdmissionCaseStatus, admission_case.status),
            project_id=admission_case.selected_project_id,
            project_name=admission_case.project_name_snapshot,
            program_id=admission_case.selected_program_id,
            program_name=admission_case.program_name_snapshot,
            coach_person_id=admission_case.selected_coach_person_id,
            coach_name=admission_case.coach_name_snapshot,
            price_cents=admission_case.agreed_tuition_cents,
            payment_preference=cast(
                EnrollmentPaymentPreference,
                admission_case.payment_preference,
            ),
            payment_status=payment.status,
            created_at=admission_case.created_at,
            updated_at=admission_case.updated_at,
        )

    def get_catalog(
        self,
        *,
        user_id: UUID,
        actor_person_id: UUID,
        reference_date: date,
    ) -> EnrollmentCatalogResponse:
        students = self._authorized_students(
            user_id=user_id,
            actor_person_id=actor_person_id,
            reference_date=reference_date,
        )
        student_ids = [item.student_person_id for item in students]
        projects = list(
            self.db.scalars(
                select(GameProject)
                .where(GameProject.is_active.is_(True))
                .order_by(GameProject.sort_order, GameProject.name, GameProject.id)
            )
        )
        coaches_by_project: dict[UUID, list[EnrollmentCoachResponse]] = {
            project.id: [] for project in projects
        }
        if coaches_by_project:
            for project_id, person_id, full_name in self.db.execute(
                select(
                    ProjectCoachAssignment.project_id,
                    Person.id,
                    Person.full_name,
                )
                .join(Person, Person.id == ProjectCoachAssignment.coach_person_id)
                .where(
                    ProjectCoachAssignment.project_id.in_(coaches_by_project),
                    ProjectCoachAssignment.is_active.is_(True),
                    Person.deleted_at.is_(None),
                    CoachEligibility.eligible_person_predicate(Person.id),
                )
                .order_by(
                    ProjectCoachAssignment.project_id,
                    Person.full_name,
                    Person.id,
                )
            ).tuples():
                coaches_by_project[project_id].append(
                    EnrollmentCoachResponse(
                        person_id=person_id,
                        full_name=full_name,
                    )
                )
        programs = list(
            self.db.scalars(
                select(EnrollmentProgram)
                .where(EnrollmentProgram.is_active.is_(True))
                .order_by(
                    EnrollmentProgram.sort_order,
                    EnrollmentProgram.name,
                    EnrollmentProgram.id,
                )
            )
        )
        application_rows = list(
            self.db.execute(
                select(AdmissionCase, Person.full_name)
                .join(Person, Person.id == AdmissionCase.student_person_id)
                .where(
                    AdmissionCase.application_source == "self_service",
                    AdmissionCase.student_person_id.in_(student_ids),
                )
                .order_by(AdmissionCase.created_at.desc(), AdmissionCase.id.desc())
                .limit(20)
            )
        )
        return EnrollmentCatalogResponse(
            students=[self._student_response(item) for item in students],
            projects=[
                EnrollmentProjectResponse(
                    id=project.id,
                    name=project.name,
                    coaches=coaches_by_project[project.id],
                )
                for project in projects
            ],
            programs=[self._program_response(program) for program in programs],
            applications=[
                self._application_response(admission_case, student_name=student_name)
                for admission_case, student_name in application_rows
            ],
        )

    def create_application(
        self,
        input: EnrollmentApplicationCreate,
        *,
        user_id: UUID,
        actor_person_id: UUID,
        reference_date: date,
        request_id: str,
    ) -> EnrollmentApplicationResponse:
        try:
            with write_transaction(self.db):
                students = self._authorized_students(
                    user_id=user_id,
                    actor_person_id=actor_person_id,
                    reference_date=reference_date,
                )
                student = next(
                    (
                        item
                        for item in students
                        if item.student_person_id == input.student_person_id
                    ),
                    None,
                )
                if student is None:
                    raise EnrollmentApplicationForbidden("target student is not authorized")
                project = self.db.scalar(
                    select(GameProject)
                    .where(
                        GameProject.id == input.project_id,
                        GameProject.is_active.is_(True),
                    )
                    .with_for_update()
                )
                if project is None:
                    raise EnrollmentProjectNotFound("active project was not found")
                available_coaches = {
                    coach_person_id: coach_name
                    for coach_person_id, coach_name in self.db.execute(
                        select(
                            ProjectCoachAssignment.coach_person_id,
                            Person.full_name,
                        )
                        .join(Person, Person.id == ProjectCoachAssignment.coach_person_id)
                        .where(
                            ProjectCoachAssignment.project_id == project.id,
                            ProjectCoachAssignment.is_active.is_(True),
                            Person.deleted_at.is_(None),
                            CoachEligibility.eligible_person_predicate(Person.id),
                        )
                    ).tuples()
                }
                if available_coaches and input.coach_person_id is None:
                    raise EnrollmentCoachRequired("an assigned project coach must be selected")
                if (
                    input.coach_person_id is not None
                    and input.coach_person_id not in available_coaches
                ):
                    raise EnrollmentCoachNotAvailable(
                        "selected coach is not currently assigned to the project"
                    )
                coach_name = (
                    available_coaches[input.coach_person_id]
                    if input.coach_person_id is not None
                    else None
                )
                program = self.db.scalar(
                    select(EnrollmentProgram)
                    .where(
                        EnrollmentProgram.id == input.program_id,
                        EnrollmentProgram.is_active.is_(True),
                    )
                    .with_for_update()
                )
                if program is None:
                    raise EnrollmentProgramNotFound("active program was not found")
                duplicate_id = self.db.scalar(
                    select(AdmissionCase.id).where(
                        AdmissionCase.application_source == "self_service",
                        AdmissionCase.student_person_id == input.student_person_id,
                        AdmissionCase.selected_project_id == input.project_id,
                        AdmissionCase.selected_program_id == input.program_id,
                        AdmissionCase.status.in_(("pending_match", "ready")),
                    )
                )
                if duplicate_id is not None:
                    raise DuplicateEnrollmentApplication(
                        "an open application already exists for this choice"
                    )
                admission_case = AdmissionCase(
                    student_person_id=input.student_person_id,
                    status="ready",
                    agreed_tuition_cents=program.price_cents,
                    application_source="self_service",
                    selected_project_id=project.id,
                    selected_program_id=program.id,
                    selected_coach_person_id=input.coach_person_id,
                    project_name_snapshot=project.name,
                    program_name_snapshot=program.name,
                    coach_name_snapshot=coach_name,
                    payment_preference=input.payment_preference,
                    created_by_user_id=user_id,
                    updated_by_user_id=user_id,
                )
                self.db.add(admission_case)
                self.db.flush()
                AuditService(self.db).append(
                    actor_user_id=user_id,
                    action="admissions.enrollment_application.create",
                    resource_type="admission_case",
                    resource_id=admission_case.id,
                    purpose=None,
                    outcome="allowed",
                    request_id=request_id,
                    metadata={
                        "admission_case_id": str(admission_case.id),
                        "student_person_id": str(input.student_person_id),
                        "project_id": str(project.id),
                        "program_id": str(program.id),
                        "payment_preference": input.payment_preference,
                        **(
                            {"coach_person_id": str(input.coach_person_id)}
                            if input.coach_person_id is not None
                            else {}
                        ),
                    },
                )
                response = self._application_response(
                    admission_case,
                    student_name=student.student_name,
                )
        except IntegrityError as exc:
            constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if constraint == "uq_admission_cases_open_self_service_choice":
                raise DuplicateEnrollmentApplication(
                    "an open application already exists for this choice"
                ) from exc
            raise
        return response
