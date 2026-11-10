from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.identity.models import StaffProfile
from app.rbac.models import UserRole
from tests.api.test_people import make_cn_id, sign_in


@pytest.mark.parametrize("staff_type", ["team_manager", "assistant_coach"])
def test_create_query_and_count_team_staff_without_implicit_account_roles(db_client, platform_users, db: Session, staff_type: str) -> None:
    csrf = sign_in(db_client, platform_users, "admin")
    before = db_client.get("/api/v1/people/counts").json()
    response = db_client.post("/api/v1/people/staff", headers={"X-CSRF-Token": csrf}, json={
        "full_name": "合成队内工作人员", "identity_number": make_cn_id("19900102", 914),
        "sex": "female", "date_of_birth": "1990-01-02", "phone": "13900139914",
        "email": "team-staff@example.com", "staff_type": staff_type,
    })
    assert response.status_code == 201
    person_id = UUID(response.json()["id"])
    assert response.json()["staff_type"] == staff_type
    assert response.json()["roles"] == []
    assert db.scalar(select(UserAccount.id).where(UserAccount.person_id == person_id)) is None
    detail = db_client.get(f"/api/v1/people/{person_id}")
    assert detail.status_code == 200
    assert detail.json()["staff_type"] == staff_type
    listing = db_client.get("/api/v1/people", params={"type": "staff", "staff_type": staff_type})
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()["items"]] == [str(person_id)]
    counts = db_client.get("/api/v1/people/counts").json()
    count_key = "staff_team_manager" if staff_type == "team_manager" else "assistant_coach"
    assert counts[count_key] == before[count_key] + 1
    assert counts["team_manager"] == before["team_manager"]


def test_edit_primary_staff_type_keeps_concurrent_roles_unchanged(db_client, platform_users, db: Session) -> None:
    person_id = platform_users["people"]["coach"].id
    account_id = platform_users["accounts"]["coach"].id
    roles = set(db.scalars(select(UserRole.role_id).where(UserRole.user_id == account_id)))
    csrf = sign_in(db_client, platform_users, "admin")
    for staff_type in ("team_manager", "assistant_coach", "coach"):
        response = db_client.patch(f"/api/v1/people/{person_id}", headers={"X-CSRF-Token": csrf}, json={"staff_type": staff_type})
        assert response.status_code == 200
        assert response.json()["staff_type"] == staff_type
        db.expire_all()
        assert db.get(StaffProfile, person_id).staff_type == staff_type
        assert set(db.scalars(select(UserRole.role_id).where(UserRole.user_id == account_id))) == roles
