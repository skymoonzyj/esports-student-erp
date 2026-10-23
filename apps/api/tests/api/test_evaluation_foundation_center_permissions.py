"""Evaluation foundation operations intersect their capability with center access."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from httpx import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.evaluations.adapter_certification_service import (
    EvaluationAdapterCertificationService,
)
from app.evaluations.game_account_service import GameAccountService
from app.evaluations.game_models import StudentGameAccount
from app.evaluations.processing_models import EvaluationAdapterCertification
from app.evaluations.task_models import EvaluationIdempotencyKey
from app.rbac.models import UserPermissionOverride
from app.rbac.seed import PERMISSIONS
from app.teaching.seed import INITIAL_PROJECTS
from tests.api.test_evaluation_foundation import _login


def _center_deny(
    db: Session,
    *,
    account_id: UUID,
    project_id: UUID,
) -> UserPermissionOverride:
    return _project_deny(
        db,
        account_id=account_id,
        project_id=project_id,
        permission_key="evaluation_center.access",
    )


def _project_deny(
    db: Session,
    *,
    account_id: UUID,
    project_id: UUID,
    permission_key: str,
) -> UserPermissionOverride:
    now = datetime.now(UTC)
    override = UserPermissionOverride(
        user_id=account_id,
        permission_id=PERMISSIONS[permission_key].id,
        effect="deny",
        scope_kind="project",
        scope_id=project_id,
        valid_from=now - timedelta(minutes=1),
        valid_until=now + timedelta(minutes=10),
        created_by_user_id=account_id,
        grant_reason="Evaluation foundation center permission regression",
    )
    db.add(override)
    db.flush()
    return override


def _private_signature(response: Response) -> tuple[int, str | None, str | None]:
    payload = response.json()
    return response.status_code, payload.get("code"), payload.get("detail")


def _certification_payload(project_id: UUID, *, digest: str) -> dict[str, str]:
    return {
        "project_id": str(project_id),
        "adapter_id": "lol",
        "adapter_version": "1.0.0",
        "scoring_rule_version": "1",
        "result_schema_version": "1",
        "manifest_digest": digest,
    }


def test_project_center_deny_filters_game_account_history_and_directory_and_blocks_mutations(
    db: Session,
    db_client,
    platform_users,
) -> None:
    csrf = _login(db_client, platform_users, "self_student")
    actor_id = platform_users["accounts"]["self_student"].id
    student_id = platform_users["people"]["self_student"].id
    denied_project_id = INITIAL_PROJECTS[0].id
    allowed_project_id = INITIAL_PROJECTS[1].id
    base = f"/api/v1/evaluations/students/{student_id}/game-accounts"
    denied_game_id = f"CenterDenied-{uuid4().hex[:10]}"
    allowed_game_id = f"CenterAllowed-{uuid4().hex[:10]}"

    denied_account = db_client.post(
        base,
        headers={"X-CSRF-Token": csrf, "X-Request-ID": f"seed-denied-{uuid4()}"},
        json={"project_id": str(denied_project_id), "game_id": denied_game_id},
    )
    allowed_account = db_client.post(
        base,
        headers={"X-CSRF-Token": csrf, "X-Request-ID": f"seed-allowed-{uuid4()}"},
        json={"project_id": str(allowed_project_id), "game_id": allowed_game_id},
    )
    assert denied_account.status_code == allowed_account.status_code == 201

    _center_deny(db, account_id=actor_id, project_id=denied_project_id)
    db.commit()

    directory = db_client.get(
        f"/api/v1/evaluations/students/{student_id}/game-account-projects",
        params={"page_size": 100},
    )
    history = db_client.get(base, params={"include_unbound": "true", "page_size": 100})
    assert directory.status_code == history.status_code == 200
    directory_ids = {item["id"] for item in directory.json()["items"]}
    assert str(denied_project_id) not in directory_ids
    assert str(allowed_project_id) in directory_ids
    assert [item["game_id"] for item in history.json()] == [allowed_game_id]
    assert denied_game_id not in history.text

    before_accounts = db.scalar(select(func.count(StudentGameAccount.id)))
    before_audits = db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.action.in_(
                ("student_game_account.bind", "student_game_account.unbind")
            )
        )
    )
    denied_bind = db_client.post(
        base,
        headers={"X-CSRF-Token": csrf, "X-Request-ID": f"center-denied-bind-{uuid4()}"},
        json={
            "project_id": str(denied_project_id),
            "game_id": f"MustNotPersist-{uuid4().hex[:10]}",
        },
    )
    random_bind = db_client.post(
        f"/api/v1/evaluations/students/{uuid4()}/game-accounts",
        headers={"X-CSRF-Token": csrf},
        json={
            "project_id": str(denied_project_id),
            "game_id": f"RandomTarget-{uuid4().hex[:10]}",
        },
    )
    denied_unbind = db_client.post(
        f"{base}/{denied_account.json()['id']}/unbind",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": f"center-denied-unbind-{uuid4()}"},
        json={"reason": "中心权限已拒绝"},
    )
    random_unbind = db_client.post(
        f"{base}/{uuid4()}/unbind",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "随机资源"},
    )

    assert _private_signature(denied_bind) == _private_signature(random_bind)
    assert _private_signature(denied_unbind) == _private_signature(random_unbind)
    assert _private_signature(denied_bind) == (
        404,
        "evaluation_resource_not_found",
        "评价资源不存在",
    )
    db.expire_all()
    stored_denied = db.get(StudentGameAccount, UUID(denied_account.json()["id"]))
    assert stored_denied is not None and stored_denied.unbound_at is None
    assert db.scalar(select(func.count(StudentGameAccount.id))) == before_accounts
    assert (
        db.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.action.in_(
                    ("student_game_account.bind", "student_game_account.unbind")
                )
            )
        )
        == before_audits
    )

    allowed_unbind = db_client.post(
        f"{base}/{allowed_account.json()['id']}/unbind",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "允许项目仍可解绑"},
    )
    assert allowed_unbind.status_code == 200, allowed_unbind.text
    assert allowed_unbind.json()["status"] == "unbound"


def test_center_deny_keeps_private_404_when_game_manage_is_also_denied(
    db: Session,
    db_client,
    platform_users,
) -> None:
    csrf = _login(db_client, platform_users, "self_student")
    actor_id = platform_users["accounts"]["self_student"].id
    student_id = platform_users["people"]["self_student"].id
    denied_project_id = INITIAL_PROJECTS[0].id
    allowed_project_id = INITIAL_PROJECTS[1].id
    base = f"/api/v1/evaluations/students/{student_id}/game-accounts"
    denied_account = db_client.post(
        base,
        headers={"X-CSRF-Token": csrf},
        json={
            "project_id": str(denied_project_id),
            "game_id": f"DualDenied-{uuid4().hex[:10]}",
        },
    )
    allowed_account = db_client.post(
        base,
        headers={"X-CSRF-Token": csrf},
        json={
            "project_id": str(allowed_project_id),
            "game_id": f"SecondProject-{uuid4().hex[:10]}",
        },
    )
    assert denied_account.status_code == allowed_account.status_code == 201
    _center_deny(db, account_id=actor_id, project_id=denied_project_id)
    _project_deny(
        db,
        account_id=actor_id,
        project_id=denied_project_id,
        permission_key="student_game_accounts.manage",
    )
    db.commit()

    history = db_client.get(base, params={"include_unbound": "true", "page_size": 100})
    assert history.status_code == 200
    assert [item["id"] for item in history.json()] == [allowed_account.json()["id"]]
    before_audits = db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.action == "student_game_account.unbind"
        )
    )
    dual_denied = db_client.post(
        f"{base}/{denied_account.json()['id']}/unbind",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "中心与业务权限均拒绝"},
    )
    random = db_client.post(
        f"{base}/{uuid4()}/unbind",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "随机资源"},
    )
    assert _private_signature(dual_denied) == _private_signature(random)
    assert _private_signature(dual_denied) == (
        404,
        "evaluation_resource_not_found",
        "评价资源不存在",
    )

    _project_deny(
        db,
        account_id=actor_id,
        project_id=allowed_project_id,
        permission_key="student_game_accounts.manage",
    )
    db.commit()
    manage_only_denied = db_client.post(
        f"{base}/{allowed_account.json()['id']}/unbind",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "仅业务权限拒绝"},
    )
    assert manage_only_denied.status_code == 403
    assert manage_only_denied.json()["code"] == "evaluation_operation_denied"
    db.expire_all()
    stored_denied = db.get(StudentGameAccount, UUID(denied_account.json()["id"]))
    stored_manage_only = db.get(StudentGameAccount, UUID(allowed_account.json()["id"]))
    assert stored_denied is not None and stored_denied.unbound_at is None
    assert stored_manage_only is not None and stored_manage_only.unbound_at is None
    assert (
        db.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.action == "student_game_account.unbind"
            )
        )
        == before_audits
    )


def test_project_center_deny_filters_adapter_directories_and_history_and_blocks_create(
    db: Session,
    db_client,
    platform_users,
) -> None:
    csrf = _login(db_client, platform_users, "admin")
    actor_id = platform_users["accounts"]["admin"].id
    denied_project_id = INITIAL_PROJECTS[0].id
    allowed_project_id = INITIAL_PROJECTS[1].id
    endpoint = "/api/v1/evaluations/admin/adapter-certifications"
    denied_payload = _certification_payload(denied_project_id, digest="a" * 64)
    allowed_payload = _certification_payload(allowed_project_id, digest="b" * 64)

    denied_seed = db_client.post(
        endpoint,
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": str(uuid4())},
        json=denied_payload,
    )
    allowed_seed = db_client.post(
        endpoint,
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": str(uuid4())},
        json=allowed_payload,
    )
    assert denied_seed.status_code == allowed_seed.status_code == 201

    _center_deny(db, account_id=actor_id, project_id=denied_project_id)
    db.commit()

    directory = db_client.get(
        "/api/v1/evaluations/admin/adapter-certification-projects",
        params={"page_size": 100},
    )
    history = db_client.get(endpoint)
    assert directory.status_code == history.status_code == 200
    directory_ids = {item["id"] for item in directory.json()["items"]}
    history_ids = {item["id"] for item in history.json()["items"]}
    assert str(denied_project_id) not in directory_ids
    assert str(allowed_project_id) in directory_ids
    assert denied_seed.json()["certification"]["id"] not in history_ids
    assert allowed_seed.json()["certification"]["id"] in history_ids

    before_certifications = db.scalar(
        select(func.count(EvaluationAdapterCertification.id))
    )
    before_idempotency = db.scalar(select(func.count(EvaluationIdempotencyKey.id)))
    before_audits = db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.action == "evaluation.adapter.certified"
        )
    )
    denied_create = db_client.post(
        endpoint,
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": str(uuid4())},
        json=denied_payload,
    )
    random_create = db_client.post(
        endpoint,
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": str(uuid4())},
        json=_certification_payload(uuid4(), digest="c" * 64),
    )
    assert _private_signature(denied_create) == _private_signature(random_create)
    assert _private_signature(denied_create) == (
        404,
        "evaluation_resource_not_found",
        "评价资源不存在",
    )
    assert (
        db.scalar(select(func.count(EvaluationAdapterCertification.id)))
        == before_certifications
    )
    assert db.scalar(select(func.count(EvaluationIdempotencyKey.id))) == before_idempotency
    assert (
        db.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.action == "evaluation.adapter.certified"
            )
        )
        == before_audits
    )

    allowed_replay = db_client.post(
        endpoint,
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": str(uuid4())},
        json=allowed_payload,
    )
    assert allowed_replay.status_code == 201, allowed_replay.text
    assert (
        allowed_replay.json()["certification"]["id"]
        == allowed_seed.json()["certification"]["id"]
    )


def test_game_account_bind_reauthorizes_center_access_after_project_lock(
    db: Session,
    db_client,
    platform_users,
    monkeypatch,
) -> None:
    csrf = _login(db_client, platform_users, "self_student")
    actor_id = platform_users["accounts"]["self_student"].id
    student_id = platform_users["people"]["self_student"].id
    project_id = INITIAL_PROJECTS[0].id
    original = GameAccountService._require_project

    def deny_after_project_lock(self: GameAccountService, locked_project_id: UUID):
        project = original(self, locked_project_id)
        _center_deny(db, account_id=actor_id, project_id=project_id)
        return project

    monkeypatch.setattr(GameAccountService, "_require_project", deny_after_project_lock)
    before_accounts = db.scalar(select(func.count(StudentGameAccount.id)))
    before_audits = db.scalar(
        select(func.count(AuditEvent.id)).where(AuditEvent.action == "student_game_account.bind")
    )

    response = db_client.post(
        f"/api/v1/evaluations/students/{student_id}/game-accounts",
        headers={"X-CSRF-Token": csrf},
        json={
            "project_id": str(project_id),
            "game_id": f"LockReauth-{uuid4().hex[:10]}",
        },
    )

    assert _private_signature(response) == (
        404,
        "evaluation_resource_not_found",
        "评价资源不存在",
    )
    assert db.scalar(select(func.count(StudentGameAccount.id))) == before_accounts
    assert (
        db.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.action == "student_game_account.bind"
            )
        )
        == before_audits
    )


def test_game_account_unbind_reauthorizes_center_access_after_account_lock(
    db: Session,
    db_client,
    platform_users,
    monkeypatch,
) -> None:
    csrf = _login(db_client, platform_users, "self_student")
    actor_id = platform_users["accounts"]["self_student"].id
    student_id = platform_users["people"]["self_student"].id
    project_id = INITIAL_PROJECTS[0].id
    base = f"/api/v1/evaluations/students/{student_id}/game-accounts"
    created = db_client.post(
        base,
        headers={"X-CSRF-Token": csrf},
        json={
            "project_id": str(project_id),
            "game_id": f"LockUnbind-{uuid4().hex[:10]}",
        },
    )
    assert created.status_code == 201, created.text
    account_id = UUID(created.json()["id"])
    original = GameAccountService._lock_live_student_person

    def deny_after_student_lock(
        self: GameAccountService,
        locked_student_id: UUID,
    ) -> None:
        original(self, locked_student_id)
        _center_deny(db, account_id=actor_id, project_id=project_id)

    monkeypatch.setattr(
        GameAccountService,
        "_lock_live_student_person",
        deny_after_student_lock,
    )
    before_audits = db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.action == "student_game_account.unbind"
        )
    )

    response = db_client.post(
        f"{base}/{account_id}/unbind",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "锁后权限撤销"},
    )

    assert _private_signature(response) == (
        404,
        "evaluation_resource_not_found",
        "评价资源不存在",
    )
    db.expire_all()
    stored = db.get(StudentGameAccount, account_id)
    assert stored is not None and stored.unbound_at is None
    assert (
        db.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.action == "student_game_account.unbind"
            )
        )
        == before_audits
    )


def test_adapter_certification_reauthorizes_center_access_after_project_lock(
    db: Session,
    db_client,
    platform_users,
    monkeypatch,
) -> None:
    csrf = _login(db_client, platform_users, "admin")
    actor_id = platform_users["accounts"]["admin"].id
    project_id = INITIAL_PROJECTS[0].id
    original = EvaluationAdapterCertificationService._require_project_scope

    def deny_before_locked_scope_check(
        self: EvaluationAdapterCertificationService,
        *,
        actor_user_id: UUID,
        project_id: UUID,
        occurred_at: datetime,
    ) -> None:
        _center_deny(db, account_id=actor_id, project_id=project_id)
        original(
            self,
            actor_user_id=actor_user_id,
            project_id=project_id,
            occurred_at=occurred_at,
        )

    monkeypatch.setattr(
        EvaluationAdapterCertificationService,
        "_require_project_scope",
        deny_before_locked_scope_check,
    )
    before_certifications = db.scalar(
        select(func.count(EvaluationAdapterCertification.id))
    )
    before_idempotency = db.scalar(select(func.count(EvaluationIdempotencyKey.id)))
    before_audits = db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.action == "evaluation.adapter.certified"
        )
    )

    response = db_client.post(
        "/api/v1/evaluations/admin/adapter-certifications",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": str(uuid4())},
        json=_certification_payload(project_id, digest="d" * 64),
    )

    assert _private_signature(response) == (
        404,
        "evaluation_resource_not_found",
        "评价资源不存在",
    )
    assert (
        db.scalar(select(func.count(EvaluationAdapterCertification.id)))
        == before_certifications
    )
    assert db.scalar(select(func.count(EvaluationIdempotencyKey.id))) == before_idempotency
    assert (
        db.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.action == "evaluation.adapter.certified"
            )
        )
        == before_audits
    )
