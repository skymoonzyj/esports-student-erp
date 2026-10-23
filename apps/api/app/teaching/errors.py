from __future__ import annotations


class TeachingServiceError(ValueError):
    code = "teaching_service_error"


class ProjectNotFound(TeachingServiceError):
    code = "project_not_found"


class ProfessionalTeamNotFound(TeachingServiceError):
    code = "professional_team_not_found"


class DuplicateProject(TeachingServiceError):
    code = "duplicate_project"


class DuplicateProfessionalTeam(TeachingServiceError):
    code = "duplicate_professional_team"


class InactiveProject(TeachingServiceError):
    code = "inactive_project"


class TeamProjectImmutable(TeachingServiceError):
    code = "team_project_immutable"


class CohortNotFound(TeachingServiceError):
    code = "cohort_not_found"


class InvalidCohortTransitionError(TeachingServiceError):
    code = "invalid_cohort_transition"


class PrimaryCoachRequired(TeachingServiceError):
    code = "primary_coach_required"


class CohortScheduleLocked(TeachingServiceError):
    code = "cohort_schedule_locked"


class CohortArchived(TeachingServiceError):
    code = "cohort_archived"


class ClassNotFound(TeachingServiceError):
    code = "class_not_found"


class ClassHasActiveEnrollments(TeachingServiceError):
    code = "class_has_active_enrollments"


class DuplicateClass(TeachingServiceError):
    code = "duplicate_class"


class StandardClassTeamForbidden(TeachingServiceError):
    code = "standard_class_team_forbidden"


class EliteTeamRequired(TeachingServiceError):
    code = "elite_team_required"


class InactiveProfessionalTeam(TeachingServiceError):
    code = "inactive_professional_team"


class ProjectTeamMismatch(TeachingServiceError):
    code = "project_team_mismatch"


class CoachNotEligible(TeachingServiceError):
    code = "coach_not_eligible"

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class PrimaryCoachConflict(TeachingServiceError):
    code = "primary_coach_conflict"


class CoachAssignmentConflict(TeachingServiceError):
    code = "coach_assignment_conflict"


class CoachAssignmentNotFound(TeachingServiceError):
    code = "coach_assignment_not_found"


class CoachAssignmentAlreadyEnded(TeachingServiceError):
    code = "coach_assignment_already_ended"
