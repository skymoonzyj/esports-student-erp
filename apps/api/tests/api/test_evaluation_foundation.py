from __future__ import annotations

import base64
import io
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from PIL import Image
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.auth.models import UserAccount
from app.evaluations.access import EvaluationAccessService
from app.evaluations.analysis_models import ProjectAnalysisProfile
from app.evaluations.dependencies import get_private_media_store
from app.evaluations.game_id_digest import student_game_id_digest
from app.evaluations.game_models import StudentAuxiliaryPhone, StudentGameAccount
from app.evaluations.media_models import EvaluationMediaAsset, EvaluationMediaBlob
from app.evaluations.media_service import EvaluationMediaService
from app.evaluations.media_store import PrivateMediaStore
from app.evaluations.staff_models import CohortStaffAssignment
from app.identity.models import GuardianRelationship, Person, StudentProfile
from app.rbac.models import UserPermissionOverride
from app.rbac.seed import PERMISSIONS
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.teaching.models import Cohort, GameProject, TeachingClass
from app.teaching.seed import INITIAL_PROJECTS


def _login(client: TestClient, platform_users, key: str) -> str:
    phone, password, _role = platform_users["credentials"][key]
    response = client.post(
        "/api/v1/auth/login",
        json={"phone": phone, "password": password},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _synthetic_png() -> bytes:
    output = io.BytesIO()
    with Image.new("RGB", (32, 24), (22, 88, 154)) as image:
        image.save(output, format="PNG")
    return output.getvalue()


def _evaluation_field_crypto(client: TestClient) -> tuple[EnvelopeCipher, LookupDigest]:
    settings = client.app.state.settings
    cipher_key = base64.b64decode(
        settings.field_encryption_master_key.get_secret_value(),
        altchars=b"-_",
        validate=True,
    )
    lookup_key = base64.b64decode(
        settings.field_lookup_key.get_secret_value(),
        altchars=b"-_",
        validate=True,
    )
    return EnvelopeCipher(cipher_key), LookupDigest(lookup_key)


def _create_staff_test_cohort(
    client: TestClient,
    *,
    csrf: str,
    project_id: UUID,
) -> tuple[UUID, UUID]:
    cohort = client.post(
        "/api/v1/cohorts",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": f"评价权限届次 {uuid4().hex[:6]}",
            "starts_at": "2026-07-01T09:00:00+08:00",
            "ends_at": "2026-08-22T18:00:00+08:00",
            "default_check_in_at": "2026-07-01T08:00:00+08:00",
            "default_check_out_at": "2026-08-22T19:00:00+08:00",
        },
    )
    assert cohort.status_code == 201
    cohort_id = UUID(cohort.json()["id"])
    teaching_class = client.post(
        "/api/v1/classes",
        headers={"X-CSRF-Token": csrf},
        json={
            "cohort_id": str(cohort_id),
            "project_id": str(project_id),
            "name": f"评价权限班级 {uuid4().hex[:6]}",
            "class_type": "standard",
            "capacity": 20,
            "default_tuition_cents": 100_000,
        },
    )
    assert teaching_class.status_code == 201
    return cohort_id, UUID(teaching_class.json()["id"])


def test_foundation_openapi_exposes_frozen_routes_without_media_writes(
    db_client: TestClient,
) -> None:
    paths = db_client.app.openapi()["paths"]
    expected_methods = {
        "/api/v1/evaluations/context": {"get"},
        "/api/v1/evaluations/bridge/tickets": {"post"},
        "/api/v1/evaluations/game-account-targets": {"get"},
        "/api/v1/evaluations/students/{student_id}/game-account-projects": {"get"},
        "/api/v1/evaluations/students/{student_id}/game-accounts": {
            "get",
            "post",
        },
        "/api/v1/evaluations/students/{student_id}/game-accounts/{account_id}/unbind": {"post"},
        "/api/v1/evaluations/admin/students": {"get"},
        "/api/v1/evaluations/admin/students/{student_id}/matching-profile": {"get"},
        "/api/v1/evaluations/admin/students/{student_id}/game-account-limit": {
            "get",
            "put",
        },
        "/api/v1/evaluations/admin/students/{student_id}/auxiliary-phones": {
            "get",
            "post",
        },
        "/api/v1/evaluations/admin/students/{student_id}/auxiliary-phones/{phone_id}/replace": {
            "post"
        },
        "/api/v1/evaluations/admin/students/{student_id}/auxiliary-phones/{phone_id}/deactivate": {
            "post"
        },
        "/api/v1/evaluations/admin/staff-roles": {"get", "post"},
        "/api/v1/evaluations/admin/staff-directory": {"get"},
        "/api/v1/evaluations/admin/staff-roles/{role_id}/deactivate": {"post"},
        "/api/v1/evaluations/admin/cohorts": {"get"},
        "/api/v1/evaluations/admin/cohorts/{cohort_id}/staff-scopes": {"get"},
        "/api/v1/evaluations/admin/cohorts/{cohort_id}/staff-assignments": {
            "get",
            "post",
        },
        "/api/v1/evaluations/admin/staff-assignments/{assignment_id}/end": {"post"},
        "/api/v1/evaluations/admin/projects": {"get"},
        "/api/v1/evaluations/admin/projects/{project_id}/analysis-profiles": {
            "get",
            "post",
        },
        "/api/v1/evaluations/admin/media-storage/status": {"get"},
        "/api/v1/evaluations/media-assets/{media_id}/preview": {"get"},
        "/api/v1/evaluations/media-assets/{media_id}/original": {"get"},
    }

    evaluation_methods = {
        path: {
            method for method in operations if method in {"get", "post", "put", "patch", "delete"}
        }
        for path, operations in paths.items()
        if path.startswith("/api/v1/evaluations/")
    }
    assert evaluation_methods == expected_methods

    integration_methods = {
        path: {
            method for method in operations if method in {"get", "post", "put", "patch", "delete"}
        }
        for path, operations in paths.items()
        if path.startswith("/api/v1/integrations/evaluations/v1/")
    }
    assert integration_methods == {
        "/api/v1/integrations/evaluations/v1/capabilities": {"get"},
        "/api/v1/integrations/evaluations/v1/login-requests": {"post"},
        "/api/v1/integrations/evaluations/v1/login-tickets/exchange": {"post"},
        "/api/v1/integrations/evaluations/v1/session-context": {"post"},
    }


def test_student_context_and_targets_are_computed_server_side(
    db_client: TestClient,
    platform_users,
) -> None:
    _login(db_client, platform_users, "self_student")

    context = db_client.get("/api/v1/evaluations/context")
    targets = db_client.get("/api/v1/evaluations/game-account-targets")

    assert context.status_code == 200
    assert context.json() == {
        "accessible": True,
        "system_evaluation_available": True,
        "embedded_start_url": "/evaluation-tool/auth/erp/start?mode=embedded&return_path=%2F",
        "standalone_start_url": (
            "http://localhost:8010/auth/erp/start?mode=standalone&return_path=%2F"
        ),
        "upload_available": False,
        "video_available": False,
        "reports_available": False,
        "surveys_available": False,
        "game_accounts_available": True,
        "admin_available": False,
        "admin_sections": {
            "cohort_staff": False,
            "student_matching": False,
            "analysis_profiles": False,
        },
        "integration_api_version": "v1",
    }
    assert targets.status_code == 200
    assert targets.json() == [
        {
            "student_person_id": str(platform_users["people"]["self_student"].id),
            "student_name": "自助账号学员",
            "student_number": "API-S-004",
            "access_source": "self",
            "active_count": 0,
            "active_limit": 3,
        }
    ]


def test_context_targets_and_project_directory_use_sql_authorization(
    db_client: TestClient,
    platform_users,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _login(db_client, platform_users, "self_student")
    student_id = platform_users["people"]["self_student"].id

    def reject_python_filter(*_args, **_kwargs):
        raise AssertionError("evaluation directories must not materialize Python resources")

    monkeypatch.setattr(
        EvaluationAccessService,
        "filter_resources",
        reject_python_filter,
    )

    context = db_client.get("/api/v1/evaluations/context")
    targets = db_client.get("/api/v1/evaluations/game-account-targets")
    projects = db_client.get(
        f"/api/v1/evaluations/students/{student_id}/game-account-projects",
        params={"page": 1, "page_size": 1},
    )

    assert context.status_code == 200
    assert targets.status_code == 200
    assert projects.status_code == 200
    assert projects.json()["total"] >= 1
    assert len(projects.json()["items"]) == 1


def test_admin_context_uses_sections_without_unbounded_targets(
    db_client: TestClient,
    platform_users,
) -> None:
    _login(db_client, platform_users, "admin")

    context = db_client.get("/api/v1/evaluations/context")
    targets = db_client.get("/api/v1/evaluations/game-account-targets")

    assert context.status_code == 200
    assert context.json()["game_accounts_available"] is False
    assert context.json()["admin_available"] is True
    assert context.json()["admin_sections"] == {
        "cohort_staff": False,
        "student_matching": True,
        "analysis_profiles": True,
    }
    assert targets.status_code == 200
    assert targets.json() == []


def test_admin_context_keeps_inactive_analysis_history_without_other_operable_objects(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    _login(db_client, platform_users, "admin")
    assert list(db.scalars(select(Cohort.id))) == []
    now = datetime.now(UTC)
    student_people = list(
        db.scalars(
            select(Person)
            .join(StudentProfile, StudentProfile.person_id == Person.id)
            .where(Person.deleted_at.is_(None))
        )
    )
    for person in student_people:
        person.deleted_at = now
    for project in db.scalars(select(GameProject)):
        project.is_active = False
    db.commit()

    response = db_client.get("/api/v1/evaluations/context")

    assert response.status_code == 200
    assert response.json()["admin_available"] is True
    assert response.json()["admin_sections"] == {
        "cohort_staff": False,
        "student_matching": False,
        "analysis_profiles": True,
    }


def test_context_discovers_project_scoped_analysis_administration(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    coach_id = platform_users["accounts"]["coach"].id
    admin_id = platform_users["accounts"]["admin"].id
    project_id = INITIAL_PROJECTS[0].id
    other_project_id = INITIAL_PROJECTS[1].id
    now = datetime.now(UTC)
    db.add(
        UserPermissionOverride(
            user_id=coach_id,
            permission_id=PERMISSIONS["analysis_profiles.manage"].id,
            effect="allow",
            scope_kind="project",
            scope_id=project_id,
            valid_from=now - timedelta(minutes=1),
            valid_until=now + timedelta(minutes=10),
            created_by_user_id=admin_id,
            grant_reason="允许教练管理指定项目分析配置",
        )
    )
    db.commit()
    _login(db_client, platform_users, "coach")

    context = db_client.get("/api/v1/evaluations/context")
    allowed = db_client.get(f"/api/v1/evaluations/admin/projects/{project_id}/analysis-profiles")
    denied = db_client.get(
        f"/api/v1/evaluations/admin/projects/{other_project_id}/analysis-profiles"
    )
    missing = db_client.get(
        f"/api/v1/evaluations/admin/projects/{uuid4()}/analysis-profiles"
    )

    assert context.status_code == 200
    assert context.json()["admin_available"] is True
    assert context.json()["admin_sections"] == {
        "cohort_staff": False,
        "student_matching": False,
        "analysis_profiles": True,
    }
    assert allowed.status_code == 200
    assert denied.status_code == missing.status_code == 404
    assert denied.json()["code"] == missing.json()["code"] == "evaluation_resource_not_found"
    assert denied.json()["detail"] == missing.json()["detail"]


def test_context_keeps_analysis_administration_for_an_authorized_inactive_project(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    coach_id = platform_users["accounts"]["coach"].id
    admin_id = platform_users["accounts"]["admin"].id
    project = db.get(GameProject, INITIAL_PROJECTS[0].id)
    assert project is not None
    now = datetime.now(UTC)
    project.is_active = False
    db.add(
        UserPermissionOverride(
            user_id=coach_id,
            permission_id=PERMISSIONS["analysis_profiles.manage"].id,
            effect="allow",
            scope_kind="project",
            scope_id=project.id,
            valid_from=now - timedelta(minutes=1),
            valid_until=now + timedelta(minutes=10),
            created_by_user_id=admin_id,
            grant_reason="查看停用项目不可变分析历史",
        )
    )
    db.commit()
    _login(db_client, platform_users, "coach")

    context = db_client.get("/api/v1/evaluations/context")
    directory = db_client.get(
        "/api/v1/evaluations/admin/projects",
        params={"query": project.name, "page": 1, "page_size": 100},
    )
    profiles = db_client.get(
        f"/api/v1/evaluations/admin/projects/{project.id}/analysis-profiles"
    )

    assert context.status_code == 200
    assert context.json()["admin_sections"]["analysis_profiles"] is True
    assert directory.status_code == 200
    assert directory.json()["items"] == [
        {"id": str(project.id), "name": project.name, "is_active": False}
    ]
    assert profiles.status_code == 200


def test_context_hides_analysis_section_when_all_projects_are_explicitly_denied(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    _login(db_client, platform_users, "admin")
    admin_id = platform_users["accounts"]["admin"].id
    now = datetime.now(UTC)
    db.add_all(
        [
            UserPermissionOverride(
                user_id=admin_id,
                permission_id=PERMISSIONS["analysis_profiles.manage"].id,
                effect="deny",
                scope_kind="project",
                scope_id=project.id,
                valid_from=now - timedelta(minutes=1),
                valid_until=now + timedelta(minutes=10),
                created_by_user_id=admin_id,
                grant_reason="项目分析配置暂不开放",
            )
            for project in INITIAL_PROJECTS
        ]
    )
    db.commit()

    context = db_client.get("/api/v1/evaluations/context")

    assert context.status_code == 200
    assert context.json()["admin_sections"]["analysis_profiles"] is False


def test_context_hides_student_matching_when_all_students_are_explicitly_denied(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    _login(db_client, platform_users, "admin")
    admin_id = platform_users["accounts"]["admin"].id
    student_ids = list(
        db.scalars(
            select(StudentProfile.person_id)
            .join(Person, Person.id == StudentProfile.person_id)
            .where(Person.deleted_at.is_(None))
        )
    )
    db.add_all(
        [
            UserPermissionOverride(
                user_id=admin_id,
                permission_id=PERMISSIONS["student_game_accounts.manage"].id,
                effect="deny",
                scope_kind="person",
                scope_id=student_id,
                created_by_user_id=admin_id,
                grant_reason="该学员匹配资料暂不开放",
            )
            for student_id in student_ids
        ]
    )
    db.commit()

    context = db_client.get("/api/v1/evaluations/context")

    assert context.status_code == 200
    assert context.json()["admin_sections"]["student_matching"] is False


def test_guardian_target_disappears_after_relationship_ends(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    guardian_id = platform_users["accounts"]["guardian"].id
    student_id = platform_users["people"]["own_student"].id
    admin_id = platform_users["accounts"]["admin"].id
    now = datetime.now(UTC)
    relationship = db.scalar(
        select(GuardianRelationship).where(
            GuardianRelationship.guardian_person_id == platform_users["people"]["guardian"].id,
            GuardianRelationship.student_person_id == student_id,
            GuardianRelationship.ended_at.is_(None),
        )
    )
    assert relationship is not None
    db.add(
        UserPermissionOverride(
            user_id=guardian_id,
            permission_id=PERMISSIONS["student_game_accounts.manage"].id,
            effect="allow",
            scope_kind="family",
            scope_id=None,
            valid_from=now - timedelta(minutes=1),
            valid_until=now + timedelta(minutes=10),
            created_by_user_id=admin_id,
            grant_reason="允许监护人管理指定学员游戏账号",
        )
    )
    db.commit()
    _login(db_client, platform_users, "guardian")

    before = db_client.get("/api/v1/evaluations/game-account-targets")
    assert before.status_code == 200
    assert {item["student_person_id"] for item in before.json()} == {
        str(student_id),
        str(platform_users["people"]["minor_student"].id),
    }
    assert {item["access_source"] for item in before.json()} == {"family"}

    relationship.ended_at = datetime.now(UTC)
    relationship.ended_by_user_id = admin_id
    relationship.end_reason = "家庭权限 API 测试结束"
    db.commit()

    after = db_client.get("/api/v1/evaluations/game-account-targets")
    context = db_client.get("/api/v1/evaluations/context")
    assert after.status_code == 200
    assert [item["student_person_id"] for item in after.json()] == [
        str(platform_users["people"]["minor_student"].id)
    ]
    assert context.json()["game_accounts_available"] is True


def _private_resource_signature(response: Response) -> tuple[int, str | None, str | None]:
    payload = response.json()
    return response.status_code, payload.get("code"), payload.get("detail")


def test_expired_family_manage_grant_hides_target_mutations_like_random_target(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    guardian_id = platform_users["accounts"]["guardian"].id
    student_id = platform_users["people"]["own_student"].id
    admin_id = platform_users["accounts"]["admin"].id
    project_id = INITIAL_PROJECTS[0].id
    now = datetime.now(UTC)
    cipher, lookup = _evaluation_field_crypto(db_client)
    existing_game_id = f"ExpiredFamily#{uuid4().hex[:8]}"
    account = StudentGameAccount(
        student_person_id=student_id,
        project_id=project_id,
        identifier_ciphertext=cipher.encrypt(
            existing_game_id,
            field_name="student_game_id",
        ),
        identifier_digest=student_game_id_digest(lookup, existing_game_id),
        valid_from=now - timedelta(days=1),
        created_by_user_id=admin_id,
    )
    db.add_all(
        [
            account,
            UserPermissionOverride(
                user_id=guardian_id,
                permission_id=PERMISSIONS["student_game_accounts.manage"].id,
                effect="allow",
                scope_kind="family",
                scope_id=None,
                valid_from=now - timedelta(days=2),
                valid_until=now - timedelta(minutes=1),
                created_by_user_id=admin_id,
                grant_reason="已到期的家庭游戏账号管理权限",
            ),
        ]
    )
    db.commit()
    csrf = _login(db_client, platform_users, "guardian")
    random_student_id = uuid4()

    targets = db_client.get("/api/v1/evaluations/game-account-targets")
    existing_bind = db_client.post(
        f"/api/v1/evaluations/students/{student_id}/game-accounts",
        headers={"X-CSRF-Token": csrf},
        json={"project_id": str(project_id), "game_id": "ExpiredFamily#Bind"},
    )
    random_bind = db_client.post(
        f"/api/v1/evaluations/students/{random_student_id}/game-accounts",
        headers={"X-CSRF-Token": csrf},
        json={"project_id": str(project_id), "game_id": "ExpiredFamily#Random"},
    )
    existing_unbind = db_client.post(
        f"/api/v1/evaluations/students/{student_id}/game-accounts/{account.id}/unbind",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "授权已过期"},
    )
    random_unbind = db_client.post(
        f"/api/v1/evaluations/students/{random_student_id}/game-accounts/{account.id}/unbind",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "随机对象"},
    )

    assert targets.status_code == 200
    assert str(student_id) not in {
        item["student_person_id"] for item in targets.json()
    }
    assert _private_resource_signature(existing_bind) == _private_resource_signature(random_bind)
    assert _private_resource_signature(existing_unbind) == _private_resource_signature(
        random_unbind
    )
    assert _private_resource_signature(existing_bind) == (
        404,
        "evaluation_resource_not_found",
        "评价资源不存在",
    )
    assert db_client.get("/api/v1/auth/me").status_code == 200


def test_person_deny_hides_personal_target_mutations_like_random_target(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    student_account_id = platform_users["accounts"]["self_student"].id
    student_id = platform_users["people"]["self_student"].id
    admin_id = platform_users["accounts"]["admin"].id
    project_id = INITIAL_PROJECTS[0].id
    now = datetime.now(UTC)
    cipher, lookup = _evaluation_field_crypto(db_client)
    existing_game_id = f"DeniedSelf#{uuid4().hex[:8]}"
    account = StudentGameAccount(
        student_person_id=student_id,
        project_id=project_id,
        identifier_ciphertext=cipher.encrypt(
            existing_game_id,
            field_name="student_game_id",
        ),
        identifier_digest=student_game_id_digest(lookup, existing_game_id),
        valid_from=now - timedelta(days=1),
        created_by_user_id=admin_id,
    )
    db.add_all(
        [
            account,
            UserPermissionOverride(
                user_id=student_account_id,
                permission_id=PERMISSIONS["student_game_accounts.manage"].id,
                effect="deny",
                scope_kind="person",
                scope_id=student_id,
                created_by_user_id=admin_id,
                grant_reason="该学员游戏账号暂不开放",
            ),
        ]
    )
    db.commit()
    csrf = _login(db_client, platform_users, "self_student")
    random_student_id = uuid4()

    targets = db_client.get("/api/v1/evaluations/game-account-targets")
    existing_bind = db_client.post(
        f"/api/v1/evaluations/students/{student_id}/game-accounts",
        headers={"X-CSRF-Token": csrf},
        json={"project_id": str(project_id), "game_id": "DeniedSelf#Bind"},
    )
    random_bind = db_client.post(
        f"/api/v1/evaluations/students/{random_student_id}/game-accounts",
        headers={"X-CSRF-Token": csrf},
        json={"project_id": str(project_id), "game_id": "DeniedSelf#Random"},
    )
    existing_unbind = db_client.post(
        f"/api/v1/evaluations/students/{student_id}/game-accounts/{account.id}/unbind",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "权限已拒绝"},
    )
    random_unbind = db_client.post(
        f"/api/v1/evaluations/students/{random_student_id}/game-accounts/{account.id}/unbind",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "随机对象"},
    )

    assert targets.status_code == 200
    assert targets.json() == []
    assert _private_resource_signature(existing_bind) == _private_resource_signature(random_bind)
    assert _private_resource_signature(existing_unbind) == _private_resource_signature(
        random_unbind
    )
    assert _private_resource_signature(existing_bind) == (
        404,
        "evaluation_resource_not_found",
        "评价资源不存在",
    )
    assert db_client.get("/api/v1/auth/me").status_code == 200


def test_project_scoped_game_account_grant_exposes_current_family_targets(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    guardian_id = platform_users["accounts"]["guardian"].id
    admin_id = platform_users["accounts"]["admin"].id
    now = datetime.now(UTC)
    db.add(
        UserPermissionOverride(
            user_id=guardian_id,
            permission_id=PERMISSIONS["student_game_accounts.manage"].id,
            effect="allow",
            scope_kind="project",
            scope_id=INITIAL_PROJECTS[0].id,
            valid_from=now - timedelta(minutes=1),
            valid_until=now + timedelta(minutes=10),
            created_by_user_id=admin_id,
            grant_reason="允许管理指定项目游戏账号",
        )
    )
    db.commit()
    csrf = _login(db_client, platform_users, "guardian")
    student_id = platform_users["people"]["own_student"].id

    context = db_client.get("/api/v1/evaluations/context")
    targets = db_client.get("/api/v1/evaluations/game-account-targets")
    projects = db_client.get(
        f"/api/v1/evaluations/students/{student_id}/game-account-projects"
    )
    empty_history = db_client.get(
        f"/api/v1/evaluations/students/{student_id}/game-accounts"
    )
    allowed_game_id = f"ProjectFirstBind#{uuid4().hex[:8]}"
    allowed_bind = db_client.post(
        f"/api/v1/evaluations/students/{student_id}/game-accounts",
        headers={"X-CSRF-Token": csrf},
        json={
            "project_id": str(INITIAL_PROJECTS[0].id),
            "game_id": allowed_game_id,
        },
    )
    denied_bind = db_client.post(
        f"/api/v1/evaluations/students/{student_id}/game-accounts",
        headers={"X-CSRF-Token": csrf},
        json={
            "project_id": str(INITIAL_PROJECTS[1].id),
            "game_id": f"ProjectDenied#{uuid4().hex[:8]}",
        },
    )
    visible_history = db_client.get(
        f"/api/v1/evaluations/students/{student_id}/game-accounts",
        params={"include_unbound": "true"},
    )

    assert context.status_code == 200
    assert context.json()["game_accounts_available"] is True
    assert targets.status_code == 200
    assert str(platform_users["people"]["own_student"].id) in {
        item["student_person_id"] for item in targets.json()
    }
    assert {item["access_source"] for item in targets.json()} == {"family"}
    assert projects.status_code == 200
    assert projects.json()["total"] == 1
    assert projects.json()["items"] == [
        {
            "id": str(INITIAL_PROJECTS[0].id),
            "name": INITIAL_PROJECTS[0].name,
            "is_active": True,
        }
    ]
    assert empty_history.status_code == 200
    assert empty_history.json() == []
    assert allowed_bind.status_code == 201
    assert allowed_bind.json()["game_id"] == allowed_game_id
    assert denied_bind.status_code == 403
    assert denied_bind.json()["code"] == "evaluation_operation_denied"
    assert visible_history.status_code == 200
    assert [item["id"] for item in visible_history.json()] == [allowed_bind.json()["id"]]


def test_project_scoped_grant_cannot_cross_the_family_student_boundary(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    guardian_id = platform_users["accounts"]["guardian"].id
    admin_id = platform_users["accounts"]["admin"].id
    unrelated_student_id = platform_users["people"]["other_student"].id
    project_id = INITIAL_PROJECTS[0].id
    now = datetime.now(UTC)
    cipher, lookup = _evaluation_field_crypto(db_client)
    existing_game_id = f"UnrelatedExisting#{uuid4().hex[:8]}"
    attempted_game_id = f"UnrelatedAttempt#{uuid4().hex[:8]}"
    existing = StudentGameAccount(
        student_person_id=unrelated_student_id,
        project_id=project_id,
        identifier_ciphertext=cipher.encrypt(
            existing_game_id,
            field_name="student_game_id",
        ),
        identifier_digest=student_game_id_digest(lookup, existing_game_id),
        valid_from=now - timedelta(days=1),
        created_by_user_id=admin_id,
    )
    db.add_all(
        [
            existing,
            UserPermissionOverride(
                user_id=guardian_id,
                permission_id=PERMISSIONS["student_game_accounts.manage"].id,
                effect="allow",
                scope_kind="project",
                scope_id=project_id,
                valid_from=now - timedelta(minutes=1),
                valid_until=now + timedelta(minutes=10),
                created_by_user_id=admin_id,
                grant_reason="仅允许管理家庭学员的指定项目",
            ),
        ]
    )
    db.commit()
    csrf = _login(db_client, platform_users, "guardian")

    history = db_client.get(
        f"/api/v1/evaluations/students/{unrelated_student_id}/game-accounts",
        params={"include_unbound": "true"},
    )
    bound = db_client.post(
        f"/api/v1/evaluations/students/{unrelated_student_id}/game-accounts",
        headers={"X-CSRF-Token": csrf},
        json={"project_id": str(project_id), "game_id": attempted_game_id},
    )
    unbound = db_client.post(
        f"/api/v1/evaluations/students/{unrelated_student_id}/game-accounts/{existing.id}/unbind",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "不得跨学员解绑"},
    )

    assert _private_resource_signature(history) == (
        404,
        "evaluation_resource_not_found",
        "评价资源不存在",
    )
    assert _private_resource_signature(bound) == _private_resource_signature(history)
    assert _private_resource_signature(unbound) == _private_resource_signature(history)
    db.refresh(existing)
    assert existing.unbound_at is None
    assert (
        db.scalar(
            select(StudentGameAccount.id).where(
                StudentGameAccount.identifier_digest
                == student_game_id_digest(lookup, attempted_game_id)
            )
        )
        is None
    )


def test_default_student_lists_only_active_game_account_projects_without_teaching_read(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    _login(db_client, platform_users, "self_student")
    student_id = platform_users["people"]["self_student"].id
    inactive_project = db.get(GameProject, INITIAL_PROJECTS[-1].id)
    assert inactive_project is not None
    inactive_project.is_active = False
    db.commit()

    response = db_client.get(
        f"/api/v1/evaluations/students/{student_id}/game-account-projects",
        params={"page": 1, "page_size": 2},
    )

    assert response.status_code == 200
    assert response.json()["page"] == 1
    assert response.json()["page_size"] == 2
    assert response.json()["total"] == len(INITIAL_PROJECTS) - 1
    assert len(response.json()["items"]) == 2
    assert all(set(item) == {"id", "name", "is_active"} for item in response.json()["items"])
    assert all(item["is_active"] is True for item in response.json()["items"])
    assert str(inactive_project.id) not in response.text


def test_game_account_project_directory_hides_missing_and_unauthorized_portal_targets(
    db_client: TestClient,
    platform_users,
) -> None:
    _login(db_client, platform_users, "self_student")

    existing = db_client.get(
        f"/api/v1/evaluations/students/{platform_users['people']['other_student'].id}"
        "/game-account-projects"
    )
    random = db_client.get(f"/api/v1/evaluations/students/{uuid4()}/game-account-projects")

    assert existing.status_code == random.status_code == 404
    assert existing.json()["code"] == random.json()["code"] == "evaluation_resource_not_found"
    assert existing.json()["detail"] == random.json()["detail"]


def test_student_binds_lists_and_unbinds_game_account_without_conflict_leakage(
    db_client: TestClient,
    platform_users,
) -> None:
    csrf = _login(db_client, platform_users, "self_student")
    student_id = platform_users["people"]["self_student"].id
    project_id = INITIAL_PROJECTS[0].id
    game_id = f"API-Game-{uuid4().hex[:10]}"

    created = db_client.post(
        f"/api/v1/evaluations/students/{student_id}/game-accounts",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": "api-game-bind"},
        json={"project_id": str(project_id), "game_id": game_id},
    )
    duplicate = db_client.post(
        f"/api/v1/evaluations/students/{student_id}/game-accounts",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": "api-game-bind-duplicate"},
        json={"project_id": str(project_id), "game_id": game_id},
    )

    assert created.status_code == 201
    account_id = UUID(created.json()["id"])
    assert created.json()["game_id"] == game_id
    assert created.json()["status"] == "active"
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "game_id_already_bound"
    assert game_id not in duplicate.text

    listed = db_client.get(
        f"/api/v1/evaluations/students/{student_id}/game-accounts",
        params={"include_unbound": "true"},
    )
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [str(account_id)]

    unbound = db_client.post(
        f"/api/v1/evaluations/students/{student_id}/game-accounts/{account_id}/unbind",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": "api-game-unbind"},
        json={"reason": "更换游戏账号"},
    )
    assert unbound.status_code == 200
    assert unbound.json()["status"] == "unbound"
    assert unbound.json()["unbind_reason"] == "更换游戏账号"


def test_portal_mutations_return_exact_account_beyond_first_history_page(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    csrf = _login(db_client, platform_users, "self_student")
    student_id = platform_users["people"]["self_student"].id
    actor_id = platform_users["accounts"]["self_student"].id
    project_id = INITIAL_PROJECTS[0].id
    cipher, lookup = _evaluation_field_crypto(db_client)
    future = datetime.now(UTC) + timedelta(days=1)
    for index in range(100):
        game_id = f"FutureHistory{index:03d}"
        valid_from = future + timedelta(minutes=index)
        db.add(
            StudentGameAccount(
                student_person_id=student_id,
                project_id=project_id,
                identifier_ciphertext=cipher.encrypt(
                    game_id,
                    field_name="student_game_id",
                ),
                identifier_digest=student_game_id_digest(lookup, game_id),
                valid_from=valid_from,
                unbound_at=valid_from + timedelta(seconds=1),
                unbound_by_user_id=actor_id,
                unbind_reason="历史测试数据",
                created_by_user_id=actor_id,
            )
        )
    db.commit()

    target_game_id = f"ExactWriteBack-{uuid4().hex[:10]}"
    created = db_client.post(
        f"/api/v1/evaluations/students/{student_id}/game-accounts",
        headers={"X-CSRF-Token": csrf},
        json={"project_id": str(project_id), "game_id": target_game_id},
    )

    assert created.status_code == 201
    assert created.json()["game_id"] == target_game_id
    account_id = created.json()["id"]

    unbound = db_client.post(
        f"/api/v1/evaluations/students/{student_id}/game-accounts/{account_id}/unbind",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "验证精确写后回读"},
    )

    assert unbound.status_code == 200
    assert unbound.json()["id"] == account_id
    assert unbound.json()["status"] == "unbound"


def test_existing_unauthorized_and_random_students_share_one_private_404(
    db_client: TestClient,
    platform_users,
) -> None:
    _login(db_client, platform_users, "coach")
    existing_id = platform_users["people"]["self_student"].id

    existing = db_client.get(f"/api/v1/evaluations/students/{existing_id}/game-accounts")
    random = db_client.get(f"/api/v1/evaluations/students/{uuid4()}/game-accounts")

    assert existing.status_code == random.status_code == 404
    assert existing.json()["code"] == "evaluation_resource_not_found"
    assert random.json()["code"] == "evaluation_resource_not_found"
    assert existing.json()["detail"] == random.json()["detail"]


def test_admin_student_directory_and_matching_profile_are_minimal_and_masked(
    db_client: TestClient,
    platform_users,
) -> None:
    csrf = _login(db_client, platform_users, "admin")
    student_id = platform_users["people"]["self_student"].id
    phone = "13912345678"

    directory = db_client.get(
        "/api/v1/evaluations/admin/students",
        params={"query": "API-S-004", "page": 1, "page_size": 10},
    )
    created_phone = db_client.post(
        f"/api/v1/evaluations/admin/students/{student_id}/auxiliary-phones",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": "api-aux-phone-create"},
        json={"phone": phone},
    )
    matching = db_client.get(f"/api/v1/evaluations/admin/students/{student_id}/matching-profile")

    assert directory.status_code == 200
    assert directory.json()["total"] == 1
    assert set(directory.json()["items"][0]) == {
        "student_person_id",
        "student_name",
        "student_number",
    }
    assert created_phone.status_code == 201
    assert created_phone.json()["masked_phone"] == "139****5678"
    assert phone not in created_phone.text
    assert matching.status_code == 200
    assert matching.json()["student_person_id"] == str(student_id)
    assert matching.json()["active_limit"] == 3
    assert matching.json()["auxiliary_phones"][0]["masked_phone"] == "139****5678"
    assert phone not in matching.text


def test_admin_student_directory_query_count_is_independent_of_candidate_count(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    _login(db_client, platform_users, "admin")
    bind = db.get_bind()

    def add_student(index: int) -> None:
        token = uuid4().hex
        person = Person(
            full_name=f"SQL分页学员{index:03d}",
            identity_ciphertext=f"cipher:{token}",
            identity_digest=(token * 2)[:64],
            sex="female",
            date_of_birth=date(2000, 1, 1),
        )
        db.add(person)
        db.flush()
        db.add(
            StudentProfile(
                person_id=person.id,
                student_number=f"SQL-PAGE-{index:04d}",
                status="active",
            )
        )

    def count_directory_selects() -> tuple[int, Response]:
        statements: list[str] = []

        def record_statement(
            _connection,
            _cursor,
            statement: str,
            _parameters,
            _context,
            _executemany,
        ) -> None:
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        event.listen(bind, "before_cursor_execute", record_statement)
        try:
            response = db_client.get(
                "/api/v1/evaluations/admin/students",
                params={"query": "SQL分页学员", "page": 1, "page_size": 10},
            )
        finally:
            event.remove(bind, "before_cursor_execute", record_statement)
        return len(statements), response

    add_student(0)
    db.commit()
    small_query_count, small_response = count_directory_selects()
    assert small_response.status_code == 200
    assert small_response.json()["total"] == 1

    for index in range(1, 205):
        add_student(index)
    db.commit()
    large_query_count, large_response = count_directory_selects()

    assert large_response.status_code == 200
    assert large_response.json()["total"] == 205
    assert len(large_response.json()["items"]) == 10
    assert large_query_count <= small_query_count + 1


def test_admin_matching_profile_caps_authorized_previews_and_reports_totals(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    _login(db_client, platform_users, "admin")
    student_id = platform_users["people"]["self_student"].id
    admin_id = platform_users["accounts"]["admin"].id
    denied_project_id = INITIAL_PROJECTS[0].id
    allowed_project_id = INITIAL_PROJECTS[1].id
    cipher, lookup = _evaluation_field_crypto(db_client)
    history_start = datetime.now(UTC) - timedelta(days=2)
    for index in range(101):
        valid_from = history_start + timedelta(minutes=index)
        game_id = f"CompleteHistory{index:03d}"
        game_account = StudentGameAccount(
            student_person_id=student_id,
            project_id=allowed_project_id,
            identifier_ciphertext=cipher.encrypt(
                game_id,
                field_name="student_game_id",
            ),
            identifier_digest=student_game_id_digest(lookup, game_id),
            valid_from=valid_from,
            unbound_at=valid_from + timedelta(seconds=1),
            unbound_by_user_id=admin_id,
            unbind_reason="完整历史测试数据",
            created_by_user_id=admin_id,
        )
        phone = f"1330000{index:04d}"
        auxiliary_phone = StudentAuxiliaryPhone(
            student_person_id=student_id,
            phone_ciphertext=cipher.encrypt(
                phone,
                field_name="student_auxiliary_phone",
            ),
            phone_digest=lookup.digest(phone),
            valid_from=valid_from,
            disabled_at=valid_from + timedelta(seconds=1),
            disabled_by_user_id=admin_id,
            disable_reason="完整历史测试数据",
            created_by_user_id=admin_id,
        )
        db.add_all([game_account, auxiliary_phone])
    denied_game_id = "DeniedHistoryNewest"
    denied_valid_from = history_start + timedelta(minutes=101)
    db.add_all(
        [
            StudentGameAccount(
                student_person_id=student_id,
                project_id=denied_project_id,
                identifier_ciphertext=cipher.encrypt(
                    denied_game_id,
                    field_name="student_game_id",
                ),
                identifier_digest=student_game_id_digest(lookup, denied_game_id),
                valid_from=denied_valid_from,
                unbound_at=denied_valid_from + timedelta(seconds=1),
                unbound_by_user_id=admin_id,
                unbind_reason="被拒项目历史测试数据",
                created_by_user_id=admin_id,
            ),
            UserPermissionOverride(
                user_id=admin_id,
                permission_id=PERMISSIONS["student_game_accounts.manage"].id,
                effect="deny",
                scope_kind="project",
                scope_id=denied_project_id,
                created_by_user_id=admin_id,
                grant_reason="被拒项目不应截断允许项目历史",
            ),
        ]
    )
    db.commit()

    response = db_client.get(f"/api/v1/evaluations/admin/students/{student_id}/matching-profile")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["game_accounts"]) == 50
    assert payload["game_accounts_total"] == 101
    assert payload["game_accounts_truncated"] is True
    assert payload["game_accounts"][0]["game_id"] == "CompleteHistory100"
    assert denied_game_id not in response.text
    assert len(payload["auxiliary_phones"]) == 50
    assert payload["auxiliary_phones_total"] == 101
    assert payload["auxiliary_phones_truncated"] is True


def test_admin_matching_profile_query_count_is_constant_above_preview_limit(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    _login(db_client, platform_users, "admin")
    student_id = platform_users["people"]["self_student"].id
    admin_id = platform_users["accounts"]["admin"].id
    project_id = INITIAL_PROJECTS[1].id
    cipher, lookup = _evaluation_field_crypto(db_client)
    bind = db.get_bind()
    history_start = datetime.now(UTC) - timedelta(days=2)

    def add_history(index: int) -> None:
        valid_from = history_start + timedelta(minutes=index)
        game_id = f"BoundedProfile{index:03d}"
        phone = f"1320000{index:04d}"
        db.add_all(
            [
                StudentGameAccount(
                    student_person_id=student_id,
                    project_id=project_id,
                    identifier_ciphertext=cipher.encrypt(
                        game_id,
                        field_name="student_game_id",
                    ),
                    identifier_digest=student_game_id_digest(lookup, game_id),
                    valid_from=valid_from,
                    unbound_at=valid_from + timedelta(seconds=1),
                    unbound_by_user_id=admin_id,
                    unbind_reason="查询数测试数据",
                    created_by_user_id=admin_id,
                ),
                StudentAuxiliaryPhone(
                    student_person_id=student_id,
                    phone_ciphertext=cipher.encrypt(
                        phone,
                        field_name="student_auxiliary_phone",
                    ),
                    phone_digest=lookup.digest(phone),
                    valid_from=valid_from,
                    disabled_at=valid_from + timedelta(seconds=1),
                    disabled_by_user_id=admin_id,
                    disable_reason="查询数测试数据",
                    created_by_user_id=admin_id,
                ),
            ]
        )

    def count_profile_selects() -> tuple[int, Response]:
        statements: list[str] = []

        def record_statement(
            _connection,
            _cursor,
            statement: str,
            _parameters,
            _context,
            _executemany,
        ) -> None:
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        event.listen(bind, "before_cursor_execute", record_statement)
        try:
            response = db_client.get(
                f"/api/v1/evaluations/admin/students/{student_id}/matching-profile"
            )
        finally:
            event.remove(bind, "before_cursor_execute", record_statement)
        return len(statements), response

    add_history(0)
    db.commit()
    small_query_count, small_response = count_profile_selects()
    assert small_response.status_code == 200

    for index in range(1, 101):
        add_history(index)
    db.commit()

    large_query_count, large_response = count_profile_selects()

    assert large_response.status_code == 200
    assert large_query_count <= small_query_count + 1
    assert len(large_response.json()["game_accounts"]) == 50
    assert len(large_response.json()["auxiliary_phones"]) == 50


def test_admin_student_directory_uses_the_same_explicit_deny_as_detail(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    _login(db_client, platform_users, "admin")
    admin_id = platform_users["accounts"]["admin"].id
    denied_student_id = platform_users["people"]["self_student"].id
    db.add(
        UserPermissionOverride(
            user_id=admin_id,
            permission_id=PERMISSIONS["student_game_accounts.manage"].id,
            effect="deny",
            scope_kind="person",
            scope_id=denied_student_id,
            created_by_user_id=admin_id,
            grant_reason="该学员匹配资料暂不开放",
        )
    )
    db.commit()

    directory = db_client.get(
        "/api/v1/evaluations/admin/students",
        params={"query": "API-S-004", "page": 1, "page_size": 10},
    )
    detail = db_client.get(
        f"/api/v1/evaluations/admin/students/{denied_student_id}/matching-profile"
    )
    missing = db_client.get(
        f"/api/v1/evaluations/admin/students/{uuid4()}/matching-profile"
    )

    assert directory.status_code == 200
    assert directory.json()["total"] == 0
    assert directory.json()["items"] == []
    assert detail.status_code == missing.status_code == 404
    assert detail.json()["code"] == missing.json()["code"] == "evaluation_resource_not_found"
    assert detail.json()["detail"] == missing.json()["detail"]


def test_admin_student_private_object_matrix_is_uniform_and_atomic(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    csrf = _login(db_client, platform_users, "admin")
    admin_id = platform_users["accounts"]["admin"].id
    student_id = platform_users["people"]["self_student"].id
    base_url = f"/api/v1/evaluations/admin/students/{student_id}"
    created_phone = db_client.post(
        f"{base_url}/auxiliary-phones",
        headers={"X-CSRF-Token": csrf},
        json={"phone": "13712345678"},
    )
    assert created_phone.status_code == 201
    phone_id = UUID(created_phone.json()["id"])
    profile = db.get(StudentProfile, student_id)
    assert profile is not None
    before_limit = profile.game_account_limit
    before_phone_count = db.scalar(
        select(func.count(StudentAuxiliaryPhone.id)).where(
            StudentAuxiliaryPhone.student_person_id == student_id
        )
    )
    before_audits = db.scalar(select(func.count(AuditEvent.id)))
    db.add(
        UserPermissionOverride(
            user_id=admin_id,
            permission_id=PERMISSIONS["student_game_accounts.manage"].id,
            effect="deny",
            scope_kind="person",
            scope_id=student_id,
            created_by_user_id=admin_id,
            grant_reason="隐藏该学员全部匹配对象",
        )
    )
    db.commit()
    random_student_id = uuid4()
    random_phone_id = uuid4()
    existing_random_pairs = [
        (
            db_client.get(f"{base_url}/matching-profile"),
            db_client.get(
                f"/api/v1/evaluations/admin/students/{random_student_id}/matching-profile"
            ),
        ),
        (
            db_client.get(f"{base_url}/game-account-limit"),
            db_client.get(
                f"/api/v1/evaluations/admin/students/{random_student_id}/game-account-limit"
            ),
        ),
        (
            db_client.get(f"{base_url}/auxiliary-phones", params={"include_inactive": True}),
            db_client.get(
                f"/api/v1/evaluations/admin/students/{random_student_id}/auxiliary-phones",
                params={"include_inactive": True},
            ),
        ),
        (
            db_client.put(
                f"{base_url}/game-account-limit",
                headers={"X-CSRF-Token": csrf},
                json={"max_active_accounts": before_limit + 1, "reason": "拒绝路径限额"},
            ),
            db_client.put(
                f"/api/v1/evaluations/admin/students/{random_student_id}/game-account-limit",
                headers={"X-CSRF-Token": csrf},
                json={"max_active_accounts": before_limit + 1, "reason": "随机路径限额"},
            ),
        ),
        (
            db_client.post(
                f"{base_url}/auxiliary-phones",
                headers={"X-CSRF-Token": csrf},
                json={"phone": "13612345678"},
            ),
            db_client.post(
                f"/api/v1/evaluations/admin/students/{random_student_id}/auxiliary-phones",
                headers={"X-CSRF-Token": csrf},
                json={"phone": "13612345678"},
            ),
        ),
        (
            db_client.post(
                f"{base_url}/auxiliary-phones/{phone_id}/replace",
                headers={"X-CSRF-Token": csrf},
                json={"phone": "13512345678", "reason": "拒绝路径更换"},
            ),
            db_client.post(
                f"/api/v1/evaluations/admin/students/{random_student_id}"
                f"/auxiliary-phones/{random_phone_id}/replace",
                headers={"X-CSRF-Token": csrf},
                json={"phone": "13512345678", "reason": "随机路径更换"},
            ),
        ),
        (
            db_client.post(
                f"{base_url}/auxiliary-phones/{phone_id}/deactivate",
                headers={"X-CSRF-Token": csrf},
                json={"reason": "拒绝路径停用"},
            ),
            db_client.post(
                f"/api/v1/evaluations/admin/students/{random_student_id}"
                f"/auxiliary-phones/{random_phone_id}/deactivate",
                headers={"X-CSRF-Token": csrf},
                json={"reason": "随机路径停用"},
            ),
        ),
    ]

    for existing, random in existing_random_pairs:
        assert (existing.status_code, existing.json()["code"], existing.json()["detail"]) == (
            random.status_code,
            random.json()["code"],
            random.json()["detail"],
        ) == (404, "evaluation_resource_not_found", "评价资源不存在")

    db.expire_all()
    stored_profile = db.get(StudentProfile, student_id)
    stored_phone = db.get(StudentAuxiliaryPhone, phone_id)
    assert stored_profile is not None and stored_profile.game_account_limit == before_limit
    assert stored_phone is not None and stored_phone.disabled_at is None
    assert (
        db.scalar(
            select(func.count(StudentAuxiliaryPhone.id)).where(
                StudentAuxiliaryPhone.student_person_id == student_id
            )
        )
        == before_phone_count
    )
    assert db.scalar(select(func.count(AuditEvent.id))) == before_audits


def test_student_cannot_read_administrator_game_account_limit(
    db_client: TestClient,
    platform_users,
) -> None:
    _login(db_client, platform_users, "self_student")
    student_id = platform_users["people"]["self_student"].id

    response = db_client.get(f"/api/v1/evaluations/admin/students/{student_id}/game-account-limit")

    assert response.status_code == 403
    assert response.json()["code"] == "evaluation_operation_denied"
    assert db_client.get("/api/v1/auth/me").status_code == 200


def test_admin_updates_limit_and_preserves_masked_auxiliary_phone_history(
    db_client: TestClient,
    platform_users,
) -> None:
    csrf = _login(db_client, platform_users, "admin")
    student_id = platform_users["people"]["self_student"].id
    first_phone = "13712345678"
    replacement_phone = "13687654321"
    base_url = f"/api/v1/evaluations/admin/students/{student_id}"

    initial_limit = db_client.get(f"{base_url}/game-account-limit")
    updated_limit = db_client.put(
        f"{base_url}/game-account-limit",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": "api-limit-update"},
        json={"max_active_accounts": 5, "reason": "增加项目账号名额"},
    )
    created = db_client.post(
        f"{base_url}/auxiliary-phones",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": "api-phone-first"},
        json={"phone": first_phone},
    )

    assert initial_limit.status_code == 200
    assert initial_limit.json()["active_limit"] == 3
    assert updated_limit.status_code == 200
    assert updated_limit.json()["active_limit"] == 5
    assert created.status_code == 201
    first_phone_id = created.json()["id"]

    replaced = db_client.post(
        f"{base_url}/auxiliary-phones/{first_phone_id}/replace",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": "api-phone-replace"},
        json={"phone": replacement_phone, "reason": "更新联系号码"},
    )
    assert replaced.status_code == 201
    replacement_id = replaced.json()["id"]

    deactivated = db_client.post(
        f"{base_url}/auxiliary-phones/{replacement_id}/deactivate",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": "api-phone-deactivate"},
        json={"reason": "停止用于自动匹配"},
    )
    history = db_client.get(
        f"{base_url}/auxiliary-phones",
        params={"include_inactive": "true"},
    )

    assert deactivated.status_code == 200
    assert deactivated.json()["status"] == "inactive"
    assert history.status_code == 200
    assert {item["status"] for item in history.json()} == {"inactive"}
    assert {item["masked_phone"] for item in history.json()} == {
        "137****5678",
        "136****4321",
    }
    assert first_phone not in history.text
    assert replacement_phone not in history.text


def test_admin_phone_mutations_return_exact_item_beyond_first_history_page(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    csrf = _login(db_client, platform_users, "admin")
    student_id = platform_users["people"]["self_student"].id
    admin_id = platform_users["accounts"]["admin"].id
    cipher, lookup = _evaluation_field_crypto(db_client)
    future = datetime.now(UTC) + timedelta(days=1)
    for index in range(100):
        phone = f"1300000{index:04d}"
        valid_from = future + timedelta(minutes=index)
        db.add(
            StudentAuxiliaryPhone(
                student_person_id=student_id,
                phone_ciphertext=cipher.encrypt(
                    phone,
                    field_name="student_auxiliary_phone",
                ),
                phone_digest=lookup.digest(phone),
                valid_from=valid_from,
                disabled_at=valid_from + timedelta(seconds=1),
                disabled_by_user_id=admin_id,
                disable_reason="历史测试数据",
                created_by_user_id=admin_id,
            )
        )
    db.commit()

    base_url = f"/api/v1/evaluations/admin/students/{student_id}/auxiliary-phones"
    created = db_client.post(
        base_url,
        headers={"X-CSRF-Token": csrf},
        json={"phone": "13112345678"},
    )

    assert created.status_code == 201
    assert created.json()["masked_phone"] == "131****5678"

    replaced = db_client.post(
        f"{base_url}/{created.json()['id']}/replace",
        headers={"X-CSRF-Token": csrf},
        json={"phone": "13287654321", "reason": "验证精确替换回读"},
    )

    assert replaced.status_code == 201
    assert replaced.json()["masked_phone"] == "132****4321"

    deactivated = db_client.post(
        f"{base_url}/{replaced.json()['id']}/deactivate",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "验证精确停用回读"},
    )

    assert deactivated.status_code == 200
    assert deactivated.json()["id"] == replaced.json()["id"]
    assert deactivated.json()["status"] == "inactive"


def test_invalid_auxiliary_phone_is_field_validation_without_raw_value(
    db_client: TestClient,
    platform_users,
) -> None:
    csrf = _login(db_client, platform_users, "admin")
    student_id = platform_users["people"]["self_student"].id
    invalid_phone = "not-a-private-phone"

    response = db_client.post(
        f"/api/v1/evaluations/admin/students/{student_id}/auxiliary-phones",
        headers={"X-CSRF-Token": csrf},
        json={"phone": invalid_phone},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert invalid_phone not in response.text


def test_evaluation_mutation_reasons_never_enter_audit_storage_or_public_json(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    csrf = _login(db_client, platform_users, "admin")
    admin_id = platform_users["accounts"]["admin"].id
    student_id = platform_users["people"]["self_student"].id
    private_reason = "PrivateGameIdentifier"
    project_id = INITIAL_PROJECTS[0].id
    request_ids = {
        "student_game_account.unbind": f"audit-private-unbind-{uuid4().hex}",
        "student_game_account.limit_update": f"audit-private-limit-{uuid4().hex}",
        "student_auxiliary_phone.replace": f"audit-private-replace-{uuid4().hex}",
        "student_auxiliary_phone.deactivate": f"audit-private-deactivate-{uuid4().hex}",
    }
    account = db_client.post(
        f"/api/v1/evaluations/students/{student_id}/game-accounts",
        headers={"X-CSRF-Token": csrf},
        json={"project_id": str(project_id), "game_id": private_reason},
    )
    assert account.status_code == 201
    account_id = UUID(account.json()["id"])
    unbound = db_client.post(
        f"/api/v1/evaluations/students/{student_id}/game-accounts/{account_id}/unbind",
        headers={
            "X-CSRF-Token": csrf,
            "X-Request-ID": request_ids["student_game_account.unbind"],
        },
        json={"reason": private_reason},
    )
    assert unbound.status_code == 200

    updated_limit = db_client.put(
        f"/api/v1/evaluations/admin/students/{student_id}/game-account-limit",
        headers={
            "X-CSRF-Token": csrf,
            "X-Request-ID": request_ids["student_game_account.limit_update"],
        },
        json={"max_active_accounts": 4, "reason": private_reason},
    )
    assert updated_limit.status_code == 200

    first_phone = db_client.post(
        f"/api/v1/evaluations/admin/students/{student_id}/auxiliary-phones",
        headers={"X-CSRF-Token": csrf},
        json={"phone": "13712345678"},
    )
    assert first_phone.status_code == 201
    first_phone_id = UUID(first_phone.json()["id"])
    replacement = db_client.post(
        f"/api/v1/evaluations/admin/students/{student_id}/auxiliary-phones"
        f"/{first_phone_id}/replace",
        headers={
            "X-CSRF-Token": csrf,
            "X-Request-ID": request_ids["student_auxiliary_phone.replace"],
        },
        json={"phone": "13687654321", "reason": private_reason},
    )
    assert replacement.status_code == 201
    replacement_id = UUID(replacement.json()["id"])
    deactivated = db_client.post(
        f"/api/v1/evaluations/admin/students/{student_id}/auxiliary-phones"
        f"/{replacement_id}/deactivate",
        headers={
            "X-CSRF-Token": csrf,
            "X-Request-ID": request_ids["student_auxiliary_phone.deactivate"],
        },
        json={"reason": private_reason},
    )
    assert deactivated.status_code == 200

    audits = {
        audit.action: audit
        for audit in db.scalars(
            select(AuditEvent).where(AuditEvent.request_id.in_(request_ids.values()))
        )
    }
    assert set(audits) == set(request_ids)
    assert all(audit.purpose is None for audit in audits.values())
    assert audits["student_game_account.unbind"].event_metadata == {
        "game_account_id": str(account_id),
        "student_person_id": str(student_id),
        "project_id": str(project_id),
    }
    assert audits["student_game_account.limit_update"].event_metadata == {
        "old_limit": 3,
        "new_limit": 4,
    }
    assert audits["student_auxiliary_phone.replace"].event_metadata == {
        "auxiliary_phone_id": str(replacement_id),
        "student_person_id": str(student_id),
    }
    assert audits["student_auxiliary_phone.deactivate"].event_metadata == {
        "auxiliary_phone_id": str(replacement_id),
        "student_person_id": str(student_id),
    }

    stored_account = db.get(StudentGameAccount, account_id)
    stored_first_phone = db.get(StudentAuxiliaryPhone, first_phone_id)
    stored_replacement = db.get(StudentAuxiliaryPhone, replacement_id)
    assert stored_account is not None and stored_account.unbind_reason == private_reason
    assert stored_first_phone is not None and stored_first_phone.disable_reason == private_reason
    assert stored_replacement is not None and stored_replacement.disable_reason == private_reason

    public_items: list[dict[str, object]] = []
    for action, request_id in request_ids.items():
        response = db_client.get(
            "/api/v1/audit-events",
            params={"action": action, "actor_user_id": str(admin_id)},
        )
        assert response.status_code == 200
        item = next(
            item for item in response.json()["items"] if item["request_id"] == request_id
        )
        assert item["purpose"] is None
        public_items.append(item)
    assert private_reason not in repr(public_items)


def test_admin_creates_and_deactivates_custom_staff_role(
    db_client: TestClient,
    platform_users,
) -> None:
    csrf = _login(db_client, platform_users, "admin")
    role_key = f"api_role_{uuid4().hex[:8]}"

    initial = db_client.get("/api/v1/evaluations/admin/staff-roles")
    created = db_client.post(
        "/api/v1/evaluations/admin/staff-roles",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": "api-staff-role-create"},
        json={"key": role_key, "name": "赛事运营", "category": "operations"},
    )

    assert initial.status_code == 200
    assert any(item["key"] == "life_teacher" for item in initial.json())
    assert created.status_code == 201
    assert created.json()["key"] == role_key
    assert created.json()["is_active"] is True

    deactivated = db_client.post(
        f"/api/v1/evaluations/admin/staff-roles/{created.json()['id']}/deactivate",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": "api-staff-role-disable"},
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False


def test_staff_role_rejects_blank_name_as_field_validation(
    db_client: TestClient,
    platform_users,
) -> None:
    csrf = _login(db_client, platform_users, "admin")

    response = db_client.post(
        "/api/v1/evaluations/admin/staff-roles",
        headers={"X-CSRF-Token": csrf},
        json={
            "key": f"blank_name_{uuid4().hex[:8]}",
            "name": "  ",
            "category": "operations",
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert response.json()["errors"][0]["field"] == "body.name"


def test_staff_role_rejects_trailing_underscore_key_as_field_validation(
    db_client: TestClient,
    platform_users,
) -> None:
    csrf = _login(db_client, platform_users, "admin")

    response = db_client.post(
        "/api/v1/evaluations/admin/staff-roles",
        headers={"X-CSRF-Token": csrf},
        json={
            "key": f"invalid_{uuid4().hex[:8]}_",
            "name": "无效角色键",
            "category": "operations",
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert response.json()["errors"][0]["field"] == "body.key"


def test_staff_directory_only_returns_live_staff_with_active_accounts(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    _login(db_client, platform_users, "admin")
    disabled = db.get(UserAccount, platform_users["accounts"]["customer_service"].id)
    assert disabled is not None
    disabled.status = "disabled"
    deleted = db.get(Person, platform_users["people"]["coach"].id)
    assert deleted is not None
    deleted.deleted_at = datetime.now(UTC)
    db.commit()

    response = db_client.get("/api/v1/evaluations/admin/staff-directory?page=1&page_size=10")

    assert response.status_code == 200
    assert response.json()["page"] == 1
    assert response.json()["page_size"] == 10
    returned_ids = {item["staff_person_id"] for item in response.json()["items"]}
    assert str(platform_users["people"]["admin"].id) in returned_ids
    assert str(platform_users["people"]["customer_service"].id) not in returned_ids
    assert str(platform_users["people"]["coach"].id) not in returned_ids
    assert all(
        set(item) == {"staff_person_id", "staff_name", "staff_type"}
        for item in response.json()["items"]
    )


def test_staff_directory_filters_explicitly_denied_people_before_counting(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    _login(db_client, platform_users, "admin")
    admin_id = platform_users["accounts"]["admin"].id
    denied_person = platform_users["people"]["admin"]
    db.add(
        UserPermissionOverride(
            user_id=admin_id,
            permission_id=PERMISSIONS["cohort_staff.manage"].id,
            effect="deny",
            scope_kind="person",
            scope_id=denied_person.id,
            created_by_user_id=admin_id,
            grant_reason="该工作人员不可用于评价服务分配",
        )
    )
    db.commit()

    response = db_client.get(
        "/api/v1/evaluations/admin/staff-directory",
        params={"query": denied_person.full_name, "page": 1, "page_size": 1},
    )

    assert response.status_code == 200
    assert response.json()["total"] == 0
    assert response.json()["items"] == []
    assert str(denied_person.id) not in response.text
    assert denied_person.full_name not in response.text


@pytest.mark.parametrize("override_scope_kind", ["project", "assigned_class"])
def test_scoped_staff_manager_lists_only_cohorts_with_manageable_resources(
    db_client: TestClient,
    platform_users,
    db: Session,
    override_scope_kind: str,
) -> None:
    admin_csrf = _login(db_client, platform_users, "admin")
    cohort_id, class_id = _create_staff_test_cohort(
        db_client,
        csrf=admin_csrf,
        project_id=INITIAL_PROJECTS[0].id,
    )
    other_cohort_id, _other_class_id = _create_staff_test_cohort(
        db_client,
        csrf=admin_csrf,
        project_id=INITIAL_PROJECTS[1].id,
    )
    cohort = db.get(Cohort, cohort_id)
    assert cohort is not None
    scope_id = INITIAL_PROJECTS[0].id if override_scope_kind == "project" else class_id
    now = datetime.now(UTC)
    db.add(
        UserPermissionOverride(
            user_id=platform_users["accounts"]["coach"].id,
            permission_id=PERMISSIONS["cohort_staff.manage"].id,
            effect="allow",
            scope_kind=override_scope_kind,
            scope_id=scope_id,
            valid_from=now - timedelta(minutes=1),
            valid_until=now + timedelta(minutes=10),
            created_by_user_id=platform_users["accounts"]["admin"].id,
            grant_reason="管理指定营期资源",
        )
    )
    db.commit()
    _login(db_client, platform_users, "coach")

    response = db_client.get(
        "/api/v1/evaluations/admin/cohorts",
        params={"query": "评价权限届次", "page": 1, "page_size": 100},
    )

    assert response.status_code == 200
    assert response.json() == {
        "items": [{"id": str(cohort_id), "name": cohort.name}],
        "total": 1,
        "page": 1,
        "page_size": 100,
    }
    assert str(other_cohort_id) not in response.text


@pytest.mark.parametrize(
    ("override_scope_kind", "directory_scope_kind"),
    [("project", "project"), ("assigned_class", "class")],
)
def test_staff_scope_directory_returns_only_unique_manageable_project_or_class(
    db_client: TestClient,
    platform_users,
    db: Session,
    override_scope_kind: str,
    directory_scope_kind: str,
) -> None:
    admin_csrf = _login(db_client, platform_users, "admin")
    project = db.get(GameProject, INITIAL_PROJECTS[0].id)
    assert project is not None
    cohort_id, class_id = _create_staff_test_cohort(
        db_client,
        csrf=admin_csrf,
        project_id=project.id,
    )
    teaching_class = db.get(TeachingClass, class_id)
    assert teaching_class is not None
    if directory_scope_kind == "project":
        duplicate_project_class = db_client.post(
            "/api/v1/classes",
            headers={"X-CSRF-Token": admin_csrf},
            json={
                "cohort_id": str(cohort_id),
                "project_id": str(project.id),
                "name": f"同项目范围班级 {uuid4().hex[:6]}",
                "class_type": "standard",
                "capacity": 20,
                "default_tuition_cents": 100_000,
            },
        )
        assert duplicate_project_class.status_code == 201
    scope_id = project.id if override_scope_kind == "project" else class_id
    now = datetime.now(UTC)
    db.add(
        UserPermissionOverride(
            user_id=platform_users["accounts"]["coach"].id,
            permission_id=PERMISSIONS["cohort_staff.manage"].id,
            effect="allow",
            scope_kind=override_scope_kind,
            scope_id=scope_id,
            valid_from=now - timedelta(minutes=1),
            valid_until=now + timedelta(minutes=10),
            created_by_user_id=platform_users["accounts"]["admin"].id,
            grant_reason="管理指定工作人员服务范围",
        )
    )
    db.commit()
    _login(db_client, platform_users, "coach")

    response = db_client.get(
        f"/api/v1/evaluations/admin/cohorts/{cohort_id}/staff-scopes",
        params={"scope_kind": directory_scope_kind, "page": 1, "page_size": 100},
    )

    expected_id = project.id if directory_scope_kind == "project" else class_id
    expected_name = project.name if directory_scope_kind == "project" else teaching_class.name
    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "id": str(expected_id),
                "name": expected_name,
                "project_id": str(project.id),
                "project_name": project.name,
                "is_active": True,
            }
        ],
        "total": 1,
        "page": 1,
        "page_size": 100,
    }


def test_staff_scope_directory_hides_missing_and_unauthorized_cohorts(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    admin_csrf = _login(db_client, platform_users, "admin")
    allowed_cohort_id, _allowed_class_id = _create_staff_test_cohort(
        db_client,
        csrf=admin_csrf,
        project_id=INITIAL_PROJECTS[0].id,
    )
    unauthorized_cohort_id, _unauthorized_class_id = _create_staff_test_cohort(
        db_client,
        csrf=admin_csrf,
        project_id=INITIAL_PROJECTS[1].id,
    )
    now = datetime.now(UTC)
    db.add(
        UserPermissionOverride(
            user_id=platform_users["accounts"]["coach"].id,
            permission_id=PERMISSIONS["cohort_staff.manage"].id,
            effect="allow",
            scope_kind="project",
            scope_id=INITIAL_PROJECTS[0].id,
            valid_from=now - timedelta(minutes=1),
            valid_until=now + timedelta(minutes=10),
            created_by_user_id=platform_users["accounts"]["admin"].id,
            grant_reason="仅管理一个营期项目",
        )
    )
    db.commit()
    coach_csrf = _login(db_client, platform_users, "coach")

    allowed = db_client.get(
        f"/api/v1/evaluations/admin/cohorts/{allowed_cohort_id}/staff-scopes",
        params={"scope_kind": "project"},
    )
    unauthorized = db_client.get(
        f"/api/v1/evaluations/admin/cohorts/{unauthorized_cohort_id}/staff-scopes",
        params={"scope_kind": "project"},
    )
    missing = db_client.get(
        f"/api/v1/evaluations/admin/cohorts/{uuid4()}/staff-scopes",
        params={"scope_kind": "project"},
    )
    roles = db_client.get("/api/v1/evaluations/admin/staff-roles")
    role_id = next(item["id"] for item in roles.json() if item["key"] == "life_teacher")
    random_cohort_id = uuid4()
    unauthorized_history_url = (
        f"/api/v1/evaluations/admin/cohorts/{unauthorized_cohort_id}/staff-assignments"
    )
    random_history_url = (
        f"/api/v1/evaluations/admin/cohorts/{random_cohort_id}/staff-assignments"
    )
    payload = {
        "role_id": role_id,
        "staff_person_id": str(platform_users["people"]["customer_service"].id),
        "scope_kind": "project",
        "scope_id": str(INITIAL_PROJECTS[1].id),
        "valid_from": (now - timedelta(minutes=1)).isoformat(),
        "permission_keys": [],
    }
    before_assignments = db.scalar(select(func.count()).select_from(CohortStaffAssignment))
    before_audits = db.scalar(select(func.count(AuditEvent.id)))
    history_pair = (
        db_client.get(unauthorized_history_url),
        db_client.get(random_history_url),
    )
    create_pair = (
        db_client.post(
            unauthorized_history_url,
            headers={"X-CSRF-Token": coach_csrf},
            json=payload,
        ),
        db_client.post(
            random_history_url,
            headers={"X-CSRF-Token": coach_csrf},
            json=payload,
        ),
    )

    assert allowed.status_code == 200
    assert unauthorized.status_code == missing.status_code == 404
    assert unauthorized.json()["code"] == missing.json()["code"] == "evaluation_resource_not_found"
    assert unauthorized.json()["detail"] == missing.json()["detail"]
    for existing, random in (history_pair, create_pair):
        assert (existing.status_code, existing.json()["code"], existing.json()["detail"]) == (
            random.status_code,
            random.json()["code"],
            random.json()["detail"],
        ) == (404, "evaluation_resource_not_found", "评价资源不存在")
    assert db.scalar(select(func.count()).select_from(CohortStaffAssignment)) == before_assignments
    assert db.scalar(select(func.count(AuditEvent.id))) == before_audits


@pytest.mark.parametrize("override_scope_kind", ["project", "assigned_class"])
def test_scoped_staff_directory_uses_cohort_resources_and_person_denies_before_counting(
    db_client: TestClient,
    platform_users,
    db: Session,
    override_scope_kind: str,
) -> None:
    admin_csrf = _login(db_client, platform_users, "admin")
    cohort_id, class_id = _create_staff_test_cohort(
        db_client,
        csrf=admin_csrf,
        project_id=INITIAL_PROJECTS[0].id,
    )
    other_cohort_id, _other_class_id = _create_staff_test_cohort(
        db_client,
        csrf=admin_csrf,
        project_id=INITIAL_PROJECTS[1].id,
    )
    now = datetime.now(UTC)
    coach_id = platform_users["accounts"]["coach"].id
    admin_id = platform_users["accounts"]["admin"].id
    denied_person = platform_users["people"]["admin"]
    db.add_all(
        [
            UserPermissionOverride(
                user_id=coach_id,
                permission_id=PERMISSIONS["cohort_staff.manage"].id,
                effect="allow",
                scope_kind=override_scope_kind,
                scope_id=(INITIAL_PROJECTS[0].id if override_scope_kind == "project" else class_id),
                valid_from=now - timedelta(minutes=1),
                valid_until=now + timedelta(minutes=10),
                created_by_user_id=admin_id,
                grant_reason="管理指定营期工作人员",
            ),
            UserPermissionOverride(
                user_id=coach_id,
                permission_id=PERMISSIONS["cohort_staff.manage"].id,
                effect="deny",
                scope_kind="person",
                scope_id=denied_person.id,
                valid_from=now - timedelta(minutes=1),
                valid_until=now + timedelta(minutes=10),
                created_by_user_id=admin_id,
                grant_reason="该工作人员不可分配",
            ),
        ]
    )
    db.commit()
    _login(db_client, platform_users, "coach")

    response = db_client.get(
        "/api/v1/evaluations/admin/staff-directory",
        params={"cohort_id": str(cohort_id), "query": "平台", "page": 1, "page_size": 1},
    )
    denied = db_client.get(
        "/api/v1/evaluations/admin/staff-directory",
        params={
            "cohort_id": str(cohort_id),
            "query": denied_person.full_name,
            "page": 1,
            "page_size": 1,
        },
    )
    unauthorized = db_client.get(
        "/api/v1/evaluations/admin/staff-directory",
        params={"cohort_id": str(other_cohort_id)},
    )

    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert len(response.json()["items"]) == 1
    assert str(denied_person.id) not in response.text
    assert denied.status_code == 200
    assert denied.json()["total"] == 0
    assert denied.json()["items"] == []
    assert str(denied_person.id) not in denied.text
    assert denied_person.full_name not in denied.text
    assert unauthorized.status_code == 404
    assert unauthorized.json()["code"] == "evaluation_resource_not_found"


def test_scoped_staff_directory_requires_cohort_id(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    admin_csrf = _login(db_client, platform_users, "admin")
    _create_staff_test_cohort(
        db_client,
        csrf=admin_csrf,
        project_id=INITIAL_PROJECTS[0].id,
    )
    now = datetime.now(UTC)
    db.add(
        UserPermissionOverride(
            user_id=platform_users["accounts"]["coach"].id,
            permission_id=PERMISSIONS["cohort_staff.manage"].id,
            effect="allow",
            scope_kind="project",
            scope_id=INITIAL_PROJECTS[0].id,
            valid_from=now - timedelta(minutes=1),
            valid_until=now + timedelta(minutes=10),
            created_by_user_id=platform_users["accounts"]["admin"].id,
            grant_reason="专项授权必须绑定营期目录",
        )
    )
    db.commit()
    _login(db_client, platform_users, "coach")

    response = db_client.get("/api/v1/evaluations/admin/staff-directory")

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_evaluation_directories_count_and_page_authorized_rows_in_sql(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    csrf = _login(db_client, platform_users, "admin")
    cohort_id, _class_id = _create_staff_test_cohort(
        db_client,
        csrf=csrf,
        project_id=INITIAL_PROJECTS[0].id,
    )
    bind = db.get_bind()

    def capture(path: str, params: dict[str, str | int]) -> tuple[Response, list[str]]:
        statements: list[str] = []

        def record_statement(
            _connection,
            _cursor,
            statement: str,
            _parameters,
            _context,
            _executemany,
        ) -> None:
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement.lower())

        event.listen(bind, "before_cursor_execute", record_statement)
        try:
            response = db_client.get(path, params=params)
        finally:
            event.remove(bind, "before_cursor_execute", record_statement)
        return response, statements

    cases = [
        (
            "/api/v1/evaluations/admin/cohorts",
            {"page": 1, "page_size": 1},
            "cohorts",
        ),
        (
            f"/api/v1/evaluations/admin/cohorts/{cohort_id}/staff-scopes",
            {"scope_kind": "class", "page": 1, "page_size": 1},
            "teaching_classes",
        ),
        (
            "/api/v1/evaluations/admin/staff-directory",
            {"cohort_id": str(cohort_id), "page": 1, "page_size": 1},
            "staff_profiles",
        ),
        (
            "/api/v1/evaluations/admin/projects",
            {"page": 1, "page_size": 1},
            "game_projects",
        ),
    ]
    for path, params, table_name in cases:
        response, statements = capture(path, params)
        assert response.status_code == 200, (path, response.text)
        assert any("count(" in statement and table_name in statement for statement in statements)
        assert any("limit" in statement and table_name in statement for statement in statements)


def test_admin_staff_history_merges_coach_and_generic_assignment_once(
    db_client: TestClient,
    platform_users,
) -> None:
    csrf = _login(db_client, platform_users, "admin")
    project_id = INITIAL_PROJECTS[0].id
    cohort = db_client.post(
        "/api/v1/cohorts",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": f"评价人员 API 届次 {uuid4().hex[:6]}",
            "starts_at": "2026-07-01T09:00:00+08:00",
            "ends_at": "2026-08-22T18:00:00+08:00",
            "default_check_in_at": "2026-07-01T08:00:00+08:00",
            "default_check_out_at": "2026-08-22T19:00:00+08:00",
        },
    )
    assert cohort.status_code == 201
    cohort_id = cohort.json()["id"]
    teaching_class = db_client.post(
        "/api/v1/classes",
        headers={"X-CSRF-Token": csrf},
        json={
            "cohort_id": cohort_id,
            "project_id": str(project_id),
            "name": "评价 API 普通班",
            "class_type": "standard",
            "capacity": 20,
            "default_tuition_cents": 100_000,
        },
    )
    assert teaching_class.status_code == 201
    class_id = teaching_class.json()["id"]
    coach = db_client.post(
        f"/api/v1/classes/{class_id}/coach-assignments",
        headers={"X-CSRF-Token": csrf},
        json={
            "coach_person_id": str(platform_users["people"]["coach"].id),
            "assignment_type": "primary",
            "valid_from": "2026-07-01T09:00:00+08:00",
        },
    )
    assert coach.status_code == 201

    roles = db_client.get("/api/v1/evaluations/admin/staff-roles")
    life_teacher_role = next(item for item in roles.json() if item["key"] == "life_teacher")
    created = db_client.post(
        f"/api/v1/evaluations/admin/cohorts/{cohort_id}/staff-assignments",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": "api-staff-assign"},
        json={
            "role_id": life_teacher_role["id"],
            "staff_person_id": str(platform_users["people"]["customer_service"].id),
            "scope_kind": "project",
            "scope_id": str(project_id),
            "valid_from": "2026-07-01T09:00:00+08:00",
            "permission_keys": ["evaluation_media.original.read"],
        },
    )
    history = db_client.get(f"/api/v1/evaluations/admin/cohorts/{cohort_id}/staff-assignments")

    assert created.status_code == 201
    assert history.status_code == 200
    assert len(history.json()) == 2
    assert {item["source"] for item in history.json()} == {
        "coach_assignment",
        "cohort_staff_assignment",
    }
    assert len({item["id"] for item in history.json()}) == 2
    generic = next(item for item in history.json() if item["source"] == "cohort_staff_assignment")
    assert generic["permission_keys"] == ["evaluation_media.original.read"]

    ended = db_client.post(
        f"/api/v1/evaluations/admin/staff-assignments/{generic['id']}/end",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": "api-staff-end"},
        json={"reason": "本期服务结束"},
    )
    assert ended.status_code == 200
    assert ended.json()["ended_at"] is not None
    assert ended.json()["end_reason"] == "本期服务结束"


def test_staff_assignment_end_rejects_blank_reason_without_mutating_history(
    db_client: TestClient,
    platform_users,
) -> None:
    csrf = _login(db_client, platform_users, "admin")
    project_id = INITIAL_PROJECTS[0].id
    cohort_id, _class_id = _create_staff_test_cohort(
        db_client,
        csrf=csrf,
        project_id=project_id,
    )
    roles = db_client.get("/api/v1/evaluations/admin/staff-roles")
    role_id = next(item["id"] for item in roles.json() if item["key"] == "life_teacher")
    history_url = f"/api/v1/evaluations/admin/cohorts/{cohort_id}/staff-assignments"
    created = db_client.post(
        history_url,
        headers={"X-CSRF-Token": csrf},
        json={
            "role_id": role_id,
            "staff_person_id": str(platform_users["people"]["customer_service"].id),
            "scope_kind": "project",
            "scope_id": str(project_id),
            "valid_from": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            "permission_keys": [],
        },
    )
    assert created.status_code == 201

    response = db_client.post(
        f"/api/v1/evaluations/admin/staff-assignments/{created.json()['id']}/end",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "  "},
    )
    history = db_client.get(history_url)

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert response.json()["errors"][0]["field"] == "body.reason"
    stored = next(item for item in history.json() if item["id"] == created.json()["id"])
    assert stored["ended_at"] is None
    assert stored["end_reason"] is None


@pytest.mark.parametrize(
    "selector_case",
    [
        "missing_staff",
        "deleted_staff",
        "disabled_staff",
        "non_staff_person",
        "denied_staff",
        "missing_project",
        "wrong_cohort_project",
        "denied_project",
        "missing_class",
        "wrong_cohort_class",
        "denied_class",
        "scoped_project_miss",
    ],
)
def test_staff_assignment_create_rejects_objects_hidden_from_selector_directories(
    db_client: TestClient,
    platform_users,
    db: Session,
    selector_case: str,
) -> None:
    admin_csrf = _login(db_client, platform_users, "admin")
    target_project_id = INITIAL_PROJECTS[0].id
    other_project_id = INITIAL_PROJECTS[1].id
    cohort_id, class_id = _create_staff_test_cohort(
        db_client,
        csrf=admin_csrf,
        project_id=target_project_id,
    )
    _other_cohort_id, other_class_id = _create_staff_test_cohort(
        db_client,
        csrf=admin_csrf,
        project_id=other_project_id,
    )
    roles = db_client.get("/api/v1/evaluations/admin/staff-roles")
    role_id = next(item["id"] for item in roles.json() if item["key"] == "life_teacher")
    staff_person_id = platform_users["people"]["admin"].id
    scope_kind = "project"
    scope_id = target_project_id
    csrf = admin_csrf
    now = datetime.now(UTC)

    if selector_case == "missing_staff":
        staff_person_id = uuid4()
    elif selector_case == "deleted_staff":
        staff_person_id = platform_users["people"]["coach"].id
        person = db.get(Person, staff_person_id)
        assert person is not None
        person.deleted_at = now
    elif selector_case == "disabled_staff":
        staff_person_id = platform_users["people"]["customer_service"].id
        account = db.get(UserAccount, platform_users["accounts"]["customer_service"].id)
        assert account is not None
        account.status = "disabled"
    elif selector_case == "non_staff_person":
        staff_person_id = platform_users["people"]["self_student"].id
    elif selector_case == "denied_staff":
        staff_person_id = platform_users["people"]["coach"].id
        db.add(
            UserPermissionOverride(
                user_id=platform_users["accounts"]["admin"].id,
                permission_id=PERMISSIONS["cohort_staff.manage"].id,
                effect="deny",
                scope_kind="person",
                scope_id=staff_person_id,
                created_by_user_id=platform_users["accounts"]["admin"].id,
                grant_reason="隐藏不可分配工作人员",
            )
        )
    elif selector_case == "missing_project":
        scope_id = uuid4()
    elif selector_case == "wrong_cohort_project":
        scope_id = other_project_id
    elif selector_case == "denied_project":
        db.add(
            UserPermissionOverride(
                user_id=platform_users["accounts"]["admin"].id,
                permission_id=PERMISSIONS["cohort_staff.manage"].id,
                effect="deny",
                scope_kind="project",
                scope_id=target_project_id,
                created_by_user_id=platform_users["accounts"]["admin"].id,
                grant_reason="隐藏不可分配项目",
            )
        )
    elif selector_case == "missing_class":
        scope_kind = "class"
        scope_id = uuid4()
    elif selector_case == "wrong_cohort_class":
        scope_kind = "class"
        scope_id = other_class_id
    elif selector_case == "denied_class":
        scope_kind = "class"
        scope_id = class_id
        db.add(
            UserPermissionOverride(
                user_id=platform_users["accounts"]["admin"].id,
                permission_id=PERMISSIONS["cohort_staff.manage"].id,
                effect="deny",
                scope_kind="assigned_class",
                scope_id=class_id,
                created_by_user_id=platform_users["accounts"]["admin"].id,
                grant_reason="隐藏不可分配班级",
            )
        )
    else:
        second_class = db_client.post(
            "/api/v1/classes",
            headers={"X-CSRF-Token": admin_csrf},
            json={
                "cohort_id": str(cohort_id),
                "project_id": str(other_project_id),
                "name": f"范围外班级 {uuid4().hex[:6]}",
                "class_type": "standard",
                "capacity": 20,
                "default_tuition_cents": 100_000,
            },
        )
        assert second_class.status_code == 201
        db.add(
            UserPermissionOverride(
                user_id=platform_users["accounts"]["coach"].id,
                permission_id=PERMISSIONS["cohort_staff.manage"].id,
                effect="allow",
                scope_kind="project",
                scope_id=target_project_id,
                valid_from=now - timedelta(minutes=1),
                valid_until=now + timedelta(minutes=10),
                created_by_user_id=platform_users["accounts"]["admin"].id,
                grant_reason="仅管理一个项目",
            )
        )
        scope_id = other_project_id
        csrf = _login(db_client, platform_users, "coach")
    db.commit()

    before_assignments = db.scalar(select(func.count()).select_from(CohortStaffAssignment))
    before_audits = db.scalar(select(func.count(AuditEvent.id)))
    response = db_client.post(
        f"/api/v1/evaluations/admin/cohorts/{cohort_id}/staff-assignments",
        headers={"X-CSRF-Token": csrf},
        json={
            "role_id": role_id,
            "staff_person_id": str(staff_person_id),
            "scope_kind": scope_kind,
            "scope_id": str(scope_id),
            "valid_from": (now - timedelta(minutes=1)).isoformat(),
            "permission_keys": [],
        },
    )

    assert (response.status_code, response.json()["code"], response.json()["detail"]) == (
        404,
        "evaluation_resource_not_found",
        "评价资源不存在",
    )
    assert db.scalar(select(func.count()).select_from(CohortStaffAssignment)) == before_assignments
    assert db.scalar(select(func.count(AuditEvent.id))) == before_audits


def test_staff_assignment_create_without_manage_capability_remains_operation_denied(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    admin_csrf = _login(db_client, platform_users, "admin")
    cohort_id, _class_id = _create_staff_test_cohort(
        db_client,
        csrf=admin_csrf,
        project_id=INITIAL_PROJECTS[0].id,
    )
    roles = db_client.get("/api/v1/evaluations/admin/staff-roles")
    role_id = next(item["id"] for item in roles.json() if item["key"] == "life_teacher")
    coach_csrf = _login(db_client, platform_users, "coach")
    before_assignments = db.scalar(select(func.count()).select_from(CohortStaffAssignment))
    before_audits = db.scalar(select(func.count(AuditEvent.id)))

    response = db_client.post(
        f"/api/v1/evaluations/admin/cohorts/{cohort_id}/staff-assignments",
        headers={"X-CSRF-Token": coach_csrf},
        json={
            "role_id": role_id,
            "staff_person_id": str(platform_users["people"]["customer_service"].id),
            "scope_kind": "project",
            "scope_id": str(INITIAL_PROJECTS[0].id),
            "valid_from": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
            "permission_keys": [],
        },
    )

    assert response.status_code == 403
    assert response.json()["code"] == "evaluation_operation_denied"
    assert db.scalar(select(func.count()).select_from(CohortStaffAssignment)) == before_assignments
    assert db.scalar(select(func.count(AuditEvent.id))) == before_audits


@pytest.mark.parametrize(
    ("override_scope_kind", "assignment_scope_kind"),
    [("project", "project"), ("assigned_class", "class")],
)
def test_scoped_staff_manager_can_manage_only_the_matching_cohort_scope(
    db_client: TestClient,
    platform_users,
    db: Session,
    override_scope_kind: str,
    assignment_scope_kind: str,
) -> None:
    admin_csrf = _login(db_client, platform_users, "admin")
    project_id = INITIAL_PROJECTS[0].id
    cohort_id, class_id = _create_staff_test_cohort(
        db_client,
        csrf=admin_csrf,
        project_id=project_id,
    )
    roles = db_client.get("/api/v1/evaluations/admin/staff-roles")
    role_id = next(item["id"] for item in roles.json() if item["key"] == "life_teacher")
    now = datetime.now(UTC)
    scope_id = project_id if override_scope_kind == "project" else class_id
    history_url = f"/api/v1/evaluations/admin/cohorts/{cohort_id}/staff-assignments"
    hidden_assignment = db_client.post(
        history_url,
        headers={"X-CSRF-Token": admin_csrf},
        json={
            "role_id": role_id,
            "staff_person_id": str(platform_users["people"]["admin"].id),
            "scope_kind": assignment_scope_kind,
            "scope_id": str(scope_id),
            "valid_from": (now - timedelta(minutes=2)).isoformat(),
            "permission_keys": [],
        },
    )
    assert hidden_assignment.status_code == 201
    db.add_all(
        [
            UserPermissionOverride(
                user_id=platform_users["accounts"]["coach"].id,
                permission_id=PERMISSIONS["cohort_staff.manage"].id,
                effect="allow",
                scope_kind=override_scope_kind,
                scope_id=scope_id,
                valid_from=now - timedelta(minutes=5),
                valid_until=now + timedelta(hours=1),
                created_by_user_id=platform_users["accounts"]["admin"].id,
                grant_reason="管理指定项目工作人员",
            ),
            UserPermissionOverride(
                user_id=platform_users["accounts"]["coach"].id,
                permission_id=PERMISSIONS["cohort_staff.manage"].id,
                effect="deny",
                scope_kind="person",
                scope_id=platform_users["people"]["admin"].id,
                valid_from=now - timedelta(minutes=5),
                valid_until=now + timedelta(hours=1),
                created_by_user_id=platform_users["accounts"]["admin"].id,
                grant_reason="不可分配该工作人员",
            ),
        ]
    )
    db.commit()
    coach_csrf = _login(db_client, platform_users, "coach")

    context = db_client.get("/api/v1/evaluations/context")
    role_directory = db_client.get("/api/v1/evaluations/admin/staff-roles")
    initial_history = db_client.get(history_url)
    before_assignments = db.scalar(select(func.count()).select_from(CohortStaffAssignment))
    before_audits = db.scalar(select(func.count(AuditEvent.id)))
    denied_payload = {
        "role_id": role_id,
        "staff_person_id": str(platform_users["people"]["admin"].id),
        "scope_kind": assignment_scope_kind,
        "scope_id": str(scope_id),
        "valid_from": (now - timedelta(minutes=1)).isoformat(),
        "permission_keys": [],
    }
    denied = db_client.post(
        history_url,
        headers={"X-CSRF-Token": coach_csrf},
        json=denied_payload,
    )
    missing_create = db_client.post(
        f"/api/v1/evaluations/admin/cohorts/{uuid4()}/staff-assignments",
        headers={"X-CSRF-Token": coach_csrf},
        json=denied_payload,
    )
    denied_end = db_client.post(
        f"/api/v1/evaluations/admin/staff-assignments/{hidden_assignment.json()['id']}/end",
        headers={"X-CSRF-Token": coach_csrf},
        json={"reason": "不可见服务不得结束"},
    )
    missing_end = db_client.post(
        f"/api/v1/evaluations/admin/staff-assignments/{uuid4()}/end",
        headers={"X-CSRF-Token": coach_csrf},
        json={"reason": "随机服务不得结束"},
    )

    for existing, missing in ((denied, missing_create), (denied_end, missing_end)):
        assert (existing.status_code, existing.json()["code"], existing.json()["detail"]) == (
            missing.status_code,
            missing.json()["code"],
            missing.json()["detail"],
        ) == (404, "evaluation_resource_not_found", "评价资源不存在")
    assert db.scalar(select(func.count()).select_from(CohortStaffAssignment)) == before_assignments
    assert db.scalar(select(func.count(AuditEvent.id))) == before_audits
    hidden_row = db.get(CohortStaffAssignment, UUID(hidden_assignment.json()["id"]))
    assert hidden_row is not None and hidden_row.ended_at is None

    created = db_client.post(
        history_url,
        headers={"X-CSRF-Token": coach_csrf},
        json={
            "role_id": role_id,
            "staff_person_id": str(platform_users["people"]["customer_service"].id),
            "scope_kind": assignment_scope_kind,
            "scope_id": str(scope_id),
            "valid_from": (now - timedelta(minutes=1)).isoformat(),
            "permission_keys": [],
        },
    )

    assert context.status_code == 200
    assert context.json()["admin_sections"]["cohort_staff"] is True
    assert role_directory.status_code == 200
    assert initial_history.status_code == 200
    assert initial_history.json() == []
    assert created.status_code == 201
    ended = db_client.post(
        f"/api/v1/evaluations/admin/staff-assignments/{created.json()['id']}/end",
        headers={"X-CSRF-Token": coach_csrf},
        json={"reason": "权限范围内结束服务"},
    )
    assert ended.status_code == 200


def test_staff_assignment_validation_and_archived_cohort_errors_are_controlled(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    csrf = _login(db_client, platform_users, "admin")
    project_id = INITIAL_PROJECTS[0].id
    cohort_id, _class_id = _create_staff_test_cohort(
        db_client,
        csrf=csrf,
        project_id=project_id,
    )
    roles = db_client.get("/api/v1/evaluations/admin/staff-roles")
    role_id = next(item["id"] for item in roles.json() if item["key"] == "life_teacher")
    url = f"/api/v1/evaluations/admin/cohorts/{cohort_id}/staff-assignments"
    payload = {
        "role_id": role_id,
        "staff_person_id": str(platform_users["people"]["customer_service"].id),
        "scope_kind": "project",
        "scope_id": str(project_id),
        "valid_from": "2026-07-01T09:00:00+08:00",
        "permission_keys": [],
    }

    naive = db_client.post(
        url,
        headers={"X-CSRF-Token": csrf},
        json={**payload, "valid_from": "2026-07-01T09:00:00"},
    )
    assert naive.status_code == 422
    assert naive.json()["code"] == "validation_error"

    created = db_client.post(url, headers={"X-CSRF-Token": csrf}, json=payload)
    assert created.status_code == 201
    cohort = db.get(Cohort, cohort_id)
    assert cohort is not None
    cohort.status = "archived"
    db.commit()

    archived_create = db_client.post(url, headers={"X-CSRF-Token": csrf}, json=payload)
    archived_end = db_client.post(
        f"/api/v1/evaluations/admin/staff-assignments/{created.json()['id']}/end",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "归档后不可结束"},
    )

    assert archived_create.status_code == archived_end.status_code == 409
    assert archived_create.json()["code"] == "cohort_archived"
    assert archived_end.json()["code"] == "cohort_archived"


def test_missing_staff_cohort_uses_one_private_not_found_response(
    db_client: TestClient,
    platform_users,
) -> None:
    csrf = _login(db_client, platform_users, "admin")
    roles = db_client.get("/api/v1/evaluations/admin/staff-roles")
    role_id = next(item["id"] for item in roles.json() if item["key"] == "life_teacher")
    url = f"/api/v1/evaluations/admin/cohorts/{uuid4()}/staff-assignments"

    listed = db_client.get(url)
    created = db_client.post(
        url,
        headers={"X-CSRF-Token": csrf},
        json={
            "role_id": role_id,
            "staff_person_id": str(platform_users["people"]["customer_service"].id),
            "scope_kind": "project",
            "scope_id": str(INITIAL_PROJECTS[0].id),
            "valid_from": "2026-07-01T09:00:00+08:00",
            "permission_keys": [],
        },
    )

    assert listed.status_code == created.status_code == 404
    assert listed.json()["code"] == "evaluation_resource_not_found"
    assert created.json()["code"] == "evaluation_resource_not_found"
    assert listed.json()["detail"] == created.json()["detail"]


def test_project_scoped_analysis_manager_lists_only_authorized_projects(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    coach_id = platform_users["accounts"]["coach"].id
    admin_id = platform_users["accounts"]["admin"].id
    project = db.get(GameProject, INITIAL_PROJECTS[0].id)
    assert project is not None
    now = datetime.now(UTC)
    db.add(
        UserPermissionOverride(
            user_id=coach_id,
            permission_id=PERMISSIONS["analysis_profiles.manage"].id,
            effect="allow",
            scope_kind="project",
            scope_id=project.id,
            valid_from=now - timedelta(minutes=1),
            valid_until=now + timedelta(minutes=10),
            created_by_user_id=admin_id,
            grant_reason="管理指定项目分析配置",
        )
    )
    db.commit()
    _login(db_client, platform_users, "coach")

    response = db_client.get(
        "/api/v1/evaluations/admin/projects",
        params={"query": project.name, "page": 1, "page_size": 1},
    )

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "id": str(project.id),
                "name": project.name,
                "is_active": project.is_active,
            }
        ],
        "total": 1,
        "page": 1,
        "page_size": 1,
    }


def test_analysis_private_project_list_and_create_match_random_without_writes(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    coach_id = platform_users["accounts"]["coach"].id
    admin_id = platform_users["accounts"]["admin"].id
    allowed_project_id = INITIAL_PROJECTS[0].id
    denied_project_id = INITIAL_PROJECTS[1].id
    now = datetime.now(UTC)
    db.add_all(
        [
            UserPermissionOverride(
                user_id=coach_id,
                permission_id=PERMISSIONS["analysis_profiles.manage"].id,
                effect="allow",
                scope_kind="project",
                scope_id=allowed_project_id,
                valid_from=now - timedelta(minutes=1),
                valid_until=now + timedelta(minutes=10),
                created_by_user_id=admin_id,
                grant_reason="管理指定项目分析配置",
            ),
            UserPermissionOverride(
                user_id=coach_id,
                permission_id=PERMISSIONS["analysis_profiles.manage"].id,
                effect="deny",
                scope_kind="project",
                scope_id=denied_project_id,
                valid_from=now - timedelta(minutes=1),
                valid_until=now + timedelta(minutes=10),
                created_by_user_id=admin_id,
                grant_reason="隐藏未授权项目分析配置",
            ),
        ]
    )
    db.commit()
    csrf = _login(db_client, platform_users, "coach")
    random_project_id = uuid4()
    denied_url = (
        f"/api/v1/evaluations/admin/projects/{denied_project_id}/analysis-profiles"
    )
    random_url = (
        f"/api/v1/evaluations/admin/projects/{random_project_id}/analysis-profiles"
    )
    payload = {
        "expected_current_version": 1,
        "mode": "manual_only",
        "publish_mode": "review_required",
        "effective_from": "2026-08-01T00:00:00+08:00",
        "change_reason": "私有对象不得创建分析配置",
    }
    before_profiles = db.scalar(select(func.count()).select_from(ProjectAnalysisProfile))
    before_audits = db.scalar(select(func.count(AuditEvent.id)))

    response_pairs = [
        (db_client.get(denied_url), db_client.get(random_url)),
        (
            db_client.post(denied_url, headers={"X-CSRF-Token": csrf}, json=payload),
            db_client.post(random_url, headers={"X-CSRF-Token": csrf}, json=payload),
        ),
    ]

    for existing, random in response_pairs:
        assert (existing.status_code, existing.json()["code"], existing.json()["detail"]) == (
            random.status_code,
            random.json()["code"],
            random.json()["detail"],
        ) == (404, "evaluation_resource_not_found", "评价资源不存在")
    assert db.scalar(select(func.count()).select_from(ProjectAnalysisProfile)) == before_profiles
    assert db.scalar(select(func.count(AuditEvent.id))) == before_audits
    allowed_url = (
        f"/api/v1/evaluations/admin/projects/{allowed_project_id}/analysis-profiles"
    )
    allowed = db_client.get(allowed_url)
    assert allowed.status_code == 200
    created = db_client.post(
        allowed_url,
        headers={"X-CSRF-Token": csrf},
        json={
            **payload,
            "expected_current_version": allowed.json()[0]["version"],
            "change_reason": "范围内项目允许创建分析配置",
        },
    )
    assert created.status_code == 201


def test_global_analysis_capability_keeps_all_denied_projects_private(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    admin_id = platform_users["accounts"]["admin"].id
    denied_project_id = INITIAL_PROJECTS[0].id
    project_ids = list(db.scalars(select(GameProject.id)))
    assert denied_project_id in project_ids
    now = datetime.now(UTC)
    db.add_all(
        [
            UserPermissionOverride(
                user_id=admin_id,
                permission_id=PERMISSIONS["analysis_profiles.manage"].id,
                effect="deny",
                scope_kind="project",
                scope_id=project_id,
                valid_from=now - timedelta(minutes=1),
                valid_until=now + timedelta(minutes=10),
                created_by_user_id=admin_id,
                grant_reason="隐藏全部项目以验证对象隐私",
            )
            for project_id in project_ids
        ]
    )
    db.commit()
    csrf = _login(db_client, platform_users, "admin")
    random_project_id = uuid4()
    denied_url = (
        f"/api/v1/evaluations/admin/projects/{denied_project_id}/analysis-profiles"
    )
    random_url = (
        f"/api/v1/evaluations/admin/projects/{random_project_id}/analysis-profiles"
    )
    payload = {
        "expected_current_version": 1,
        "mode": "manual_only",
        "publish_mode": "review_required",
        "effective_from": "2026-08-01T00:00:00+08:00",
        "change_reason": "全部对象拒绝时不得泄露项目存在性",
    }
    before_profiles = db.scalar(select(func.count()).select_from(ProjectAnalysisProfile))
    before_audits = db.scalar(select(func.count(AuditEvent.id)))

    response_pairs = [
        (db_client.get(denied_url), db_client.get(random_url)),
        (
            db_client.post(denied_url, headers={"X-CSRF-Token": csrf}, json=payload),
            db_client.post(random_url, headers={"X-CSRF-Token": csrf}, json=payload),
        ),
    ]

    for existing, random in response_pairs:
        assert (existing.status_code, existing.json()["code"], existing.json()["detail"]) == (
            random.status_code,
            random.json()["code"],
            random.json()["detail"],
        ) == (404, "evaluation_resource_not_found", "评价资源不存在")
    assert db.scalar(select(func.count()).select_from(ProjectAnalysisProfile)) == before_profiles
    assert db.scalar(select(func.count(AuditEvent.id))) == before_audits


def test_global_analysis_manager_project_directory_includes_inactive_projects(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    project = db.get(GameProject, INITIAL_PROJECTS[-1].id)
    assert project is not None
    project.is_active = False
    db.commit()
    _login(db_client, platform_users, "admin")

    response = db_client.get(
        "/api/v1/evaluations/admin/projects",
        params={"query": project.name, "page": 1, "page_size": 100},
    )

    assert response.status_code == 200
    assert response.json()["items"] == [
        {"id": str(project.id), "name": project.name, "is_active": False}
    ]
    assert response.json()["total"] == 1


def test_analysis_profiles_are_append_only_and_reject_unaccepted_adapter(
    db_client: TestClient,
    platform_users,
) -> None:
    csrf = _login(db_client, platform_users, "admin")
    project_id = INITIAL_PROJECTS[0].id
    url = f"/api/v1/evaluations/admin/projects/{project_id}/analysis-profiles"

    initial = db_client.get(url)
    created = db_client.post(
        url,
        headers={"X-CSRF-Token": csrf, "X-Request-ID": "api-profile-v2"},
        json={
            "expected_current_version": 1,
            "mode": "manual_only",
            "publish_mode": "review_required",
            "effective_from": "2026-08-01T00:00:00+08:00",
            "change_reason": "启用第二版人工分析配置",
        },
    )

    assert initial.status_code == 200
    assert [item["version"] for item in initial.json()] == [1]
    assert created.status_code == 201
    assert created.json()["version"] == 2
    assert created.json()["mode"] == "manual_only"

    rejected = db_client.post(
        url,
        headers={"X-CSRF-Token": csrf, "X-Request-ID": "api-profile-auto"},
        json={
            "expected_current_version": 2,
            "mode": "automatic",
            "adapter_id": "not-accepted-adapter",
            "adapter_version": "1.0",
            "scoring_rule_version": "1.0",
            "required_fields": ["score"],
            "confidence_thresholds": {"score": 0.8},
            "auto_publish_warning_levels": ["info"],
            "publish_mode": "review_required",
            "effective_from": "2026-08-02T00:00:00+08:00",
            "change_reason": "尝试尚未验收的自动适配器",
        },
    )
    history = db_client.get(url)

    assert rejected.status_code == 409
    assert rejected.json()["code"] == "analysis_adapter_not_accepted"
    assert "not-accepted-adapter" not in rejected.text
    assert [item["version"] for item in history.json()] == [2, 1]


def test_missing_analysis_project_uses_one_private_not_found_response(
    db_client: TestClient,
    platform_users,
) -> None:
    csrf = _login(db_client, platform_users, "admin")
    url = f"/api/v1/evaluations/admin/projects/{uuid4()}/analysis-profiles"

    listed = db_client.get(url)
    created = db_client.post(
        url,
        headers={"X-CSRF-Token": csrf},
        json={
            "expected_current_version": 1,
            "mode": "manual_only",
            "publish_mode": "review_required",
            "effective_from": "2026-08-01T00:00:00+08:00",
            "change_reason": "不存在项目不应创建配置",
        },
    )

    assert listed.status_code == created.status_code == 404
    assert listed.json()["code"] == "evaluation_resource_not_found"
    assert created.json()["code"] == "evaluation_resource_not_found"
    assert listed.json()["detail"] == created.json()["detail"]


@pytest.mark.parametrize(
    ("denied_permission", "denied_path", "allowed_path"),
    [
        (
            "cohort_staff.manage",
            "/api/v1/evaluations/admin/staff-roles",
            f"/api/v1/evaluations/admin/projects/{INITIAL_PROJECTS[0].id}/analysis-profiles",
        ),
        (
            "analysis_profiles.manage",
            f"/api/v1/evaluations/admin/projects/{INITIAL_PROJECTS[0].id}/analysis-profiles",
            "/api/v1/evaluations/admin/staff-roles",
        ),
    ],
)
def test_staff_and_profile_permissions_are_independent_without_logout(
    db_client: TestClient,
    platform_users,
    db: Session,
    denied_permission: str,
    denied_path: str,
    allowed_path: str,
) -> None:
    _login(db_client, platform_users, "admin")
    admin_id = platform_users["accounts"]["admin"].id
    now = datetime.now(UTC)
    db.add(
        UserPermissionOverride(
            user_id=admin_id,
            permission_id=PERMISSIONS[denied_permission].id,
            effect="deny",
            scope_kind="global",
            scope_id=None,
            valid_from=now - timedelta(minutes=1),
            valid_until=now + timedelta(minutes=10),
            created_by_user_id=admin_id,
            grant_reason="验证评价管理权限相互独立",
        )
    )
    db.commit()

    denied = db_client.get(denied_path)
    allowed = db_client.get(allowed_path)
    still_authenticated = db_client.get("/api/v1/auth/me")

    assert denied.status_code == 403
    assert denied.json()["code"] == "evaluation_operation_denied"
    assert allowed.status_code == 200
    assert still_authenticated.status_code == 200


def test_original_read_denial_is_audited_when_center_access_is_revoked(
    db_client: TestClient,
    platform_users,
    db: Session,
    tmp_path: Path,
) -> None:
    media_id = uuid4()
    request_id = "api-media-center-denied"
    admin_id = platform_users["accounts"]["admin"].id
    _login(db_client, platform_users, "admin")
    db.add(
        UserPermissionOverride(
            user_id=admin_id,
            permission_id=PERMISSIONS["evaluation_center.access"].id,
            effect="deny",
            scope_kind="global",
            scope_id=None,
        )
    )
    db.commit()

    def override_store():
        store = PrivateMediaStore(
            root=tmp_path / "media",
            quarantine_root=tmp_path / "quarantine",
            max_bytes=20 * 1024 * 1024,
            max_pixels=50_000_000,
            preview_max_edge=1600,
        )
        try:
            yield store
        finally:
            store.close()

    db_client.app.dependency_overrides[get_private_media_store] = override_store
    try:
        response = db_client.get(
            f"/api/v1/evaluations/media-assets/{media_id}/original",
            headers={"X-Request-ID": request_id},
        )
    finally:
        db_client.app.dependency_overrides.pop(get_private_media_store, None)

    assert response.status_code == 403
    assert response.json()["code"] == "evaluation_access_denied"
    audits = list(db.scalars(select(AuditEvent).where(AuditEvent.request_id == request_id)))
    assert len(audits) == 1
    assert audits[0].action == "evaluation.media.original.read"
    assert audits[0].outcome == "denied"
    assert audits[0].event_metadata == {"media_id": str(media_id)}


def test_media_reads_close_descriptors_and_emit_one_sanitized_original_audit(
    db_client: TestClient,
    platform_users,
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media_root = tmp_path / "private-media"
    quarantine_root = tmp_path / "private-quarantine"
    seed_store = PrivateMediaStore(
        root=media_root,
        quarantine_root=quarantine_root,
        max_bytes=20 * 1024 * 1024,
        max_pixels=50_000_000,
        preview_max_edge=1600,
    )
    payload = _synthetic_png()
    validated = seed_store.stage(
        io.BytesIO(payload),
        declared_filename="synthetic.png",
    )
    blob_id = uuid4()
    files = seed_store.persist(validated, blob_id=blob_id)
    seed_store.finalize(files)
    seed_store.release(files)
    seed_store.close()

    admin_id = platform_users["accounts"]["admin"].id
    blob = EvaluationMediaBlob(
        id=blob_id,
        storage_key=files.storage_key,
        preview_storage_key=files.preview_storage_key,
        sha256=validated.sha256,
        mime_type=validated.mime_type,
        canonical_extension=validated.extension,
        size_bytes=validated.byte_size,
        width_pixels=validated.width,
        height_pixels=validated.height,
        storage_status="available",
        validation_status="validated",
        retention_status="retained",
    )
    asset = EvaluationMediaAsset(
        blob_id=blob.id,
        uploaded_by_user_id=admin_id,
        project_id=INITIAL_PROJECTS[0].id,
    )
    db.add(blob)
    db.flush()
    db.add(asset)
    db.commit()

    request_stores: list[PrivateMediaStore] = []

    def override_store():
        store = PrivateMediaStore(
            root=media_root,
            quarantine_root=quarantine_root,
            max_bytes=20 * 1024 * 1024,
            max_pixels=50_000_000,
            preview_max_edge=1600,
        )
        request_stores.append(store)
        try:
            yield store
        finally:
            store.close()

    protected_files = []
    real_preview = EvaluationMediaService.read_preview
    real_original = EvaluationMediaService.read_original

    def capture_preview(self, *args, **kwargs):
        protected = real_preview(self, *args, **kwargs)
        protected_files.append(protected)
        return protected

    def capture_original(self, *args, **kwargs):
        protected = real_original(self, *args, **kwargs)
        protected_files.append(protected)
        return protected

    monkeypatch.setattr(EvaluationMediaService, "read_preview", capture_preview)
    monkeypatch.setattr(EvaluationMediaService, "read_original", capture_original)
    db_client.app.dependency_overrides[get_private_media_store] = override_store
    missing_id = uuid4()
    try:
        _login(db_client, platform_users, "admin")
        preview = db_client.get(
            f"/api/v1/evaluations/media-assets/{asset.id}/preview",
            headers={"X-Request-ID": "api-media-preview"},
        )
        original = db_client.get(
            f"/api/v1/evaluations/media-assets/{asset.id}/original",
            headers={"X-Request-ID": "api-media-original"},
        )
        missing_preview = db_client.get(
            f"/api/v1/evaluations/media-assets/{missing_id}/preview",
            headers={"X-Request-ID": "api-media-missing-preview"},
        )
        missing_original = db_client.get(
            f"/api/v1/evaluations/media-assets/{missing_id}/original",
            headers={"X-Request-ID": "api-media-missing"},
        )
        status = db_client.get("/api/v1/evaluations/admin/media-storage/status")
        _login(db_client, platform_users, "coach")
        unauthorized_preview = db_client.get(
            f"/api/v1/evaluations/media-assets/{asset.id}/preview",
            headers={"X-Request-ID": "api-media-unauthorized-preview"},
        )
        unauthorized_original = db_client.get(
            f"/api/v1/evaluations/media-assets/{asset.id}/original",
            headers={"X-Request-ID": "api-media-unauthorized-original"},
        )
    finally:
        db_client.app.dependency_overrides.pop(get_private_media_store, None)

    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/webp"
    assert original.status_code == 200
    assert original.content == payload
    assert original.headers["x-content-type-options"] == "nosniff"
    assert "evaluation-image.png" in original.headers["content-disposition"]
    assert "synthetic.png" not in original.headers["content-disposition"]
    assert set(original.headers["cache-control"].replace(" ", "").split(",")) == {
        "private",
        "no-store",
    }
    for response in (
        missing_preview,
        missing_original,
        unauthorized_preview,
        unauthorized_original,
    ):
        assert (response.status_code, response.json()["code"], response.json()["detail"]) == (
            404,
            "media_asset_not_found",
            "媒体资源不存在",
        )
    assert status.status_code == 200
    assert set(status.json()) == {
        "ready",
        "media_writable",
        "quarantine_writable",
        "max_bytes",
        "max_pixels",
        "preview_max_edge",
    }
    assert not any("path" in key or "root" in key for key in status.json())

    assert len(protected_files) == 2
    assert all(
        getattr(protected.path, "_descriptor", None) is None for protected in protected_files
    )
    assert len(request_stores) == 7
    assert all(store._root_descriptor is None for store in request_stores)
    assert all(store._quarantine_descriptor is None for store in request_stores)

    original_audits = list(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.request_id.in_(
                    {
                        "api-media-original",
                        "api-media-missing",
                        "api-media-unauthorized-original",
                    }
                ),
            )
        )
    )
    assert {
        audit.request_id: (audit.outcome, audit.event_metadata)
        for audit in original_audits
    } == {
        "api-media-original": ("allowed", {"media_id": str(asset.id)}),
        "api-media-missing": ("denied", {"media_id": str(missing_id)}),
        "api-media-unauthorized-original": (
            "denied",
            {"media_id": str(asset.id)},
        ),
    }
    assert all(
        not any(
            key in audit.event_metadata
            for key in ("sha256", "storage_key", "path", "filename")
        )
        for audit in original_audits
    )


def test_media_response_closes_pinned_file_when_response_construction_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.evaluations import portal_router
    from app.evaluations.media_service import ProtectedMediaFile

    store = PrivateMediaStore(
        root=tmp_path / "media",
        quarantine_root=tmp_path / "quarantine",
        max_bytes=20 * 1024 * 1024,
        max_pixels=50_000_000,
        preview_max_edge=1600,
    )
    validated = store.stage(
        io.BytesIO(_synthetic_png()),
        declared_filename="synthetic.png",
    )
    files = store.persist(validated, blob_id=uuid4())
    store.finalize(files)
    store.release(files)
    pinned_path = store.resolve(files.storage_key)
    protected = ProtectedMediaFile(
        path=pinned_path,
        mime_type="image/png",
        headers={},
    )

    def reject_file_response(*args, **kwargs):
        del args, kwargs
        raise OSError("forced response construction failure")

    monkeypatch.setattr(portal_router, "FileResponse", reject_file_response)
    with pytest.raises(OSError, match="forced response construction failure"):
        portal_router._media_response(protected)

    assert getattr(pinned_path, "_descriptor", None) is None
    store.close()


def test_evaluation_integration_v1_capabilities_remain_frozen(
    db_client: TestClient,
) -> None:
    response = db_client.get(
        "/api/v1/integrations/evaluations/v1/capabilities",
        headers={"Authorization": "Bearer evaluation-local-service-token-2026-change-me"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "api_version": "v1",
        "features": ["auth_bridge", "page_shell"],
        "legacy_writes_enabled": False,
    }
