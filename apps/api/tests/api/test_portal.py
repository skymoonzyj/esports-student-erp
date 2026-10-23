from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

import app.housing.api_support as housing_api_support
import app.housing.dependencies as housing_dependencies
from app.audit.models import AuditEvent
from app.audit.service import AuditMetadataRejected
from app.housing.errors import HousingResourceNotFound
from app.housing.models import (
    HousingBuilding,
    HousingFloor,
    HousingImportBatch,
    HousingRoom,
    HousingRoomIssue,
    HousingStay,
    HousingTransfer,
)
from app.housing.schemas import (
    HousingPortalResponse,
    HousingPortalStay,
    HousingPortalSubject,
)
from app.identity.models import Person
from app.portal.dependencies import (
    get_portal_coach_service,
    get_portal_context_service,
    get_portal_payment_service,
)
from app.portal.schemas import (
    CoachClassSummary,
    CoachNextCohortsResponse,
    CoachRosterStudent,
    CoachStartOption,
    PortalContextResponse,
    PortalPaymentSummary,
    PortalRefundSummary,
    SelfPaymentsResponse,
)
from app.rbac.models import UserPermissionOverride
from app.rbac.seed import PERMISSIONS
from app.rbac.service import RbacService


class ExplodingPortalContextService:
    def get_context(self, **_: object) -> None:
        raise RuntimeError("portal context exploded")


class RecordingPortalContextService:
    def __init__(self) -> None:
        self.occurred_at: datetime | None = None

    def get_context(
        self,
        *,
        user_id: UUID,
        actor_person_id: UUID,
        occurred_at: datetime,
    ) -> PortalContextResponse:
        del user_id, actor_person_id
        self.occurred_at = occurred_at
        return PortalContextResponse(
            coach_available=False,
            learning_available=True,
            payments_available=True,
            housing_management_available=False,
            housing_available=True,
            adult_guardian_invitations_available=False,
            food_profile_available=False,
            food_preferences_onboarding_required=False,
            learning_mode="family",
        )


class FakeCoachPortalService:
    def list_next(self, **_: object) -> CoachNextCohortsResponse:
        starts_at = datetime(2030, 8, 1, 1, tzinfo=UTC)
        ends_at = datetime(2030, 8, 11, 10, tzinfo=UTC)
        cohort_id = UUID("81000000-0000-4000-8000-000000000010")
        class_id = UUID("82000000-0000-4000-8000-000000000010")
        return CoachNextCohortsResponse(
            observed_at=datetime(2026, 7, 29, 8, tzinfo=UTC),
            selected_starts_at=starts_at,
            options=[
                CoachStartOption(
                    anchor_cohort_id=cohort_id,
                    starts_at=starts_at,
                    cohort_names=["API 门户营期"],
                    class_count=1,
                )
            ],
            classes=[
                CoachClassSummary(
                    cohort_id=cohort_id,
                    cohort_name="API 门户营期",
                    starts_at=starts_at,
                    ends_at=ends_at,
                    class_id=class_id,
                    class_name="API 门户班级",
                    project_name="英雄联盟",
                    class_type="standard",
                    professional_team_name=None,
                    assignment_type="primary",
                    student_count=1,
                )
            ],
            items=[
                CoachRosterStudent(
                    enrollment_id=UUID("83000000-0000-4000-8000-000000000010"),
                    student_person_id=UUID(
                        "84000000-0000-4000-8000-000000000010"
                    ),
                    student_name="API 门户学员",
                    student_number="API-PORTAL-001",
                    age_status="minor",
                    cohort_id=cohort_id,
                    cohort_name="API 门户营期",
                    project_name="英雄联盟",
                    class_id=class_id,
                    class_name="API 门户班级",
                    class_type="standard",
                    professional_team_name=None,
                    assignment_type="primary",
                    enrollment_status="active",
                )
            ],
            page=1,
            page_size=20,
            total=1,
        )


class FakePaymentPortalService:
    def list_payments(self, **_: object) -> SelfPaymentsResponse:
        return SelfPaymentsResponse(
            items=[
                PortalPaymentSummary(
                    payment_id=UUID("a1000000-0000-4000-8000-000000000010"),
                    order_number="BM20260701000001",
                    source_kind="manual",
                    amount_cents=100_000,
                    currency="CNY",
                    paid_at=datetime(2026, 7, 1, 1, tzinfo=UTC),
                    refunded_cents=20_000,
                    net_cents=80_000,
                    status="partially_refunded",
                    related_student_name="API 付款学员",
                    refunds=[
                        PortalRefundSummary(
                            refund_id=UUID(
                                "a2000000-0000-4000-8000-000000000010"
                            ),
                            order_number="BM20260701000001",
                            amount_cents=20_000,
                            refunded_at=datetime(2026, 7, 2, 1, tzinfo=UTC),
                            refund_type="manual",
                        )
                    ],
                )
            ],
            page=1,
            page_size=20,
            total=1,
        )


_HOUSING_SUBJECT_ID = UUID("b1000000-0000-4000-8000-000000000010")
_NO_SUBJECTS_SECRET = "private-no-authorized-subject-name"
_MISSING_SUBJECT_SECRET = "private-missing-target-name"
_RUNTIME_SECRET = "private-housing-runtime-secret-13800000999"
_AUDIT_REJECTION_SECRET = "private-housing-audit-rejection-secret"
_AUDIT_DATABASE_FAILURE_SECRET = "private-housing-audit-database-failure-secret"
_FORBIDDEN_HOUSING_FIELD_NAMES = (
    "phone",
    "email",
    "identity",
    "birthday",
    "date_of_birth",
    "sex",
    "account_status",
    "notes",
    "problem_description",
    "room_id",
    "stay_id",
    "roommate_id",
    "access_source",
)
_HOUSING_ERROR_SECRETS = (
    _NO_SUBJECTS_SECRET,
    _MISSING_SUBJECT_SECRET,
    _RUNTIME_SECRET,
    _AUDIT_REJECTION_SECRET,
    _AUDIT_DATABASE_FAILURE_SECRET,
)


class FakeHousingPortalService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.call: tuple[UUID, UUID, datetime] | None = None

    def get_housing(
        self,
        *,
        user_id: UUID,
        actor_person_id: UUID,
        as_of: datetime,
    ) -> HousingPortalResponse:
        self.call = (user_id, actor_person_id, as_of)
        return HousingPortalResponse(
            as_of=as_of,
            subjects=[
                HousingPortalSubject(
                    subject_person_id=_HOUSING_SUBJECT_ID,
                    subject_name="门户住宿学员",
                    current_stay=HousingPortalStay(
                        building_name="门户当前楼栋",
                        floor_name="门户当前楼层",
                        room_name="门户当前房间",
                        stay_purpose="camp",
                        planned_check_in_at=datetime(2026, 8, 1, 1, tzinfo=UTC),
                        planned_check_out_at=None,
                        actual_check_in_at=datetime(2026, 8, 1, 2, tzinfo=UTC),
                        actual_check_out_at=None,
                        status="checked_in",
                        roommates=["门户当前室友"],
                    ),
                    planned_stays=[
                        HousingPortalStay(
                            building_name="门户计划楼栋",
                            floor_name="门户计划楼层",
                            room_name="门户计划房间",
                            stay_purpose="temporary",
                            planned_check_in_at=datetime(
                                2026, 9, 1, 1, tzinfo=UTC
                            ),
                            planned_check_out_at=datetime(
                                2026, 9, 5, 1, tzinfo=UTC
                            ),
                            actual_check_in_at=None,
                            actual_check_out_at=None,
                            status="planned",
                            roommates=None,
                        )
                    ],
                )
            ],
        )


class NoAuthorizedSubjectsHousingPortalService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_housing(self, **_: object) -> HousingPortalResponse:
        raise HousingResourceNotFound(_NO_SUBJECTS_SECRET)


class MissingEquivalentHousingPortalService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_housing(self, **_: object) -> HousingPortalResponse:
        raise HousingResourceNotFound(_MISSING_SUBJECT_SECRET)


class ExplodingHousingPortalService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_housing(self, **_: object) -> HousingPortalResponse:
        raise RuntimeError(_RUNTIME_SECRET)


def _override_housing_portal_service(
    client: TestClient,
    service: object,
) -> None:
    dependency = getattr(
        housing_dependencies,
        "get_housing_portal_service",
        None,
    )
    if dependency is not None:
        client.app.dependency_overrides[dependency] = lambda: service


def _housing_domain_snapshot(db: Session) -> dict[str, tuple[tuple[object, ...], ...]]:
    db.expire_all()
    snapshot: dict[str, tuple[tuple[object, ...], ...]] = {}
    for model in (
        HousingBuilding,
        HousingFloor,
        HousingImportBatch,
        HousingRoom,
        HousingStay,
        HousingTransfer,
        HousingRoomIssue,
    ):
        table = model.__table__
        rows = db.execute(
            select(*table.c).order_by(*table.primary_key.columns)
        ).all()
        snapshot[table.name] = tuple(tuple(row) for row in rows)
    return snapshot


def _single_housing_portal_audit(
    db: Session,
    request_id: str,
    *,
    actor_user_id: UUID,
    outcome: str,
    metadata: dict[str, object],
) -> AuditEvent:
    db.expire_all()
    events = list(
        db.scalars(select(AuditEvent).where(AuditEvent.request_id == request_id))
    )
    assert len(events) == 1
    event = events[0]
    assert event.actor_user_id == actor_user_id
    assert event.action == "housing.portal.read"
    assert event.resource_type == "housing_request"
    assert event.resource_id is None
    assert event.purpose is None
    assert event.outcome == outcome
    assert event.event_metadata == metadata
    return event


def _assert_forbidden_housing_data_absent(
    *values: object,
    forbidden_values: tuple[object, ...] = (),
) -> None:
    serialized = "\n".join(str(value) for value in values)
    forbidden_strings = (
        *_FORBIDDEN_HOUSING_FIELD_NAMES,
        *_HOUSING_ERROR_SECRETS,
        *(str(value) for value in forbidden_values),
    )
    for forbidden in forbidden_strings:
        assert forbidden not in serialized


def sign_in(client: TestClient, platform_users, key: str) -> None:
    phone, password, _ = platform_users["credentials"][key]
    response = client.post(
        "/api/v1/auth/login",
        json={"identifier": phone, "password": password},
    )
    assert response.status_code == 200


def test_guardian_portal_context_is_private(
    db_client: TestClient,
    platform_users,
) -> None:
    sign_in(db_client, platform_users, "guardian")

    response = db_client.get("/api/v1/portal/context")

    assert response.status_code == 200
    assert response.json() == {
        "coach_available": False,
        "learning_available": True,
        "payments_available": True,
        "housing_management_available": False,
        "housing_available": True,
        "adult_guardian_invitations_available": False,
        "food_profile_available": True,
        "food_preferences_onboarding_required": False,
        "learning_mode": "family",
    }
    assert response.headers["cache-control"] == "no-store, private"


def test_pure_admin_context_has_only_housing_management_card(
    db_client: TestClient,
    platform_users,
) -> None:
    sign_in(db_client, platform_users, "admin")

    response = db_client.get("/api/v1/portal/context")

    assert response.status_code == 200
    assert response.json() == {
        "coach_available": False,
        "learning_available": False,
        "payments_available": False,
        "housing_management_available": True,
        "housing_available": False,
        "adult_guardian_invitations_available": False,
        "food_profile_available": True,
        "food_preferences_onboarding_required": True,
        "learning_mode": None,
    }
    assert response.headers["cache-control"] == "no-store, private"


def test_portal_context_route_passes_one_aware_observed_time(
    db_client: TestClient,
    platform_users,
) -> None:
    sign_in(db_client, platform_users, "guardian")
    recording_service = RecordingPortalContextService()
    db_client.app.dependency_overrides[get_portal_context_service] = (
        lambda: recording_service
    )

    response = db_client.get("/api/v1/portal/context")

    assert response.status_code == 200
    assert recording_service.occurred_at is not None
    assert recording_service.occurred_at.tzinfo is not None
    assert recording_service.occurred_at.utcoffset() == timedelta(0)


def test_portal_authentication_error_is_also_private(db_client: TestClient) -> None:
    response = db_client.get("/api/v1/portal/context")

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_session"
    assert response.headers["cache-control"] == "no-store, private"


def test_unexpected_portal_error_is_private(
    db_client: TestClient,
    platform_users,
) -> None:
    sign_in(db_client, platform_users, "guardian")
    db_client.app.dependency_overrides[get_portal_context_service] = (
        ExplodingPortalContextService
    )

    with TestClient(db_client.app, raise_server_exceptions=False) as crashing_client:
        crashing_client.cookies.update(db_client.cookies)
        response = crashing_client.get("/api/v1/portal/context")

    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert response.headers["cache-control"] == "no-store, private"


def test_coach_roster_response_excludes_sensitive_and_financial_fields(
    db_client: TestClient,
    platform_users,
) -> None:
    sign_in(db_client, platform_users, "coach")
    db_client.app.dependency_overrides[get_portal_coach_service] = (
        FakeCoachPortalService
    )

    response = db_client.get("/api/v1/coach/next-cohorts")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    assert response.json()["items"][0]["student_name"] == "API 门户学员"
    for forbidden in (
        "identity_number",
        "phone",
        "email",
        "payment_status",
        "paid_cents",
        "agreed_tuition_cents",
    ):
        assert forbidden not in response.text


def test_coach_route_uses_private_headers_for_success_and_errors(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    sign_in(db_client, platform_users, "coach")

    success = db_client.get("/api/v1/coach/next-cohorts")
    masked = db_client.get(
        "/api/v1/coach/next-cohorts",
        params={"anchor_cohort_id": str(uuid4())},
    )
    invalid = db_client.get(
        "/api/v1/coach/next-cohorts",
        params={"page": 0},
    )
    coach_account = platform_users["accounts"]["coach"]
    db.add(
        UserPermissionOverride(
            user_id=coach_account.id,
            permission_id=PERMISSIONS["coach_roster.read"].id,
            effect="deny",
            scope_kind="global",
            scope_id=None,
        )
    )
    db.commit()
    denied = db_client.get("/api/v1/coach/next-cohorts")

    assert success.status_code == 200
    assert success.json()["items"] == []
    assert masked.status_code == 404
    assert masked.json()["code"] == "coach_cohort_not_found"
    assert invalid.status_code == 422
    assert denied.status_code == 403
    assert denied.json()["code"] == "permission_denied"
    for response in (success, masked, invalid, denied):
        assert response.headers["cache-control"] == "no-store, private"


def test_guardian_learning_routes_are_private_and_minimal(
    db_client: TestClient,
    platform_users,
) -> None:
    sign_in(db_client, platform_users, "guardian")

    listed = db_client.get("/api/v1/family/students")
    student_id = platform_users["people"]["own_student"].id
    detail = db_client.get(f"/api/v1/family/students/{student_id}/teaching")

    assert listed.status_code == 200
    assert detail.status_code == 200
    assert listed.headers["cache-control"] == "no-store, private"
    assert detail.headers["cache-control"] == "no-store, private"
    assert detail.json()["student"]["student_person_id"] == str(student_id)
    for forbidden in (
        "identity",
        "phone",
        "email",
        "guardian_issues",
        "agreed_tuition_cents",
        "payment_status",
    ):
        assert forbidden not in listed.text
        assert forbidden not in detail.text


def test_student_learning_route_lists_only_self(
    db_client: TestClient,
    platform_users,
) -> None:
    sign_in(db_client, platform_users, "self_student")

    response = db_client.get("/api/v1/family/students")

    assert response.status_code == 200
    assert [item["student_person_id"] for item in response.json()["items"]] == [
        str(platform_users["people"]["self_student"].id)
    ]


def test_learning_routes_mask_unknown_student_and_validate_paging(
    db_client: TestClient,
    platform_users,
) -> None:
    sign_in(db_client, platform_users, "guardian")

    missing = db_client.get(f"/api/v1/family/students/{uuid4()}/teaching")
    invalid = db_client.get(
        f"/api/v1/family/students/{platform_users['people']['own_student'].id}/teaching",
        params={"page_size": 101},
    )

    assert missing.status_code == 404
    assert missing.json()["code"] == "family_student_not_found"
    assert invalid.status_code == 422
    for response in (missing, invalid):
        assert response.headers["cache-control"] == "no-store, private"


def test_back_office_admin_has_no_learning_portal_access(
    db_client: TestClient,
    platform_users,
) -> None:
    sign_in(db_client, platform_users, "admin")

    response = db_client.get("/api/v1/family/students")

    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"
    assert response.headers["cache-control"] == "no-store, private"


def test_self_payment_route_is_private_minimal_and_audited(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    sign_in(db_client, platform_users, "guardian")
    db_client.app.dependency_overrides[get_portal_payment_service] = (
        FakePaymentPortalService
    )

    response = db_client.get(
        "/api/v1/me/payments",
        headers={"X-Request-ID": "portal-payment-amount-audit"},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    assert response.json()["items"][0]["amount_cents"] == 100_000
    for forbidden in (
        "forbidden-other-order",
        "无账号付款内部备注",
        "退款原因",
        "manual_note",
        "external_order_id",
        "raw_payload",
    ):
        assert forbidden not in response.text
    event = db.query(AuditEvent).filter_by(
        request_id="portal-payment-amount-audit"
    ).one()
    assert event.actor_user_id == platform_users["accounts"]["guardian"].id
    assert event.action == "portal.payment.list_amounts"
    assert event.event_metadata == {"result_count": 1}


def test_denied_self_payment_request_does_not_append_amount_audit(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    guardian = platform_users["accounts"]["guardian"]
    db.add(
        UserPermissionOverride(
            user_id=guardian.id,
            permission_id=PERMISSIONS["payments.read"].id,
            effect="deny",
            scope_kind="self",
            scope_id=None,
        )
    )
    db.commit()
    sign_in(db_client, platform_users, "guardian")

    response = db_client.get(
        "/api/v1/me/payments",
        headers={"X-Request-ID": "portal-payment-denied-no-audit"},
    )

    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store, private"
    assert db.query(AuditEvent).filter_by(
        request_id="portal-payment-denied-no-audit"
    ).count() == 0


def test_personal_housing_is_private_minimal_read_only_and_audited(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    sign_in(db_client, platform_users, "guardian")
    service = FakeHousingPortalService(db)
    _override_housing_portal_service(db_client, service)
    before = _housing_domain_snapshot(db)
    request_id = "housing-portal-allowed"

    response = db_client.get(
        "/api/v1/me/housing",
        headers={"X-Request-ID": request_id},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    assert service.call is not None
    user_id, actor_person_id, as_of = service.call
    assert user_id == platform_users["accounts"]["guardian"].id
    assert actor_person_id == platform_users["people"]["guardian"].id
    assert as_of.tzinfo is not None
    assert as_of.utcoffset() == timedelta(0)
    assert response.json() == {
        "as_of": as_of.isoformat().replace("+00:00", "Z"),
        "subjects": [
            {
                "subject_person_id": str(_HOUSING_SUBJECT_ID),
                "subject_name": "门户住宿学员",
                "current_stay": {
                    "building_name": "门户当前楼栋",
                    "floor_name": "门户当前楼层",
                    "room_name": "门户当前房间",
                    "stay_purpose": "camp",
                    "planned_check_in_at": "2026-08-01T01:00:00Z",
                    "planned_check_out_at": None,
                    "actual_check_in_at": "2026-08-01T02:00:00Z",
                    "actual_check_out_at": None,
                    "status": "checked_in",
                    "roommates": ["门户当前室友"],
                },
                "planned_stays": [
                    {
                        "building_name": "门户计划楼栋",
                        "floor_name": "门户计划楼层",
                        "room_name": "门户计划房间",
                        "stay_purpose": "temporary",
                        "planned_check_in_at": "2026-09-01T01:00:00Z",
                        "planned_check_out_at": "2026-09-05T01:00:00Z",
                        "actual_check_in_at": None,
                        "actual_check_out_at": None,
                        "status": "planned",
                        "roommates": None,
                    }
                ],
            }
        ],
    }
    event = _single_housing_portal_audit(
        db,
        request_id,
        actor_user_id=platform_users["accounts"]["guardian"].id,
        outcome="allowed",
        metadata={"result_count": 1},
    )
    _assert_forbidden_housing_data_absent(response.text, event.event_metadata)
    assert _housing_domain_snapshot(db) == before


def test_personal_housing_real_service_excludes_persisted_sensitive_data(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    sign_in(db_client, platform_users, "guardian")
    guardian_summary = platform_users["people"]["guardian"]
    guardian = db.get(Person, guardian_summary.id)
    assert guardian is not None
    guardian_account = platform_users["accounts"]["guardian"]
    own_student = platform_users["people"]["own_student"]
    roommate = platform_users["people"]["other_student"]
    actor_id = guardian_account.id
    now = datetime.now(UTC)
    unique_suffix = uuid4().hex[:10]
    building = HousingBuilding(
        code=f"P-{unique_suffix}",
        name=f"门户隐私楼栋-{unique_suffix}",
        display_order=900,
        is_active=True,
        created_by_user_id=actor_id,
    )
    db.add(building)
    db.flush()
    floor = HousingFloor(
        building_id=building.id,
        number=90,
        name="门户隐私楼层",
        display_order=900,
        is_active=True,
        created_by_user_id=actor_id,
    )
    db.add(floor)
    db.flush()
    room_note = f"门户真实房间敏感备注-{unique_suffix}"
    room = HousingRoom(
        building_id=building.id,
        floor_id=floor.id,
        room_number=f"门户真实房间-{unique_suffix}",
        normalized_key=f"portal-{unique_suffix}",
        room_type="double",
        capacity=2,
        verification_status="verified",
        service_status="problem",
        notes=room_note,
        version=1,
        created_by_user_id=actor_id,
    )
    db.add(room)
    db.flush()
    problem_description = f"门户真实房间问题-{unique_suffix}"
    issue = HousingRoomIssue(
        room_id=room.id,
        description=problem_description,
        previous_service_status="available",
        reported_at=now - timedelta(days=1),
        reported_by_user_id=actor_id,
        status="open",
    )
    stay_note = f"门户真实住宿敏感备注-{unique_suffix}"
    subject_stay = HousingStay(
        person_id=own_student.id,
        room_id=room.id,
        stay_purpose="permanent",
        duration_mode="long_term",
        planned_check_in_at=now - timedelta(days=2),
        planned_check_out_at=None,
        actual_check_in_at=now - timedelta(days=2),
        actual_check_out_at=None,
        status="checked_in",
        room_name_snapshot=room.room_number,
        floor_name_snapshot=floor.name,
        building_name_snapshot=building.name,
        notes=stay_note,
        created_by_user_id=actor_id,
        checked_in_by_user_id=actor_id,
        version=1,
    )
    roommate_stay = HousingStay(
        person_id=roommate.id,
        room_id=room.id,
        stay_purpose="permanent",
        duration_mode="long_term",
        planned_check_in_at=now - timedelta(days=1),
        planned_check_out_at=None,
        actual_check_in_at=now - timedelta(days=1),
        actual_check_out_at=None,
        status="checked_in",
        room_name_snapshot=room.room_number,
        floor_name_snapshot=floor.name,
        building_name_snapshot=building.name,
        notes=None,
        created_by_user_id=actor_id,
        checked_in_by_user_id=actor_id,
        version=1,
    )
    db.add_all([issue, subject_stay, roommate_stay])
    db.commit()

    identity_service = platform_users["identity_service"]
    guardian_phone = identity_service.cipher.decrypt(
        guardian.phone_ciphertext,
        field_name="phone",
    )
    guardian_identity = identity_service.cipher.decrypt(
        guardian.identity_ciphertext,
        field_name="identity_number",
    )
    resource_tokens = RbacService(db).person_resource_tokens(
        actor_person_id=guardian.id,
        target_person_id=own_student.id,
        reference_date=now.date(),
    )
    family_token = f"family:{own_student.id}"
    assert family_token in resource_tokens
    actual_access_source = family_token.partition(":")[0]
    sensitive_values = (
        building.id,
        floor.id,
        room.id,
        subject_stay.id,
        roommate.id,
        roommate_stay.id,
        issue.id,
        room_note,
        stay_note,
        problem_description,
        guardian_phone,
        identity_service.decrypt_email(guardian),
        guardian_identity,
        guardian.date_of_birth.isoformat(),
        guardian.sex,
        guardian_account.email_normalized,
        guardian_account.status,
        actual_access_source,
    )
    before = _housing_domain_snapshot(db)
    request_id = "housing-portal-real-service-privacy"
    assert (
        housing_dependencies.get_housing_portal_service
        not in db_client.app.dependency_overrides
    )

    response = db_client.get(
        "/api/v1/me/housing",
        headers={"X-Request-ID": request_id},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    payload = response.json()
    expected_subject_ids = {
        platform_users["people"]["minor_student"].id,
        own_student.id,
    }
    assert {
        UUID(subject["subject_person_id"]) for subject in payload["subjects"]
    } == expected_subject_ids
    own_student_payload = next(
        subject
        for subject in payload["subjects"]
        if subject["subject_person_id"] == str(own_student.id)
    )
    assert own_student_payload["subject_name"] == own_student.full_name
    assert own_student_payload["current_stay"]["room_name"] == room.room_number
    assert own_student_payload["current_stay"]["roommates"] == [
        roommate.full_name
    ]
    event = _single_housing_portal_audit(
        db,
        request_id,
        actor_user_id=guardian_account.id,
        outcome="allowed",
        metadata={"result_count": len(expected_subject_ids)},
    )
    _assert_forbidden_housing_data_absent(
        response.text,
        event.event_metadata,
        forbidden_values=sensitive_values,
    )
    assert _housing_domain_snapshot(db) == before


@pytest.mark.parametrize(
    "service_type",
    [
        pytest.param(
            NoAuthorizedSubjectsHousingPortalService,
            id="no-authorized-subjects",
        ),
        pytest.param(
            MissingEquivalentHousingPortalService,
            id="missing-equivalent",
        ),
    ],
)
def test_personal_housing_not_found_cases_are_indistinguishable_and_audited(
    db_client: TestClient,
    platform_users,
    db: Session,
    service_type,
) -> None:
    sign_in(db_client, platform_users, "guardian")
    _override_housing_portal_service(db_client, service_type(db))
    before = _housing_domain_snapshot(db)
    request_id = "housing-portal-private-not-found"

    response = db_client.get(
        "/api/v1/me/housing",
        headers={"X-Request-ID": request_id},
    )

    assert response.status_code == 404
    assert response.json() == {
        "type": "about:blank",
        "title": "Not Found",
        "status": 404,
        "detail": "房态资源不存在",
        "code": "housing_resource_not_found",
        "request_id": request_id,
    }
    assert response.headers["cache-control"] == "no-store, private"
    event = _single_housing_portal_audit(
        db,
        request_id,
        actor_user_id=platform_users["accounts"]["guardian"].id,
        outcome="denied",
        metadata={"error_code": "housing_resource_not_found"},
    )
    _assert_forbidden_housing_data_absent(response.text, event.event_metadata)
    assert _housing_domain_snapshot(db) == before


def test_personal_housing_unexpected_failure_is_sanitized_audited_and_read_only(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    sign_in(db_client, platform_users, "guardian")
    _override_housing_portal_service(db_client, ExplodingHousingPortalService(db))
    before = _housing_domain_snapshot(db)
    request_id = "housing-portal-unexpected-failure"

    response = db_client.get(
        "/api/v1/me/housing",
        headers={"X-Request-ID": request_id},
    )

    assert response.status_code == 500
    assert response.json() == {
        "type": "about:blank",
        "title": "Internal Server Error",
        "status": 500,
        "detail": "房态服务暂时不可用",
        "code": "housing_service_failure",
        "request_id": request_id,
    }
    assert response.headers["cache-control"] == "no-store, private"
    event = _single_housing_portal_audit(
        db,
        request_id,
        actor_user_id=platform_users["accounts"]["guardian"].id,
        outcome="failed",
        metadata={"error_code": "housing_service_failure"},
    )
    _assert_forbidden_housing_data_absent(response.text, event.event_metadata)
    assert _housing_domain_snapshot(db) == before


def test_personal_housing_audit_rejection_fails_closed_without_retry(
    db_client: TestClient,
    platform_users,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def reject(*args, **kwargs) -> None:
        nonlocal attempts
        del args, kwargs
        attempts += 1
        raise AuditMetadataRejected(_AUDIT_REJECTION_SECRET)

    monkeypatch.setattr(housing_api_support.AuditService, "append", reject)
    sign_in(db_client, platform_users, "guardian")
    _override_housing_portal_service(db_client, FakeHousingPortalService(db))
    before = _housing_domain_snapshot(db)
    request_id = "housing-portal-audit-rejected"

    response = db_client.get(
        "/api/v1/me/housing",
        headers={"X-Request-ID": request_id},
    )

    assert response.status_code == 500
    assert response.json() == {
        "type": "about:blank",
        "title": "Internal Server Error",
        "status": 500,
        "detail": "房态审计记录失败",
        "code": "housing_audit_failed",
        "request_id": request_id,
    }
    assert response.headers["cache-control"] == "no-store, private"
    assert "subjects" not in response.text
    assert "门户住宿学员" not in response.text
    assert attempts == 1
    assert list(
        db.scalars(select(AuditEvent).where(AuditEvent.request_id == request_id))
    ) == []
    _assert_forbidden_housing_data_absent(response.text)
    assert _housing_domain_snapshot(db) == before


def test_personal_housing_audit_database_failure_fails_closed_without_retry(
    db_client: TestClient,
    platform_users,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def reject(*args, **kwargs) -> None:
        nonlocal attempts
        del args, kwargs
        attempts += 1
        raise OperationalError(
            "INSERT INTO audit_events",
            {},
            RuntimeError(_AUDIT_DATABASE_FAILURE_SECRET),
        )

    monkeypatch.setattr(housing_api_support.AuditService, "append", reject)
    sign_in(db_client, platform_users, "guardian")
    _override_housing_portal_service(db_client, FakeHousingPortalService(db))
    before = _housing_domain_snapshot(db)
    request_id = "housing-portal-audit-database-failed"

    response = db_client.get(
        "/api/v1/me/housing",
        headers={"X-Request-ID": request_id},
    )

    assert response.status_code == 500
    assert response.json() == {
        "type": "about:blank",
        "title": "Internal Server Error",
        "status": 500,
        "detail": "房态审计记录失败",
        "code": "housing_audit_failed",
        "request_id": request_id,
    }
    assert response.headers["cache-control"] == "no-store, private"
    assert "subjects" not in response.text
    assert "门户住宿学员" not in response.text
    assert attempts == 1
    assert list(
        db.scalars(select(AuditEvent).where(AuditEvent.request_id == request_id))
    ) == []
    _assert_forbidden_housing_data_absent(response.text)
    assert _housing_domain_snapshot(db) == before


@pytest.mark.parametrize(
    ("session_token", "request_id"),
    [
        pytest.param(None, "housing-portal-no-session", id="no-session"),
        pytest.param(
            "invalid-housing-session",
            "housing-portal-invalid-session",
            id="invalid-session",
        ),
    ],
)
def test_personal_housing_invalid_or_missing_session_is_private_without_audit(
    db_client: TestClient,
    db: Session,
    session_token: str | None,
    request_id: str,
) -> None:
    if session_token is not None:
        db_client.cookies.set("erp_session", session_token, path="/api")

    response = db_client.get(
        "/api/v1/me/housing",
        headers={"X-Request-ID": request_id},
    )

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_session"
    assert response.headers["cache-control"] == "no-store, private"
    assert list(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.request_id == request_id,
                AuditEvent.action == "housing.portal.read",
            )
        )
    ) == []


def test_personal_housing_forced_password_account_is_private_without_audit(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    sign_in(db_client, platform_users, "guardian")
    platform_users["accounts"]["guardian"].force_password_change = True
    db.commit()
    request_id = "housing-portal-force-password"

    response = db_client.get(
        "/api/v1/me/housing",
        headers={"X-Request-ID": request_id},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "force_password_change_required"
    assert response.headers["cache-control"] == "no-store, private"
    assert list(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.request_id == request_id,
                AuditEvent.action == "housing.portal.read",
            )
        )
    ) == []


def test_personal_housing_rejects_post_privately_without_audit(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    sign_in(db_client, platform_users, "guardian")
    request_id = "housing-portal-wrong-method"

    response = db_client.post(
        "/api/v1/me/housing",
        headers={"X-Request-ID": request_id},
    )

    assert response.status_code == 405
    assert response.json()["code"] == "http_error"
    assert response.headers["cache-control"] == "no-store, private"
    assert list(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.request_id == request_id,
                AuditEvent.action == "housing.portal.read",
            )
        )
    ) == []
