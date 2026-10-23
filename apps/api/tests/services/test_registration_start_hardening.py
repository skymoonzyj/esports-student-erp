from __future__ import annotations

import hashlib
import hmac
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI, Request, Response
from sqlalchemy import delete, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.models import AuthRateLimit
from app.core.config import Settings
from app.core.errors import ProblemError
from app.registration.delivery import CapturingVerificationSender
from app.registration.dependencies import RegistrationSecretMaterial
from app.registration.identifiers import (
    normalize_registration_identifier,
    registration_identifier_digest,
)
from app.registration.models import RegistrationApplication
from app.registration.router import start_registration
from app.registration.schemas import RegistrationApplicationCreate
from app.registration.start_limiter import (
    RegistrationStartLimiter,
    RegistrationStartRateLimited,
)
from app.security.encryption import LookupDigest

NOW = datetime(2026, 9, 16, 8, tzinfo=UTC)
PEPPER = bytes(range(1, 33))


@dataclass
class MutableClock:
    current: datetime

    def __call__(self) -> datetime:
        return self.current


def _rate_limit_key(domain: str, value: str) -> str:
    return hmac.new(
        PEPPER,
        f"{domain}\x00{value}".encode(),
        hashlib.sha256,
    ).hexdigest()


def _limiter_keys(*, identifier_digests: set[str], sources: set[str]) -> set[str]:
    keys = {
        _rate_limit_key("registration-start-identifier", digest)
        for digest in identifier_digests
    }
    for source in sources:
        keys.add(_rate_limit_key("registration-start-source", source))
        keys.add(_rate_limit_key("registration-start-source-minute", source))
    return keys


def _record_start(
    engine: Engine,
    *,
    clock: MutableClock,
    identifier_digest: str,
    request_source: str,
) -> None:
    with Session(engine) as db:
        RegistrationStartLimiter(db, pepper=PEPPER, clock=clock).record(
            identifier_digest=identifier_digest,
            request_source=request_source,
        )


def _delete_limiter_keys(engine: Engine, keys: set[str]) -> None:
    with Session(engine) as db, db.begin():
        db.execute(delete(AuthRateLimit).where(AuthRateLimit.key_digest.in_(keys)))


def test_start_source_minute_limit_is_ten_across_fresh_sessions_and_ipv6_forms(
    postgres_engine: Engine,
) -> None:
    clock = MutableClock(NOW)
    compact_source = "2001:db8::916"
    expanded_source = "2001:0db8:0000:0000:0000:0000:0000:0916"
    identifier_digests = {f"minute-source-{index}" for index in range(12)}
    keys = _limiter_keys(
        identifier_digests=identifier_digests,
        sources={compact_source},
    )

    try:
        for index in range(10):
            _record_start(
                postgres_engine,
                clock=clock,
                identifier_digest=f"minute-source-{index}",
                request_source=compact_source if index % 2 == 0 else expanded_source,
            )

        with pytest.raises(RegistrationStartRateLimited):
            _record_start(
                postgres_engine,
                clock=clock,
                identifier_digest="minute-source-10",
                request_source=expanded_source,
            )

        clock.current = NOW + timedelta(minutes=1)
        _record_start(
            postgres_engine,
            clock=clock,
            identifier_digest="minute-source-11",
            request_source=compact_source,
        )
    finally:
        _delete_limiter_keys(postgres_engine, keys)


def test_start_identifier_budget_remains_five_per_hour(
    postgres_engine: Engine,
) -> None:
    clock = MutableClock(NOW)
    identifier_digest = "hourly-identifier-budget"
    sources = {f"198.51.100.{index}" for index in range(1, 8)}
    keys = _limiter_keys(
        identifier_digests={identifier_digest},
        sources=sources,
    )

    try:
        for index in range(1, 6):
            _record_start(
                postgres_engine,
                clock=clock,
                identifier_digest=identifier_digest,
                request_source=f"198.51.100.{index}",
            )

        with pytest.raises(RegistrationStartRateLimited):
            _record_start(
                postgres_engine,
                clock=clock,
                identifier_digest=identifier_digest,
                request_source="198.51.100.6",
            )

        clock.current = NOW + timedelta(hours=1)
        _record_start(
            postgres_engine,
            clock=clock,
            identifier_digest=identifier_digest,
            request_source="198.51.100.7",
        )
    finally:
        _delete_limiter_keys(postgres_engine, keys)


def test_start_source_hour_budget_remains_twenty_across_minute_windows(
    postgres_engine: Engine,
) -> None:
    clock = MutableClock(NOW)
    request_source = "203.0.113.216"
    identifier_digests = {f"hour-source-{index}" for index in range(22)}
    keys = _limiter_keys(
        identifier_digests=identifier_digests,
        sources={request_source},
    )

    try:
        for index in range(20):
            if index == 10:
                clock.current = NOW + timedelta(minutes=1)
            _record_start(
                postgres_engine,
                clock=clock,
                identifier_digest=f"hour-source-{index}",
                request_source=request_source,
            )

        clock.current = NOW + timedelta(minutes=2)
        with pytest.raises(RegistrationStartRateLimited):
            _record_start(
                postgres_engine,
                clock=clock,
                identifier_digest="hour-source-20",
                request_source=request_source,
            )

        clock.current = NOW + timedelta(hours=1)
        _record_start(
            postgres_engine,
            clock=clock,
            identifier_digest="hour-source-21",
            request_source=request_source,
        )
    finally:
        _delete_limiter_keys(postgres_engine, keys)


def test_concurrent_normalized_email_starts_from_different_ips_create_one_draft(
    postgres_engine: Engine,
    test_settings: Settings,
) -> None:
    identifier = "concurrent-draft@example.com"
    normalized = normalize_registration_identifier("email", identifier)
    identifier_digest = registration_identifier_digest(
        LookupDigest(bytes(range(32))),
        normalized,
    )
    sources = {"198.51.100.31", "203.0.113.31"}
    limiter_keys = _limiter_keys(
        identifier_digests={identifier_digest},
        sources=sources,
    )
    app = FastAPI()
    app.state.settings = test_settings
    material = RegistrationSecretMaterial(
        verification_pepper=PEPPER,
        session_token_pepper=bytes(range(2, 34)),
    )
    sender = CapturingVerificationSender(messages=[])
    barrier = threading.Barrier(2)

    def run(raw_identifier: str, source: str) -> tuple[str, int | None, str | None]:
        request = Request(
            {
                "type": "http",
                "http_version": "1.1",
                "method": "POST",
                "scheme": "https",
                "path": "/api/v1/registration/applications",
                "raw_path": b"/api/v1/registration/applications",
                "query_string": b"",
                "headers": [],
                "client": (source, 43123),
                "server": ("testserver", 443),
                "app": app,
            }
        )
        request.state.request_id = f"registration-concurrency-{source}"
        barrier.wait(timeout=10)
        with Session(postgres_engine) as db:
            try:
                started = start_registration(
                    RegistrationApplicationCreate(
                        primary_purpose="student",
                        identifier_kind="email",
                        identifier=raw_identifier,
                    ),
                    request,
                    Response(),
                    material,
                    sender,
                    db,
                )
            except ProblemError as exc:
                return exc.code, exc.status, None
            return "created", 201, str(started.application_id)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = [
                future.result(timeout=20)
                for future in (
                    executor.submit(run, identifier, "198.51.100.31"),
                    executor.submit(
                        run,
                        "  CONCURRENT-DRAFT@EXAMPLE.COM  ",
                        "203.0.113.31",
                    ),
                )
            ]

        assert sorted(code for code, _status, _application_id in outcomes) == [
            "created",
            "registration_application_pending",
        ]
        assert sorted(status for _code, status, _application_id in outcomes) == [201, 429]
        assert sum(application_id is not None for _code, _status, application_id in outcomes) == 1
        with Session(postgres_engine) as verification:
            assert (
                verification.scalar(
                    select(func.count(RegistrationApplication.id)).where(
                        RegistrationApplication.verified_identifier_digest
                        == identifier_digest,
                        RegistrationApplication.state == "draft",
                    )
                )
                == 1
            )
    finally:
        with Session(postgres_engine) as cleanup, cleanup.begin():
            cleanup.execute(
                delete(RegistrationApplication).where(
                    RegistrationApplication.verified_identifier_digest
                    == identifier_digest
                )
            )
            cleanup.execute(
                delete(AuthRateLimit).where(AuthRateLimit.key_digest.in_(limiter_keys))
            )
