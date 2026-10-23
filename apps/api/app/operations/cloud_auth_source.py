"""Validate a frozen source independently of mutable candidate directory revisions."""

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.operations.cloud_auth_errors import auth_problem
from app.operations.cloud_auth_models import CloudAuthApplication, CloudAuthSession
from app.operations.cloud_auth_schemas import AuthSource


def require_auth_source_frozen(row: CloudAuthSession) -> None:
    """Validate historical provenance without consulting mutable application state.

    Retirement callers must separately prove the exact original candidate/target
    linkage. This helper is not an authorization or stale-version bypass.
    """
    try:
        source = AuthSource(
            mode=row.mode,
            auth_config_id=row.auth_config_id,
            auth_config_revision=row.auth_config_revision,
            app_revision=row.app_revision,
        )
    except ValidationError:
        raise auth_problem("cloud_auth_revision_changed") from None
    expected_id = (
        "own-app:" if source.mode == "own_app" else "openlist-builtin-import-v1:"
    ) + row.provider
    if source.auth_config_id != expected_id:
        raise auth_problem("cloud_auth_revision_changed")
    if (
        type(row.protocol_version) is not int
        or row.protocol_version not in (1, 2)
        or (row.protocol_version == 1 and source.mode != "own_app")
    ):
        raise auth_problem("cloud_auth_revision_changed")
    if source.mode == "openlist_builtin_import":
        if row.application_provider is not None:
            raise auth_problem("cloud_auth_revision_changed")
        return
    if row.application_provider != row.provider:
        raise auth_problem("cloud_auth_revision_changed")


def require_auth_source_current(db: Session, row: CloudAuthSession) -> None:
    require_auth_source_frozen(row)
    if row.mode == "openlist_builtin_import":
        return
    app = db.get(CloudAuthApplication, row.application_provider, populate_existing=True)
    if app is None or app.revision != row.app_revision:
        raise auth_problem("cloud_auth_revision_changed")
