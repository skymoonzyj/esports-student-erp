from __future__ import annotations

import base64
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

TEST_KEY = base64.urlsafe_b64encode(bytes(range(32))).decode("ascii")
REGISTRATION_VERIFICATION_KEY = base64.urlsafe_b64encode(bytes(range(1, 33))).decode(
    "ascii"
)
REGISTRATION_SESSION_KEY = base64.urlsafe_b64encode(bytes(range(2, 34))).decode(
    "ascii"
)

# app.main exposes the ASGI application at import time. Give that development
# application safe throw-away values while individual tests inject Settings.
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://erp:erp@localhost:5432/erp",
)
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")
os.environ.setdefault("FIELD_ENCRYPTION_MASTER_KEY", TEST_KEY)
os.environ.setdefault("FIELD_LOOKUP_KEY", TEST_KEY)
os.environ.setdefault("SESSION_TOKEN_PEPPER", TEST_KEY)
os.environ.setdefault("REGISTRATION_VERIFICATION_PEPPER", REGISTRATION_VERIFICATION_KEY)
os.environ.setdefault("REGISTRATION_SESSION_TOKEN_PEPPER", REGISTRATION_SESSION_KEY)
os.environ.setdefault(
    "EVALUATION_TOOL_SERVICE_TOKEN",
    "evaluation-local-service-token-2026-change-me",
)
os.environ.setdefault(
    "EVALUATION_TOOL_WEB_SERVICE_TOKEN",
    "evaluation-local-web-service-token-2026-change-me",
)
os.environ.setdefault(
    "EVALUATION_TOOL_WORKER_SERVICE_TOKEN",
    "evaluation-local-worker-service-token-2026-change-me",
)

from app.cli.e2e_database_safety import reset_e2e_database_schema
from app.core.config import Settings
from app.core.database import get_db
from app.main import create_app
from app.registration.delivery import CapturingVerificationSender

API_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        app_env="test",
        database_url="postgresql+psycopg://erp:erp@localhost:5432/erp",
        session_cookie_secure=False,
        field_encryption_master_key=TEST_KEY,
        field_lookup_key=TEST_KEY,
        session_token_pepper=TEST_KEY,
        registration_verification_pepper=REGISTRATION_VERIFICATION_KEY,
        registration_session_token_pepper=REGISTRATION_SESSION_KEY,
        registration_phone_signup_enabled=True,
        evaluation_tool_service_token="evaluation-local-service-token-2026-change-me",
        evaluation_tool_web_service_token=(
            "evaluation-local-web-service-token-2026-change-me"
        ),
        evaluation_tool_worker_service_token=(
            "evaluation-local-worker-service-token-2026-change-me"
        ),
    )


@pytest.fixture
def client(test_settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(test_settings)) as value:
        yield value


@pytest.fixture
def captured_sender() -> CapturingVerificationSender:
    return CapturingVerificationSender(messages=[])


@pytest.fixture
def esports_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicitly enable every registered suite for isolated assessment tests."""
    from app.esports_assessments import catalog

    monkeypatch.setattr(
        catalog,
        "AVAILABLE_SUITES",
        frozenset({"pc-fps", "mobile-fps", "pc-moba", "mobile-moba"}),
    )


@pytest.fixture
def db_client(
    test_settings: Settings,
    db: Session,
    captured_sender: CapturingVerificationSender,
) -> Iterator[TestClient]:
    app = create_app(test_settings)
    app.state.registration_verification_sender = captured_sender

    def override_db() -> Iterator[Session]:
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as value:
        yield value


@pytest.fixture(scope="session")
def postgres_engine() -> Iterator[Engine]:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL tests")
    engine = reset_e2e_database_schema(database_url)

    alembic_config = Config(str(API_ROOT / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)
    alembic_config.attributes["database_url"] = database_url
    command.upgrade(alembic_config, "head")
    yield engine
    engine.dispose()


@pytest.fixture
def db(postgres_engine: Engine) -> Iterator[Session]:
    with postgres_engine.connect() as connection:
        transaction = connection.begin()
        with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
            yield session
        transaction.rollback()
