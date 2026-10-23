from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.asset_management.models import Asset, AssetAssignment
from app.asset_management.schemas import AssetAssignRequest, AssetCreate
from app.asset_management.service import AssetService
from app.audit.models import AuditEvent
from app.audit.service import AuditService
from app.auth.dependencies import require_standard_account
from app.auth.models import UserAccount
from app.auth.service import AuthContext
from app.core.database import get_db
from app.identity.models import Person
from app.main import create_app
from app.rbac.models import UserRole
from app.rbac.seed import ROLES


@pytest.mark.parametrize("operation", ["create", "update", "assign", "return"])
def test_asset_audit_failure_rolls_back_committed_request(
    postgres_engine, test_settings, monkeypatch, operation: str
) -> None:
    """Use independent committed sessions so an outer fixture cannot mask commits."""
    with Session(postgres_engine, expire_on_commit=False) as setup:
        marker = uuid4().hex
        person = Person(
            full_name="资产原子性测试",
            identity_ciphertext=marker,
            identity_digest=marker,
            sex="male",
            date_of_birth=date(1990, 1, 1),
        )
        setup.add(person)
        setup.flush()
        actor = UserAccount(
            person_id=person.id,
            email_normalized=f"{marker}@example.test",
            password_hash="unused-test-password",
            status="active",
            force_password_change=False,
            failed_login_count=0,
        )
        setup.add(actor)
        setup.flush()
        setup.add(UserRole(user_id=actor.id, role_id=ROLES["super_admin"].id))
        setup.commit()
        asset_id = None
        if operation != "create":
            asset_id = (
                AssetService(setup)
                .create(
                    AssetCreate(name="原资产名称"),
                    actor_user_id=actor.id,
                )
                .id
            )
        if operation == "return":
            AssetService(setup).assign(
                asset_id,
                AssetAssignRequest(
                    person_id=person.id, assigned_at=datetime(2026, 9, 1, tzinfo=UTC)
                ),
                actor_user_id=actor.id,
            )
    try:
        app = create_app(test_settings)

        def request_db():
            with Session(postgres_engine) as request_session:
                yield request_session

        app.dependency_overrides[get_db] = request_db
        app.dependency_overrides[require_standard_account] = lambda: AuthContext(
            session=None,
            account=actor,
            person=person,
        )

        def audit_failure(*args, **kwargs):
            raise RuntimeError("test-only unavailable audit")

        monkeypatch.setattr(AuditService, "append", audit_failure)
        paths = {
            "create": ("POST", "/api/v1/assets", {"name": "不得持久化的资产"}),
            "update": ("PATCH", f"/api/v1/assets/{asset_id}", {"name": "不得持久化的名称"}),
            "assign": (
                "POST",
                f"/api/v1/assets/{asset_id}/assign",
                {
                    "person_id": str(person.id),
                    "assigned_at": "2026-09-14T00:00:00Z",
                },
            ),
            "return": (
                "POST",
                f"/api/v1/assets/{asset_id}/return",
                {
                    "returned_at": "2026-09-14T00:00:00Z",
                },
            ),
        }
        method, path, payload = paths[operation]
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.request(method, path, json=payload)
        assert response.status_code == 500
        with Session(postgres_engine) as observer:
            assert (
                observer.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(
                        AuditEvent.actor_user_id == actor.id,
                    )
                )
                == 0
            )
            assets = observer.scalars(
                select(Asset).where(Asset.created_by_user_id == actor.id)
            ).all()
            if operation == "create":
                assert assets == []
            else:
                assert len(assets) == 1
                assert assets[0].name == "原资产名称"
                assert assets[0].status == ("assigned" if operation == "return" else "available")
                assignments = observer.scalars(
                    select(AssetAssignment).where(
                        AssetAssignment.asset_id == asset_id,
                    )
                ).all()
                assert len(assignments) == (1 if operation == "return" else 0)
                if assignments:
                    assert assignments[0].returned_at is None
    finally:
        with postgres_engine.begin() as cleanup:
            asset_ids = select(Asset.id).where(Asset.created_by_user_id == actor.id)
            cleanup.execute(delete(AssetAssignment).where(AssetAssignment.asset_id.in_(asset_ids)))
            cleanup.execute(delete(Asset).where(Asset.created_by_user_id == actor.id))
            cleanup.execute(delete(UserRole).where(UserRole.user_id == actor.id))
            cleanup.execute(delete(UserAccount).where(UserAccount.id == actor.id))
            cleanup.execute(delete(Person).where(Person.id == person.id))
