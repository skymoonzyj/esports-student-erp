from __future__ import annotations

from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.teaching.router as teaching_router
from app.audit.models import AuditEvent
from app.audit.service import AuditMetadataRejected
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES
from app.teaching.models import Cohort, CohortStatusEvent, GameProject, ProfessionalTeam


def sign_in(client: TestClient, platform_users, key: str) -> str:
    phone, password, _ = platform_users["credentials"][key]
    response = client.post(
        "/api/v1/auth/login",
        json={"phone": phone, "password": password},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def problem_without_request_id(response: Response) -> dict[str, object]:
    return {
        key: value
        for key, value in response.json().items()
        if key != "request_id"
    }


def test_authenticated_teaching_responses_are_not_cacheable(
    db_client: TestClient,
    platform_users,
) -> None:
    sign_in(db_client, platform_users, "admin")

    responses = [
        db_client.get("/api/v1/projects"),
        db_client.get("/api/v1/cohorts"),
        db_client.get("/api/v1/classes"),
    ]

    assert all(response.status_code == 200 for response in responses)
    assert {
        response.headers.get("cache-control") for response in responses
    } == {"no-store, private"}


def test_admin_manages_projects_and_professional_teams_with_one_audit_each(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    csrf = sign_in(db_client, platform_users, "admin")
    requests = {
        "project_create": "teaching-project-create",
        "project_update": "teaching-project-update",
        "team_create": "teaching-team-create",
        "team_update": "teaching-team-update",
    }

    project_created = db_client.post(
        "/api/v1/projects",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": requests["project_create"]},
        json={"name": "  API 测试项目  ", "sort_order": 70},
    )
    assert project_created.status_code == 201
    project_id = UUID(project_created.json()["id"])
    assert project_created.json()["name"] == "API 测试项目"

    project_updated = db_client.patch(
        f"/api/v1/projects/{project_id}",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": requests["project_update"]},
        json={"name": "API 测试项目更新", "sort_order": 80},
    )
    assert project_updated.status_code == 200
    assert project_updated.json()["sort_order"] == 80

    team_created = db_client.post(
        "/api/v1/professional-teams",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": requests["team_create"]},
        json={"project_id": str(project_id), "name": "  API 测试战队  "},
    )
    assert team_created.status_code == 201
    team_id = UUID(team_created.json()["id"])
    assert team_created.json()["name"] == "API 测试战队"

    team_updated = db_client.patch(
        f"/api/v1/professional-teams/{team_id}",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": requests["team_update"]},
        json={"project_id": str(project_id), "notes": "青训队"},
    )
    assert team_updated.status_code == 200
    assert team_updated.json()["project_id"] == str(project_id)
    assert team_updated.json()["notes"] == "青训队"

    projects = db_client.get(
        "/api/v1/projects",
        params={"name": "  API 测试项目更新  ", "page": 1, "page_size": 1},
    )
    teams = db_client.get(
        "/api/v1/professional-teams",
        params={
            "project_id": str(project_id),
            "name": "  API 测试战队  ",
            "page": 1,
            "page_size": 1,
        },
    )
    assert projects.status_code == 200
    assert projects.json()["total"] == 1
    assert [item["id"] for item in projects.json()["items"]] == [str(project_id)]
    assert teams.status_code == 200
    assert teams.json()["total"] == 1
    assert [item["id"] for item in teams.json()["items"]] == [str(team_id)]

    events = list(
        db.scalars(
            select(AuditEvent)
            .where(AuditEvent.request_id.in_(requests.values()))
            .order_by(AuditEvent.request_id)
        )
    )
    assert len(events) == 4
    assert {event.action for event in events} == {
        "teaching.project.create",
        "teaching.project.update",
        "teaching.professional_team.create",
        "teaching.professional_team.update",
    }
    assert all(event.outcome == "allowed" for event in events)
    assert all("name" not in event.event_metadata for event in events)
    assert all(event.actor_user_id == platform_users["accounts"]["admin"].id for event in events)
    assert {
        event.resource_id for event in events if event.resource_type == "game_project"
    } == {project_id}
    assert {
        event.resource_id
        for event in events
        if event.resource_type == "professional_team"
    } == {team_id}


def test_customer_service_reads_catalog_but_cannot_write(
    db_client: TestClient,
    platform_users,
) -> None:
    csrf = sign_in(db_client, platform_users, "customer_service")

    listed = db_client.get("/api/v1/projects")
    denied = db_client.post(
        "/api/v1/projects",
        headers={"X-CSRF-Token": csrf},
        json={"name": "客服不应创建的项目"},
    )

    assert listed.status_code == 200
    assert listed.json()["total"] >= 4
    assert denied.status_code == 403
    assert denied.json()["code"] == "permission_denied"


def test_explicit_global_denies_override_admin_catalog_grants(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    csrf = sign_in(db_client, platform_users, "admin")
    account_id = platform_users["accounts"]["admin"].id
    db.add_all(
        [
            UserPermissionOverride(
                user_id=account_id,
                permission_id=PERMISSIONS[permission].id,
                effect="deny",
                scope_kind="global",
                scope_id=None,
            )
            for permission in ("teaching.read", "projects.manage")
        ]
    )
    db.commit()

    read_denied = db_client.get("/api/v1/projects")
    write_denied = db_client.post(
        "/api/v1/projects",
        headers={"X-CSRF-Token": csrf},
        json={"name": "显式拒绝项目"},
    )

    assert read_denied.status_code == 403
    assert read_denied.json()["code"] == "permission_denied"
    assert write_denied.status_code == 403
    assert write_denied.json()["code"] == "permission_denied"


def test_inactive_project_rejects_team_without_partial_row_or_audit(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    csrf = sign_in(db_client, platform_users, "admin")
    created = db_client.post(
        "/api/v1/projects",
        headers={"X-CSRF-Token": csrf},
        json={"name": "API 停用项目"},
    )
    assert created.status_code == 201
    project_id = UUID(created.json()["id"])
    deactivated = db_client.patch(
        f"/api/v1/projects/{project_id}",
        headers={"X-CSRF-Token": csrf},
        json={"is_active": False},
    )
    assert deactivated.status_code == 200

    rejected = db_client.post(
        "/api/v1/professional-teams",
        headers={
            "X-CSRF-Token": csrf,
            "X-Request-ID": "inactive-project-team-rejected",
        },
        json={"project_id": str(project_id), "name": "不应创建的 API 战队"},
    )

    assert rejected.status_code == 409
    assert rejected.json()["code"] == "inactive_project"
    assert db.scalar(
        select(func.count(ProfessionalTeam.id)).where(
            ProfessionalTeam.project_id == project_id
        )
    ) == 0
    assert db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.request_id == "inactive-project-team-rejected"
        )
    ) == 0


def test_catalog_inputs_reject_unknown_fields(
    db_client: TestClient,
    platform_users,
) -> None:
    csrf = sign_in(db_client, platform_users, "admin")

    response = db_client.post(
        "/api/v1/projects",
        headers={"X-CSRF-Token": csrf},
        json={"name": "严格输入项目", "unexpected": "ignored?"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_catalog_audit_failure_rolls_back_the_business_write(
    db_client: TestClient,
    platform_users,
    db: Session,
    monkeypatch,
) -> None:
    csrf = sign_in(db_client, platform_users, "admin")

    def reject_audit(*args, **kwargs):
        del args, kwargs
        raise AuditMetadataRejected("simulated audit failure")

    monkeypatch.setattr(teaching_router.AuditService, "append", reject_audit)
    response = db_client.post(
        "/api/v1/projects",
        headers={"X-CSRF-Token": csrf},
        json={"name": "审计失败回滚项目"},
    )

    assert response.status_code == 500
    assert response.json()["code"] == "teaching_audit_failed"
    assert db.scalar(
        select(func.count(GameProject.id)).where(
            GameProject.name == "审计失败回滚项目"
        )
    ) == 0


def cohort_payload(name: str, *, past_start: bool = False) -> dict[str, object]:
    if past_start:
        return {
            "name": name,
            "starts_at": "2020-08-01T00:00:00+08:00",
            "ends_at": "2030-08-20T00:00:00+08:00",
            "default_check_in_at": "2020-08-01T02:00:00+08:00",
            "default_check_out_at": "2030-08-19T20:00:00+08:00",
            "auto_start_enabled": True,
            "auto_complete_enabled": False,
        }
    return {
        "name": name,
        "starts_at": "2030-08-01T00:00:00+08:00",
        "ends_at": "2030-08-20T00:00:00+08:00",
        "default_check_in_at": "2030-08-01T02:00:00+08:00",
        "default_check_out_at": "2030-08-19T20:00:00+08:00",
        "auto_start_enabled": False,
        "auto_complete_enabled": False,
    }


def create_api_class(
    client: TestClient,
    *,
    csrf: str,
    cohort_id: str,
    name: str,
) -> dict[str, object]:
    response = client.post(
        "/api/v1/classes",
        headers={"X-CSRF-Token": csrf},
        json={
            "cohort_id": cohort_id,
            "project_id": "30000000-0000-4000-8000-000000000001",
            "name": name,
            "class_type": "standard",
            "capacity": 20,
            "default_tuition_cents": 0,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_admin_manages_cohort_lifecycle_and_status_history(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    csrf = sign_in(db_client, platform_users, "admin")
    request_ids = {
        "cohort_create": "cohort-api-create",
        "cohort_update": "cohort-api-update",
        "cohort_open": "cohort-api-open",
    }
    created = db_client.post(
        "/api/v1/cohorts",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": request_ids["cohort_create"]},
        json=cohort_payload("  API 营期  "),
    )
    assert created.status_code == 201
    cohort_id = UUID(created.json()["id"])
    assert created.json()["name"] == "API 营期"
    assert created.json()["status"] == "draft"
    assert created.json()["automation_issues"] == []
    assert created.json()["enabled_class_count"] == 0
    assert created.json()["missing_primary_class_count"] == 0

    updated = db_client.patch(
        f"/api/v1/cohorts/{cohort_id}",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": request_ids["cohort_update"]},
        json={
            "name": "API 营期修正",
            "ends_at": "2030-08-21T00:00:00+08:00",
            "default_check_out_at": "2030-08-20T20:00:00+08:00",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "API 营期修正"
    assert updated.json()["ends_at"] == "2030-08-21T00:00:00+08:00"

    opened = db_client.post(
        f"/api/v1/cohorts/{cohort_id}/transitions",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": request_ids["cohort_open"]},
        json={"target": "open"},
    )
    assert opened.status_code == 200
    assert opened.json()["status"] == "open"

    listed = db_client.get(
        "/api/v1/cohorts",
        params={"name": "  API 营期修正  ", "status": "open"},
    )
    detail = db_client.get(f"/api/v1/cohorts/{cohort_id}")
    history = db_client.get(f"/api/v1/cohorts/{cohort_id}/status-events")
    assert listed.status_code == 200 and listed.json()["total"] == 1
    assert detail.status_code == 200 and detail.json()["id"] == str(cohort_id)
    assert history.status_code == 200 and history.json()["total"] == 1
    assert history.json()["items"][0]["trigger"] == "manual"
    assert history.json()["items"][0]["actor_user_id"] == str(
        platform_users["accounts"]["admin"].id
    )

    audits = list(
        db.scalars(
            select(AuditEvent).where(AuditEvent.request_id.in_(request_ids.values()))
        )
    )
    assert len(audits) == 3
    assert {event.action for event in audits} == {
        "teaching.cohort.create",
        "teaching.cohort.update",
        "teaching.cohort.transition",
    }
    assert all(event.resource_id == cohort_id for event in audits)
    assert all("name" not in event.event_metadata for event in audits)


def test_customer_service_reads_cohorts_but_cannot_manage_them(
    db_client: TestClient,
    platform_users,
) -> None:
    admin_csrf = sign_in(db_client, platform_users, "admin")
    created = db_client.post(
        "/api/v1/cohorts",
        headers={"X-CSRF-Token": admin_csrf},
        json=cohort_payload("客服只读营期"),
    )
    assert created.status_code == 201
    cohort_id = created.json()["id"]

    customer_csrf = sign_in(db_client, platform_users, "customer_service")
    listed = db_client.get("/api/v1/cohorts")
    detail = db_client.get(f"/api/v1/cohorts/{cohort_id}")
    denied = db_client.patch(
        f"/api/v1/cohorts/{cohort_id}",
        headers={"X-CSRF-Token": customer_csrf},
        json={"name": "客服不应修改"},
    )

    assert listed.status_code == 200
    assert detail.status_code == 200
    assert denied.status_code == 404
    assert denied.json()["code"] == "cohort_not_found"


def test_invalid_cohort_transition_has_stable_error_and_no_partial_history(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    csrf = sign_in(db_client, platform_users, "admin")
    created = db_client.post(
        "/api/v1/cohorts",
        headers={"X-CSRF-Token": csrf},
        json=cohort_payload("非法 API 流转营期"),
    )
    cohort_id = UUID(created.json()["id"])

    response = db_client.post(
        f"/api/v1/cohorts/{cohort_id}/transitions",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": "api-invalid-transition"},
        json={"target": "active"},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "invalid_cohort_transition"
    stored = db.get(Cohort, cohort_id)
    assert stored is not None and stored.status == "draft"
    assert db.scalar(
        select(func.count(CohortStatusEvent.id)).where(
            CohortStatusEvent.cohort_id == cohort_id
        )
    ) == 0
    assert db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.request_id == "api-invalid-transition"
        )
    ) == 0


def test_clients_cannot_supply_internal_transition_trigger(
    db_client: TestClient,
    platform_users,
) -> None:
    csrf = sign_in(db_client, platform_users, "admin")
    created = db_client.post(
        "/api/v1/cohorts",
        headers={"X-CSRF-Token": csrf},
        json=cohort_payload("内部触发字段营期"),
    )
    cohort_id = created.json()["id"]

    response = db_client.post(
        f"/api/v1/cohorts/{cohort_id}/transitions",
        headers={"X-CSRF-Token": csrf},
        json={"target": "open", "trigger": "scheduler"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_get_reconciles_the_exact_due_cohort_with_actor_null_history(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    csrf = sign_in(db_client, platform_users, "admin")
    created = db_client.post(
        "/api/v1/cohorts",
        headers={"X-CSRF-Token": csrf},
        json=cohort_payload("读取校准营期", past_start=True),
    )
    cohort_id = UUID(created.json()["id"])
    opened = db_client.post(
        f"/api/v1/cohorts/{cohort_id}/transitions",
        headers={"X-CSRF-Token": csrf},
        json={"target": "open"},
    )
    assert opened.status_code == 200 and opened.json()["status"] == "open"

    reconciled = db_client.get(
        f"/api/v1/cohorts/{cohort_id}",
        headers={"X-Request-ID": "api-direct-reconcile"},
    )
    repeated = db_client.get(
        f"/api/v1/cohorts/{cohort_id}",
        headers={"X-Request-ID": "api-direct-reconcile-repeat"},
    )

    assert reconciled.status_code == 200 and reconciled.json()["status"] == "active"
    assert repeated.status_code == 200 and repeated.json()["status"] == "active"
    automatic = list(
        db.scalars(
            select(CohortStatusEvent).where(
                CohortStatusEvent.cohort_id == cohort_id,
                CohortStatusEvent.trigger == "request_reconciliation",
            )
        )
    )
    assert len(automatic) == 1
    assert automatic[0].actor_user_id is None
    assert automatic[0].request_id == "api-direct-reconcile"
    assert db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.request_id == "api-direct-reconcile"
        )
    ) == 1


def test_admin_manages_standard_and_elite_classes_with_safe_audits(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    csrf = sign_in(db_client, platform_users, "admin")
    cohort = db_client.post(
        "/api/v1/cohorts",
        headers={"X-CSRF-Token": csrf},
        json=cohort_payload("班级 API 营期"),
    )
    assert cohort.status_code == 201
    cohort_id = cohort.json()["id"]
    project_id = "30000000-0000-4000-8000-000000000001"
    team = db_client.post(
        "/api/v1/professional-teams",
        headers={"X-CSRF-Token": csrf},
        json={"project_id": project_id, "name": "班级 API 战队"},
    )
    assert team.status_code == 201
    team_id = team.json()["id"]
    request_ids = {
        "standard": "class-api-standard-create",
        "elite": "class-api-elite-create",
        "update": "class-api-standard-update",
    }

    standard = db_client.post(
        "/api/v1/classes",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": request_ids["standard"]},
        json={
            "cohort_id": cohort_id,
            "project_id": project_id,
            "name": "  API 普通班  ",
            "class_type": "standard",
            "capacity": 20,
            "default_tuition_cents": 120_000,
        },
    )
    assert standard.status_code == 201
    standard_id = standard.json()["id"]
    assert standard.json()["professional_team_id"] is None

    elite = db_client.post(
        "/api/v1/classes",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": request_ids["elite"]},
        json={
            "cohort_id": cohort_id,
            "project_id": project_id,
            "name": "API 菁英班",
            "class_type": "elite",
            "professional_team_id": team_id,
            "capacity": 10,
            "default_tuition_cents": 260_000,
        },
    )
    assert elite.status_code == 201
    assert elite.json()["professional_team_id"] == team_id

    updated = db_client.patch(
        f"/api/v1/classes/{standard_id}",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": request_ids["update"]},
        json={"default_tuition_cents": 150_000, "is_active": False},
    )
    assert updated.status_code == 200
    assert updated.json()["default_tuition_cents"] == 150_000
    assert updated.json()["is_active"] is False

    listed = db_client.get(
        "/api/v1/classes",
        params={"cohort_id": cohort_id, "project_id": project_id},
    )
    exact = db_client.get(
        "/api/v1/classes",
        params={"name": "  API 菁英班  ", "page_size": 1},
    )
    detail = db_client.get(f"/api/v1/classes/{standard_id}")
    assert listed.status_code == 200 and listed.json()["total"] == 2
    assert exact.status_code == 200 and exact.json()["total"] == 1
    assert detail.status_code == 200 and detail.json()["id"] == standard_id

    audits = list(
        db.scalars(
            select(AuditEvent).where(AuditEvent.request_id.in_(request_ids.values()))
        )
    )
    assert len(audits) == 3
    assert {event.action for event in audits} == {
        "teaching.class.create",
        "teaching.class.update",
    }
    assert all(event.resource_type == "teaching_class" for event in audits)
    assert all(event.resource_id is not None for event in audits)
    assert all("tuition" not in str(event.event_metadata) for event in audits)


def test_customer_service_reads_classes_but_cannot_manage_them(
    db_client: TestClient,
    platform_users,
) -> None:
    admin_csrf = sign_in(db_client, platform_users, "admin")
    cohort = db_client.post(
        "/api/v1/cohorts",
        headers={"X-CSRF-Token": admin_csrf},
        json=cohort_payload("客服班级只读营期"),
    )
    cohort_id = cohort.json()["id"]
    created = db_client.post(
        "/api/v1/classes",
        headers={"X-CSRF-Token": admin_csrf},
        json={
            "cohort_id": cohort_id,
            "project_id": "30000000-0000-4000-8000-000000000001",
            "name": "客服可读班级",
            "class_type": "standard",
            "capacity": 20,
            "default_tuition_cents": 0,
        },
    )
    assert created.status_code == 201
    class_id = created.json()["id"]
    customer_csrf = sign_in(db_client, platform_users, "customer_service")

    listed = db_client.get("/api/v1/classes", params={"cohort_id": cohort_id})
    detail = db_client.get(f"/api/v1/classes/{class_id}")
    denied = db_client.patch(
        f"/api/v1/classes/{class_id}",
        headers={"X-CSRF-Token": customer_csrf},
        json={"name": "客服不应改班名"},
    )

    assert listed.status_code == 200 and listed.json()["total"] == 1
    assert detail.status_code == 200
    assert denied.status_code == 404
    assert denied.json()["code"] == "class_not_found"


def test_class_write_reconciles_exact_parent_cohort_before_mutation(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    csrf = sign_in(db_client, platform_users, "admin")
    cohort = db_client.post(
        "/api/v1/cohorts",
        headers={"X-CSRF-Token": csrf},
        json=cohort_payload("班级写入校准营期", past_start=True),
    )
    cohort_id = UUID(cohort.json()["id"])
    opened = db_client.post(
        f"/api/v1/cohorts/{cohort_id}/transitions",
        headers={"X-CSRF-Token": csrf},
        json={"target": "open"},
    )
    assert opened.status_code == 200 and opened.json()["status"] == "open"

    created = db_client.post(
        "/api/v1/classes",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": "class-parent-reconcile"},
        json={
            "cohort_id": str(cohort_id),
            "project_id": "30000000-0000-4000-8000-000000000001",
            "name": "校准后创建班级",
            "class_type": "standard",
            "capacity": 20,
            "default_tuition_cents": 0,
        },
    )

    assert created.status_code == 201
    db.expire_all()
    stored = db.get(Cohort, cohort_id)
    assert stored is not None and stored.status == "active"
    automatic = list(
        db.scalars(
            select(CohortStatusEvent).where(
                CohortStatusEvent.cohort_id == cohort_id,
                CohortStatusEvent.trigger == "request_reconciliation",
            )
        )
    )
    assert len(automatic) == 1 and automatic[0].actor_user_id is None
    assert automatic[0].request_id == "class-parent-reconcile"


def test_admin_manages_coach_assignments_with_safe_audits(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    csrf = sign_in(db_client, platform_users, "admin")
    cohort = db_client.post(
        "/api/v1/cohorts",
        headers={"X-CSRF-Token": csrf},
        json=cohort_payload("教练分配 API 营期"),
    )
    assert cohort.status_code == 201
    teaching_class = create_api_class(
        db_client,
        csrf=csrf,
        cohort_id=cohort.json()["id"],
        name="教练分配 API 班",
    )
    class_id = teaching_class["id"]
    coach = platform_users["people"]["coach"]

    eligible = db_client.get(
        f"/api/v1/classes/{class_id}/eligible-coaches",
        params={"query": "平台教练", "page": 1, "page_size": 1},
    )
    created = db_client.post(
        f"/api/v1/classes/{class_id}/coach-assignments",
        headers={
            "X-CSRF-Token": csrf,
            "X-Request-ID": "coach-assignment-api-create",
        },
        json={
            "coach_person_id": str(coach.id),
            "assignment_type": "primary",
            "valid_from": "2026-07-01T00:00:00+08:00",
        },
    )
    assert eligible.status_code == 200
    assert eligible.json()["total"] == 1
    assert eligible.json()["items"] == [
        {"id": str(coach.id), "full_name": "平台教练"}
    ]
    assert created.status_code == 201
    assert created.json()["coach_full_name"] == "平台教练"
    assignment_id = created.json()["id"]

    active = db_client.get(f"/api/v1/classes/{class_id}/coach-assignments")
    ended = db_client.post(
        f"/api/v1/classes/{class_id}/coach-assignments/{assignment_id}/end",
        headers={
            "X-CSRF-Token": csrf,
            "X-Request-ID": "coach-assignment-api-end",
        },
        json={"reason": "  教学计划调整  "},
    )
    repeated = db_client.post(
        f"/api/v1/classes/{class_id}/coach-assignments/{assignment_id}/end",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "不得覆盖历史"},
    )
    history = db_client.get(
        f"/api/v1/classes/{class_id}/coach-assignments",
        params={"include_ended": True},
    )

    assert active.status_code == 200 and active.json()["total"] == 1
    assert active.json()["items"][0]["coach_full_name"] == "平台教练"
    assert ended.status_code == 200
    assert ended.json()["end_reason"] == "教学计划调整"
    assert repeated.status_code == 409
    assert repeated.json()["code"] == "coach_assignment_already_ended"
    assert history.status_code == 200 and history.json()["total"] == 1
    events = list(
        db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.request_id.in_(
                    ["coach-assignment-api-create", "coach-assignment-api-end"]
                )
            )
            .order_by(AuditEvent.request_id)
        )
    )
    assert len(events) == 2
    assert {event.action for event in events} == {
        "teaching.coach_assignment.create",
        "teaching.coach_assignment.end",
    }
    assert all(event.resource_type == "coach_assignment" for event in events)
    assert all(event.resource_id == UUID(assignment_id) for event in events)
    assert all(event.event_metadata == {"class_id": class_id} for event in events)
    assert all("教练" not in str(event.event_metadata) for event in events)
    assert all("调整" not in str(event.event_metadata) for event in events)


def test_scoped_coach_sees_only_current_class_without_hidden_aggregates(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    admin_csrf = sign_in(db_client, platform_users, "admin")
    own_cohort = db_client.post(
        "/api/v1/cohorts",
        headers={"X-CSRF-Token": admin_csrf},
        json=cohort_payload("教练可见营期"),
    )
    hidden_cohort = db_client.post(
        "/api/v1/cohorts",
        headers={"X-CSRF-Token": admin_csrf},
        json=cohort_payload("教练隐藏营期"),
    )
    assert own_cohort.status_code == hidden_cohort.status_code == 201
    own_class = create_api_class(
        db_client,
        csrf=admin_csrf,
        cohort_id=own_cohort.json()["id"],
        name="教练可见班",
    )
    hidden_sibling = create_api_class(
        db_client,
        csrf=admin_csrf,
        cohort_id=own_cohort.json()["id"],
        name="同营期隐藏班",
    )
    create_api_class(
        db_client,
        csrf=admin_csrf,
        cohort_id=hidden_cohort.json()["id"],
        name="其他隐藏班",
    )
    assignment = db_client.post(
        f"/api/v1/classes/{own_class['id']}/coach-assignments",
        headers={"X-CSRF-Token": admin_csrf},
        json={
            "coach_person_id": str(platform_users["people"]["coach"].id),
            "assignment_type": "primary",
            "valid_from": "2026-07-01T00:00:00+08:00",
        },
    )
    assert assignment.status_code == 201

    sign_in(db_client, platform_users, "coach")
    classes = db_client.get("/api/v1/classes", params={"page": 1, "page_size": 1})
    cohorts = db_client.get("/api/v1/cohorts", params={"page": 1, "page_size": 1})
    own_detail = db_client.get(f"/api/v1/classes/{own_class['id']}")
    own_cohort_detail = db_client.get(f"/api/v1/cohorts/{own_cohort.json()['id']}")
    own_status = db_client.get(
        f"/api/v1/cohorts/{own_cohort.json()['id']}/status-events"
    )
    hidden_detail = db_client.get(f"/api/v1/classes/{hidden_sibling['id']}")
    missing_detail = db_client.get(f"/api/v1/classes/{uuid4()}")
    hidden_cohort_detail = db_client.get(
        f"/api/v1/cohorts/{hidden_cohort.json()['id']}"
    )
    missing_cohort_id = uuid4()
    missing_cohort_detail = db_client.get(f"/api/v1/cohorts/{missing_cohort_id}")
    hidden_status = db_client.get(
        f"/api/v1/cohorts/{hidden_cohort.json()['id']}/status-events"
    )
    missing_status = db_client.get(
        f"/api/v1/cohorts/{missing_cohort_id}/status-events"
    )
    hidden_filter = db_client.get(
        "/api/v1/classes",
        params={"name": "同营期隐藏班"},
    )
    catalog = db_client.get("/api/v1/projects")

    assert classes.status_code == 200
    assert classes.json()["total"] == 1
    assert [item["id"] for item in classes.json()["items"]] == [own_class["id"]]
    assert cohorts.status_code == 200 and cohorts.json()["total"] == 1
    assert own_detail.status_code == 200
    assert own_cohort_detail.status_code == 200
    assert own_status.status_code == 200
    assert cohorts.json()["items"][0]["enabled_class_count"] == 1
    assert own_cohort_detail.json()["enabled_class_count"] == 1
    assert own_cohort_detail.json()["missing_primary_class_count"] == 0
    assert hidden_filter.status_code == 200 and hidden_filter.json()["total"] == 0
    assert hidden_detail.status_code == missing_detail.status_code == 404
    assert problem_without_request_id(hidden_detail) == problem_without_request_id(
        missing_detail
    )
    assert hidden_cohort_detail.status_code == missing_cohort_detail.status_code == 404
    assert problem_without_request_id(
        hidden_cohort_detail
    ) == problem_without_request_id(missing_cohort_detail)
    assert hidden_status.status_code == missing_status.status_code == 404
    assert problem_without_request_id(hidden_status) == problem_without_request_id(
        missing_status
    )
    assert catalog.status_code == 403

    coach_account = platform_users["accounts"]["coach"]
    role_assignment = db.get(UserRole, (coach_account.id, ROLES["coach"].id))
    assert role_assignment is not None
    db.delete(role_assignment)
    db.commit()
    after_role_removal = db_client.get(f"/api/v1/classes/{own_class['id']}")
    after_role_list = db_client.get("/api/v1/classes")
    assert after_role_removal.status_code == 404
    assert after_role_list.status_code == 403

    db.add(UserRole(user_id=coach_account.id, role_id=ROLES["coach"].id))
    coach_account.status = "disabled"
    db.commit()
    after_account_disable = db_client.get(f"/api/v1/classes/{own_class['id']}")
    assert after_account_disable.status_code == 401
    assert after_account_disable.json()["code"] == "invalid_session"


def test_scoped_coach_list_reconciles_only_visible_due_cohort(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    admin_csrf = sign_in(db_client, platform_users, "admin")
    visible = db_client.post(
        "/api/v1/cohorts",
        headers={"X-CSRF-Token": admin_csrf},
        json=cohort_payload("教练到期可见营期", past_start=True),
    )
    hidden = db_client.post(
        "/api/v1/cohorts",
        headers={"X-CSRF-Token": admin_csrf},
        json=cohort_payload("教练到期隐藏营期", past_start=True),
    )
    assert visible.status_code == hidden.status_code == 201
    visible_class = create_api_class(
        db_client,
        csrf=admin_csrf,
        cohort_id=visible.json()["id"],
        name="到期可见班",
    )
    assignment = db_client.post(
        f"/api/v1/classes/{visible_class['id']}/coach-assignments",
        headers={"X-CSRF-Token": admin_csrf},
        json={
            "coach_person_id": str(platform_users["people"]["coach"].id),
            "assignment_type": "primary",
            "valid_from": "2026-07-01T00:00:00+08:00",
        },
    )
    assert assignment.status_code == 201
    opened_visible = db_client.post(
        f"/api/v1/cohorts/{visible.json()['id']}/transitions",
        headers={"X-CSRF-Token": admin_csrf},
        json={"target": "open"},
    )
    opened_hidden = db_client.post(
        f"/api/v1/cohorts/{hidden.json()['id']}/transitions",
        headers={"X-CSRF-Token": admin_csrf},
        json={"target": "open"},
    )
    assert opened_visible.json()["status"] == "open"
    assert opened_hidden.json()["status"] == "open"

    sign_in(db_client, platform_users, "coach")
    listed = db_client.get("/api/v1/cohorts", params={"status": "active"})

    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == visible.json()["id"]
    db.expire_all()
    stored_visible = db.get(Cohort, UUID(visible.json()["id"]))
    stored_hidden = db.get(Cohort, UUID(hidden.json()["id"]))
    assert stored_visible is not None and stored_visible.status == "active"
    assert stored_hidden is not None and stored_hidden.status == "open"


def test_expired_and_future_assignments_do_not_grant_class_access(
    db_client: TestClient,
    platform_users,
) -> None:
    admin_csrf = sign_in(db_client, platform_users, "admin")
    cohort = db_client.post(
        "/api/v1/cohorts",
        headers={"X-CSRF-Token": admin_csrf},
        json=cohort_payload("非当前分配营期"),
    )
    assert cohort.status_code == 201
    expired_class = create_api_class(
        db_client,
        csrf=admin_csrf,
        cohort_id=cohort.json()["id"],
        name="已过期分配班",
    )
    future_class = create_api_class(
        db_client,
        csrf=admin_csrf,
        cohort_id=cohort.json()["id"],
        name="未来分配班",
    )
    coach_id = str(platform_users["people"]["coach"].id)
    expired = db_client.post(
        f"/api/v1/classes/{expired_class['id']}/coach-assignments",
        headers={"X-CSRF-Token": admin_csrf},
        json={
            "coach_person_id": coach_id,
            "assignment_type": "assistant",
            "valid_from": "2020-01-01T00:00:00+08:00",
            "valid_until": "2021-01-01T00:00:00+08:00",
        },
    )
    future = db_client.post(
        f"/api/v1/classes/{future_class['id']}/coach-assignments",
        headers={"X-CSRF-Token": admin_csrf},
        json={
            "coach_person_id": coach_id,
            "assignment_type": "assistant",
            "valid_from": "2035-01-01T00:00:00+08:00",
        },
    )
    assert expired.status_code == future.status_code == 201

    phone, password, _ = platform_users["credentials"]["coach"]
    login = db_client.post(
        "/api/v1/auth/login",
        json={"phone": phone, "password": password},
    )
    listed = db_client.get("/api/v1/classes")
    expired_detail = db_client.get(f"/api/v1/classes/{expired_class['id']}")
    future_detail = db_client.get(f"/api/v1/classes/{future_class['id']}")

    assert login.status_code == 200
    assert "teaching.read" not in login.json()["user"]["scoped_permissions"]
    assert listed.status_code == 403
    assert expired_detail.status_code == future_detail.status_code == 404


def test_specific_class_denies_override_global_teaching_permissions(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    csrf = sign_in(db_client, platform_users, "admin")
    cohort = db_client.post(
        "/api/v1/cohorts",
        headers={"X-CSRF-Token": csrf},
        json=cohort_payload("局部拒绝营期"),
    )
    assert cohort.status_code == 201
    allowed_class = create_api_class(
        db_client,
        csrf=csrf,
        cohort_id=cohort.json()["id"],
        name="局部允许班",
    )
    denied_class = create_api_class(
        db_client,
        csrf=csrf,
        cohort_id=cohort.json()["id"],
        name="局部拒绝班",
    )
    assignment = db_client.post(
        f"/api/v1/classes/{denied_class['id']}/coach-assignments",
        headers={"X-CSRF-Token": csrf},
        json={
            "coach_person_id": str(platform_users["people"]["coach"].id),
            "assignment_type": "assistant",
            "valid_from": "2026-07-01T00:00:00+08:00",
        },
    )
    assert assignment.status_code == 201
    account_id = platform_users["accounts"]["admin"].id
    db.add_all(
        [
            UserPermissionOverride(
                user_id=account_id,
                permission_id=PERMISSIONS[key].id,
                effect="deny",
                scope_kind="assigned_class",
                scope_id=UUID(denied_class["id"]),
            )
            for key in (
                "teaching.read",
                "classes.manage",
                "cohorts.manage",
                "coach_assignments.manage",
            )
        ]
    )
    db.commit()

    listed = db_client.get("/api/v1/classes", params={"page_size": 1})
    missing = db_client.get(f"/api/v1/classes/{uuid4()}")
    hidden = db_client.get(f"/api/v1/classes/{denied_class['id']}")
    allowed_update = db_client.patch(
        f"/api/v1/classes/{allowed_class['id']}",
        headers={"X-CSRF-Token": csrf},
        json={"name": "局部允许班更新"},
    )
    denied_update = db_client.patch(
        f"/api/v1/classes/{denied_class['id']}",
        headers={"X-CSRF-Token": csrf},
        json={"name": "不得更新"},
    )
    sibling_create = db_client.post(
        "/api/v1/classes",
        headers={"X-CSRF-Token": csrf},
        json={
            "cohort_id": cohort.json()["id"],
            "project_id": "30000000-0000-4000-8000-000000000001",
            "name": "不得新增兄弟班",
            "class_type": "standard",
            "capacity": 20,
            "default_tuition_cents": 0,
        },
    )
    cohort_update = db_client.patch(
        f"/api/v1/cohorts/{cohort.json()['id']}",
        headers={"X-CSRF-Token": csrf},
        json={"name": "不得更新营期"},
    )
    transition = db_client.post(
        f"/api/v1/cohorts/{cohort.json()['id']}/transitions",
        headers={"X-CSRF-Token": csrf},
        json={"target": "open"},
    )
    assignment_create = db_client.post(
        f"/api/v1/classes/{denied_class['id']}/coach-assignments",
        headers={"X-CSRF-Token": csrf},
        json={
            "coach_person_id": str(platform_users["people"]["coach"].id),
            "assignment_type": "primary",
            "valid_from": "2026-07-01T00:00:00+08:00",
        },
    )
    assignment_end = db_client.post(
        f"/api/v1/classes/{denied_class['id']}/coach-assignments/"
        f"{assignment.json()['id']}/end",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "不得结束"},
    )

    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == allowed_class["id"]
    assert hidden.status_code == missing.status_code == 404
    assert problem_without_request_id(hidden) == problem_without_request_id(missing)
    assert allowed_update.status_code == 200
    for response in (
        denied_update,
        sibling_create,
        cohort_update,
        transition,
        assignment_create,
        assignment_end,
    ):
        assert response.status_code == 404
