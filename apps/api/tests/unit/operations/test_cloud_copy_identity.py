"""Private copy claims bind managed credentials to a registered connector UUID."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.operations import cloud_schemas
from app.operations.cloud_auth_models import CloudAuthCandidate, CloudConnectorState
from app.operations.cloud_models import CloudBackupJob, CloudBackupTarget
from app.operations.cloud_service import CloudBackupService
from app.security.encryption import EnvelopeCipher


@pytest.fixture
def copy_db():
    engine = create_engine("sqlite://")
    for model in (CloudAuthCandidate, CloudConnectorState, CloudBackupTarget, CloudBackupJob):
        model.__table__.create(engine)
    with Session(engine) as db:
        yield db
    engine.dispose()


def test_private_managed_claim_returns_exact_candidate_connector_identity(copy_db, monkeypatch):
    db = copy_db
    # The heartbeat uses PostgreSQL-only regex constraints, unrelated to the
    # credential/connector handoff exercised with real ORM reads below.
    monkeypatch.setattr(CloudBackupService, "_heartbeat", lambda self: None)
    cipher = EnvelopeCipher(bytes(range(32)))
    connector = CloudConnectorState(id=uuid4(), registered=True)
    candidate = CloudAuthCandidate(
        id=uuid4(),
        session_id=uuid4(),
        provider="baidu",
        # This isolated copy-routing fixture has no authorization-session table;
        # freeze a valid legacy source rather than invoking linked insert defaults.
        mode="own_app",
        protocol_version=1,
        auth_config_id="own-app:baidu",
        auth_config_revision=1,
        app_revision=1,
        operation_id=uuid4(),
        connector_id=connector.id,
        mount_path="/erp-candidates/test",
        state="active",
        config_revision=1,
    )
    target = CloudBackupTarget(
        provider="baidu",
        revision=1,
        enabled=False,
        remote_path="/",
        managed_candidate_id=candidate.id,
        username_ciphertext=cipher.encrypt("dedicated", field_name="cloud-backup:baidu:username"),
        password_ciphertext=cipher.encrypt(
            "private-password", field_name="cloud-backup:baidu:password"
        ),
        connection_status="ok",
        authorization_status="valid",
    )
    job = CloudBackupJob(
        id=uuid4(),
        provider="baidu",
        revision=1,
        kind="copy",
        status="queued",
        created_at=datetime.now(UTC),
    )
    db.add_all([connector, candidate, target, job])
    db.commit()
    claimed = CloudBackupService(db, cipher=cipher).claim()["job"]
    assert claimed["managed_auth"] is True
    assert claimed["connector_id"] == str(connector.id)
    assert "connector_url" not in claimed


def test_private_manual_claim_has_no_managed_connector_identity(copy_db, monkeypatch):
    monkeypatch.setattr(CloudBackupService, "_heartbeat", lambda self: None)
    db = copy_db
    cipher = EnvelopeCipher(bytes(range(32)))
    target = CloudBackupTarget(
        provider="quark",
        revision=1,
        enabled=False,
        remote_path="/manual",
        connection_status="untested",
        username_ciphertext=cipher.encrypt("manual", field_name="cloud-backup:quark:username"),
        password_ciphertext=cipher.encrypt("secret", field_name="cloud-backup:quark:password"),
        authorization_status="manual",
    )
    job = CloudBackupJob(
        id=uuid4(),
        provider="quark",
        revision=1,
        kind="test",
        status="queued",
        created_at=datetime.now(UTC),
    )
    db.add_all([target, job])
    db.commit()
    claimed = CloudBackupService(db, cipher=cipher).claim()["job"]
    assert claimed["managed_auth"] is False and claimed["connector_id"] is None
    assert "connector_url" not in claimed


@pytest.mark.parametrize(
    "payload",
    [
        {"managed_auth": "true", "connector_id": str(uuid4())},
        {"managed_auth": 1, "connector_id": str(uuid4())},
        {"managed_auth": True},
        {"managed_auth": False, "connector_id": str(uuid4())},
        {
            "managed_auth": True,
            "connector_id": str(uuid4()),
            "connector_url": "http://public.invalid",
        },
    ],
)
def test_private_routing_schema_rejects_coercion_missing_identity_and_arbitrary_endpoint(payload):
    model = getattr(cloud_schemas, "CloudCopyRouting", None)
    assert model is not None, "private managed routing requires a strict identity schema"
    with pytest.raises(ValidationError):
        model.model_validate(payload)
