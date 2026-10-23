from uuid import UUID


class TalentTestError(ValueError):
    """Base error for deterministic talent-test contract failures."""


class TalentNotFound(TalentTestError):
    """A missing or invisible talent resource; never disclose which."""


class TalentForbidden(TalentTestError):
    """The actor cannot perform a talent-test operation."""


class UnknownCatalogVersion(TalentTestError):
    def __init__(self, catalog_version: str) -> None:
        super().__init__(f"unknown talent-test catalog version: {catalog_version}")
        self.catalog_version = catalog_version


class UnknownTestId(TalentTestError):
    def __init__(self, test_id: str) -> None:
        super().__init__(f"unknown talent-test id: {test_id}")
        self.test_id = test_id


class TalentConflict(TalentTestError):
    """A replay mismatch or an operation incompatible with persisted state."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "talent_operation_conflict",
        active_session_id: UUID | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.active_session_id = active_session_id
