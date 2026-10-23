class PortalServiceError(ValueError):
    code = "portal_service_error"


class PortalPermissionDenied(PortalServiceError):
    code = "permission_denied"


class CoachCohortNotFound(PortalServiceError):
    code = "coach_cohort_not_found"


class FamilyStudentNotFound(PortalServiceError):
    code = "family_student_not_found"
