"""Frozen source and one-way import contracts; fixture tokens are synthetic."""

import importlib
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.operations import cloud_auth_requests as requests
from app.operations import cloud_auth_schemas as schemas
from app.operations import cloud_auth_state as state

NOW = datetime(2026, 10, 9, tzinfo=UTC)


def test_legacy_create_defaults_to_own_app_and_unknown_mode_is_rejected():
    assert requests.CloudSessionCreate(operation_id=uuid4()).mode == "own_app"
    with pytest.raises(ValidationError):
        requests.CloudSessionCreate(operation_id=uuid4(), mode="automatic")


def test_import_tokens_are_trimmed_bounded_and_never_represented():
    command = requests.CloudImportCommand(
        operation_id=uuid4(),
        expected_revision=1,
        access_token="  " + "a" * 4096 + "  ",
        refresh_token="fixture-refresh",
    )
    assert command.access_token.get_secret_value() == "a" * 4096
    assert "fixture-refresh" not in repr(command)
    assert "a" * 64 not in repr(command)


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "a" * 4097,
        "inner space",
        "a\nb",
        "a\x00b",
        "https://example.invalid/callback?access_token=fixture-secret",
        "<html>fixture</html>",
        '{"access_token":"fixture-secret"}',
        "UID=fixture-secret",
        "Cookie:fixture-secret",
        "AppKey=fixture-secret",
        "123456789",
        "a\u200bb",
        123,
        {"token": "fixture-secret"},
    ],
)
def test_mispasted_import_is_rejected_without_secret_in_validation_exception(bad):
    with pytest.raises(ValidationError) as error:
        requests.CloudImportCommand(
            operation_id=uuid4(),
            expected_revision=1,
            access_token=bad,
            refresh_token="fixture-refresh",
        )
    assert "fixture-secret" not in str(error.value)
    assert "fixture-refresh" not in str(error.value)
    assert "input_value" not in str(error.value)


def test_unknown_expiry_only_baidu_builtin_and_legacy_grant_remains_strict():
    values = {
        "access_token": "fixture-access",
        "refresh_token": "fixture-refresh",
        "expires_at": None,
        "expiry_source": "unreported",
        "refreshed_at": NOW,
        "provider": "baidu",
        "mode": "openlist_builtin_import",
    }
    grant = schemas.ImportedTokenGrant(**values)
    assert grant.expires_at is None
    assert "fixture-access" not in repr(grant)
    for update in ({"provider": "115"}, {"mode": "own_app"}, {"expiry_source": "provider"}):
        with pytest.raises(ValidationError):
            schemas.ImportedTokenGrant(**{**values, **update})
    with pytest.raises(ValidationError):
        schemas.TokenGrant(
            access_token="fixture-access", refresh_token="fixture-refresh", expires_at=None
        )
    sys.path.insert(0, str(Path(__file__).resolve().parents[5] / "scripts"))
    private = importlib.import_module("cloud_auth.provider_types")
    assert private.ImportedTokenGrant(**values).expires_at is None
    with pytest.raises(private.ProviderError):
        private.ImportedTokenGrant(**{**values, "provider": "115"})


def test_source_revisions_are_not_candidate_directory_revisions():
    assert hasattr(schemas, "AuthSource"), "frozen authorization source is missing"
    assert (
        schemas.AuthSource(
            mode="own_app", auth_config_id="own-app:115", auth_config_revision=7, app_revision=7
        ).app_revision
        == 7
    )
    for values in (
        {
            "mode": "own_app",
            "auth_config_id": "own-app:115",
            "auth_config_revision": 7,
            "app_revision": 6,
        },
        {
            "mode": "openlist_builtin_import",
            "auth_config_id": "openlist-builtin-import-v1:115",
            "auth_config_revision": 2,
            "app_revision": None,
        },
        {
            "mode": "openlist_builtin_import",
            "auth_config_id": "openlist-builtin-import-v1:quark",
            "auth_config_revision": 1,
            "app_revision": None,
        },
    ):
        with pytest.raises(ValidationError):
            schemas.AuthSource(**values)


def test_fence_explicit_version_preserves_only_actual_legacy_format():
    legacy = {
        "session_id": uuid4(),
        "session_revision": 1,
        "app_revision": 7,
        "target_revision": 0,
        "lease_id": uuid4(),
    }
    assert schemas.AuthFence(**legacy).protocol_version == 1
    with pytest.raises(ValidationError):
        schemas.AuthFence(**legacy, mode="openlist_builtin_import")
    fence = schemas.AuthFence(
        **{**legacy, "app_revision": None},
        protocol_version=2,
        mode="openlist_builtin_import",
        auth_config_id="openlist-builtin-import-v1:baidu",
        auth_config_revision=1,
    )
    assert fence.app_revision is None
    with pytest.raises(ValidationError):
        schemas.AuthFence(**{**legacy, "app_revision": None}, protocol_version=2)


def test_import_transitions_and_mode_bound_aad_do_not_change_legacy_ciphertext_context():
    record = uuid4()
    assert state.aad("baidu", record, "token") == f"cloud_auth:baidu:{record}:token"
    builtin = state.aad(
        "baidu", record, "token", mode="openlist_builtin_import", protocol_version=2
    )
    owned = state.aad("baidu", record, "token", mode="own_app", protocol_version=2)
    assert builtin != owned and builtin != state.aad("baidu", record, "token")
    deadline = NOW + timedelta(minutes=10)
    assert (
        state.transition("creating", "import_created", now=NOW, expires_at=deadline)
        == "awaiting_import"
    )
    assert (
        state.transition("awaiting_import", "verify_import", now=NOW, expires_at=deadline)
        == "verifying_import"
    )
    assert (
        state.transition("verifying_import", "authorized", now=NOW, expires_at=deadline)
        == "authorized"
    )


def test_public_import_view_is_secret_free_and_does_not_invent_identity_verification_time():
    session = schemas.CloudAuthSessionView(
        id=uuid4(),
        provider="baidu",
        mode="openlist_builtin_import",
        state="awaiting_import",
        revision=1,
        expires_at=NOW + timedelta(minutes=10),
    )
    assert (
        session.verified_at is None
        and session.token_expires_at is None
        and session.expiry_source is None
    )
    view = schemas.CloudImportOperationView(
        operation_id=uuid4(), status="pending", session=session, error_code=None
    )
    assert (
        "access_token" not in view.model_dump_json()
        and "refresh_token" not in view.model_dump_json()
    )


def test_frozen_source_current_uses_real_application_revision_and_fixed_builtin_source():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.core.errors import ProblemError
    from app.operations.cloud_auth_models import CloudAuthApplication, CloudAuthSession

    assert importlib.util.find_spec("app.operations.cloud_auth_source") is not None, (
        "source checker missing"
    )
    source = importlib.import_module("app.operations.cloud_auth_source")
    engine = create_engine("sqlite://")
    CloudAuthApplication.__table__.create(engine)
    with Session(engine) as db:
        app = CloudAuthApplication(
            provider="115",
            client_id="fixture-app",
            secret_ciphertext="fixture-envelope",
            revision=7,
        )
        db.add(app)
        db.flush()
        row = CloudAuthSession(
            provider="115",
            mode="own_app",
            protocol_version=2,
            application_provider="115",
            auth_config_id="own-app:115",
            auth_config_revision=7,
            app_revision=7,
        )
        source.require_auth_source_current(db, row)
        app.revision = 8
        db.flush()
        with pytest.raises(ProblemError):
            source.require_auth_source_current(db, row)
        builtin = CloudAuthSession(
            provider="baidu",
            mode="openlist_builtin_import",
            protocol_version=2,
            application_provider=None,
            auth_config_id="openlist-builtin-import-v1:baidu",
            auth_config_revision=1,
            app_revision=None,
        )
        source.require_auth_source_current(db, builtin)
        builtin.auth_config_revision = 2
        with pytest.raises(ProblemError):
            source.require_auth_source_current(db, builtin)
    engine.dispose()
