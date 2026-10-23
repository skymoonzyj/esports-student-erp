from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace
from uuid import UUID, uuid4
from zipfile import ZipFile

import anyio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, event, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.audit.service import AuditService
from app.auth.dependencies import require_standard_account
from app.auth.models import UserAccount
from app.auth.schemas import AccountCreate
from app.core.database import get_db
from app.food_preferences.dependencies import get_food_reporting_db
from app.food_preferences.export_service import (
    FoodPreferenceExportService,
    PreparedFoodPreferenceExport,
    PrepareFoodPreferenceExportCommand,
)
from app.food_preferences.models import (
    FoodCuisineOption,
    PersonFoodPreference,
    PersonFoodPreferenceCuisine,
    PersonFoodPreferenceDish,
    PersonFoodPreferenceVersion,
)
from app.food_preferences.preference_service import (
    FoodPreferenceService,
    ReplaceFoodPreferenceCommand,
)
from app.food_preferences.reporting import FoodPreferenceReportingService
from app.food_preferences.router import CompletedFoodPreferenceExportResponse
from app.food_preferences.schemas import CuisineSelectionInput
from app.identity.models import (
    GuardianRelationship,
    Person,
    StaffProfile,
    StudentProfile,
)
from app.identity.schemas import VisitorCreate
from app.main import create_app
from app.rbac.models import UserPermissionOverride
from app.rbac.seed import PERMISSIONS
from app.security.passwords import PasswordService


def _make_cn_id(birth: str, sequence: int) -> str:
    body = f"110105{birth}{sequence:03d}"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    checks = ("1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2")
    checksum = sum(int(digit) * weight for digit, weight in zip(body, weights))
    return body + checks[checksum % 11]


def _assert_private(response) -> None:
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["x-content-type-options"] == "nosniff"


def _use_transactional_reporting_db(client: TestClient, db: Session) -> None:
    def override_reporting_db():
        yield db

    client.app.dependency_overrides[get_food_reporting_db] = override_reporting_db


@dataclass
class FoodApiScenario:
    db: Session
    users: dict[str, object]
    active: FoodCuisineOption
    inactive: FoodCuisineOption
    other: FoodCuisineOption
    visitor_phone: str
    visitor_password: str
    visitor_account: UserAccount

    def login(self, client: TestClient, key: str) -> str:
        if key == "visitor":
            phone, password = self.visitor_phone, self.visitor_password
        else:
            phone, password, _ = self.users["credentials"][key]  # type: ignore[index]
        response = client.post(
            "/api/v1/auth/login",
            json={"identifier": phone, "password": password},
        )
        assert response.status_code == 200
        return response.json()["csrf_token"]

    @property
    def accounts(self) -> dict[str, UserAccount]:
        return self.users["accounts"]  # type: ignore[return-value]

    @property
    def people(self):
        return self.users["people"]

    def selection(
        self,
        option: FoodCuisineOption | None = None,
        *,
        custom_name: str | None = None,
    ) -> CuisineSelectionInput:
        return CuisineSelectionInput(
            cuisine_option_id=(option or self.active).id,
            custom_name=custom_name,
        )

    def prefill(
        self,
        person_id: UUID,
        *,
        dishes: tuple[str, ...] = ("宫保鸡丁",),
    ) -> None:
        FoodPreferenceService(self.db).replace(
            person_id,
            ReplaceFoodPreferenceCommand(
                expected_version=0,
                cuisines=(self.selection(),),
                dishes=dishes,
                actor_user_id=self.accounts["admin"].id,
                source="profile_admin",
                request_id=f"food-api-prefill-{uuid4()}",
            ),
        )


@pytest.fixture
def food_api_scenario(db: Session, platform_users) -> FoodApiScenario:
    marker = uuid4().hex[:10]
    admin_id = platform_users["accounts"]["admin"].id
    active = FoodCuisineOption(
        name=f"接口川菜{marker[:4]}",
        normalized_name=f"接口川菜{marker[:4]}".casefold(),
        sort_order=10,
        is_active=True,
        is_other=False,
        created_by_user_id=admin_id,
        updated_by_user_id=admin_id,
    )
    inactive = FoodCuisineOption(
        name=f"接口停用{marker[:4]}",
        normalized_name=f"接口停用{marker[:4]}".casefold(),
        sort_order=20,
        is_active=False,
        is_other=False,
        created_by_user_id=admin_id,
        updated_by_user_id=admin_id,
    )
    db.add_all([active, inactive])
    db.flush()
    other = db.scalar(select(FoodCuisineOption).where(FoodCuisineOption.is_other.is_(True)))
    assert other is not None

    visitor_phone = f"139{uuid4().int % 100_000_000:08d}"
    visitor_password = "Visitor2026Aa"
    visitor = platform_users["identity_service"].create_visitor(
        VisitorCreate(
            full_name="餐饮接口访客",
            identity_number=_make_cn_id("19920203", 126),
            sex="female",
            date_of_birth=date(1992, 2, 3),
            phone=visitor_phone,
            email=f"visitor-{marker}@example.com",
            visit_reason="餐饮接口测试",
        )
    )
    created = platform_users["account_service"].create_account(
        AccountCreate(
            person_id=visitor.id,
            login_phone=visitor_phone,
            email=f"visitor-{marker}@example.com",
        )
    )
    visitor_account = db.get(UserAccount, created.account.id)
    assert visitor_account is not None
    visitor_account.password_hash = PasswordService().hash(visitor_password)
    visitor_account.force_password_change = False
    db.commit()
    return FoodApiScenario(
        db=db,
        users=platform_users,
        active=active,
        inactive=inactive,
        other=other,
        visitor_phone=visitor_phone,
        visitor_password=visitor_password,
        visitor_account=visitor_account,
    )


@pytest.mark.parametrize(
    ("account_key", "onboarding_required"),
    [
        ("self_student", True),
        ("coach", True),
        ("visitor", True),
        ("guardian", False),
    ],
)
def test_me_profile_is_private_closed_and_uses_server_eligibility(
    db_client: TestClient,
    food_api_scenario: FoodApiScenario,
    account_key: str,
    onboarding_required: bool,
) -> None:
    scenario = food_api_scenario
    scenario.login(db_client, account_key)

    response = db_client.get("/api/v1/me/food-preferences")

    assert response.status_code == 200
    _assert_private(response)
    body = response.json()
    assert set(body) == {
        "profile",
        "onboarding_required",
        "onboarding_completed_at",
    }
    assert body["onboarding_required"] is onboarding_required
    assert body["onboarding_completed_at"] is None
    assert set(body["profile"]) == {
        "person_id",
        "version",
        "cuisines",
        "dishes",
        "updated_at",
        "allowed_actions",
        "access_revision",
    }
    expected_person_id = (
        scenario.visitor_account.person_id
        if account_key == "visitor"
        else scenario.accounts[account_key].person_id
    )
    assert body["profile"]["person_id"] == str(expected_person_id)
    assert body["profile"]["allowed_actions"] == ["update"]
    assert len(body["profile"]["access_revision"]) == 64
    forbidden = {"phone", "email", "identity", "account", "relationship", "actor"}
    assert not forbidden.intersection(body["profile"])


def test_guardian_staff_multi_identity_still_requires_onboarding(
    db_client: TestClient,
    food_api_scenario: FoodApiScenario,
) -> None:
    scenario = food_api_scenario
    guardian = scenario.people["guardian"]
    scenario.db.add(StaffProfile(person_id=guardian.id, staff_type="other"))
    scenario.db.commit()
    scenario.login(db_client, "guardian")

    response = db_client.get("/api/v1/me/food-preferences")

    assert response.status_code == 200
    assert response.json()["onboarding_required"] is True
    _assert_private(response)


def test_admin_family_and_private_missing_person_reads(
    db_client: TestClient,
    food_api_scenario: FoodApiScenario,
) -> None:
    scenario = food_api_scenario
    target_id = scenario.people["own_student"].id
    scenario.prefill(target_id)

    scenario.login(db_client, "admin")
    admin = db_client.get(f"/api/v1/people/{target_id}/food-preferences")
    assert admin.status_code == 200
    assert admin.json()["dishes"] == ["宫保鸡丁"]
    assert admin.json()["allowed_actions"] == ["update"]

    scenario.login(db_client, "guardian")
    family = db_client.get(f"/api/v1/people/{target_id}/food-preferences")
    headers = {"X-Request-ID": "food-private-equivalence"}
    hidden = db_client.get(
        f"/api/v1/people/{scenario.people['other_student'].id}/food-preferences",
        headers=headers,
    )
    missing = db_client.get(
        f"/api/v1/people/{uuid4()}/food-preferences",
        headers=headers,
    )

    assert family.status_code == 200
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json() == missing.json()
    assert hidden.json()["code"] == "food_preference_not_found"
    for response in (admin, family, hidden, missing):
        _assert_private(response)


@pytest.mark.parametrize("method", ["get", "put"])
@pytest.mark.parametrize(
    "failure_kind",
    ["missing", "deleted", "expired_family", "wrong_scope", "target_deny"],
)
def test_generic_person_private_failures_are_equivalent_and_write_nothing(
    db_client: TestClient,
    food_api_scenario: FoodApiScenario,
    method: str,
    failure_kind: str,
) -> None:
    scenario = food_api_scenario
    actor = scenario.accounts["guardian"]
    target_id = scenario.people["other_student"].id
    permission = f"food_preferences.{'read' if method == 'get' else 'update'}"
    if failure_kind == "missing":
        target_id = uuid4()
    elif failure_kind == "deleted":
        deleted = scenario.db.get(Person, target_id)
        assert deleted is not None
        deleted.deleted_at = datetime(2026, 8, 11, 8, tzinfo=UTC)
    elif failure_kind == "expired_family":
        target_id = scenario.people["own_student"].id
        relationship = scenario.db.scalar(
            select(GuardianRelationship).where(
                GuardianRelationship.guardian_person_id == actor.person_id,
                GuardianRelationship.student_person_id == target_id,
            )
        )
        assert relationship is not None
        relationship.valid_until = date(2026, 8, 10)
    elif failure_kind == "target_deny":
        target_id = scenario.people["minor_student"].id
        scenario.db.add(
            UserPermissionOverride(
                user_id=actor.id,
                permission_id=PERMISSIONS[permission].id,
                effect="deny",
                scope_kind="person",
                scope_id=target_id,
            )
        )
    scenario.db.commit()
    csrf = scenario.login(db_client, "guardian")
    request_headers = {
        "X-Request-ID": f"food-private-{method}",
        "X-CSRF-Token": csrf,
    }
    before = (
        scenario.db.scalar(select(func.count(PersonFoodPreference.person_id))),
        scenario.db.scalar(select(func.count(PersonFoodPreferenceVersion.id))),
        scenario.db.scalar(select(func.count(AuditEvent.id))),
        scenario.db.scalar(
            select(func.count(UserAccount.id)).where(
                UserAccount.food_preferences_onboarding_completed_at.is_not(None)
            )
        ),
    )

    if method == "get":
        failed = db_client.get(
            f"/api/v1/people/{target_id}/food-preferences",
            headers=request_headers,
        )
        missing = db_client.get(
            f"/api/v1/people/{uuid4()}/food-preferences",
            headers=request_headers,
        )
    else:
        payload = {
            "expected_version": 0,
            "cuisines": [{"cuisine_option_id": str(scenario.active.id)}],
            "dishes": ["不可写入菜品"],
        }
        failed = db_client.put(
            f"/api/v1/people/{target_id}/food-preferences",
            headers=request_headers,
            json=payload,
        )
        missing = db_client.put(
            f"/api/v1/people/{uuid4()}/food-preferences",
            headers=request_headers,
            json=payload,
        )

    assert failed.status_code == missing.status_code == 404
    assert failed.json() == missing.json()
    assert failed.json()["code"] == "food_preference_not_found"
    assert (
        scenario.db.scalar(select(func.count(PersonFoodPreference.person_id))),
        scenario.db.scalar(select(func.count(PersonFoodPreferenceVersion.id))),
        scenario.db.scalar(select(func.count(AuditEvent.id))),
        scenario.db.scalar(
            select(func.count(UserAccount.id)).where(
                UserAccount.food_preferences_onboarding_completed_at.is_not(None)
            )
        ),
    ) == before
    _assert_private(failed)
    _assert_private(missing)


def test_global_deny_returns_operation_403_before_generic_read_or_write(
    db_client: TestClient,
    food_api_scenario: FoodApiScenario,
) -> None:
    scenario = food_api_scenario
    coach = scenario.accounts["coach"]
    scenario.db.add_all(
        [
            UserPermissionOverride(
                user_id=coach.id,
                permission_id=PERMISSIONS[permission].id,
                effect="deny",
                scope_kind="global",
                scope_id=None,
            )
            for permission in ("food_preferences.read", "food_preferences.update")
        ]
    )
    scenario.db.commit()
    csrf = scenario.login(db_client, "coach")
    before = (
        scenario.db.scalar(select(func.count(PersonFoodPreference.person_id))),
        scenario.db.scalar(select(func.count(PersonFoodPreferenceVersion.id))),
        scenario.db.scalar(select(func.count(AuditEvent.id))),
        scenario.db.scalar(
            select(func.count(UserAccount.id)).where(
                UserAccount.food_preferences_onboarding_completed_at.is_not(None)
            )
        ),
    )
    target_id = scenario.people["other_student"].id

    read = db_client.get(f"/api/v1/people/{target_id}/food-preferences")
    write = db_client.put(
        f"/api/v1/people/{target_id}/food-preferences",
        headers={"X-CSRF-Token": csrf},
        json={
            "expected_version": 0,
            "cuisines": [{"cuisine_option_id": str(scenario.active.id)}],
            "dishes": ["不可写入菜品"],
        },
    )

    assert read.status_code == write.status_code == 403
    assert read.json()["code"] == write.json()["code"] == (
        "food_preference_permission_denied"
    )
    assert (
        scenario.db.scalar(select(func.count(PersonFoodPreference.person_id))),
        scenario.db.scalar(select(func.count(PersonFoodPreferenceVersion.id))),
        scenario.db.scalar(select(func.count(AuditEvent.id))),
        scenario.db.scalar(
            select(func.count(UserAccount.id)).where(
                UserAccount.food_preferences_onboarding_completed_at.is_not(None)
            )
        ),
    ) == before
    _assert_private(read)
    _assert_private(write)


def test_self_onboarding_confirmation_noop_then_clear_keeps_completion(
    db_client: TestClient,
    food_api_scenario: FoodApiScenario,
) -> None:
    scenario = food_api_scenario
    student = scenario.accounts["self_student"]
    scenario.prefill(student.person_id)

    csrf = scenario.login(db_client, "self_student")
    confirmation = db_client.put(
        "/api/v1/me/food-preferences",
        headers={"X-CSRF-Token": csrf},
        json={
            "expected_version": 1,
            "cuisines": [{"cuisine_option_id": str(scenario.active.id)}],
            "dishes": ["宫保鸡丁"],
        },
    )
    assert confirmation.status_code == 200
    assert confirmation.json()["profile"]["version"] == 1
    assert confirmation.json()["onboarding_required"] is False
    completed_at = confirmation.json()["onboarding_completed_at"]
    assert completed_at is not None
    assert scenario.db.scalar(
        select(func.count(PersonFoodPreferenceVersion.id)).where(
            PersonFoodPreferenceVersion.person_id == student.person_id
        )
    ) == 1

    cleared = db_client.put(
        "/api/v1/me/food-preferences",
        headers={"X-CSRF-Token": csrf},
        json={"expected_version": 1, "cuisines": [], "dishes": []},
    )
    assert cleared.status_code == 200
    assert cleared.json()["profile"]["version"] == 2
    assert datetime.fromisoformat(
        cleared.json()["onboarding_completed_at"]
    ) == datetime.fromisoformat(completed_at)
    _assert_private(confirmation)
    _assert_private(cleared)


@pytest.mark.parametrize(
    ("cuisines", "dishes"),
    [([], []), (["active"], []), ([], ["宫保鸡丁"])],
)
def test_incomplete_self_put_is_closed_422_with_zero_writes(
    db_client: TestClient,
    food_api_scenario: FoodApiScenario,
    cuisines: list[str],
    dishes: list[str],
) -> None:
    scenario = food_api_scenario
    account = scenario.accounts["self_student"]
    csrf = scenario.login(db_client, "self_student")
    cuisine_payload = (
        [{"cuisine_option_id": str(scenario.active.id)}] if cuisines else []
    )
    before = (
        scenario.db.scalar(select(func.count(PersonFoodPreference.person_id))),
        scenario.db.scalar(select(func.count(PersonFoodPreferenceVersion.id))),
        scenario.db.scalar(select(func.count(AuditEvent.id))),
    )

    response = db_client.put(
        "/api/v1/me/food-preferences",
        headers={"X-CSRF-Token": csrf},
        json={
            "expected_version": 0,
            "cuisines": cuisine_payload,
            "dishes": dishes,
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "food_preference_onboarding_incomplete"
    assert (
        scenario.db.scalar(select(func.count(PersonFoodPreference.person_id))),
        scenario.db.scalar(select(func.count(PersonFoodPreferenceVersion.id))),
        scenario.db.scalar(select(func.count(AuditEvent.id))),
    ) == before
    scenario.db.refresh(account)
    assert account.food_preferences_onboarding_completed_at is None
    _assert_private(response)


def test_csrf_strict_body_and_stale_version_fail_without_partial_writes(
    db_client: TestClient,
    food_api_scenario: FoodApiScenario,
) -> None:
    scenario = food_api_scenario
    student = scenario.accounts["self_student"]
    scenario.prefill(student.person_id)
    csrf = scenario.login(db_client, "self_student")
    payload = {
        "expected_version": 1,
        "cuisines": [{"cuisine_option_id": str(scenario.active.id)}],
        "dishes": ["新菜品"],
    }
    counts_before = (
        scenario.db.scalar(select(func.count(PersonFoodPreferenceVersion.id))),
        scenario.db.scalar(select(func.count(AuditEvent.id))),
    )

    no_csrf = db_client.put("/api/v1/me/food-preferences", json=payload)
    strict = db_client.put(
        "/api/v1/me/food-preferences",
        headers={"X-CSRF-Token": csrf},
        json={**payload, "unexpected": True},
    )
    stale = db_client.put(
        "/api/v1/me/food-preferences",
        headers={"X-CSRF-Token": csrf},
        json={**payload, "expected_version": 0},
    )

    assert no_csrf.status_code == 403
    assert no_csrf.json()["code"] == "csrf_invalid"
    assert strict.status_code == 422
    assert strict.json()["code"] == "validation_error"
    assert stale.status_code == 409
    assert stale.json()["code"] == "food_preference_version_changed"
    assert "新菜品" not in stale.text and "宫保鸡丁" not in stale.text
    assert (
        scenario.db.scalar(select(func.count(PersonFoodPreferenceVersion.id))),
        scenario.db.scalar(select(func.count(AuditEvent.id))),
    ) == counts_before
    scenario.db.refresh(student)
    assert student.food_preferences_onboarding_completed_at is None
    for response in (no_csrf, strict, stale):
        _assert_private(response)


def test_generic_admin_put_never_completes_target_onboarding(
    db_client: TestClient,
    food_api_scenario: FoodApiScenario,
) -> None:
    scenario = food_api_scenario
    student = scenario.accounts["self_student"]
    csrf = scenario.login(db_client, "admin")

    response = db_client.put(
        f"/api/v1/people/{student.person_id}/food-preferences",
        headers={"X-CSRF-Token": csrf},
        json={
            "expected_version": 0,
            "cuisines": [{"cuisine_option_id": str(scenario.active.id)}],
            "dishes": ["管理员代填"],
        },
    )

    assert response.status_code == 200
    assert response.json()["version"] == 1
    scenario.db.refresh(student)
    assert student.food_preferences_onboarding_completed_at is None
    _assert_private(response)


def test_guardian_family_put_uses_guardian_source_without_completing_student(
    db_client: TestClient,
    food_api_scenario: FoodApiScenario,
) -> None:
    scenario = food_api_scenario
    student = scenario.accounts["minor_student"]
    csrf = scenario.login(db_client, "guardian")

    response = db_client.put(
        f"/api/v1/people/{student.person_id}/food-preferences",
        headers={"X-CSRF-Token": csrf},
        json={
            "expected_version": 0,
            "cuisines": [{"cuisine_option_id": str(scenario.active.id)}],
            "dishes": ["监护人代填"],
        },
    )

    assert response.status_code == 200
    history = scenario.db.scalar(
        select(PersonFoodPreferenceVersion).where(
            PersonFoodPreferenceVersion.person_id == student.person_id,
            PersonFoodPreferenceVersion.version == 1,
        )
    )
    assert history is not None and history.source == "profile_guardian"
    event = scenario.db.scalar(
        select(AuditEvent).where(
            AuditEvent.resource_id == student.person_id,
            AuditEvent.action == "food_preferences.profile.update",
        )
    )
    assert event is not None and event.event_metadata["source"] == "profile_guardian"
    scenario.db.refresh(student)
    assert student.food_preferences_onboarding_completed_at is None
    _assert_private(response)


def test_family_get_reauthorizes_after_relationship_ends_before_profile_lock(
    db_client: TestClient,
    food_api_scenario: FoodApiScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = food_api_scenario
    guardian = scenario.accounts["guardian"]
    student = scenario.accounts["minor_student"]
    relationship = scenario.db.scalar(
        select(GuardianRelationship).where(
            GuardianRelationship.guardian_person_id == guardian.person_id,
            GuardianRelationship.student_person_id == student.person_id,
        )
    )
    assert relationship is not None
    scenario.prefill(student.person_id)
    scenario.login(db_client, "guardian")
    original = FoodPreferenceService.get

    def end_then_get(self, person_id, **kwargs):
        relationship.ended_at = datetime(2026, 8, 11, 9, tzinfo=UTC)
        self.db.flush()
        return original(self, person_id, **kwargs)

    monkeypatch.setattr(FoodPreferenceService, "get", end_then_get)

    response = db_client.get(
        f"/api/v1/people/{student.person_id}/food-preferences"
    )

    assert response.status_code == 404
    assert response.json()["code"] == "food_preference_not_found"
    assert "宫保鸡丁" not in response.text
    _assert_private(response)


def test_self_get_reauthorizes_after_read_deny_added_before_profile_lock(
    db_client: TestClient,
    food_api_scenario: FoodApiScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = food_api_scenario
    account = scenario.accounts["coach"]
    scenario.prefill(account.person_id)
    scenario.login(db_client, "coach")
    original = FoodPreferenceService.get_self

    def deny_then_get_self(self, **kwargs):
        self.db.add(
            UserPermissionOverride(
                user_id=account.id,
                permission_id=PERMISSIONS["food_preferences.read"].id,
                effect="deny",
                scope_kind="self",
                scope_id=None,
            )
        )
        self.db.flush()
        return original(self, **kwargs)

    monkeypatch.setattr(FoodPreferenceService, "get_self", deny_then_get_self)

    response = db_client.get("/api/v1/me/food-preferences")

    assert response.status_code == 403
    assert response.json()["code"] == "food_preference_permission_denied"
    assert "宫保鸡丁" not in response.text
    _assert_private(response)


def test_self_put_reauthorizes_after_a_deny_added_after_initial_route_check(
    db_client: TestClient,
    food_api_scenario: FoodApiScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = food_api_scenario
    account = scenario.accounts["coach"]
    csrf = scenario.login(db_client, "coach")
    before = (
        scenario.db.scalar(select(func.count(PersonFoodPreference.person_id))),
        scenario.db.scalar(select(func.count(PersonFoodPreferenceVersion.id))),
        scenario.db.scalar(select(func.count(AuditEvent.id))),
    )
    original = FoodPreferenceService.replace_self

    def deny_then_replace(self, **kwargs):
        self.db.add(
            UserPermissionOverride(
                user_id=account.id,
                permission_id=PERMISSIONS["food_preferences.update"].id,
                effect="deny",
                scope_kind="self",
                scope_id=None,
            )
        )
        self.db.flush()
        return original(self, **kwargs)

    monkeypatch.setattr(FoodPreferenceService, "replace_self", deny_then_replace)

    response = db_client.put(
        "/api/v1/me/food-preferences",
        headers={"X-CSRF-Token": csrf},
        json={
            "expected_version": 0,
            "cuisines": [{"cuisine_option_id": str(scenario.active.id)}],
            "dishes": ["撤权后不可写入"],
        },
    )

    assert response.status_code == 403
    assert response.json()["code"] == "food_preference_permission_denied"
    assert (
        scenario.db.scalar(select(func.count(PersonFoodPreference.person_id))),
        scenario.db.scalar(select(func.count(PersonFoodPreferenceVersion.id))),
        scenario.db.scalar(select(func.count(AuditEvent.id))),
    ) == before
    scenario.db.refresh(account)
    assert account.food_preferences_onboarding_completed_at is None
    _assert_private(response)


def test_incomplete_self_put_reauthorizes_before_onboarding_validation(
    db_client: TestClient,
    food_api_scenario: FoodApiScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = food_api_scenario
    account = scenario.accounts["coach"]
    csrf = scenario.login(db_client, "coach")
    before = (
        scenario.db.scalar(
            select(func.count(PersonFoodPreference.person_id)).where(
                PersonFoodPreference.person_id == account.person_id
            )
        ),
        scenario.db.scalar(
            select(func.count(PersonFoodPreferenceCuisine.person_id)).where(
                PersonFoodPreferenceCuisine.person_id == account.person_id
            )
        ),
        scenario.db.scalar(
            select(func.count(PersonFoodPreferenceDish.person_id)).where(
                PersonFoodPreferenceDish.person_id == account.person_id
            )
        ),
        scenario.db.scalar(
            select(func.count(PersonFoodPreferenceVersion.id)).where(
                PersonFoodPreferenceVersion.person_id == account.person_id
            )
        ),
        scenario.db.scalar(select(func.count(AuditEvent.id))),
    )
    assert before[:4] == (0, 0, 0, 0)
    assert account.food_preferences_onboarding_completed_at is None
    original = FoodPreferenceService.replace_self

    def deny_then_replace(self, **kwargs):
        self.db.add(
            UserPermissionOverride(
                user_id=account.id,
                permission_id=PERMISSIONS["food_preferences.update"].id,
                effect="deny",
                scope_kind="self",
                scope_id=None,
            )
        )
        self.db.flush()
        return original(self, **kwargs)

    monkeypatch.setattr(FoodPreferenceService, "replace_self", deny_then_replace)

    response = db_client.put(
        "/api/v1/me/food-preferences",
        headers={"X-CSRF-Token": csrf},
        json={"expected_version": 0, "cuisines": [], "dishes": []},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "food_preference_permission_denied"
    assert (
        scenario.db.scalar(
            select(func.count(PersonFoodPreference.person_id)).where(
                PersonFoodPreference.person_id == account.person_id
            )
        ),
        scenario.db.scalar(
            select(func.count(PersonFoodPreferenceCuisine.person_id)).where(
                PersonFoodPreferenceCuisine.person_id == account.person_id
            )
        ),
        scenario.db.scalar(
            select(func.count(PersonFoodPreferenceDish.person_id)).where(
                PersonFoodPreferenceDish.person_id == account.person_id
            )
        ),
        scenario.db.scalar(
            select(func.count(PersonFoodPreferenceVersion.id)).where(
                PersonFoodPreferenceVersion.person_id == account.person_id
            )
        ),
        scenario.db.scalar(select(func.count(AuditEvent.id))),
    ) == before
    scenario.db.refresh(account)
    assert account.food_preferences_onboarding_completed_at is None
    _assert_private(response)


def test_family_put_reauthorizes_after_relationship_ends_after_initial_route_check(
    db_client: TestClient,
    food_api_scenario: FoodApiScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = food_api_scenario
    guardian = scenario.accounts["guardian"]
    student = scenario.accounts["minor_student"]
    relationship = scenario.db.scalar(
        select(GuardianRelationship).where(
            GuardianRelationship.guardian_person_id == guardian.person_id,
            GuardianRelationship.student_person_id == student.person_id,
        )
    )
    assert relationship is not None
    csrf = scenario.login(db_client, "guardian")
    before = (
        scenario.db.scalar(select(func.count(PersonFoodPreference.person_id))),
        scenario.db.scalar(select(func.count(PersonFoodPreferenceVersion.id))),
        scenario.db.scalar(select(func.count(AuditEvent.id))),
    )
    original = FoodPreferenceService.replace

    def end_then_replace(self, person_id, command, **kwargs):
        relationship.ended_at = datetime(2026, 8, 11, 9, tzinfo=UTC)
        self.db.flush()
        return original(self, person_id, command, **kwargs)

    monkeypatch.setattr(FoodPreferenceService, "replace", end_then_replace)

    response = db_client.put(
        f"/api/v1/people/{student.person_id}/food-preferences",
        headers={"X-CSRF-Token": csrf},
        json={
            "expected_version": 0,
            "cuisines": [{"cuisine_option_id": str(scenario.active.id)}],
            "dishes": ["关系结束后不可写入"],
        },
    )

    assert response.status_code == 404
    assert response.json()["code"] == "food_preference_not_found"
    assert (
        scenario.db.scalar(select(func.count(PersonFoodPreference.person_id))),
        scenario.db.scalar(select(func.count(PersonFoodPreferenceVersion.id))),
        scenario.db.scalar(select(func.count(AuditEvent.id))),
    ) == before
    scenario.db.refresh(student)
    assert student.food_preferences_onboarding_completed_at is None
    _assert_private(response)


def test_self_deny_is_account_403_and_does_not_leak_profile(
    db_client: TestClient,
    food_api_scenario: FoodApiScenario,
) -> None:
    scenario = food_api_scenario
    coach = scenario.accounts["coach"]
    scenario.db.add(
        UserPermissionOverride(
            user_id=coach.id,
            permission_id=PERMISSIONS["food_preferences.read"].id,
            effect="deny",
            scope_kind="self",
            scope_id=None,
        )
    )
    scenario.db.commit()
    scenario.login(db_client, "coach")

    response = db_client.get("/api/v1/me/food-preferences")

    assert response.status_code == 403
    assert response.json()["code"] == "food_preference_permission_denied"
    assert "profile" not in response.text
    _assert_private(response)


def test_update_self_deny_keeps_read_but_hides_update_and_onboarding(
    db_client: TestClient,
    food_api_scenario: FoodApiScenario,
) -> None:
    scenario = food_api_scenario
    coach = scenario.accounts["coach"]
    scenario.db.add(
        UserPermissionOverride(
            user_id=coach.id,
            permission_id=PERMISSIONS["food_preferences.update"].id,
            effect="deny",
            scope_kind="self",
            scope_id=None,
        )
    )
    scenario.db.commit()
    csrf = scenario.login(db_client, "coach")
    before = (
        scenario.db.scalar(select(func.count(PersonFoodPreference.person_id))),
        scenario.db.scalar(select(func.count(PersonFoodPreferenceVersion.id))),
        scenario.db.scalar(select(func.count(AuditEvent.id))),
    )

    response = db_client.get("/api/v1/me/food-preferences")
    denied_write = db_client.put(
        "/api/v1/me/food-preferences",
        headers={"X-CSRF-Token": csrf},
        json={
            "expected_version": 0,
            "cuisines": [{"cuisine_option_id": str(scenario.active.id)}],
            "dishes": ["拒绝写入菜品"],
        },
    )

    assert response.status_code == 200
    assert response.json()["profile"]["allowed_actions"] == []
    assert response.json()["onboarding_required"] is False
    assert denied_write.status_code == 403
    assert denied_write.json()["code"] == "food_preference_permission_denied"
    assert (
        scenario.db.scalar(select(func.count(PersonFoodPreference.person_id))),
        scenario.db.scalar(select(func.count(PersonFoodPreferenceVersion.id))),
        scenario.db.scalar(select(func.count(AuditEvent.id))),
    ) == before
    scenario.db.refresh(coach)
    assert coach.food_preferences_onboarding_completed_at is None
    _assert_private(response)
    _assert_private(denied_write)


def test_catalog_management_and_suggestions_are_closed_and_reauthorized(
    db_client: TestClient,
    food_api_scenario: FoodApiScenario,
) -> None:
    scenario = food_api_scenario
    scenario.prefill(scenario.people["adult_without_account"].id, dishes=("宫保鸡丁",))
    scenario.prefill(scenario.people["other_student"].id, dishes=("宫保鸡丁",))
    admin_csrf = scenario.login(db_client, "admin")

    catalog = db_client.get(
        "/api/v1/food-preferences/cuisines",
        params={"include_inactive": True},
    )
    created = db_client.post(
        "/api/v1/food-preferences/cuisines",
        headers={"X-CSRF-Token": admin_csrf},
        json={"name": "  新  菜系  ", "sort_order": 30},
    )
    assert created.status_code == 201
    created_id = created.json()["id"]
    patched = db_client.patch(
        f"/api/v1/food-preferences/cuisines/{created_id}",
        headers={"X-CSRF-Token": admin_csrf},
        json={"is_active": False},
    )
    suggestions = db_client.get(
        "/api/v1/food-preferences/dish-suggestions",
        params={"query": "宫保", "limit": 10},
    )

    assert catalog.status_code == 200
    assert set(catalog.json()) == {"items", "allowed_actions", "access_revision"}
    assert catalog.json()["allowed_actions"] == ["create", "update", "disable"]
    assert patched.status_code == 200
    assert patched.json()["is_active"] is False
    assert suggestions.status_code == 200
    assert suggestions.json()["items"] == [{"name": "宫保鸡丁"}]
    assert set(suggestions.json()) == {"items", "access_revision"}
    assert "count" not in suggestions.text and "person" not in suggestions.text

    scenario.login(db_client, "self_student")
    active_only = db_client.get("/api/v1/food-preferences/cuisines")
    forbidden_inactive = db_client.get(
        "/api/v1/food-preferences/cuisines",
        params={"include_inactive": True},
    )
    invalid_limit = db_client.get(
        "/api/v1/food-preferences/dish-suggestions",
        params={"query": "宫保", "limit": 21},
    )
    assert active_only.status_code == 200
    assert all(item["is_active"] for item in active_only.json()["items"])
    assert active_only.json()["allowed_actions"] == []
    assert forbidden_inactive.status_code == 403
    assert invalid_limit.status_code == 422
    for response in (
        catalog,
        created,
        patched,
        suggestions,
        active_only,
        forbidden_inactive,
        invalid_limit,
    ):
        _assert_private(response)


def test_statistics_are_global_closed_and_aggregate_before_pagination(
    db_client: TestClient,
    food_api_scenario: FoodApiScenario,
) -> None:
    scenario = food_api_scenario
    _use_transactional_reporting_db(db_client, scenario.db)
    for profile in scenario.db.scalars(select(StudentProfile)):
        profile.status = "active"
    scenario.db.commit()
    scenario.prefill(
        scenario.people["own_student"].id,
        dishes=("统计共同菜",),
    )
    scenario.prefill(
        scenario.people["other_student"].id,
        dishes=("统计共同菜",),
    )
    scenario.login(db_client, "admin")

    first = db_client.get(
        "/api/v1/food-preferences/statistics",
        params={
            "population": "students",
            "include_inactive": False,
            "page": 1,
            "page_size": 1,
        },
    )
    second = db_client.get(
        "/api/v1/food-preferences/statistics",
        params={
            "population": "students",
            "include_inactive": False,
            "page": 2,
            "page_size": 1,
        },
    )

    assert first.status_code == second.status_code == 200
    assert set(first.json()) == {
        "population",
        "include_inactive",
        "total_people",
        "filled_people",
        "unfilled_people",
        "filled_ratio",
        "cuisines",
        "dishes",
        "people",
        "access_revision",
    }
    assert first.json()["population"] == "students"
    assert first.json()["include_inactive"] is False
    assert first.json()["total_people"] >= 2
    assert first.json()["filled_people"] == 2
    assert first.json()["unfilled_people"] == first.json()["total_people"] - 2
    assert first.json()["dishes"][0]["label"] == "统计共同菜"
    assert first.json()["dishes"][0]["count"] == 2
    assert first.json()["dishes"] == second.json()["dishes"]
    assert first.json()["cuisines"] == second.json()["cuisines"]
    assert first.json()["filled_ratio"] == second.json()["filled_ratio"]
    assert first.json()["people"]["page"] == 1
    assert second.json()["people"]["page"] == 2
    assert first.json()["people"]["page_size"] == 1
    assert first.json()["people"]["total"] == first.json()["total_people"]
    assert len(first.json()["people"]["items"]) == 1
    assert len(second.json()["people"]["items"]) == 1
    assert set(first.json()["people"]["items"][0]) == {
        "person_id",
        "full_name",
        "person_types",
        "status",
        "cuisines",
        "dishes",
        "updated_at",
    }
    assert len(first.json()["access_revision"]) == 64
    response_text = first.text.casefold()
    for forbidden_field in (
        "phone",
        "email",
        "identity",
        "account",
        "relationship",
    ):
        assert forbidden_field not in response_text
    _assert_private(first)
    _assert_private(second)


def test_statistics_reject_scoped_only_export_and_invalid_queries_privately(
    db_client: TestClient,
    food_api_scenario: FoodApiScenario,
) -> None:
    scenario = food_api_scenario
    _use_transactional_reporting_db(db_client, scenario.db)
    account = scenario.accounts["self_student"]
    scenario.db.add(
        UserPermissionOverride(
            user_id=account.id,
            permission_id=PERMISSIONS["food_preferences.export"].id,
            effect="allow",
            scope_kind="person",
            scope_id=account.person_id,
        )
    )
    scenario.db.commit()
    scenario.login(db_client, "self_student")
    audit_before = scenario.db.scalar(select(func.count(AuditEvent.id)))

    forbidden = db_client.get(
        "/api/v1/food-preferences/statistics",
        params={"population": "students"},
    )
    scenario.login(db_client, "admin")
    invalid = [
        db_client.get(
            "/api/v1/food-preferences/statistics",
            params={"population": "coaches"},
        ),
        db_client.get(
            "/api/v1/food-preferences/statistics",
            params={"population": "students", "page": 0},
        ),
        db_client.get(
            "/api/v1/food-preferences/statistics",
            params={"population": "students", "page_size": 101},
        ),
    ]

    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "food_preference_permission_denied"
    assert all(response.status_code == 422 for response in invalid)
    assert all(response.json()["code"] == "validation_error" for response in invalid)
    assert scenario.db.scalar(select(func.count(AuditEvent.id))) == audit_before
    for response in (forbidden, *invalid):
        _assert_private(response)


def test_statistics_reauthorizes_in_reporting_session_before_population_query(
    db_client: TestClient,
    food_api_scenario: FoodApiScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = food_api_scenario
    _use_transactional_reporting_db(db_client, scenario.db)
    account = scenario.accounts["admin"]
    scenario.login(db_client, "admin")
    original = FoodPreferenceReportingService.statistics

    def deny_then_statistics(self, **kwargs):
        self.db.add(
            UserPermissionOverride(
                user_id=account.id,
                permission_id=PERMISSIONS["food_preferences.export"].id,
                effect="deny",
                scope_kind="global",
                scope_id=None,
            )
        )
        self.db.flush()
        return original(self, **kwargs)

    monkeypatch.setattr(
        FoodPreferenceReportingService,
        "statistics",
        deny_then_statistics,
    )

    response = db_client.get(
        "/api/v1/food-preferences/statistics",
        params={"population": "all_people", "include_inactive": True},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "food_preference_permission_denied"
    _assert_private(response)


@pytest.mark.parametrize(
    ("format", "extension", "media_type"),
    (
        (
            "xlsx",
            "xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
        ("csv_zip", "zip", "application/zip"),
    ),
)
def test_export_is_private_attachment_and_audits_only_after_full_response(
    db_client: TestClient,
    food_api_scenario: FoodApiScenario,
    monkeypatch: pytest.MonkeyPatch,
    format: str,
    extension: str,
    media_type: str,
) -> None:
    scenario = food_api_scenario
    _use_transactional_reporting_db(db_client, scenario.db)
    scenario.prefill(
        scenario.people["own_student"].id,
        dishes=("@导出菜品",),
    )
    csrf = scenario.login(db_client, "admin")
    occurred_at = datetime(2026, 8, 11, 12, 13, 14, tzinfo=UTC)
    monkeypatch.setattr("app.food_preferences.router._now", lambda: occurred_at)
    request_id = f"food-export-api-{format}"
    expected_person_count = scenario.db.scalar(
        select(func.count(StudentProfile.person_id))
    )
    assert expected_person_count is not None

    response = db_client.post(
        "/api/v1/food-preferences/exports",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": request_id},
        json={
            "population": "students",
            "include_inactive": True,
            "format": format,
            "purpose": "  安排   下一期营地餐食  ",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == media_type
    assert response.headers["content-disposition"] == (
        f'attachment; filename="food-preferences-students-20260811T121314Z.{extension}"'
    )
    assert response.headers["x-request-id"] == request_id
    _assert_private(response)
    if format == "xlsx":
        assert response.content.startswith(b"PK")
    else:
        with ZipFile(BytesIO(response.content)) as archive:
            assert archive.namelist() == [
                "人员偏好明细.csv",
                "菜系统计.csv",
                "菜品统计.csv",
            ]
    audit = scenario.db.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "food_preferences.export",
            AuditEvent.request_id == request_id,
        )
    )
    assert audit is not None
    assert audit.purpose == "安排 下一期营地餐食"
    assert audit.event_metadata == {
        "population": "students",
        "include_inactive": True,
        "format": format,
        "person_count": expected_person_count,
        "filled_count": 1,
        "file_sha256": sha256(response.content).hexdigest(),
    }


def test_export_rejects_csrf_scoped_permission_and_strict_invalid_bodies(
    db_client: TestClient,
    food_api_scenario: FoodApiScenario,
) -> None:
    scenario = food_api_scenario
    _use_transactional_reporting_db(db_client, scenario.db)
    admin_csrf = scenario.login(db_client, "admin")
    audit_before = scenario.db.scalar(select(func.count(AuditEvent.id))) or 0
    payload = {
        "population": "students",
        "include_inactive": False,
        "format": "xlsx",
        "purpose": "安排下一期营地餐食",
    }

    no_csrf = db_client.post("/api/v1/food-preferences/exports", json=payload)
    invalid_payloads = (
        {**payload, "unexpected": True},
        {**payload, "include_inactive": "false"},
        {**payload, "population": "coaches"},
        {**payload, "format": "csv"},
        {**payload, "purpose": "短"},
        {**payload, "purpose": "x" * 201},
        {**payload, "purpose": "     "},
    )
    invalid = [
        db_client.post(
            "/api/v1/food-preferences/exports",
            headers={"X-CSRF-Token": admin_csrf},
            json=item,
        )
        for item in invalid_payloads
    ]

    account = scenario.accounts["self_student"]
    scenario.db.add(
        UserPermissionOverride(
            user_id=account.id,
            permission_id=PERMISSIONS["food_preferences.export"].id,
            effect="allow",
            scope_kind="person",
            scope_id=account.person_id,
        )
    )
    scenario.db.commit()
    scoped_csrf = scenario.login(db_client, "self_student")
    scoped = db_client.post(
        "/api/v1/food-preferences/exports",
        headers={"X-CSRF-Token": scoped_csrf},
        json=payload,
    )

    assert no_csrf.status_code == 403
    assert no_csrf.json()["code"] == "csrf_invalid"
    assert all(response.status_code == 422 for response in invalid)
    assert all(response.json()["code"] == "validation_error" for response in invalid)
    assert scoped.status_code == 403
    assert scoped.json()["code"] == "food_preference_permission_denied"
    assert (scenario.db.scalar(select(func.count(AuditEvent.id))) or 0) == audit_before
    for response in (no_csrf, *invalid, scoped):
        _assert_private(response)


def test_export_reauthorizes_before_snapshot_and_writes_no_success_audit(
    db_client: TestClient,
    food_api_scenario: FoodApiScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = food_api_scenario
    _use_transactional_reporting_db(db_client, scenario.db)
    account = scenario.accounts["admin"]
    csrf = scenario.login(db_client, "admin")
    original = FoodPreferenceExportService.prepare

    def deny_then_prepare(self, command):
        self.db.add(
            UserPermissionOverride(
                user_id=account.id,
                permission_id=PERMISSIONS["food_preferences.export"].id,
                effect="deny",
                scope_kind="global",
                scope_id=None,
            )
        )
        self.db.flush()
        return original(self, command)

    monkeypatch.setattr(FoodPreferenceExportService, "prepare", deny_then_prepare)

    response = db_client.post(
        "/api/v1/food-preferences/exports",
        headers={"X-CSRF-Token": csrf},
        json={
            "population": "all_people",
            "include_inactive": True,
            "format": "xlsx",
            "purpose": "安排下一期营地餐食",
        },
    )

    assert response.status_code == 403
    assert response.json()["code"] == "food_preference_permission_denied"
    assert scenario.db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.action == "food_preferences.export"
        )
    ) == 0
    _assert_private(response)


@pytest.mark.parametrize("failure_kind", ("renderer", "audit"))
def test_export_preflight_failure_is_closed_private_and_has_zero_success_audit(
    test_settings,
    food_api_scenario: FoodApiScenario,
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
) -> None:
    scenario = food_api_scenario
    app = create_app(test_settings)

    def override_db():
        yield scenario.db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_food_reporting_db] = override_db
    secret = "food-export-secret-13900001111"

    def explode(*args, **kwargs):
        del args, kwargs
        raise RuntimeError(secret)

    if failure_kind == "renderer":
        monkeypatch.setattr("app.food_preferences.export_service.render_xlsx", explode)
    else:
        monkeypatch.setattr(AuditService, "append", explode)
    with TestClient(app, raise_server_exceptions=False) as client:
        csrf = scenario.login(client, "admin")
        response = client.post(
            "/api/v1/food-preferences/exports",
            headers={"X-CSRF-Token": csrf},
            json={
                "population": "students",
                "include_inactive": False,
                "format": "xlsx",
                "purpose": "安排下一期营地餐食",
            },
        )

    assert response.status_code == 500
    assert response.json()["code"] == "food_preference_export_failed"
    assert secret not in response.text
    assert scenario.db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.action == "food_preferences.export"
        )
    ) == 0
    _assert_private(response)


@dataclass
class _FakePreparedExport:
    content: bytes = b"export-payload"
    media_type: str = "application/zip"
    filename: str = "food-preferences-students-20260811T121314Z.zip"
    complete_calls: int = 0
    cancel_calls: int = 0
    event_loop_thread_id: int | None = None
    complete_thread_id: int | None = None
    cancel_thread_ids: list[int] = field(default_factory=list)
    cancel_errors: list[RuntimeError] = field(default_factory=list)

    def complete(self) -> None:
        self.complete_calls += 1
        self.complete_thread_id = threading.get_ident()

    def cancel(self) -> None:
        self.cancel_calls += 1
        self.cancel_thread_ids.append(threading.get_ident())
        if self.cancel_errors:
            raise self.cancel_errors.pop(0)


@dataclass
class _BlockingPreparedExport(_FakePreparedExport):
    finalizer_started: threading.Event = field(default_factory=threading.Event)
    finalizer_release: threading.Event = field(default_factory=threading.Event)
    finalizer_finished: threading.Event = field(default_factory=threading.Event)
    finalizer_error: RuntimeError | None = None

    def _block_finalizer(self) -> None:
        self.finalizer_started.set()
        self.finalizer_release.wait()
        self.finalizer_finished.set()
        if self.finalizer_error is not None:
            raise self.finalizer_error

    def complete(self) -> None:
        super().complete()
        self._block_finalizer()

    def cancel(self) -> None:
        super().cancel()
        self._block_finalizer()


def _run_export_response(
    prepared: _FakePreparedExport,
    *,
    fail_on: str | None = None,
) -> tuple[list[object], RuntimeError | None]:
    events: list[object] = []
    response = CompletedFoodPreferenceExportResponse(prepared)  # type: ignore[arg-type]

    async def receive() -> dict[str, str]:
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        if message["type"] == "http.response.body":
            terminal = message.get("more_body") is False
            events.append("terminal" if terminal else "payload")
            if fail_on == ("terminal" if terminal else "payload"):
                raise RuntimeError(f"{fail_on} send failed")
        else:
            events.append(message["type"])

    async def invoke() -> RuntimeError | None:
        prepared.event_loop_thread_id = threading.get_ident()
        try:
            await response(
                {
                    "type": "http",
                    "asgi": {"version": "3.0", "spec_version": "2.4"},
                },
                receive,
                send,
            )
        except RuntimeError as exc:
            return exc
        return None

    error = asyncio.run(invoke())
    events.append((prepared.complete_calls, prepared.cancel_calls))
    return events, error


def test_export_response_commits_only_after_terminal_frame_succeeds() -> None:
    prepared = _FakePreparedExport()

    events, error = _run_export_response(prepared)

    assert error is None
    assert events == ["http.response.start", "payload", "terminal", (1, 0)]
    assert prepared.complete_thread_id != prepared.event_loop_thread_id


def test_export_response_cancels_off_event_loop_thread_on_body_send_failure() -> None:
    prepared = _FakePreparedExport()

    _events, error = _run_export_response(prepared, fail_on="payload")

    assert isinstance(error, RuntimeError)
    assert prepared.cancel_calls == 1
    assert len(prepared.cancel_thread_ids) == 1
    assert prepared.cancel_thread_ids[0] != prepared.event_loop_thread_id


def test_export_response_waits_for_cancel_after_task_cancel_during_payload() -> None:
    prepared = _BlockingPreparedExport()
    response = CompletedFoodPreferenceExportResponse(prepared)  # type: ignore[arg-type]

    async def invoke() -> None:
        payload_started = asyncio.Event()
        hold_payload_send = asyncio.Event()

        async def receive() -> dict[str, str]:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        async def send(message: dict[str, object]) -> None:
            if (
                message["type"] == "http.response.body"
                and message.get("more_body") is not False
            ):
                payload_started.set()
                await hold_payload_send.wait()

        response_task = asyncio.create_task(
            response(
                {
                    "type": "http",
                    "asgi": {"version": "3.0", "spec_version": "2.4"},
                },
                receive,
                send,
            )
        )
        await payload_started.wait()
        response_task.cancel()
        finalizer_started = asyncio.create_task(
            asyncio.to_thread(prepared.finalizer_started.wait)
        )
        try:
            done, _pending = await asyncio.wait(
                {response_task, finalizer_started},
                return_when=asyncio.FIRST_COMPLETED,
            )
            assert finalizer_started in done
            assert prepared.cancel_calls == 1
            assert prepared.complete_calls == 0
            assert not response_task.done()
        finally:
            prepared.finalizer_started.set()
            prepared.finalizer_release.set()

        with pytest.raises(asyncio.CancelledError):
            await response_task
        assert prepared.finalizer_finished.is_set()
        assert prepared.cancel_calls == 1

    asyncio.run(invoke())


def test_export_response_preserves_cancel_error_after_payload_task_cancel() -> None:
    expected_error = RuntimeError("cancel failed")
    prepared = _BlockingPreparedExport(finalizer_error=expected_error)
    response = CompletedFoodPreferenceExportResponse(prepared)  # type: ignore[arg-type]

    async def invoke() -> None:
        payload_started = asyncio.Event()
        hold_payload_send = asyncio.Event()

        async def receive() -> dict[str, str]:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        async def send(message: dict[str, object]) -> None:
            if (
                message["type"] == "http.response.body"
                and message.get("more_body") is not False
            ):
                payload_started.set()
                await hold_payload_send.wait()

        response_task = asyncio.create_task(
            response(
                {
                    "type": "http",
                    "asgi": {"version": "3.0", "spec_version": "2.4"},
                },
                receive,
                send,
            )
        )
        await payload_started.wait()
        response_task.cancel()
        finalizer_started = asyncio.create_task(
            asyncio.to_thread(prepared.finalizer_started.wait)
        )
        try:
            done, _pending = await asyncio.wait(
                {response_task, finalizer_started},
                return_when=asyncio.FIRST_COMPLETED,
            )
            assert finalizer_started in done
            assert not response_task.done()
        finally:
            prepared.finalizer_started.set()
            prepared.finalizer_release.set()

        with pytest.raises(RuntimeError) as exc_info:
            await response_task
        assert exc_info.value is expected_error
        assert isinstance(exc_info.value.__cause__, asyncio.CancelledError)
        assert prepared.finalizer_finished.is_set()
        assert prepared.cancel_calls == 1
        assert prepared.complete_calls == 0

    asyncio.run(invoke())


@pytest.mark.parametrize("with_finalizer_error", (False, True))
def test_export_response_preserves_send_cancel_and_finalizer_error_chain(
    with_finalizer_error: bool,
) -> None:
    send_error = RuntimeError("payload send failed")
    finalizer_error = RuntimeError("cancel rollback failed")
    prepared = _BlockingPreparedExport(
        finalizer_error=finalizer_error if with_finalizer_error else None
    )
    response = CompletedFoodPreferenceExportResponse(prepared)  # type: ignore[arg-type]

    async def invoke() -> None:
        async def receive() -> dict[str, str]:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        async def send(message: dict[str, object]) -> None:
            if (
                message["type"] == "http.response.body"
                and message.get("more_body") is not False
            ):
                raise send_error

        response_task = asyncio.create_task(
            response(
                {
                    "type": "http",
                    "asgi": {"version": "3.0", "spec_version": "2.4"},
                },
                receive,
                send,
            )
        )
        finalizer_started = asyncio.create_task(
            asyncio.to_thread(prepared.finalizer_started.wait)
        )
        try:
            done, _pending = await asyncio.wait(
                {response_task, finalizer_started},
                return_when=asyncio.FIRST_COMPLETED,
            )
            assert finalizer_started in done
            response_task.cancel()
            cancel_processed = asyncio.Event()
            asyncio.get_running_loop().call_soon(cancel_processed.set)
            await cancel_processed.wait()
            assert not response_task.done()
        finally:
            prepared.finalizer_started.set()
            prepared.finalizer_release.set()

        expected_error = finalizer_error if with_finalizer_error else send_error
        with pytest.raises(RuntimeError) as exc_info:
            await response_task
        assert exc_info.value is expected_error
        assert prepared.finalizer_finished.is_set()
        assert prepared.cancel_calls == 1
        assert prepared.complete_calls == 0

        if with_finalizer_error:
            assert finalizer_error.__cause__ is send_error
        cancellation_error = send_error.__cause__
        assert isinstance(cancellation_error, asyncio.CancelledError)
        assert cancellation_error.__cause__ is None

    asyncio.run(invoke())


def test_export_response_waits_for_complete_after_terminal_task_cancel() -> None:
    prepared = _BlockingPreparedExport()
    response = CompletedFoodPreferenceExportResponse(prepared)  # type: ignore[arg-type]

    async def invoke() -> None:
        async def receive() -> dict[str, str]:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        async def send(_message: dict[str, object]) -> None:
            return

        response_task = asyncio.create_task(
            response(
                {
                    "type": "http",
                    "asgi": {"version": "3.0", "spec_version": "2.4"},
                },
                receive,
                send,
            )
        )
        finalizer_started = asyncio.create_task(
            asyncio.to_thread(prepared.finalizer_started.wait)
        )
        try:
            done, _pending = await asyncio.wait(
                {response_task, finalizer_started},
                return_when=asyncio.FIRST_COMPLETED,
            )
            assert finalizer_started in done
            assert prepared.complete_calls == 1
            assert prepared.cancel_calls == 0

            response_task.cancel()
            cancel_processed = asyncio.Event()
            asyncio.get_running_loop().call_soon(cancel_processed.set)
            await cancel_processed.wait()
            assert not response_task.done()
        finally:
            prepared.finalizer_started.set()
            prepared.finalizer_release.set()

        with pytest.raises(asyncio.CancelledError):
            await response_task
        assert prepared.finalizer_finished.is_set()
        assert prepared.complete_calls == 1

    asyncio.run(invoke())


def test_export_response_propagates_anyio_scope_cancel_after_finalizer() -> None:
    prepared = _BlockingPreparedExport()
    response = CompletedFoodPreferenceExportResponse(prepared)  # type: ignore[arg-type]

    async def invoke() -> None:
        response_returned = False
        scope_holder: dict[str, anyio.CancelScope] = {}

        async def cancel_when_finalizer_starts() -> None:
            await asyncio.to_thread(prepared.finalizer_started.wait)
            scope_holder["scope"].cancel()
            prepared.finalizer_release.set()

        async def receive() -> dict[str, str]:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        async def send(_message: dict[str, object]) -> None:
            return

        trigger_task = asyncio.create_task(cancel_when_finalizer_starts())
        try:
            with anyio.CancelScope() as cancel_scope:
                scope_holder["scope"] = cancel_scope
                await response(
                    {
                        "type": "http",
                        "asgi": {"version": "3.0", "spec_version": "2.4"},
                    },
                    receive,
                    send,
                )
                response_returned = True
        finally:
            prepared.finalizer_started.set()
            prepared.finalizer_release.set()
            await trigger_task

        assert cancel_scope.cancel_called
        assert cancel_scope.cancelled_caught
        assert not response_returned
        assert prepared.finalizer_finished.is_set()
        assert prepared.complete_calls == 1
        assert prepared.cancel_calls == 0

    asyncio.run(invoke())


def test_export_response_preserves_finalizer_error_after_task_cancel() -> None:
    expected_error = RuntimeError("complete failed")
    prepared = _BlockingPreparedExport(finalizer_error=expected_error)
    response = CompletedFoodPreferenceExportResponse(prepared)  # type: ignore[arg-type]

    async def invoke() -> None:
        async def receive() -> dict[str, str]:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        async def send(_message: dict[str, object]) -> None:
            return

        response_task = asyncio.create_task(
            response(
                {
                    "type": "http",
                    "asgi": {"version": "3.0", "spec_version": "2.4"},
                },
                receive,
                send,
            )
        )
        finalizer_started = asyncio.create_task(
            asyncio.to_thread(prepared.finalizer_started.wait)
        )
        try:
            done, _pending = await asyncio.wait(
                {response_task, finalizer_started},
                return_when=asyncio.FIRST_COMPLETED,
            )
            assert finalizer_started in done
            response_task.cancel()
            cancel_processed = asyncio.Event()
            asyncio.get_running_loop().call_soon(cancel_processed.set)
            await cancel_processed.wait()
            assert not response_task.done()
        finally:
            prepared.finalizer_started.set()
            prepared.finalizer_release.set()

        with pytest.raises(RuntimeError) as exc_info:
            await response_task
        assert exc_info.value is expected_error
        assert prepared.finalizer_finished.is_set()
        assert prepared.complete_calls == 1
        assert prepared.cancel_calls == 0

    asyncio.run(invoke())


def test_export_response_cancels_on_asgi_23_disconnect_after_payload() -> None:
    prepared = _FakePreparedExport()
    response = CompletedFoodPreferenceExportResponse(prepared)  # type: ignore[arg-type]
    events: list[str] = []

    async def invoke() -> None:
        prepared.event_loop_thread_id = threading.get_ident()
        payload_sent = asyncio.Event()
        hold_payload_send = asyncio.Event()

        async def receive() -> dict[str, str]:
            await payload_sent.wait()
            events.append("disconnect")
            return {"type": "http.disconnect"}

        async def send(message: dict[str, object]) -> None:
            if message["type"] == "http.response.body":
                terminal = message.get("more_body") is False
                events.append("terminal" if terminal else "payload")
                if not terminal:
                    payload_sent.set()
                    await hold_payload_send.wait()
            else:
                events.append(str(message["type"]))

        await response(
            {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
            },
            receive,
            send,
        )

    asyncio.run(invoke())

    assert events == ["http.response.start", "payload", "disconnect"]
    assert prepared.complete_calls == 0
    assert prepared.cancel_calls == 1
    assert prepared.cancel_thread_ids[0] != prepared.event_loop_thread_id


def test_export_response_preserves_first_cancel_error_and_calls_cancel_once() -> None:
    first_error = RuntimeError("first cancel error")
    second_error = RuntimeError("second cancel error")
    prepared = _FakePreparedExport(cancel_errors=[first_error, second_error])
    response = CompletedFoodPreferenceExportResponse(prepared)  # type: ignore[arg-type]

    async def invoke() -> RuntimeError | None:
        payload_sent = asyncio.Event()
        hold_payload_send = asyncio.Event()

        async def receive() -> dict[str, str]:
            await payload_sent.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict[str, object]) -> None:
            if (
                message["type"] == "http.response.body"
                and message.get("more_body") is not False
            ):
                payload_sent.set()
                await hold_payload_send.wait()

        try:
            await response(
                {
                    "type": "http",
                    "asgi": {"version": "3.0", "spec_version": "2.3"},
                },
                receive,
                send,
            )
        except RuntimeError as exc:
            return exc
        return None

    error = asyncio.run(invoke())

    assert error is first_error
    assert prepared.cancel_calls == 1


def test_export_response_preserves_cancel_error_after_body_send_failure() -> None:
    expected_error = RuntimeError("cancel failed")
    prepared = _FakePreparedExport(cancel_errors=[expected_error])

    _events, error = _run_export_response(prepared, fail_on="payload")

    assert error is expected_error
    assert isinstance(error.__cause__, RuntimeError)
    assert str(error.__cause__) == "payload send failed"
    assert prepared.cancel_calls == 1


@pytest.mark.parametrize("fail_on", ("payload", "terminal"))
def test_export_response_rolls_back_when_any_body_frame_fails(fail_on: str) -> None:
    prepared = _FakePreparedExport()

    events, error = _run_export_response(prepared, fail_on=fail_on)

    assert isinstance(error, RuntimeError)
    assert prepared.complete_calls == 0
    assert prepared.cancel_calls == 1
    assert events[-1] == (0, 1)


@dataclass(frozen=True, slots=True)
class _ExportLifecycleResult:
    reporting_is_request_db: bool
    isolation_level: str
    transaction_active_at_terminal: bool
    observer_count_at_terminal: int
    observer_count_after_response: int
    commit_calls: int
    rollback_calls: int
    cancel_calls: int
    lifecycle_events: tuple[str, ...]
    error: BaseException | None


def _run_export_lifecycle_request(
    *,
    test_settings: object,
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    failure_mode: str | None,
) -> _ExportLifecycleResult:
    app = create_app(test_settings)  # type: ignore[arg-type]
    request_id = f"food-export-lifecycle-{uuid4().hex}"
    request_sessions: list[Session] = []
    reporting_sessions: list[Session] = []
    isolation_levels: list[str] = []
    lifecycle_events: list[str] = []
    counts = {"commit": 0, "rollback": 0, "cancel": 0}

    def override_db():
        with Session(postgres_engine) as request_db:
            request_sessions.append(request_db)
            yield request_db

    def override_standard_account():
        return SimpleNamespace(account=SimpleNamespace(id=uuid4()))

    def prepare_lifecycle_export(
        self: FoodPreferenceExportService,
        command: PrepareFoodPreferenceExportCommand,
    ) -> PreparedFoodPreferenceExport:
        reporting_sessions.append(self.db)
        isolation_levels.append(self.db.connection().get_isolation_level().upper())

        def after_commit(_: Session) -> None:
            counts["commit"] += 1
            lifecycle_events.append("commit")

        def after_rollback(_: Session) -> None:
            counts["rollback"] += 1
            lifecycle_events.append("rollback")

        event.listen(self.db, "after_commit", after_commit)
        event.listen(self.db, "after_rollback", after_rollback)
        AuditService(self.db).append(
            actor_user_id=None,
            action="food_preferences.export",
            resource_type="food_preference_export",
            resource_id=None,
            purpose=command.purpose,
            outcome="allowed",
            request_id=command.request_id,
            metadata={},
        )
        content = b"food-export-lifecycle-payload"
        return PreparedFoodPreferenceExport(
            content=content,
            media_type="application/zip",
            filename="food-preferences-students-20260811T121314Z.zip",
            file_sha256=sha256(content).hexdigest(),
            db=self.db,
        )

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[require_standard_account] = override_standard_account
    monkeypatch.setattr(
        FoodPreferenceExportService,
        "prepare",
        prepare_lifecycle_export,
    )

    if failure_mode is not None:

        def record_cancel(_: PreparedFoodPreferenceExport) -> None:
            counts["cancel"] += 1
            lifecycle_events.append("cancel")

        monkeypatch.setattr(PreparedFoodPreferenceExport, "cancel", record_cancel)

    async def invoke() -> tuple[int, int, bool, BaseException | None]:
        body = json.dumps(
            {
                "population": "students",
                "include_inactive": False,
                "format": "csv_zip",
                "purpose": "验证导出事务生命周期",
            }
        ).encode()
        request_delivered = False
        payload_sent = asyncio.Event()
        hold_payload_send = asyncio.Event()
        observed_at_terminal = -1
        active_at_terminal = False

        async def receive() -> dict[str, object]:
            nonlocal request_delivered
            if not request_delivered:
                request_delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            if failure_mode == "disconnect":
                await payload_sent.wait()
                return {"type": "http.disconnect"}
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        with Session(postgres_engine) as observer:

            def audit_count() -> int:
                return (
                    observer.scalar(
                        select(func.count(AuditEvent.id)).where(
                            AuditEvent.request_id == request_id
                        )
                    )
                    or 0
                )

            async def send(message: dict[str, object]) -> None:
                nonlocal active_at_terminal, observed_at_terminal
                if message["type"] != "http.response.body":
                    return
                terminal = message.get("more_body") is False
                if not terminal:
                    payload_sent.set()
                    if failure_mode == "disconnect":
                        await hold_payload_send.wait()
                    return
                assert len(reporting_sessions) == 1
                transaction = reporting_sessions[0].get_transaction()
                active_at_terminal = transaction is not None and transaction.is_active
                observed_at_terminal = audit_count()
                if failure_mode == "terminal_send_failure":
                    raise RuntimeError("terminal send failed")

            error: BaseException | None = None
            try:
                await app(
                    {
                        "type": "http",
                        "asgi": {
                            "version": "3.0",
                            "spec_version": (
                                "2.3" if failure_mode == "disconnect" else "2.4"
                            ),
                        },
                        "http_version": "1.1",
                        "method": "POST",
                        "scheme": "http",
                        "path": "/api/v1/food-preferences/exports",
                        "raw_path": b"/api/v1/food-preferences/exports",
                        "query_string": b"",
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode()),
                            (b"x-request-id", request_id.encode()),
                        ],
                        "client": ("127.0.0.1", 12345),
                        "server": ("testserver", 80),
                        "root_path": "",
                        "state": {},
                    },
                    receive,
                    send,
                )
            except BaseException as exc:  # noqa: BLE001 - assert exact ASGI failure below
                error = exc
            return (
                observed_at_terminal,
                audit_count(),
                active_at_terminal,
                error,
            )

    try:
        observed_at_terminal, observed_after, active_at_terminal, error = asyncio.run(
            invoke()
        )
        assert len(request_sessions) == len(reporting_sessions) == 1
        assert len(isolation_levels) == 1
        reporting_is_request = reporting_sessions[0] is request_sessions[0]
        return _ExportLifecycleResult(
            reporting_is_request_db=reporting_is_request,
            isolation_level=isolation_levels[0],
            transaction_active_at_terminal=active_at_terminal,
            observer_count_at_terminal=observed_at_terminal,
            observer_count_after_response=observed_after,
            commit_calls=counts["commit"],
            rollback_calls=counts["rollback"],
            cancel_calls=counts["cancel"],
            lifecycle_events=tuple(lifecycle_events),
            error=error,
        )
    finally:
        with Session(postgres_engine) as cleanup, cleanup.begin():
            cleanup.execute(
                text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update")
            )
            try:
                cleanup.execute(
                    delete(AuditEvent).where(AuditEvent.request_id == request_id)
                )
            finally:
                cleanup.execute(
                    text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update")
                )


def test_export_reporting_session_survives_until_terminal_and_commits_once(
    test_settings,
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _run_export_lifecycle_request(
        test_settings=test_settings,
        postgres_engine=postgres_engine,
        monkeypatch=monkeypatch,
        failure_mode=None,
    )

    assert result.reporting_is_request_db is False
    assert result.isolation_level == "REPEATABLE READ"
    assert result.transaction_active_at_terminal is True
    assert result.observer_count_at_terminal == 0
    assert result.observer_count_after_response == 1
    assert result.commit_calls == 1
    assert result.rollback_calls == 0
    assert result.cancel_calls == 0
    assert result.lifecycle_events == ("commit",)
    assert result.error is None


@pytest.mark.parametrize("failure_mode", ("disconnect", "terminal_send_failure"))
def test_export_reporting_dependency_rolls_back_after_stream_abort(
    test_settings,
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    failure_mode: str,
) -> None:
    result = _run_export_lifecycle_request(
        test_settings=test_settings,
        postgres_engine=postgres_engine,
        monkeypatch=monkeypatch,
        failure_mode=failure_mode,
    )

    assert result.reporting_is_request_db is False
    assert result.isolation_level == "REPEATABLE READ"
    assert result.observer_count_after_response == 0
    assert result.commit_calls == 0
    assert result.rollback_calls == 1
    assert result.cancel_calls == 1
    assert result.lifecycle_events == ("cancel", "rollback")
    if failure_mode == "terminal_send_failure":
        assert isinstance(result.error, RuntimeError)
        assert str(result.error) == "terminal send failed"
    else:
        assert result.error is None


def test_non_manager_catalog_mutations_are_403_before_business_or_audit_writes(
    db_client: TestClient,
    food_api_scenario: FoodApiScenario,
) -> None:
    scenario = food_api_scenario
    csrf = scenario.login(db_client, "self_student")
    before = (
        scenario.db.scalar(select(func.count(FoodCuisineOption.id))),
        scenario.db.scalar(select(func.count(AuditEvent.id))),
        scenario.active.sort_order,
    )

    created = db_client.post(
        "/api/v1/food-preferences/cuisines",
        headers={"X-CSRF-Token": csrf},
        json={"name": "无权限新增菜系", "sort_order": 99},
    )
    patched = db_client.patch(
        f"/api/v1/food-preferences/cuisines/{scenario.active.id}",
        headers={"X-CSRF-Token": csrf},
        json={"sort_order": 99},
    )

    assert created.status_code == patched.status_code == 403
    assert created.json()["code"] == patched.json()["code"] == (
        "food_preference_permission_denied"
    )
    scenario.db.refresh(scenario.active)
    assert (
        scenario.db.scalar(select(func.count(FoodCuisineOption.id))),
        scenario.db.scalar(select(func.count(AuditEvent.id))),
        scenario.active.sort_order,
    ) == before
    _assert_private(created)
    _assert_private(patched)


def test_invalid_person_uuid_and_unknown_food_child_are_private(
    db_client: TestClient,
    food_api_scenario: FoodApiScenario,
) -> None:
    food_api_scenario.login(db_client, "admin")
    invalid = db_client.get("/api/v1/people/not-a-uuid/food-preferences")
    missing_child = db_client.get("/api/v1/food-preferences/not-a-route")

    assert invalid.status_code == 422
    assert missing_child.status_code == 404
    _assert_private(invalid)
    _assert_private(missing_child)


def test_unexpected_food_error_is_closed_private_500(
    test_settings,
    food_api_scenario: FoodApiScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = food_api_scenario
    app = create_app(test_settings)

    def override_db():
        yield scenario.db

    app.dependency_overrides[get_db] = override_db
    secret = "food-runtime-secret-13900001111"

    def explode(*args, **kwargs):
        del args, kwargs
        raise RuntimeError(secret)

    monkeypatch.setattr(FoodPreferenceService, "get", explode)
    with TestClient(app, raise_server_exceptions=False) as client:
        scenario.login(client, "self_student")
        response = client.get("/api/v1/me/food-preferences")

    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert secret not in response.text
    _assert_private(response)


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/food-preferences-extra",
        "/api/v1/me/food-preferences-extra",
        f"/api/v1/people/{uuid4()}/food-preferences-extra",
    ],
)
def test_food_path_matcher_does_not_capture_similar_unrelated_route(
    db_client: TestClient,
    path: str,
) -> None:
    response = db_client.get(path)

    assert response.status_code == 404
    assert response.headers.get("cache-control") is None
    assert response.headers.get("pragma") is None
    assert response.headers.get("x-content-type-options") is None
