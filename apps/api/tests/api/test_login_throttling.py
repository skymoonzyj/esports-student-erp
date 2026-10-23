from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_auth_service
from app.auth.models import AuthRateLimit, UserAccount
from app.auth.service import AuthService, InvalidCredentials
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService

NOW = datetime(2026, 9, 16, 8, 0, tzinfo=UTC)
WRONG_PASSWORD = "WrongLogin2026Aa"


def _service(db: Session, current_time: list[datetime]) -> AuthService:
    return AuthService(
        db,
        cipher=EnvelopeCipher(bytes(range(32))),
        lookup=LookupDigest(bytes(range(32))),
        passwords=PasswordService(),
        session_token_pepper=bytes(range(32)),
        session_ttl_minutes=480,
        clock=lambda: current_time[0],
    )


def _service_with_passwords(
    db: Session,
    current_time: list[datetime],
    passwords: PasswordService,
) -> AuthService:
    return AuthService(
        db,
        cipher=EnvelopeCipher(bytes(range(32))),
        lookup=LookupDigest(bytes(range(32))),
        passwords=passwords,
        session_token_pepper=bytes(range(32)),
        session_ttl_minutes=480,
        clock=lambda: current_time[0],
    )


def _use_clocked_service(
    client: TestClient,
    db: Session,
    current_time: list[datetime],
) -> AuthService:
    service = _service(db, current_time)
    client.app.dependency_overrides[get_auth_service] = lambda: service
    return service


def _credentials(platform_users, key: str = "admin") -> tuple[str, str]:
    identifier, password, _role = platform_users["credentials"][key]
    return identifier, password


def _post_login(
    client: TestClient,
    *,
    identifier: str,
    password: str = WRONG_PASSWORD,
    source_ip: str | None = None,
):
    headers = {"X-Real-IP": source_ip} if source_ip is not None else None
    return client.post(
        "/api/v1/auth/login",
        headers=headers,
        json={"identifier": identifier, "password": password},
    )


def _assert_rate_limited(exc: InvalidCredentials, retry_after: int) -> None:
    assert getattr(exc, "code", None) == "login_rate_limited"
    assert getattr(exc, "retry_after_seconds", None) == retry_after


def test_account_lock_uses_maximum_active_deadline_for_retry_after_and_recovers(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    current_time = [NOW]
    _use_clocked_service(db_client, db, current_time)
    identifier, password = _credentials(platform_users)

    for _ in range(4):
        assert _post_login(db_client, identifier=identifier).status_code == 401

    locked = _post_login(db_client, identifier=identifier)
    assert locked.status_code == 423
    assert locked.json()["code"] == "account_locked"
    assert locked.headers["Retry-After"] == "900"
    assert "retry_after_seconds" not in locked.json()

    pair_row = db.scalar(
        select(AuthRateLimit).where(AuthRateLimit.blocked_until.is_not(None))
    )
    assert pair_row is not None
    pair_row.blocked_until = NOW + timedelta(minutes=20)
    db.flush()

    current_time[0] = NOW + timedelta(seconds=1)
    still_locked = _post_login(
        db_client,
        identifier=identifier,
        password=password,
    )
    assert still_locked.status_code == 423
    assert still_locked.headers["Retry-After"] == "1199"

    current_time[0] = NOW + timedelta(minutes=15)
    pair_only = _post_login(
        db_client,
        identifier=identifier,
        password=password,
    )
    assert pair_only.status_code == 429
    assert pair_only.json()["code"] == "login_rate_limited"
    assert pair_only.headers["Retry-After"] == "300"

    current_time[0] = NOW + timedelta(minutes=20)
    recovered = _post_login(
        db_client,
        identifier=identifier,
        password=password,
    )
    assert recovered.status_code == 200


@pytest.mark.parametrize(
    "identifier,disable_account",
    [
        ("13900139000", False),
        ("not-an-identifier", False),
        ("19990000001", True),
    ],
    ids=["unknown", "invalid", "disabled"],
)
def test_pair_limiter_returns_429_when_fifth_failure_blocks_identifier_and_ip(
    db_client: TestClient,
    platform_users,
    db: Session,
    identifier: str,
    disable_account: bool,
) -> None:
    current_time = [NOW]
    _use_clocked_service(db_client, db, current_time)
    if disable_account:
        account = platform_users["accounts"]["admin"]
        account.status = "disabled"
        db.flush()

    for _ in range(4):
        assert _post_login(db_client, identifier=identifier).status_code == 401

    blocked = _post_login(db_client, identifier=identifier)
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "login_rate_limited"
    assert blocked.headers["Retry-After"] == "900"


def test_disabled_account_correct_password_counts_rate_failures_not_account_failures(
    platform_users,
    db: Session,
) -> None:
    current_time = [NOW]
    service = _service(db, current_time)
    identifier, password = _credentials(platform_users)
    account_id = platform_users["accounts"]["admin"].id
    account = db.get(UserAccount, account_id)
    assert account is not None
    account.status = "disabled"
    db.flush()

    for _ in range(4):
        with pytest.raises(InvalidCredentials) as rejected:
            service.login(
                identifier=identifier,
                password=password,
                ip_address="203.0.113.10",
                request_id="disabled-correct-password",
            )
        assert getattr(rejected.value, "code", None) != "login_rate_limited"

    with pytest.raises(InvalidCredentials) as blocked:
        service.login(
            identifier=identifier,
            password=password,
            ip_address="203.0.113.10",
            request_id="disabled-correct-password-threshold",
        )
    _assert_rate_limited(blocked.value, 900)
    db.expire_all()
    account = db.get(UserAccount, account_id)
    assert account is not None
    assert account.failed_login_count == 0
    assert account.locked_until is None


def test_disabled_locked_account_counts_rate_failure_without_extending_account_lock(
    platform_users,
    db: Session,
) -> None:
    current_time = [NOW]
    service = _service(db, current_time)
    identifier, _password = _credentials(platform_users)
    account_id = platform_users["accounts"]["admin"].id
    account = db.get(UserAccount, account_id)
    assert account is not None
    original_deadline = NOW + timedelta(minutes=30)
    account.status = "disabled"
    account.failed_login_count = 5
    account.locked_until = original_deadline
    db.flush()

    with pytest.raises(InvalidCredentials) as rejected:
        service.login(
            identifier=identifier,
            password=WRONG_PASSWORD,
            ip_address="203.0.113.99",
            request_id="disabled-existing-account-lock",
        )
    assert getattr(rejected.value, "code", None) != "login_rate_limited"

    db.expire_all()
    account = db.get(UserAccount, account_id)
    assert account is not None
    assert account.failed_login_count == 5
    assert account.locked_until == original_deadline
    rate_rows = db.scalars(select(AuthRateLimit)).all()
    assert len(rate_rows) == 2
    assert {row.attempts for row in rate_rows} == {1}


def test_unknown_identifier_counts_rate_failures_even_if_dummy_hash_verifies(
    db: Session,
) -> None:
    class DummyHashMatches(PasswordService):
        def verify(self, password: str, encoded: str) -> bool:
            del password, encoded
            return True

    current_time = [NOW]
    service = _service_with_passwords(db, current_time, DummyHashMatches())
    for _ in range(4):
        with pytest.raises(InvalidCredentials) as rejected:
            service.login(
                identifier="13900139000",
                password=WRONG_PASSWORD,
                ip_address="203.0.113.10",
                request_id="unknown-dummy-match",
            )
        assert getattr(rejected.value, "code", None) != "login_rate_limited"

    with pytest.raises(InvalidCredentials) as blocked:
        service.login(
            identifier="13900139000",
            password=WRONG_PASSWORD,
            ip_address="203.0.113.10",
            request_id="unknown-dummy-match-threshold",
        )
    _assert_rate_limited(blocked.value, 900)


def test_source_ip_limiter_blocks_rotating_identifiers_without_affecting_distinct_ip(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    current_time = [NOW]
    _use_clocked_service(db_client, db, current_time)
    db_client.app.state.settings.trusted_proxy_cidrs = "127.0.0.1/32"

    with TestClient(db_client.app, client=("127.0.0.1", 43123)) as proxy_client:
        for index in range(19):
            response = _post_login(
                proxy_client,
                identifier=f"139{index:08d}",
                source_ip="203.0.113.10",
            )
            assert response.status_code == 401

        blocked = _post_login(
            proxy_client,
            identifier="13900000019",
            source_ip="203.0.113.10",
        )
        unaffected = _post_login(
            proxy_client,
            identifier="13900000020",
            source_ip="203.0.113.11",
        )

    assert blocked.status_code == 429
    assert blocked.json()["code"] == "login_rate_limited"
    assert blocked.headers["Retry-After"] == "60"
    assert unaffected.status_code == 401


def test_correct_password_rejected_by_source_block_does_not_increment_account_failures(
    platform_users,
    db: Session,
) -> None:
    current_time = [NOW]
    service = _service(db, current_time)
    for index in range(20):
        with pytest.raises(InvalidCredentials):
            service.login(
                identifier=f"139{index:08d}",
                password=WRONG_PASSWORD,
                ip_address="203.0.113.10",
                request_id=f"rotating-{index}",
            )

    identifier, password = _credentials(platform_users)
    account_id = platform_users["accounts"]["admin"].id
    with pytest.raises(InvalidCredentials) as caught:
        service.login(
            identifier=identifier,
            password=password,
            ip_address="203.0.113.10",
            request_id="correct-password-source-blocked",
        )
    _assert_rate_limited(caught.value, 60)

    db.expire_all()
    account = db.get(UserAccount, account_id)
    assert account is not None
    assert account.failed_login_count == 0
    assert account.locked_until is None

    result = service.login(
        identifier=identifier,
        password=password,
        ip_address="203.0.113.11",
        request_id="correct-password-distinct-source",
    )
    assert result.context.account.id == account_id


def test_equivalent_ipv6_forms_share_source_ip_failure_bucket(
    db: Session,
) -> None:
    current_time = [NOW]
    service = _service(db, current_time)
    ipv6_forms = ("2001:0db8:0000:0000:0000:0000:0000:0001", "2001:db8::1")

    for index in range(19):
        with pytest.raises(InvalidCredentials):
            service.login(
                identifier=f"139{index:08d}",
                password=WRONG_PASSWORD,
                ip_address=ipv6_forms[index % 2],
                request_id=f"ipv6-{index}",
            )

    with pytest.raises(InvalidCredentials) as caught:
        service.login(
            identifier="13900000019",
            password=WRONG_PASSWORD,
            ip_address=ipv6_forms[1],
            request_id="ipv6-19",
        )
    _assert_rate_limited(caught.value, 60)


def test_blocked_attempts_do_not_extend_pair_or_source_deadlines(
    db: Session,
) -> None:
    current_time = [NOW]
    service = _service(db, current_time)

    for _ in range(5):
        try:
            service.login(
                identifier="13900139000",
                password=WRONG_PASSWORD,
                ip_address="203.0.113.10",
                request_id="pair-threshold",
            )
        except InvalidCredentials:
            pass

    current_time[0] = NOW + timedelta(minutes=10)
    with pytest.raises(InvalidCredentials) as pair_blocked:
        service.login(
            identifier="13900139000",
            password=WRONG_PASSWORD,
            ip_address="203.0.113.10",
            request_id="pair-still-blocked",
        )
    _assert_rate_limited(pair_blocked.value, 300)

    current_time[0] = NOW + timedelta(minutes=15)
    with pytest.raises(InvalidCredentials) as expired:
        service.login(
            identifier="13900139000",
            password=WRONG_PASSWORD,
            ip_address="203.0.113.10",
            request_id="pair-expired",
        )
    assert getattr(expired.value, "code", None) != "login_rate_limited"
