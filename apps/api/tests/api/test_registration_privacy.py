from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.registration.router as registration_router
from app.audit.models import AuditEvent
from app.auth.models import AuthSession, UserAccount
from app.registration.delivery import CapturingVerificationSender
from app.registration.identifiers import normalize_registration_identifier
from app.registration.models import (
    RegistrationApplication,
    RegistrationVerificationChallenge,
)
from app.security.encryption import EnvelopeCipher, LookupDigest


def test_start_registration_runs_bounded_cleanup_before_creating_application(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prune_calls: list[tuple[Session, int]] = []
    identifier_calls: list[tuple[Session, str]] = []

    class CleanupService:
        def __init__(self, db: Session) -> None:
            self.db = db

        def prune(self, *, limit: int) -> int:
            prune_calls.append((self.db, limit))
            return 0

        def prune_identifier(self, *, identifier_digest: str) -> int:
            identifier_calls.append((self.db, identifier_digest))
            return 0

    monkeypatch.setattr(
        registration_router,
        "RegistrationCleanupService",
        CleanupService,
    )

    response = db_client.post(
        "/api/v1/registration/applications",
        json={
            "primary_purpose": "student",
            "identifier_kind": "phone",
            "identifier": "13800000739",
        },
    )

    assert response.status_code == 201
    assert len(prune_calls) == 1
    assert prune_calls[0][1] == 25
    assert len(identifier_calls) == 1
    assert identifier_calls[0][0] is prune_calls[0][0]
    assert len(identifier_calls[0][1]) == 64


def test_create_helper_never_global_prunes_inside_caller_owned_transaction(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, bool]] = []

    class CleanupService:
        def __init__(self, session: Session) -> None:
            self.db = session

        def prune(self, *, limit: int) -> int:
            assert limit == 25
            calls.append(("global", self.db.in_transaction()))
            return 0

        def prune_identifier(self, *, identifier_digest: str) -> int:
            assert len(identifier_digest) == 64
            calls.append(("identifier", self.db.in_transaction()))
            return 0

    monkeypatch.setattr(
        registration_router,
        "RegistrationCleanupService",
        CleanupService,
    )

    with db.begin():
        registration_router._create_registration_application(
            db=db,
            cipher=EnvelopeCipher(bytes(range(32))),
            lookup=LookupDigest(bytes(range(32))),
            primary_purpose="student",
            identifier=normalize_registration_identifier(
                "email",
                "owned-transaction@example.com",
            ),
        )

    assert calls == [("identifier", True)]


def test_start_registration_expires_stale_identifier_before_reusing_it(
    db_client: TestClient,
    db: Session,
) -> None:
    payload = {
        "primary_purpose": "student",
        "identifier_kind": "phone",
        "identifier": "13800000738",
    }
    first = db_client.post("/api/v1/registration/applications", json=payload)
    assert first.status_code == 201
    first_id = UUID(first.json()["application_id"])
    stale = db.get(RegistrationApplication, first_id)
    assert stale is not None
    stale.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()

    second = db_client.post("/api/v1/registration/applications", json=payload)

    assert second.status_code == 201
    assert second.json()["application_id"] != str(first_id)
    db.expire_all()
    stale = db.get(RegistrationApplication, first_id)
    assert stale is not None
    assert stale.state == "expired"
    assert stale.verified_identifier_digest is None
    assert stale.verified_identifier_ciphertext is None


def test_start_registration_reuses_stale_identifier_beyond_global_cleanup_batch(
    db_client: TestClient,
    db: Session,
) -> None:
    payload = {
        "primary_purpose": "student",
        "identifier_kind": "phone",
        "identifier": "13800000737",
    }
    first = db_client.post("/api/v1/registration/applications", json=payload)
    assert first.status_code == 201
    first_id = UUID(first.json()["application_id"])
    stale = db.get(RegistrationApplication, first_id)
    assert stale is not None
    stale.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    for index in range(25):
        application_id = UUID(f"72000000-0000-4000-8000-{index:012d}")
        db.add(
            RegistrationApplication(
                id=application_id,
                primary_purpose="student",
                state="draft",
                verified_identifier_kind="phone",
                verified_identifier_digest=f"{application_id.int:064x}"[-64:],
                verified_identifier_ciphertext=f"encrypted-backlog-{index}",
                expires_at=datetime.now(UTC) - timedelta(days=2),
            )
        )
    db.commit()

    second = db_client.post("/api/v1/registration/applications", json=payload)

    assert second.status_code == 201
    assert second.json()["application_id"] != str(first_id)
    db.expire_all()
    stale = db.get(RegistrationApplication, first_id)
    assert stale is not None
    assert stale.state == "expired"
    assert stale.verified_identifier_digest is None


def test_start_registration_expires_stale_verified_identifier_beyond_cleanup_batch(
    db_client: TestClient,
    db: Session,
) -> None:
    payload = {
        "primary_purpose": "student",
        "identifier_kind": "phone",
        "identifier": "13800000736",
    }
    first = db_client.post("/api/v1/registration/applications", json=payload)
    assert first.status_code == 201
    first_id = UUID(first.json()["application_id"])
    stale = db.get(RegistrationApplication, first_id)
    assert stale is not None
    stale.state = "contact_verified"
    stale.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    for index in range(25, 50):
        application_id = UUID(f"72000000-0000-4000-8000-{index:012d}")
        db.add(
            RegistrationApplication(
                id=application_id,
                primary_purpose="student",
                state="draft",
                verified_identifier_kind="phone",
                verified_identifier_digest=f"{application_id.int:064x}"[-64:],
                verified_identifier_ciphertext=f"encrypted-verified-backlog-{index}",
                expires_at=datetime.now(UTC) - timedelta(days=2),
            )
        )
    db.commit()

    second = db_client.post("/api/v1/registration/applications", json=payload)

    assert second.status_code == 201
    assert second.json()["application_id"] != str(first_id)
    db.expire_all()
    stale = db.get(RegistrationApplication, first_id)
    assert stale is not None
    assert stale.state == "expired"
    assert stale.verified_identifier_digest is None


def make_cn_id(birth: str, sequence: int) -> str:
    body = f"110105{birth}{sequence:03d}"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    checks = ("1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2")
    return body + checks[sum(int(a) * b for a, b in zip(body, weights)) % 11]


@dataclass(frozen=True)
class FinalizationScenario:
    phone: str
    full_name: str
    identity_number: str
    sex: str
    birth_date: str
    expected_action: str

    def finalize(
        self,
        client: TestClient,
        sender: CapturingVerificationSender,
    ):
        started = client.post(
            "/api/v1/registration/applications",
            json={
                "primary_purpose": "student",
                "identifier_kind": "phone",
                "identifier": self.phone,
            },
        )
        assert started.status_code == 201
        application_id = UUID(started.json()["application_id"])
        challenged = client.post(
            f"/api/v1/registration/applications/{application_id}/challenge"
        )
        assert challenged.status_code == 201
        challenge_id = UUID(challenged.json()["challenge_id"])
        verified = client.post(
            f"/api/v1/registration/applications/{application_id}/verify",
            json={
                "challenge_id": str(challenge_id),
                "code": sender.code_for(challenge_id),
            },
        )
        assert verified.status_code == 200
        csrf = verified.json()["csrf_token"]
        completed = client.patch(
            "/api/v1/registration/me",
            headers={"X-Registration-CSRF-Token": csrf},
            json={
                "password": "Privacy731PassAa",
                "full_name": self.full_name,
                "identity_number": self.identity_number,
                "sex": self.sex,
                "date_of_birth": self.birth_date,
                "consent_version": "2026-08",
                "purpose_details": {},
            },
        )
        assert completed.status_code == 200
        return application_id, client.post(
            "/api/v1/registration/me/finalize",
            headers={"X-Registration-CSRF-Token": csrf},
        )


@pytest.fixture
def new_person_scenario() -> FinalizationScenario:
    return FinalizationScenario(
        phone="13800000731",
        full_name="全新隐私注册人",
        identity_number=make_cn_id("19900102", 731),
        sex="male",
        birth_date="1990-01-02",
        expected_action="login",
    )


@pytest.fixture
def existing_account_scenario(platform_users) -> FinalizationScenario:
    del platform_users
    return FinalizationScenario(
        phone="13800000004",
        full_name="自助账号学员",
        identity_number=make_cn_id("19950102", 112),
        sex="female",
        birth_date="1995-01-02",
        expected_action="login_or_reset",
    )


@pytest.fixture
def disabled_account_scenario(
    platform_users,
    db: Session,
) -> FinalizationScenario:
    account = platform_users["accounts"]["self_student"]
    account.status = "disabled"
    db.commit()
    return FinalizationScenario(
        phone="13800000004",
        full_name="自助账号学员",
        identity_number=make_cn_id("19950102", 112),
        sex="female",
        birth_date="1995-01-02",
        expected_action="login_or_reset",
    )


@pytest.fixture
def conflict_scenario(platform_users) -> FinalizationScenario:
    del platform_users
    return FinalizationScenario(
        phone="13800000004",
        full_name="待开通账号人员",
        identity_number=make_cn_id("19900102", 110),
        sex="female",
        birth_date="1990-01-02",
        expected_action="await_review",
    )


@pytest.mark.parametrize(
    "fixture_name",
    [
        "new_person_scenario",
        "existing_account_scenario",
        "disabled_account_scenario",
        "conflict_scenario",
    ],
)
def test_public_finalization_does_not_disclose_account_state(
    db_client: TestClient,
    captured_sender: CapturingVerificationSender,
    db: Session,
    request: pytest.FixtureRequest,
    fixture_name: str,
) -> None:
    scenario: FinalizationScenario = request.getfixturevalue(fixture_name)

    application_id, response = scenario.finalize(db_client, captured_sender)

    assert response.status_code == 200
    assert set(response.json()) == {"state", "next_action", "request_id"}
    assert response.json()["next_action"] == scenario.expected_action
    assert "person_id" not in response.text
    assert "account_id" not in response.text
    assert scenario.identity_number not in response.text
    assert scenario.phone not in response.text
    assert "erp_session=" not in response.headers.get("set-cookie", "")
    assert db.scalar(select(func.count(AuthSession.id))) == 0
    for event in db.scalars(
        select(AuditEvent).where(AuditEvent.resource_id == application_id)
    ):
        assert scenario.identity_number not in str(event.event_metadata)
        assert scenario.phone not in str(event.event_metadata)


def test_disabled_and_active_existing_accounts_have_same_public_result(
    db_client: TestClient,
    captured_sender: CapturingVerificationSender,
    platform_users,
    db: Session,
) -> None:
    scenario = FinalizationScenario(
        phone="13800000004",
        full_name="自助账号学员",
        identity_number=make_cn_id("19950102", 112),
        sex="female",
        birth_date="1995-01-02",
        expected_action="login_or_reset",
    )
    _first_id, active = scenario.finalize(db_client, captured_sender)
    account = platform_users["accounts"]["self_student"]
    account.status = "disabled"
    for challenge in db.scalars(select(RegistrationVerificationChallenge)):
        challenge.created_at = datetime.now(UTC) - timedelta(minutes=2)
        challenge.last_sent_at = datetime.now(UTC) - timedelta(minutes=2)
    db.commit()
    db_client.cookies.clear()
    _second_id, disabled = scenario.finalize(db_client, captured_sender)

    assert active.status_code == disabled.status_code == 200
    assert {
        key: value for key, value in active.json().items() if key != "request_id"
    } == {
        key: value for key, value in disabled.json().items() if key != "request_id"
    }
    assert db.scalar(select(func.count(UserAccount.id))) == len(
        platform_users["accounts"]
    )
