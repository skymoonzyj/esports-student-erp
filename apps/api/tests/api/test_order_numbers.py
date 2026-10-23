from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.admissions.models import AdmissionCase, Enrollment
from app.rbac.models import UserPermissionOverride
from app.rbac.seed import PERMISSIONS
from app.teaching.models import Cohort, TeachingClass

NOW = datetime(2026, 9, 11, 8, tzinfo=UTC)
FORGED_ORDER_NUMBER = "BM19990101000001"


def _sign_in(client: TestClient, platform_users, key: str) -> str:
    identifier, password, _ = platform_users["credentials"][key]
    response = client.post(
        "/api/v1/auth/login",
        json={"identifier": identifier, "password": password},
    )
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def _apply(
    client: TestClient,
    platform_users,
    key: str,
    *,
    supplied_order_number: str | None = None,
) -> dict[str, Any]:
    csrf = _sign_in(client, platform_users, key)
    catalog = client.get("/api/v1/enrollment-catalog")
    assert catalog.status_code == 200, catalog.text
    body = catalog.json()
    payload = {
        "student_person_id": str(platform_users["people"][key].id),
        "project_id": body["projects"][0]["id"],
        "program_id": body["programs"][0]["id"],
        "payment_preference": "offline",
    }
    if supplied_order_number is not None:
        payload["order_number"] = supplied_order_number
    response = client.post(
        "/api/v1/enrollment-applications",
        headers={"X-CSRF-Token": csrf},
        json=payload,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _pay(
    client: TestClient,
    *,
    csrf: str,
    case_id: str,
    amount: str = "100.05",
    supplied_order_number: str | None = None,
) -> dict[str, Any]:
    payload = {
        "admission_case_id": case_id,
        "idempotency_key": str(uuid4()),
        "amount": amount,
        "paid_at": NOW.isoformat(),
        "note": "订单编号回归测试线下收款",
    }
    if supplied_order_number is not None:
        payload["order_number"] = supplied_order_number
    response = client.post(
        "/api/v1/payments",
        headers={"X-CSRF-Token": csrf},
        json=payload,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _open_class(
    db: Session,
    *,
    admin_id: UUID,
    project_id: UUID,
    tuition_cents: int,
) -> TeachingClass:
    starts_at = datetime(2030, 7, 1, 1, tzinfo=UTC)
    cohort = Cohort(
        name=f"报名编号营期-{uuid4().hex[:8]}",
        starts_at=starts_at,
        ends_at=starts_at + timedelta(days=21),
        default_check_in_at=starts_at,
        default_check_out_at=starts_at + timedelta(days=21),
        status="open",
        auto_start_enabled=False,
        auto_complete_enabled=False,
        created_by_user_id=admin_id,
    )
    db.add(cohort)
    db.flush()
    teaching_class = TeachingClass(
        cohort_id=cohort.id,
        project_id=project_id,
        name=f"报名编号班级-{uuid4().hex[:8]}",
        class_type="standard",
        capacity=20,
        default_tuition_cents=tuition_cents,
        is_active=True,
        created_by_user_id=admin_id,
    )
    db.add(teaching_class)
    db.commit()
    return teaching_class


@dataclass(frozen=True)
class OrderPair:
    own_application: dict[str, Any]
    other_application: dict[str, Any]
    own_payment: dict[str, Any]
    other_payment: dict[str, Any]


@pytest.fixture
def order_pair(db_client: TestClient, platform_users) -> OrderPair:
    own = _apply(db_client, platform_users, "self_student")
    other = _apply(db_client, platform_users, "minor_student")
    csrf = _sign_in(db_client, platform_users, "admin")
    own_payment = _pay(db_client, csrf=csrf, case_id=own["id"])
    other_payment = _pay(db_client, csrf=csrf, case_id=other["id"])
    return OrderPair(own, other, own_payment, other_payment)


def test_self_service_order_keeps_one_number_through_payment_enrollment_and_refund(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    application = _apply(db_client, platform_users, "self_student")
    order_number = application["order_number"]
    assert re.fullmatch(r"BM[0-9]{8}[0-9]{6,}", order_number)
    created_at = datetime.fromisoformat(application["created_at"])
    assert order_number[2:10] == created_at.astimezone(ZoneInfo("Asia/Shanghai")).strftime(
        "%Y%m%d"
    )
    assert int(order_number[10:]) > 0

    csrf = _sign_in(db_client, platform_users, "admin")
    orders = db_client.get(
        "/api/v1/admission-cases", params={"order_number": order_number}
    )
    assert orders.status_code == 200, orders.text
    assert orders.json()["total"] == 1
    assert orders.json()["items"][0]["id"] == application["id"]
    assert orders.json()["items"][0]["order_number"] == order_number
    assert orders.json()["items"][0]["payment_status"] == "unpaid"

    first = _pay(db_client, csrf=csrf, case_id=application["id"])
    remainder = application["price_cents"] - first["amount_cents"]
    second = _pay(
        db_client,
        csrf=csrf,
        case_id=application["id"],
        amount=f"{remainder // 100}.{remainder % 100:02d}",
    )
    assert first["id"] != second["id"]
    assert first["order_number"] == second["order_number"] == order_number

    _sign_in(db_client, platform_users, "self_student")
    catalog = db_client.get("/api/v1/enrollment-catalog")
    assert catalog.status_code == 200, catalog.text
    assert catalog.json()["applications"][0]["order_number"] == order_number
    assert catalog.json()["applications"][0]["payment_status"] == "paid"

    csrf = _sign_in(db_client, platform_users, "admin")
    teaching_class = _open_class(
        db,
        admin_id=platform_users["accounts"]["admin"].id,
        project_id=UUID(application["project_id"]),
        tuition_cents=application["price_cents"],
    )
    confirmed = db_client.post(
        f"/api/v1/admission-cases/{application['id']}/confirm-enrollment",
        headers={"X-CSRF-Token": csrf},
        json={"class_id": str(teaching_class.id)},
    )
    assert confirmed.status_code == 201, confirmed.text
    assert confirmed.json()["order_number"] == order_number
    enrollment = db_client.get(f"/api/v1/enrollments/{confirmed.json()['id']}")
    assert enrollment.status_code == 200, enrollment.text
    assert enrollment.json()["order_number"] == order_number

    refund = db_client.post(
        f"/api/v1/payments/{first['id']}/refunds",
        headers={"X-CSRF-Token": csrf},
        json={
            "amount": "20.05",
            "idempotency_key": str(uuid4()),
            "refunded_at": (NOW + timedelta(days=1)).isoformat(),
            "reason": "订单编号回归测试部分退款",
        },
    )
    assert refund.status_code == 201, refund.text
    assert refund.json()["payment_id"] == first["id"]
    assert refund.json()["order_number"] == order_number

    payments = db_client.get("/api/v1/payments", params={"order_number": order_number})
    assert payments.status_code == 200, payments.text
    assert payments.json()["total"] == 2
    assert {item["order_number"] for item in payments.json()["items"]} == {order_number}

    _sign_in(db_client, platform_users, "self_student")
    personal = db_client.get("/api/v1/me/payments")
    assert personal.status_code == 200, personal.text
    assert personal.headers["cache-control"] == "no-store, private"
    assert personal.json()["total"] == 2
    assert {item["order_number"] for item in personal.json()["items"]} == {order_number}
    refunded_payment = next(
        item for item in personal.json()["items"] if item["payment_id"] == first["id"]
    )
    assert refunded_payment["status"] == "partially_refunded"
    assert refunded_payment["refunds"][0]["refund_id"] == refund.json()["id"]
    assert refunded_payment["refunds"][0]["order_number"] == order_number
    assert refunded_payment["refunds"][0]["amount_cents"] == 2_005


def test_order_number_filters_are_normalized_exact_and_keep_pagination_totals(
    db_client: TestClient,
    order_pair: OrderPair,
) -> None:
    number = order_pair.own_application["order_number"]
    assert number != order_pair.other_application["order_number"]
    for path, expected_id in (
        ("/api/v1/admission-cases", order_pair.own_application["id"]),
        ("/api/v1/payments", order_pair.own_payment["id"]),
    ):
        for normalized_input in (number, f"  {number.lower()}  "):
            response = db_client.get(path, params={"order_number": normalized_input})
            assert response.status_code == 200, response.text
            assert response.json()["total"] == 1
            assert [item["id"] for item in response.json()["items"]] == [expected_id]
            assert response.json()["items"][0]["order_number"] == number
        for not_an_exact_match in (number[:10], number + "9"):
            response = db_client.get(path, params={"order_number": not_an_exact_match})
            assert response.status_code == 200, response.text
            assert response.json() == {"items": [], "page": 1, "page_size": 20, "total": 0}
        second_page = db_client.get(path, params={"order_number": number, "page": 2})
        assert second_page.status_code == 200, second_page.text
        assert second_page.json() == {"items": [], "page": 2, "page_size": 20, "total": 1}


def test_order_number_does_not_grant_access_to_another_payers_records(
    db_client: TestClient,
    platform_users,
    order_pair: OrderPair,
) -> None:
    _sign_in(db_client, platform_users, "self_student")
    other_number = order_pair.other_application["order_number"]
    # A supplied order number is never an authorization token, even on a portal
    # route that does not expose an order-number search control.
    personal = db_client.get("/api/v1/me/payments", params={"order_number": other_number})
    assert personal.status_code == 200, personal.text
    assert other_number not in personal.text
    assert order_pair.other_payment["id"] not in personal.text
    assert {item["payment_id"] for item in personal.json()["items"]} <= {
        order_pair.own_payment["id"]
    }
    catalog = db_client.get(
        "/api/v1/enrollment-catalog", params={"order_number": other_number}
    )
    assert catalog.status_code == 200, catalog.text
    assert other_number not in catalog.text
    assert {item["id"] for item in catalog.json()["applications"]} <= {
        order_pair.own_application["id"]
    }
    # The existing list contract permits payments.read/self callers but gives
    # them no global/class rows; admission_cases.read is unavailable to students.
    for path, expected_status in (
        ("/api/v1/admission-cases", 403),
        ("/api/v1/payments", 200),
    ):
        denied = db_client.get(path, params={"order_number": other_number})
        assert denied.status_code == expected_status, denied.text
        if expected_status == 200:
            assert denied.json() == {"items": [], "page": 1, "page_size": 20, "total": 0}
        assert other_number not in denied.text


def test_order_number_filter_respects_assigned_class_deny(
    db_client: TestClient,
    platform_users,
    order_pair: OrderPair,
    db: Session,
) -> None:
    application = order_pair.own_application
    admin_id = platform_users["accounts"]["admin"].id
    teaching_class = _open_class(
        db,
        admin_id=admin_id,
        project_id=UUID(application["project_id"]),
        tuition_cents=application["price_cents"],
    )
    case = db.get(AdmissionCase, UUID(application["id"]))
    assert case is not None
    enrollment = Enrollment(
        admission_case_id=case.id,
        student_person_id=platform_users["people"]["self_student"].id,
        cohort_id=teaching_class.cohort_id,
        class_id=teaching_class.id,
        agreed_tuition_cents=application["price_cents"],
        status="active",
        confirmed_at=NOW,
        created_by_user_id=admin_id,
    )
    db.add(enrollment)
    db.flush()
    case.status = "enrolled"
    case.current_enrollment_id = enrollment.id
    for permission in ("admission_cases.read", "payments.read"):
        db.add(
            UserPermissionOverride(
                user_id=admin_id,
                permission_id=PERMISSIONS[permission].id,
                effect="deny",
                scope_kind="assigned_class",
                scope_id=teaching_class.id,
            )
        )
    db.commit()
    _sign_in(db_client, platform_users, "admin")

    for path in ("/api/v1/admission-cases", "/api/v1/payments"):
        hidden = db_client.get(path, params={"order_number": application["order_number"]})
        assert hidden.status_code == 200, hidden.text
        assert hidden.json() == {"items": [], "page": 1, "page_size": 20, "total": 0}
        visible = db_client.get(
            path, params={"order_number": order_pair.other_application["order_number"]}
        )
        assert visible.status_code == 200, visible.text
        assert visible.json()["total"] == 1
        assert visible.json()["items"][0]["order_number"] == (
            order_pair.other_application["order_number"]
        )


def test_client_supplied_order_number_cannot_choose_or_replace_generated_number(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    application = _apply(
        db_client,
        platform_users,
        "self_student",
        supplied_order_number=FORGED_ORDER_NUMBER,
    )
    generated = application["order_number"]
    assert generated != FORGED_ORDER_NUMBER
    assert re.fullmatch(r"BM[0-9]{8}[0-9]{6,}", generated)

    csrf = _sign_in(db_client, platform_users, "admin")
    payment = _pay(
        db_client,
        csrf=csrf,
        case_id=application["id"],
        supplied_order_number=FORGED_ORDER_NUMBER,
    )
    assert payment["order_number"] == generated
    refunded = db_client.post(
        f"/api/v1/payments/{payment['id']}/refunds",
        headers={"X-CSRF-Token": csrf},
        json={
            "amount": "1.00",
            "idempotency_key": str(uuid4()),
            "refunded_at": NOW.isoformat(),
            "reason": "外部编号不可覆盖回归测试",
            "order_number": FORGED_ORDER_NUMBER,
        },
    )
    assert refunded.status_code == 201, refunded.text
    assert refunded.json()["order_number"] == generated
    case = db.get(AdmissionCase, UUID(application["id"]))
    assert case is not None
    db.refresh(case)
    assert case.order_number == generated
