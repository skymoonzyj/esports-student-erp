from app.core.errors import ProblemError


class PersonnelIntakeError(ProblemError):
    """Safe public error code; never include decrypted identity/contact data."""

    def __init__(
        self,
        code: str = "personnel_intake_conflict",
        *,
        status_code: int = 409,
        status: int | None = None,
        detail: str = "资料存在冲突，请联系管理员核实",
    ) -> None:
        self.status_code = status if status is not None else status_code
        super().__init__(status=self.status_code, code=code, detail=detail)


class CredentialReservationConflict(PersonnelIntakeError):
    def __init__(self) -> None:
        super().__init__("personnel_credential_conflict", status_code=409)


IntakeError = PersonnelIntakeError
