from uuid import uuid4

from sqlalchemy import select

from app.rbac.models import UserPermissionOverride
from app.rbac.seed import PERMISSIONS
from app.teaching.models import GameProject
from tests.api.test_people import sign_in
from tests.services.test_administration_team_management import administration


def test_administration_catalog_lists_and_team_crud_do_not_open_teaching_or_projects(
    db, db_client, platform_users
):
    who = administration(db, platform_users)
    project = db.scalar(select(GameProject).where(GameProject.is_team_division).limit(1))
    db.commit()
    csrf = sign_in(db_client, platform_users, "customer_service")
    headers = {"X-CSRF-Token": csrf}
    for url in ("/api/v1/projects", "/api/v1/professional-teams"):
        response = db_client.get(url)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store, private"
    for url in ("/api/v1/classes", "/api/v1/cohorts", f"/api/v1/projects/{project.id}/coaches"):
        assert db_client.get(url).status_code == 403
    assert (
        db_client.post(
            "/api/v1/projects", headers=headers, json={"name": "不可创建项目"}
        ).status_code
        == 403
    )
    assert (
        db_client.patch(
            f"/api/v1/projects/{project.id}", headers=headers, json={"is_team_division": False}
        ).status_code
        == 403
    )
    created = db_client.post(
        "/api/v1/professional-teams",
        headers=headers,
        json={
            "project_id": str(project.id),
            "name": f"行政目录{uuid4().hex}",
        },
    )
    assert created.status_code == 201
    team_id = created.json()["id"]
    assert (
        db_client.patch(
            f"/api/v1/professional-teams/{team_id}", headers=headers, json={"name": "行政编辑队伍"}
        ).status_code
        == 200
    )
    db.add(
        UserPermissionOverride(
            user_id=who.account.id,
            permission_id=PERMISSIONS["professional_teams.manage"].id,
            effect="deny",
            scope_kind="global",
        )
    )
    db.commit()
    assert db_client.get("/api/v1/projects").status_code == 403
    assert db_client.get("/api/v1/professional-teams").status_code == 403
    assert (
        db_client.patch(
            f"/api/v1/professional-teams/{team_id}", headers=headers, json={"name": "不可编辑"}
        ).status_code
        == 403
    )
