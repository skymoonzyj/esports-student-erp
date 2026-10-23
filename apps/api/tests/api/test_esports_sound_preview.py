"""The isolated sound trial is private, live-authorized, and strictly read-only."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.auth.models import AuthSession, UserAccount
from app.esports_assessments.models import (
    AssessmentAttempt,
    AssessmentPracticeRun,
    AssessmentReport,
    AssessmentResult,
    AssessmentSession,
)
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES

PREFIX = "/api/v1/esports-assessments/sound-preview"
ASSETS = (
    ("index.html", "text/html"),
    ("style.css", "text/css"),
    ("core.mjs", "application/javascript"),
    ("audio.mjs", "application/javascript"),
    ("app.mjs", "application/javascript"),
)
ASSET_ROOT = Path(__file__).resolve().parents[2] / "app/esports_assessments/sound_preview"


def _login(client: TestClient, platform_users, key: str = "admin") -> None:
    phone, password, _ = platform_users["credentials"][key]
    response = client.post(
        "/api/v1/auth/login",
        json={"identifier": phone, "password": password},
    )
    assert response.status_code == 200


def _grant(
    db: Session,
    account: UserAccount,
    permission: str,
    *,
    effect: str = "allow",
    scope: str = "self",
) -> None:
    db.add(
        UserPermissionOverride(
            user_id=account.id,
            permission_id=PERMISSIONS[permission].id,
            effect=effect,
            scope_kind=scope,
        )
    )
    db.commit()


def _assessment_counts(db: Session) -> tuple[int | None, ...]:
    return tuple(
        db.scalar(select(func.count()).select_from(model))
        for model in (
            AssessmentSession,
            AssessmentAttempt,
            AssessmentPracticeRun,
            AssessmentResult,
            AssessmentReport,
            AuditEvent,
        )
    )


@pytest.mark.parametrize("asset_name,media_type", ASSETS)
def test_superadmin_can_read_exact_packaged_sound_preview_without_writing_results(
    db_client: TestClient,
    db: Session,
    platform_users,
    asset_name: str,
    media_type: str,
) -> None:
    _login(db_client, platform_users)
    before = _assessment_counts(db)

    response = db_client.get(f"{PREFIX}/{asset_name}")

    assert response.status_code == 200
    assert response.headers["content-type"].split(";", 1)[0] == media_type
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.content == (ASSET_ROOT / asset_name).read_bytes()
    assert response.content
    assert _assessment_counts(db) == before


@pytest.mark.parametrize("asset_name", [name for name, _ in ASSETS] + ["not-listed.mjs"])
def test_sound_preview_authenticates_before_asset_lookup(
    db_client: TestClient, asset_name: str
) -> None:
    response = db_client.get(f"{PREFIX}/{asset_name}")

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_session"
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("role", ["self_student", "ordinary_admin"])
def test_copied_sound_preview_links_require_actual_superadmin_even_with_take_and_read(
    db_client: TestClient, db: Session, platform_users, role: str
) -> None:
    key = "customer_service" if role == "ordinary_admin" else role
    account = platform_users["accounts"][key]
    if role == "ordinary_admin":
        db.execute(delete(UserRole).where(UserRole.user_id == account.id))
        db.add(UserRole(user_id=account.id, role_id=ROLES["admin"].id))
        db.commit()
    _grant(db, account, "esports_assessments.read", scope="global")
    _grant(db, account, "esports_assessments.take")
    _login(db_client, platform_users, key)
    before = _assessment_counts(db)

    for asset_name in [name for name, _ in ASSETS] + ["not-listed.mjs"]:
        response = db_client.get(f"{PREFIX}/{asset_name}")
        assert response.status_code == 403
        assert response.json()["code"] == "esports_forbidden"
        assert response.headers["cache-control"] == "no-store, private"
        assert response.headers["x-content-type-options"] == "nosniff"
    assert _assessment_counts(db) == before


@pytest.mark.parametrize(
    "revocation,expected_status,expected_code",
    [
        ("deny_take", 403, "esports_forbidden"),
        ("deny_read", 403, "esports_forbidden"),
        ("role_removed", 403, "esports_forbidden"),
        ("disabled", 401, "invalid_session"),
        ("session_revoked", 401, "invalid_session"),
        ("force_password_change", 403, "force_password_change_required"),
    ],
)
def test_sound_preview_rechecks_live_authority_for_every_asset_request(
    db_client: TestClient,
    db: Session,
    platform_users,
    revocation: str,
    expected_status: int,
    expected_code: str,
) -> None:
    account = platform_users["accounts"]["admin"]
    _login(db_client, platform_users)
    assert db_client.get(f"{PREFIX}/index.html").status_code == 200

    if revocation.startswith("deny_"):
        permission = (
            "esports_assessments.take" if revocation == "deny_take" else "esports_assessments.read"
        )
        _grant(db, account, permission, effect="deny")
    elif revocation == "role_removed":
        # Surviving permissions must not replace the actual live role check.
        _grant(db, account, "esports_assessments.read", scope="global")
        _grant(db, account, "esports_assessments.take")
        db.execute(delete(UserRole).where(UserRole.user_id == account.id))
        db.commit()
    elif revocation == "disabled":
        account.status = "disabled"
        db.commit()
    elif revocation == "force_password_change":
        account.force_password_change = True
        db.commit()
    else:
        session = db.scalar(select(AuthSession).where(AuthSession.user_id == account.id))
        assert session is not None
        session.revoked_at = datetime.now(UTC)
        db.commit()
    before = _assessment_counts(db)

    for asset_name, _ in ASSETS:
        response = db_client.get(f"{PREFIX}/{asset_name}")
        assert response.status_code == expected_status
        assert response.json()["code"] == expected_code
        assert response.headers["cache-control"] == "no-store, private"
        assert response.headers["x-content-type-options"] == "nosniff"
    assert _assessment_counts(db) == before


@pytest.mark.parametrize(
    "asset_name",
    [
        "preview_router.py",
        "access.py",
        "test_esports_sound_preview.py",
        "core.test.mjs",
        "package.json",
        ".env",
        "%2e%2e%2frouter.py",
        "%2e%2e%2f%2e%2e%2fauth%2fservice.py",
        "%2fetc%2fpasswd",
        "%5c..%5crouter.py",
        "index.html%00",
        "nested/index.html",
    ],
)
def test_sound_preview_never_serves_unlisted_source_tests_or_traversal_paths(
    db_client: TestClient, platform_users, asset_name: str
) -> None:
    _login(db_client, platform_users)

    response = db_client.get(f"{PREFIX}/{asset_name}")

    assert response.status_code == 404
    assert response.json()["code"] == "esports_not_found"
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["x-content-type-options"] == "nosniff"
