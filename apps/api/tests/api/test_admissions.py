from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.admissions import payment_router
from app.admissions.models import (
    AdmissionCase,
    AdmissionCasePayment,
    Enrollment,
    EnrollmentProgram,
    Payment,
    PaymentConflict,
    PaymentImportEvent,
    PaymentRefund,
    ServiceAccount,
)
from app.audit.models import AuditEvent
from app.audit.service import AuditMetadataRejected
from app.auth.identifiers import login_identifier_digest, normalize_login_identifier
from app.auth.models import AccountLoginIdentifier, UserAccount
from app.identity.models import Person
from app.rbac.models import UserPermissionOverride
from app.rbac.seed import PERMISSIONS
from app.teaching.models import Cohort, TeachingClass

NOW = datetime(2026, 8, 1, 8, 0, tzinfo=UTC)
PROJECT_ID = UUID("30000000-0000-4000-8000-000000000001")


@dataclass(frozen=True)
class AdmissionsApiScenario:
    admission_case: AdmissionCase
    enrollment: Enrollment
    payment: Payment
    cohort: Cohort
    teaching_class: TeachingClass
    target_class: TeachingClass
    student: Person
    admin: UserAccount


def sign_in(client: TestClient, platform_users, key: str) -> str:
    phone, password, _ = platform_users["credentials"][key]
    response = client.post(
        "/api/v1/auth/login",
        json={"identifier": phone, "password": password},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _switch_to_email_only_account(
    db: Session,
    platform_users,
    key: str,
) -> Person:
    person = db.get(Person, platform_users["people"][key].id)
    assert person is not None
    account = db.scalar(select(UserAccount).where(UserAccount.person_id == person.id))
    assert account is not None
    identifier = db.scalar(
        select(AccountLoginIdentifier).where(
            AccountLoginIdentifier.account_id == account.id,
            AccountLoginIdentifier.kind == "phone",
        )
    )
    assert identifier is not None
    email = platform_users["identity_service"].decrypt_email(person)
    assert email is not None
    identifier.kind = "email"
    identifier.identifier_digest = login_identifier_digest(
        platform_users["identity_service"].lookup,
        normalize_login_identifier(email),
    )
    db.commit()
    return person


@pytest.fixture
def admissions_api_scenario(
    db: Session,
    platform_users,
) -> AdmissionsApiScenario:
    admin = platform_users["accounts"]["admin"]
    student = platform_users["people"]["self_student"]
    cohort = Cohort(
        name=f"招生接口营期-{uuid4().hex[:8]}",
        starts_at=NOW + timedelta(days=1),
        ends_at=NOW + timedelta(days=11),
        default_check_in_at=NOW + timedelta(days=1),
        default_check_out_at=NOW + timedelta(days=11),
        status="open",
        auto_start_enabled=False,
        auto_complete_enabled=False,
        created_by_user_id=admin.id,
    )
    db.add(cohort)
    db.flush()
    teaching_class = TeachingClass(
        cohort_id=cohort.id,
        project_id=PROJECT_ID,
        name=f"招生接口班级-{uuid4().hex[:8]}",
        class_type="standard",
        professional_team_id=None,
        capacity=20,
        default_tuition_cents=100_000,
        is_active=True,
        created_by_user_id=admin.id,
    )
    db.add(teaching_class)
    target_class = TeachingClass(
        cohort_id=cohort.id,
        project_id=PROJECT_ID,
        name=f"招生接口目标班-{uuid4().hex[:8]}",
        class_type="standard",
        professional_team_id=None,
        capacity=20,
        default_tuition_cents=120_000,
        is_active=True,
        created_by_user_id=admin.id,
    )
    db.add(target_class)
    admission_case = AdmissionCase(
        student_person_id=student.id,
        status="ready",
        agreed_tuition_cents=100_000,
        created_by_user_id=admin.id,
    )
    db.add(admission_case)
    db.flush()
    enrollment = Enrollment(
        admission_case_id=admission_case.id,
        student_person_id=student.id,
        cohort_id=cohort.id,
        class_id=teaching_class.id,
        agreed_tuition_cents=100_000,
        status="active",
        confirmed_at=NOW,
        created_by_user_id=admin.id,
    )
    db.add(enrollment)
    db.flush()
    admission_case.status = "enrolled"
    admission_case.current_enrollment_id = enrollment.id
    payment = Payment(
        source_kind="manual",
        source_system=None,
        external_order_id=None,
        payer_account_id=platform_users["accounts"]["self_student"].id,
        payer_person_id=student.id,
        amount_cents=40_000,
        currency="CNY",
        paid_at=NOW,
        manual_note="招生接口测试付款",
        created_by_user_id=admin.id,
    )
    db.add(payment)
    db.flush()
    db.add(
        AdmissionCasePayment(
            admission_case_id=admission_case.id,
            payment_id=payment.id,
            attached_by_user_id=admin.id,
        )
    )
    db.commit()
    return AdmissionsApiScenario(
        admission_case=admission_case,
        enrollment=enrollment,
        payment=payment,
        cohort=cohort,
        teaching_class=teaching_class,
        target_class=target_class,
        student=student,
        admin=admin,
    )


def test_customer_service_reads_case_status_but_never_amounts(
    db_client: TestClient,
    platform_users,
    admissions_api_scenario: AdmissionsApiScenario,
) -> None:
    sign_in(db_client, platform_users, "customer_service")

    response = db_client.get("/api/v1/admission-cases")

    assert response.status_code == 200
    item = next(
        value
        for value in response.json()["items"]
        if value["id"] == str(admissions_api_scenario.admission_case.id)
    )
    assert item["payment_status"] == "partial"
    assert item["paid_cents"] is None
    assert item["refunded_cents"] is None
    assert item["net_paid_cents"] is None
    assert item["agreed_tuition_cents"] is None
    assert item["due_cents"] is None
    assert item["overpaid_cents"] is None


def test_customer_service_reads_enrollment_without_amounts(
    db_client: TestClient,
    platform_users,
    admissions_api_scenario: AdmissionsApiScenario,
) -> None:
    sign_in(db_client, platform_users, "customer_service")

    response = db_client.get("/api/v1/enrollments")

    assert response.status_code == 200
    item = next(
        value
        for value in response.json()["items"]
        if value["id"] == str(admissions_api_scenario.enrollment.id)
    )
    assert item["payment_status"] == "partial"
    assert item["agreed_tuition_cents"] is None
    assert item["net_paid_cents"] is None
    assert item["due_cents"] is None


def test_explicit_payments_deny_blocks_admin_amount_routes(
    db_client: TestClient,
    platform_users,
    admissions_api_scenario: AdmissionsApiScenario,
    db: Session,
) -> None:
    del admissions_api_scenario
    admin = platform_users["accounts"]["admin"]
    db.add(
        UserPermissionOverride(
            user_id=admin.id,
            permission_id=PERMISSIONS["payments.read"].id,
            effect="deny",
            scope_kind="global",
            scope_id=None,
        )
    )
    db.commit()
    sign_in(db_client, platform_users, "admin")

    response = db_client.get("/api/v1/payments")

    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"


def test_assigned_class_deny_filters_enrollment_list_and_masks_detail(
    db_client: TestClient,
    platform_users,
    admissions_api_scenario: AdmissionsApiScenario,
    db: Session,
) -> None:
    admin = platform_users["accounts"]["admin"]
    db.add(
        UserPermissionOverride(
            user_id=admin.id,
            permission_id=PERMISSIONS["enrollments.read"].id,
            effect="deny",
            scope_kind="assigned_class",
            scope_id=admissions_api_scenario.teaching_class.id,
        )
    )
    db.commit()
    sign_in(db_client, platform_users, "admin")

    listed = db_client.get(
        "/api/v1/enrollments",
        params={"class_id": str(admissions_api_scenario.teaching_class.id)},
    )
    detail = db_client.get(
        f"/api/v1/enrollments/{admissions_api_scenario.enrollment.id}"
    )

    assert listed.status_code == 200
    assert listed.json() == {"items": [], "page": 1, "page_size": 20, "total": 0}
    assert detail.status_code == 404
    assert detail.json()["code"] == "enrollment_not_found"


def test_assigned_class_payment_deny_hides_only_that_enrollment_amounts(
    db_client: TestClient,
    platform_users,
    admissions_api_scenario: AdmissionsApiScenario,
    db: Session,
) -> None:
    admin = platform_users["accounts"]["admin"]
    db.add(
        UserPermissionOverride(
            user_id=admin.id,
            permission_id=PERMISSIONS["payments.read"].id,
            effect="deny",
            scope_kind="assigned_class",
            scope_id=admissions_api_scenario.teaching_class.id,
        )
    )
    db.commit()
    sign_in(db_client, platform_users, "admin")

    detail = db_client.get(
        f"/api/v1/enrollments/{admissions_api_scenario.enrollment.id}"
    )

    assert detail.status_code == 200
    assert detail.json()["payment_status"] == "partial"
    assert detail.json()["agreed_tuition_cents"] is None
    assert detail.json()["paid_cents"] is None
    assert detail.json()["refunded_cents"] is None
    assert detail.json()["net_paid_cents"] is None
    assert detail.json()["due_cents"] is None
    assert detail.json()["overpaid_cents"] is None


def test_assigned_class_manage_deny_masks_enrollment_mutation(
    db_client: TestClient,
    platform_users,
    admissions_api_scenario: AdmissionsApiScenario,
    db: Session,
) -> None:
    admin = platform_users["accounts"]["admin"]
    db.add(
        UserPermissionOverride(
            user_id=admin.id,
            permission_id=PERMISSIONS["enrollments.manage"].id,
            effect="deny",
            scope_kind="assigned_class",
            scope_id=admissions_api_scenario.teaching_class.id,
        )
    )
    db.commit()
    csrf = sign_in(db_client, platform_users, "admin")

    response = db_client.post(
        f"/api/v1/enrollments/{admissions_api_scenario.enrollment.id}/cancel",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "不应越过班级级禁止"},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "enrollment_not_found"
    db.refresh(admissions_api_scenario.enrollment)
    assert admissions_api_scenario.enrollment.status == "active"


def test_assigned_class_deny_filters_case_and_payment_lists(
    db_client: TestClient,
    platform_users,
    admissions_api_scenario: AdmissionsApiScenario,
    db: Session,
) -> None:
    admin = platform_users["accounts"]["admin"]
    for permission in ("admission_cases.read", "payments.read"):
        db.add(
            UserPermissionOverride(
                user_id=admin.id,
                permission_id=PERMISSIONS[permission].id,
                effect="deny",
                scope_kind="assigned_class",
                scope_id=admissions_api_scenario.teaching_class.id,
            )
        )
    db.commit()
    sign_in(db_client, platform_users, "admin")

    cases = db_client.get(
        "/api/v1/admission-cases",
        params={"status": "enrolled"},
    )
    payments = db_client.get(
        "/api/v1/payments",
        params={"source_kind": "manual"},
    )

    assert cases.status_code == 200
    assert cases.json() == {"items": [], "page": 1, "page_size": 20, "total": 0}
    assert payments.status_code == 200
    assert payments.json() == {"items": [], "page": 1, "page_size": 20, "total": 0}


def test_assigned_class_payment_deny_hides_case_amount_projection(
    db_client: TestClient,
    platform_users,
    admissions_api_scenario: AdmissionsApiScenario,
    db: Session,
) -> None:
    admin = platform_users["accounts"]["admin"]
    db.add(
        UserPermissionOverride(
            user_id=admin.id,
            permission_id=PERMISSIONS["payments.read"].id,
            effect="deny",
            scope_kind="assigned_class",
            scope_id=admissions_api_scenario.teaching_class.id,
        )
    )
    db.commit()
    sign_in(db_client, platform_users, "admin")

    response = db_client.get(
        "/api/v1/admission-cases",
        params={"status": "enrolled"},
    )

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["id"] == str(admissions_api_scenario.admission_case.id)
    assert item["payment_status"] == "partial"
    assert item["paid_cents"] is None
    assert item["agreed_tuition_cents"] is None


def test_target_class_manage_deny_masks_transfer(
    db_client: TestClient,
    platform_users,
    admissions_api_scenario: AdmissionsApiScenario,
    db: Session,
) -> None:
    admin = platform_users["accounts"]["admin"]
    db.add(
        UserPermissionOverride(
            user_id=admin.id,
            permission_id=PERMISSIONS["enrollments.manage"].id,
            effect="deny",
            scope_kind="assigned_class",
            scope_id=admissions_api_scenario.target_class.id,
        )
    )
    db.commit()
    csrf = sign_in(db_client, platform_users, "admin")

    response = db_client.post(
        f"/api/v1/enrollments/{admissions_api_scenario.enrollment.id}/transfer",
        headers={"X-CSRF-Token": csrf},
        json={
            "target_class_id": str(admissions_api_scenario.target_class.id),
            "reason": "测试目标班级明确禁止",
        },
    )

    assert response.status_code == 404
    assert response.json()["code"] == "enrollment_not_found"
    db.refresh(admissions_api_scenario.enrollment)
    assert admissions_api_scenario.enrollment.class_id == admissions_api_scenario.teaching_class.id


def test_admin_reads_enrollment_action_context_and_transfer_options(
    db_client: TestClient,
    platform_users,
    admissions_api_scenario: AdmissionsApiScenario,
) -> None:
    sign_in(db_client, platform_users, "admin")

    action_context = db_client.get(
        f"/api/v1/enrollments/{admissions_api_scenario.enrollment.id}/action-context"
    )
    transfer_options = db_client.get(
        f"/api/v1/enrollments/{admissions_api_scenario.enrollment.id}/transfer-options",
        params={"page": 1, "page_size": 100},
    )

    assert action_context.status_code == 200
    assert action_context.json() == {
        "manage_allowed": True,
        "refund_allowed": True,
        "read_only_reason": None,
    }
    assert transfer_options.status_code == 200
    assert transfer_options.json() == {
        "items": [
            {
                "class_id": str(admissions_api_scenario.target_class.id),
                "class_name": admissions_api_scenario.target_class.name,
                "capacity": 20,
                "active_enrollment_count": 0,
                "is_full": False,
                "capacity_override_allowed": True,
            }
        ],
        "page": 1,
        "page_size": 100,
        "total": 1,
    }


def test_customer_service_gets_read_only_action_context_and_no_transfer_options(
    db_client: TestClient,
    platform_users,
    admissions_api_scenario: AdmissionsApiScenario,
) -> None:
    sign_in(db_client, platform_users, "customer_service")

    action_context = db_client.get(
        f"/api/v1/enrollments/{admissions_api_scenario.enrollment.id}/action-context"
    )
    transfer_options = db_client.get(
        f"/api/v1/enrollments/{admissions_api_scenario.enrollment.id}/transfer-options",
        params={"page": 1, "page_size": 100},
    )

    assert action_context.status_code == 200
    assert action_context.json() == {
        "manage_allowed": False,
        "refund_allowed": False,
        "read_only_reason": None,
    }
    assert transfer_options.status_code == 200
    assert transfer_options.json() == {
        "items": [],
        "page": 1,
        "page_size": 100,
        "total": 0,
    }


def test_payments_read_deny_only_disables_enrollment_refund_action(
    db_client: TestClient,
    platform_users,
    admissions_api_scenario: AdmissionsApiScenario,
    db: Session,
) -> None:
    db.add(
        UserPermissionOverride(
            user_id=admissions_api_scenario.admin.id,
            permission_id=PERMISSIONS["payments.read"].id,
            effect="deny",
            scope_kind="assigned_class",
            scope_id=admissions_api_scenario.teaching_class.id,
        )
    )
    db.commit()
    sign_in(db_client, platform_users, "admin")

    response = db_client.get(
        f"/api/v1/enrollments/{admissions_api_scenario.enrollment.id}/action-context"
    )

    assert response.status_code == 200
    assert response.json() == {
        "manage_allowed": True,
        "refund_allowed": False,
        "read_only_reason": None,
    }


def test_archived_cohort_locks_enrollment_actions_and_transfer_options(
    db_client: TestClient,
    platform_users,
    admissions_api_scenario: AdmissionsApiScenario,
    db: Session,
) -> None:
    admissions_api_scenario.cohort.status = "archived"
    db.commit()
    sign_in(db_client, platform_users, "admin")

    action_context = db_client.get(
        f"/api/v1/enrollments/{admissions_api_scenario.enrollment.id}/action-context"
    )
    transfer_options = db_client.get(
        f"/api/v1/enrollments/{admissions_api_scenario.enrollment.id}/transfer-options",
        params={"page": 1, "page_size": 100},
    )

    assert action_context.status_code == 200
    assert action_context.json() == {
        "manage_allowed": False,
        "refund_allowed": False,
        "read_only_reason": "archived",
    }
    assert transfer_options.status_code == 200
    assert transfer_options.json() == {
        "items": [],
        "page": 1,
        "page_size": 100,
        "total": 0,
    }


def test_non_active_enrollment_disables_actions_and_transfer_options(
    db_client: TestClient,
    platform_users,
    admissions_api_scenario: AdmissionsApiScenario,
    db: Session,
) -> None:
    admissions_api_scenario.enrollment.status = "cancelled"
    admissions_api_scenario.enrollment.cancelled_at = NOW
    db.commit()
    sign_in(db_client, platform_users, "admin")

    action_context = db_client.get(
        f"/api/v1/enrollments/{admissions_api_scenario.enrollment.id}/action-context"
    )
    transfer_options = db_client.get(
        f"/api/v1/enrollments/{admissions_api_scenario.enrollment.id}/transfer-options",
        params={"page": 1, "page_size": 100},
    )

    assert action_context.status_code == 200
    assert action_context.json()["manage_allowed"] is False
    assert action_context.json()["refund_allowed"] is False
    assert action_context.json()["read_only_reason"] is None
    assert transfer_options.status_code == 200
    assert transfer_options.json()["items"] == []
    assert transfer_options.json()["total"] == 0


def test_transfer_options_count_only_active_enrollments_and_mark_full_target(
    db_client: TestClient,
    platform_users,
    admissions_api_scenario: AdmissionsApiScenario,
    db: Session,
) -> None:
    admissions_api_scenario.target_class.capacity = 1
    active_case = AdmissionCase(
        student_person_id=platform_users["people"]["adult_without_account"].id,
        status="ready",
        created_by_user_id=admissions_api_scenario.admin.id,
    )
    cancelled_case = AdmissionCase(
        student_person_id=platform_users["people"]["other_student"].id,
        status="ready",
        created_by_user_id=admissions_api_scenario.admin.id,
    )
    db.add_all([active_case, cancelled_case])
    db.flush()
    active_enrollment = Enrollment(
        admission_case_id=active_case.id,
        student_person_id=active_case.student_person_id,
        cohort_id=admissions_api_scenario.cohort.id,
        class_id=admissions_api_scenario.target_class.id,
        agreed_tuition_cents=120_000,
        status="active",
        confirmed_at=NOW,
        created_by_user_id=admissions_api_scenario.admin.id,
    )
    cancelled_enrollment = Enrollment(
        admission_case_id=cancelled_case.id,
        student_person_id=cancelled_case.student_person_id,
        cohort_id=admissions_api_scenario.cohort.id,
        class_id=admissions_api_scenario.target_class.id,
        agreed_tuition_cents=120_000,
        status="cancelled",
        confirmed_at=NOW,
        cancelled_at=NOW,
        created_by_user_id=admissions_api_scenario.admin.id,
    )
    db.add_all([active_enrollment, cancelled_enrollment])
    db.commit()
    sign_in(db_client, platform_users, "admin")

    response = db_client.get(
        f"/api/v1/enrollments/{admissions_api_scenario.enrollment.id}/transfer-options",
        params={"page": 1, "page_size": 100},
    )

    assert response.status_code == 200
    assert response.json()["items"] == [
        {
            "class_id": str(admissions_api_scenario.target_class.id),
            "class_name": admissions_api_scenario.target_class.name,
            "capacity": 1,
            "active_enrollment_count": 1,
            "is_full": True,
            "capacity_override_allowed": True,
        }
    ]


def test_transfer_options_require_source_read_and_filter_target_permissions(
    db_client: TestClient,
    platform_users,
    admissions_api_scenario: AdmissionsApiScenario,
    db: Session,
) -> None:
    read_deny = UserPermissionOverride(
        user_id=admissions_api_scenario.admin.id,
        permission_id=PERMISSIONS["enrollments.read"].id,
        effect="deny",
        scope_kind="assigned_class",
        scope_id=admissions_api_scenario.teaching_class.id,
    )
    db.add(read_deny)
    db.commit()
    sign_in(db_client, platform_users, "admin")

    action_context = db_client.get(
        f"/api/v1/enrollments/{admissions_api_scenario.enrollment.id}/action-context"
    )
    transfer_options = db_client.get(
        f"/api/v1/enrollments/{admissions_api_scenario.enrollment.id}/transfer-options"
    )

    assert action_context.status_code == 404
    assert action_context.json()["code"] == "enrollment_not_found"
    assert transfer_options.status_code == 404
    assert transfer_options.json()["code"] == "enrollment_not_found"

    db.delete(read_deny)
    db.add(
        UserPermissionOverride(
            user_id=admissions_api_scenario.admin.id,
            permission_id=PERMISSIONS["enrollments.manage"].id,
            effect="deny",
            scope_kind="assigned_class",
            scope_id=admissions_api_scenario.target_class.id,
        )
    )
    db.commit()

    filtered = db_client.get(
        f"/api/v1/enrollments/{admissions_api_scenario.enrollment.id}/transfer-options",
        params={"page": 1, "page_size": 100},
    )

    assert filtered.status_code == 200
    assert filtered.json() == {
        "items": [],
        "page": 1,
        "page_size": 100,
        "total": 0,
    }


def test_transfer_options_project_target_capacity_override_permission(
    db_client: TestClient,
    platform_users,
    admissions_api_scenario: AdmissionsApiScenario,
    db: Session,
) -> None:
    db.add(
        UserPermissionOverride(
            user_id=admissions_api_scenario.admin.id,
            permission_id=PERMISSIONS["enrollments.capacity_override"].id,
            effect="deny",
            scope_kind="assigned_class",
            scope_id=admissions_api_scenario.target_class.id,
        )
    )
    db.commit()
    sign_in(db_client, platform_users, "admin")

    response = db_client.get(
        f"/api/v1/enrollments/{admissions_api_scenario.enrollment.id}/transfer-options",
        params={"page": 1, "page_size": 100},
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["class_id"] == str(
        admissions_api_scenario.target_class.id
    )
    assert response.json()["items"][0]["capacity_override_allowed"] is False


def test_assigned_class_payment_deny_masks_withdrawal_preview(
    db_client: TestClient,
    platform_users,
    admissions_api_scenario: AdmissionsApiScenario,
    db: Session,
) -> None:
    admin = platform_users["accounts"]["admin"]
    db.add(
        UserPermissionOverride(
            user_id=admin.id,
            permission_id=PERMISSIONS["payments.read"].id,
            effect="deny",
            scope_kind="assigned_class",
            scope_id=admissions_api_scenario.teaching_class.id,
        )
    )
    db.commit()
    csrf = sign_in(db_client, platform_users, "admin")

    response = db_client.post(
        f"/api/v1/enrollments/{admissions_api_scenario.enrollment.id}/withdrawal-refund-preview",
        headers={"X-CSRF-Token": csrf},
        json={
            "withdrawal_date": (NOW - timedelta(days=1)).date().isoformat(),
            "policy": "prorated_remaining_days",
        },
    )

    assert response.status_code == 404
    assert response.json()["code"] == "enrollment_not_found"


def test_assigned_class_case_manage_deny_masks_case_mutation(
    db_client: TestClient,
    platform_users,
    admissions_api_scenario: AdmissionsApiScenario,
    db: Session,
) -> None:
    admissions_api_scenario.enrollment.status = "cancelled"
    admissions_api_scenario.enrollment.cancelled_at = NOW
    admissions_api_scenario.admission_case.status = "ready"
    admissions_api_scenario.admission_case.current_enrollment_id = None
    admin = platform_users["accounts"]["admin"]
    db.add(
        UserPermissionOverride(
            user_id=admin.id,
            permission_id=PERMISSIONS["admission_cases.manage"].id,
            effect="deny",
            scope_kind="assigned_class",
            scope_id=admissions_api_scenario.teaching_class.id,
        )
    )
    db.commit()
    csrf = sign_in(db_client, platform_users, "admin")

    response = db_client.post(
        f"/api/v1/admission-cases/{admissions_api_scenario.admission_case.id}/close",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "不应越过班级级禁止"},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "admission_case_not_found"
    db.refresh(admissions_api_scenario.admission_case)
    assert admissions_api_scenario.admission_case.status == "ready"


def test_assigned_class_refund_deny_masks_payment_mutation(
    db_client: TestClient,
    platform_users,
    admissions_api_scenario: AdmissionsApiScenario,
    db: Session,
) -> None:
    admin = platform_users["accounts"]["admin"]
    db.add(
        UserPermissionOverride(
            user_id=admin.id,
            permission_id=PERMISSIONS["refunds.manage"].id,
            effect="deny",
            scope_kind="assigned_class",
            scope_id=admissions_api_scenario.teaching_class.id,
        )
    )
    db.commit()
    csrf = sign_in(db_client, platform_users, "admin")

    response = db_client.post(
        f"/api/v1/payments/{admissions_api_scenario.payment.id}/refunds",
        headers={"X-CSRF-Token": csrf},
        json={
            "amount": "1.00",
            "idempotency_key": str(uuid4()),
            "refunded_at": NOW.isoformat(),
            "reason": "不应越过班级级禁止",
        },
    )

    assert response.status_code == 404
    assert response.json()["code"] == "payment_not_found"


def test_family_scoped_account_cannot_probe_unrelated_enrollment_ids(
    db_client: TestClient,
    platform_users,
    admissions_api_scenario: AdmissionsApiScenario,
) -> None:
    sign_in(db_client, platform_users, "guardian")

    existing = db_client.get(
        f"/api/v1/enrollments/{admissions_api_scenario.enrollment.id}"
    )
    missing = db_client.get(f"/api/v1/enrollments/{uuid4()}")

    assert existing.status_code == 404
    assert existing.json()["code"] == "enrollment_not_found"
    assert missing.status_code == 404
    assert missing.json()["code"] == "enrollment_not_found"


def test_admin_amount_reads_are_audited_without_amount_metadata(
    db_client: TestClient,
    platform_users,
    admissions_api_scenario: AdmissionsApiScenario,
    db: Session,
) -> None:
    sign_in(db_client, platform_users, "admin")

    responses = [
        db_client.get("/api/v1/payments"),
        db_client.get("/api/v1/admission-cases"),
        db_client.get("/api/v1/enrollments"),
        db_client.get(f"/api/v1/enrollments/{admissions_api_scenario.enrollment.id}"),
    ]

    assert all(response.status_code == 200 for response in responses)
    audits = list(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.action.in_(
                    [
                        "admissions.payment.list_amounts",
                        "admissions.admission_case.list_amounts",
                        "admissions.enrollment.list_amounts",
                        "admissions.enrollment.read_amounts",
                    ]
                ),
                AuditEvent.actor_user_id == admissions_api_scenario.admin.id,
            )
        )
    )
    assert {audit.action for audit in audits} == {
        "admissions.payment.list_amounts",
        "admissions.admission_case.list_amounts",
        "admissions.enrollment.list_amounts",
        "admissions.enrollment.read_amounts",
    }
    assert all(audit.event_metadata == {"result_count": 1} for audit in audits)
    assert "amount" not in str([audit.event_metadata for audit in audits]).lower()


def test_all_admissions_success_responses_are_private_no_store(
    db_client: TestClient,
    platform_users,
    admissions_api_scenario: AdmissionsApiScenario,
) -> None:
    sign_in(db_client, platform_users, "admin")

    for path in (
        "/api/v1/payments",
        "/api/v1/admission-cases",
        "/api/v1/enrollments",
        f"/api/v1/enrollments/{admissions_api_scenario.enrollment.id}",
    ):
        response = db_client.get(path)
        assert response.status_code == 200, path
        assert response.headers["cache-control"] == "no-store, private"


def test_browser_writes_require_csrf_before_creating_payment(
    db_client: TestClient,
    platform_users,
    admissions_api_scenario: AdmissionsApiScenario,
) -> None:
    sign_in(db_client, platform_users, "admin")

    response = db_client.post(
        "/api/v1/payments",
        json={
            "admission_case_id": str(admissions_api_scenario.admission_case.id),
            "amount": "100.00",
            "idempotency_key": str(uuid4()),
            "paid_at": NOW.isoformat(),
            "note": "缺少 CSRF 不得登记",
        },
    )

    assert response.status_code == 403
    assert response.json()["code"] == "csrf_invalid"


def test_case_phone_filter_is_exact_and_uses_normalized_phone(
    db_client: TestClient,
    platform_users,
    admissions_api_scenario: AdmissionsApiScenario,
) -> None:
    sign_in(db_client, platform_users, "admin")

    matched = db_client.get(
        "/api/v1/admission-cases",
        params={"student_phone": " 13800000004 "},
    )
    missing = db_client.get(
        "/api/v1/admission-cases",
        params={"student_phone": "13800000005"},
    )

    assert matched.status_code == 200
    assert [item["id"] for item in matched.json()["items"]] == [
        str(admissions_api_scenario.admission_case.id)
    ]
    assert missing.status_code == 200
    assert missing.json()["items"] == []


def test_payment_filters_use_exact_phone_source_and_half_open_dates(
    db_client: TestClient,
    platform_users,
    admissions_api_scenario: AdmissionsApiScenario,
) -> None:
    sign_in(db_client, platform_users, "admin")

    matched = db_client.get(
        "/api/v1/payments",
        params={
            "payer_phone": " 13800000004 ",
            "source_kind": "manual",
            "date_from": (NOW - timedelta(seconds=1)).isoformat(),
            "date_to": (NOW + timedelta(seconds=1)).isoformat(),
        },
    )
    wrong_phone = db_client.get(
        "/api/v1/payments",
        params={"payer_phone": "13800000005"},
    )
    exclusive_end = db_client.get(
        "/api/v1/payments",
        params={"date_from": NOW.isoformat(), "date_to": NOW.isoformat()},
    )

    assert matched.status_code == 200
    assert [item["id"] for item in matched.json()["items"]] == [
        str(admissions_api_scenario.payment.id)
    ]
    assert wrong_phone.status_code == 200
    assert wrong_phone.json()["items"] == []
    assert exclusive_end.status_code == 200
    assert exclusive_end.json()["items"] == []


def test_admin_manages_service_account_and_one_time_credential_secret(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    csrf = sign_in(db_client, platform_users, "admin")
    created = db_client.post(
        "/api/v1/service-accounts",
        headers={"X-CSRF-Token": csrf},
        json={"name": "收款系统", "source_system": "api-contract-payment"},
    )
    assert created.status_code == 201
    account_id = created.json()["id"]

    credential = db_client.post(
        f"/api/v1/service-accounts/{account_id}/credentials",
        headers={"X-CSRF-Token": csrf},
        json={"name": "接口测试凭证"},
    )
    assert credential.status_code == 201
    assert credential.json()["secret"]
    credential_id = credential.json()["id"]

    listed = db_client.get("/api/v1/service-accounts")
    assert listed.status_code == 200
    serialized = listed.json()
    assert "secret" not in str(serialized).lower()
    item = next(value for value in serialized["items"] if value["id"] == account_id)
    assert item["credentials"][0]["public_id"] == credential.json()["public_id"]

    disabled_credential = db_client.post(
        f"/api/v1/service-account-credentials/{credential_id}/disable",
        headers={"X-CSRF-Token": csrf},
    )
    disabled_account = db_client.post(
        f"/api/v1/service-accounts/{account_id}/disable",
        headers={"X-CSRF-Token": csrf},
    )
    assert disabled_credential.status_code == 204
    assert disabled_account.status_code == 204
    assert db.scalar(
        select(func.count()).select_from(AuditEvent).where(
            AuditEvent.resource_id.in_([UUID(account_id), UUID(credential_id)]),
            AuditEvent.action.in_(
                [
                    "admissions.service_account.create",
                    "admissions.service_credential.create",
                    "admissions.service_credential.disable",
                    "admissions.service_account.disable",
                ]
            ),
        )
    ) == 4


@pytest.mark.parametrize("operation", ["payment", "refund"])
def test_manual_money_retry_is_idempotent_and_rejects_changed_payload(
    db_client: TestClient, platform_users, admissions_api_scenario: AdmissionsApiScenario,
    db: Session, operation: str,
) -> None:
    scenario = admissions_api_scenario
    csrf = sign_in(db_client, platform_users, "admin")
    operation_key = str(uuid4())
    path = "/api/v1/payments" if operation == "payment" else f"/api/v1/payments/{scenario.payment.id}/refunds"
    payload = (
        {"admission_case_id": str(scenario.admission_case.id), "paid_at": NOW.isoformat(), "note": "幂等收款"}
        if operation == "payment"
        else {"refunded_at": NOW.isoformat(), "reason": "幂等退款"}
    ) | {"amount": "10.00", "idempotency_key": operation_key}
    headers = {"X-CSRF-Token": csrf}
    first = db_client.post(path, headers=headers, json=payload)
    retry = db_client.post(path, headers=headers, json=payload)
    assert first.status_code == retry.status_code == 201
    assert first.json()["id"] == retry.json()["id"]
    conflict = db_client.post(path, headers=headers, json=payload | {"amount": "20.00"})
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "payment_operation_conflict"
    fresh = db_client.post(path, headers=headers, json=payload | {"idempotency_key": str(uuid4())})
    assert fresh.status_code == 201
    assert fresh.json()["id"] != first.json()["id"]
    model = Payment if operation == "payment" else PaymentRefund
    assert db.scalar(
        select(func.count()).select_from(model).where(model.idempotency_key == UUID(operation_key))
    ) == 1
    expected_action = "admissions.payment.create_manual" if operation == "payment" else "admissions.payment.refund"
    assert db.scalar(
        select(func.count()).select_from(AuditEvent).where(AuditEvent.action == expected_action)
    ) == 2
    coach_csrf = sign_in(db_client, platform_users, "coach")
    forbidden = db_client.post(path, headers={"X-CSRF-Token": coach_csrf}, json=payload)
    assert forbidden.status_code in (403, 404)


@pytest.mark.parametrize("operation", ["payment", "refund"])
@pytest.mark.parametrize("operation_key", [None, "invalid-operation"])
def test_manual_money_requires_valid_operation_key_with_chinese_message(
    db_client: TestClient, platform_users, admissions_api_scenario: AdmissionsApiScenario,
    operation: str, operation_key: str | None,
) -> None:
    csrf = sign_in(db_client, platform_users, "admin")
    scenario = admissions_api_scenario
    path = "/api/v1/payments" if operation == "payment" else f"/api/v1/payments/{scenario.payment.id}/refunds"
    payload = (
        {"admission_case_id": str(scenario.admission_case.id), "paid_at": NOW.isoformat(), "note": "旧页面收款"}
        if operation == "payment"
        else {"refunded_at": NOW.isoformat(), "reason": "旧页面退款"}
    ) | {"amount": "10.00"}
    if operation_key is not None:
        payload["idempotency_key"] = operation_key
    response = db_client.post(path, headers={"X-CSRF-Token": csrf}, json=payload)
    assert response.status_code == 422
    assert response.json()["code"] == "payment_operation_key_required"
    assert "请刷新页面" in response.json()["detail"]


def test_admin_records_installment_and_refunds_specific_payment(
    db_client: TestClient,
    platform_users,
    admissions_api_scenario: AdmissionsApiScenario,
) -> None:
    csrf = sign_in(db_client, platform_users, "admin")

    created = db_client.post(
        "/api/v1/payments",
        headers={"X-CSRF-Token": csrf},
        json={
            "admission_case_id": str(admissions_api_scenario.admission_case.id),
            "amount": "100.05",
            "idempotency_key": str(uuid4()),
            "paid_at": NOW.isoformat(),
            "note": "第二期线下付款",
        },
    )

    assert created.status_code == 201
    assert created.json()["amount_cents"] == 10_005
    payment_id = created.json()["id"]
    refunded = db_client.post(
        f"/api/v1/payments/{payment_id}/refunds",
        headers={"X-CSRF-Token": csrf},
        json={
            "amount": "20.00",
            "idempotency_key": str(uuid4()),
            "refunded_at": NOW.isoformat(),
            "reason": "重复转账退款",
        },
    )
    assert refunded.status_code == 201
    assert refunded.json()["amount_cents"] == 2_000
    listed = db_client.get("/api/v1/payments", params={"page_size": 100})
    item = next(value for value in listed.json()["items"] if value["id"] == payment_id)
    assert item["available_refund_cents"] == 8_005


def _new_pending_case(
    db: Session,
    *,
    admin_id: UUID,
    amount_cents: int = 20_000,
) -> AdmissionCase:
    admission_case = AdmissionCase(
        student_person_id=None,
        status="pending_match",
        created_by_user_id=admin_id,
    )
    db.add(admission_case)
    db.flush()
    payment = Payment(
        source_kind="manual",
        source_system=None,
        external_order_id=None,
        payer_account_id=None,
        payer_person_id=None,
        amount_cents=amount_cents,
        currency="CNY",
        paid_at=NOW,
        manual_note="待匹配接口付款",
        created_by_user_id=admin_id,
    )
    db.add(payment)
    db.flush()
    db.add(
        AdmissionCasePayment(
            admission_case_id=admission_case.id,
            payment_id=payment.id,
            attached_by_user_id=admin_id,
        )
    )
    db.commit()
    return admission_case


def test_admin_matches_closes_and_reopens_admission_case(
    db_client: TestClient,
    platform_users,
    admissions_api_scenario: AdmissionsApiScenario,
    db: Session,
) -> None:
    pending = _new_pending_case(db, admin_id=admissions_api_scenario.admin.id)
    csrf = sign_in(db_client, platform_users, "admin")

    matched = db_client.post(
        f"/api/v1/admission-cases/{pending.id}/match-student",
        headers={"X-CSRF-Token": csrf},
        json={"student_person_id": str(platform_users["people"]["self_student"].id)},
    )
    assert matched.status_code == 200
    assert matched.json()["status"] == "ready"

    closed = db_client.post(
        f"/api/v1/admission-cases/{pending.id}/close",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "本期不参加"},
    )
    assert closed.status_code == 200
    assert closed.json()["status"] == "closed"

    reopened = db_client.post(
        f"/api/v1/admission-cases/{pending.id}/reopen",
        headers={"X-CSRF-Token": csrf},
    )
    assert reopened.status_code == 200
    assert reopened.json()["status"] == "ready"


def test_email_only_student_can_be_matched_to_pending_case(
    db_client: TestClient,
    platform_users,
    admissions_api_scenario: AdmissionsApiScenario,
    db: Session,
) -> None:
    pending = _new_pending_case(db, admin_id=admissions_api_scenario.admin.id)
    student = _switch_to_email_only_account(db, platform_users, "minor_student")
    csrf = sign_in(db_client, platform_users, "admin")
    before_success_audits = (
        db.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.action == "admissions.admission_case.match_student",
                AuditEvent.outcome == "allowed",
            )
        )
        or 0
    )

    response = db_client.post(
        f"/api/v1/admission-cases/{pending.id}/match-student",
        headers={"X-CSRF-Token": csrf},
        json={"student_person_id": str(student.id)},
    )

    assert response.status_code == 200
    db.refresh(pending)
    assert pending.status == "ready"
    assert pending.student_person_id == student.id
    assert (
        db.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.action == "admissions.admission_case.match_student",
                AuditEvent.outcome == "allowed",
            )
        )
        == before_success_audits + 1
    )


def _new_ready_case(
    db: Session,
    *,
    admin_id: UUID,
    student_id: UUID,
    amount_cents: int = 50_000,
) -> AdmissionCase:
    admission_case = AdmissionCase(
        student_person_id=student_id,
        status="ready",
        created_by_user_id=admin_id,
    )
    db.add(admission_case)
    db.flush()
    payment = Payment(
        source_kind="manual",
        source_system=None,
        external_order_id=None,
        payer_account_id=None,
        payer_person_id=student_id,
        amount_cents=amount_cents,
        currency="CNY",
        paid_at=NOW,
        manual_note="确认报名接口付款",
        created_by_user_id=admin_id,
    )
    db.add(payment)
    db.flush()
    db.add(
        AdmissionCasePayment(
            admission_case_id=admission_case.id,
            payment_id=payment.id,
            attached_by_user_id=admin_id,
        )
    )
    db.commit()
    return admission_case


def test_email_only_student_can_confirm_paid_enrollment(
    db_client: TestClient,
    platform_users,
    admissions_api_scenario: AdmissionsApiScenario,
    db: Session,
) -> None:
    student = _switch_to_email_only_account(db, platform_users, "minor_student")
    ready = _new_ready_case(
        db,
        admin_id=admissions_api_scenario.admin.id,
        student_id=student.id,
    )
    csrf = sign_in(db_client, platform_users, "admin")
    before_enrollments = db.scalar(select(func.count()).select_from(Enrollment)) or 0
    before_success_audits = (
        db.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.action == "admissions.enrollment.confirm",
                AuditEvent.outcome == "allowed",
            )
        )
        or 0
    )

    response = db_client.post(
        f"/api/v1/admission-cases/{ready.id}/confirm-enrollment",
        headers={"X-CSRF-Token": csrf},
        json={"class_id": str(admissions_api_scenario.teaching_class.id)},
    )

    assert response.status_code == 201
    assert db.scalar(select(func.count()).select_from(Enrollment)) == before_enrollments + 1
    assert (
        db.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.action == "admissions.enrollment.confirm",
                AuditEvent.outcome == "allowed",
            )
        )
        == before_success_audits + 1
    )


def test_email_only_student_can_receive_manual_payment(
    db_client: TestClient,
    platform_users,
    admissions_api_scenario: AdmissionsApiScenario,
    db: Session,
) -> None:
    student = _switch_to_email_only_account(db, platform_users, "minor_student")
    csrf = sign_in(db_client, platform_users, "admin")
    before_payments = db.scalar(select(func.count()).select_from(Payment)) or 0
    before_cases = db.scalar(select(func.count()).select_from(AdmissionCase)) or 0
    before_success_audits = (
        db.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.action == "admissions.payment.create_manual",
                AuditEvent.outcome == "allowed",
            )
        )
        or 0
    )

    response = db_client.post(
        "/api/v1/payments",
        headers={"X-CSRF-Token": csrf},
        json={
            "student_person_id": str(student.id),
            "admission_case_id": None,
            "amount": "5000.00",
            "idempotency_key": str(uuid4()),
            "paid_at": NOW.isoformat(),
            "note": "邮箱账号付款门禁",
        },
    )

    assert response.status_code == 201
    assert db.scalar(select(func.count()).select_from(Payment)) == before_payments + 1
    assert db.scalar(select(func.count()).select_from(AdmissionCase)) == before_cases + 1
    assert (
        db.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.action == "admissions.payment.create_manual",
                AuditEvent.outcome == "allowed",
            )
        )
        == before_success_audits + 1
    )


def test_email_only_self_service_application_can_receive_offline_payment(
    db_client: TestClient,
    platform_users,
    admissions_api_scenario: AdmissionsApiScenario,
    db: Session,
) -> None:
    student = _switch_to_email_only_account(db, platform_users, "minor_student")
    program = db.scalar(select(EnrollmentProgram).order_by(EnrollmentProgram.sort_order))
    assert program is not None
    admission_case = AdmissionCase(
        student_person_id=student.id,
        status="ready",
        agreed_tuition_cents=program.price_cents,
        application_source="self_service",
        selected_project_id=PROJECT_ID,
        selected_program_id=program.id,
        project_name_snapshot="英雄联盟",
        program_name_snapshot=program.name,
        payment_preference="offline",
        created_by_user_id=admissions_api_scenario.admin.id,
        updated_by_user_id=admissions_api_scenario.admin.id,
    )
    db.add(admission_case)
    db.commit()
    csrf = sign_in(db_client, platform_users, "admin")
    before_payments = db.scalar(select(func.count()).select_from(Payment)) or 0

    response = db_client.post(
        "/api/v1/payments",
        headers={"X-CSRF-Token": csrf},
        json={
            "student_person_id": None,
            "admission_case_id": str(admission_case.id),
            "idempotency_key": str(uuid4()),
            "amount": (
                f"{program.price_cents // 100}.{program.price_cents % 100:02d}"
            ),
            "paid_at": NOW.isoformat(),
            "note": "线下报名收款确认",
        },
    )

    assert response.status_code == 201
    assert response.json()["admission_case_id"] == str(admission_case.id)
    assert response.json()["amount_cents"] == program.price_cents
    assert db.scalar(select(func.count()).select_from(Payment)) == before_payments + 1
    assert db.scalar(
        select(func.count()).select_from(AdmissionCasePayment).where(
            AdmissionCasePayment.admission_case_id == admission_case.id
        )
    ) == 1


def test_admin_confirms_transfers_and_cancels_enrollment(
    db_client: TestClient,
    platform_users,
    admissions_api_scenario: AdmissionsApiScenario,
    db: Session,
) -> None:
    ready = _new_ready_case(
        db,
        admin_id=admissions_api_scenario.admin.id,
        student_id=platform_users["people"]["minor_student"].id,
    )
    csrf = sign_in(db_client, platform_users, "admin")
    confirmed = db_client.post(
        f"/api/v1/admission-cases/{ready.id}/confirm-enrollment",
        headers={"X-CSRF-Token": csrf},
        json={"class_id": str(admissions_api_scenario.teaching_class.id)},
    )
    assert confirmed.status_code == 201
    enrollment_id = confirmed.json()["id"]

    transferred = db_client.post(
        f"/api/v1/enrollments/{enrollment_id}/transfer",
        headers={"X-CSRF-Token": csrf},
        json={
            "target_class_id": str(admissions_api_scenario.target_class.id),
            "reason": "调整训练方向",
        },
    )
    assert transferred.status_code == 200
    assert transferred.json()["class_id"] == str(admissions_api_scenario.target_class.id)

    cancelled = db_client.post(
        f"/api/v1/enrollments/{enrollment_id}/cancel",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "改到下期"},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    db.expire_all()
    stored_case = db.get(AdmissionCase, ready.id)
    assert stored_case is not None and stored_case.status == "ready"


def test_admin_previews_and_confirms_withdrawal_refund(
    db_client: TestClient,
    platform_users,
    admissions_api_scenario: AdmissionsApiScenario,
    db: Session,
) -> None:
    csrf = sign_in(db_client, platform_users, "admin")
    enrollment_id = admissions_api_scenario.enrollment.id
    preview = db_client.post(
        f"/api/v1/enrollments/{enrollment_id}/withdrawal-refund-preview",
        headers={"X-CSRF-Token": csrf},
        json={
            "withdrawal_date": (NOW - timedelta(days=1)).date().isoformat(),
            "policy": "prorated_remaining_days",
        },
    )
    assert preview.status_code == 200
    assert preview.json()["refund_cents"] == 40_000
    preview_audit = db.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "admissions.withdrawal.preview_amounts",
            AuditEvent.resource_id == enrollment_id,
        )
    )
    assert preview_audit is not None
    assert preview_audit.event_metadata == {"result_count": 1}

    confirmed = db_client.post(
        f"/api/v1/enrollments/{enrollment_id}/withdrawal-refund-confirm",
        headers={"X-CSRF-Token": csrf},
        json={**preview.json(), "reason": "家庭安排变化"},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "cancelled"


def test_payments_deny_blocks_withdrawal_amount_preview(
    db_client: TestClient,
    platform_users,
    admissions_api_scenario: AdmissionsApiScenario,
    db: Session,
) -> None:
    admin = platform_users["accounts"]["admin"]
    db.add(
        UserPermissionOverride(
            user_id=admin.id,
            permission_id=PERMISSIONS["payments.read"].id,
            effect="deny",
            scope_kind="global",
            scope_id=None,
        )
    )
    db.commit()
    csrf = sign_in(db_client, platform_users, "admin")

    response = db_client.post(
        f"/api/v1/enrollments/{admissions_api_scenario.enrollment.id}/withdrawal-refund-preview",
        headers={"X-CSRF-Token": csrf},
        json={
            "withdrawal_date": (NOW - timedelta(days=1)).date().isoformat(),
            "policy": "prorated_remaining_days",
        },
    )

    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"


def test_payment_conflict_routes_paginate_and_require_resolution_note(
    db_client: TestClient,
    platform_users,
) -> None:
    csrf = sign_in(db_client, platform_users, "admin")
    listed = db_client.get("/api/v1/payment-conflicts")
    invalid = db_client.post(
        f"/api/v1/payment-conflicts/{uuid4()}/resolve",
        headers={"X-CSRF-Token": csrf},
        json={"action": "dismissed", "note": "   "},
    )

    assert listed.status_code == 200
    assert listed.json() == {"items": [], "page": 1, "page_size": 20, "total": 0}
    assert invalid.status_code == 422


def test_resolving_conflict_never_changes_original_import_evidence(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    admin = platform_users["accounts"]["admin"]
    marker = uuid4().hex
    service_account = ServiceAccount(
        name="冲突测试机器账号",
        source_system=f"conflict-api-{marker}",
        is_active=True,
        created_by_user_id=admin.id,
    )
    db.add(service_account)
    db.flush()
    import_event = PaymentImportEvent(
        service_account_id=service_account.id,
        source_system=service_account.source_system,
        external_order_id=f"order-{marker}",
        idempotency_key=f"idem-{marker}",
        payer_phone_digest="a" * 64,
        payload_digest="b" * 64,
        raw_payload_ciphertext="encrypted-original-payload",
        processing_status="conflict",
        payment_id=None,
        conflict_id=None,
    )
    db.add(import_event)
    db.flush()
    conflict = PaymentConflict(
        original_import_event_id=import_event.id,
        conflict_type="external_order_payload_mismatch",
        attempted_payload_digest="c" * 64,
        attempted_raw_payload_ciphertext="encrypted-attempted-payload",
        status="open",
    )
    db.add(conflict)
    db.flush()
    import_event.conflict_id = conflict.id
    db.commit()
    original = (
        import_event.external_order_id,
        import_event.idempotency_key,
        import_event.payer_phone_digest,
        import_event.payload_digest,
        import_event.raw_payload_ciphertext,
        import_event.payment_id,
    )
    csrf = sign_in(db_client, platform_users, "admin")

    listed = db_client.get("/api/v1/payment-conflicts")
    resolved = db_client.post(
        f"/api/v1/payment-conflicts/{conflict.id}/resolve",
        headers={"X-CSRF-Token": csrf},
        json={"action": "resolved", "note": "已核对原付款系统记录"},
    )

    assert listed.status_code == 200
    item = next(value for value in listed.json()["items"] if value["id"] == str(conflict.id))
    assert {
        "raw_payload",
        "raw_payload_ciphertext",
        "payload_digest",
        "attempted_raw_payload",
        "attempted_raw_payload_ciphertext",
        "attempted_payload_digest",
        "payer_phone_digest",
    }.isdisjoint(item)
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"
    db.expire_all()
    stored_event = db.get(PaymentImportEvent, import_event.id)
    assert stored_event is not None
    assert (
        stored_event.external_order_id,
        stored_event.idempotency_key,
        stored_event.payer_phone_digest,
        stored_event.payload_digest,
        stored_event.raw_payload_ciphertext,
        stored_event.payment_id,
    ) == original


def test_service_account_audit_failure_rolls_back_creation(
    db_client: TestClient,
    platform_users,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    csrf = sign_in(db_client, platform_users, "admin")

    def reject_audit(*args, **kwargs) -> None:
        del args, kwargs
        raise AuditMetadataRejected("simulated admissions audit failure")

    monkeypatch.setattr(payment_router.AuditService, "append", reject_audit)
    rejected = db_client.post(
        "/api/v1/service-accounts",
        headers={"X-CSRF-Token": csrf},
        json={"name": "审计失败机器账号", "source_system": "audit-failure-source"},
    )

    assert rejected.status_code == 500
    assert rejected.json()["code"] == "admissions_audit_failed"
    assert db.scalar(
        select(ServiceAccount.id).where(
            ServiceAccount.source_system == "audit-failure-source"
        )
    ) is None


def test_missing_enrollment_is_masked_with_stable_not_found_problem(
    db_client: TestClient,
    platform_users,
) -> None:
    sign_in(db_client, platform_users, "admin")

    response = db_client.get(f"/api/v1/enrollments/{uuid4()}")

    assert response.status_code == 404
    assert response.json()["code"] == "enrollment_not_found"


def test_admissions_lists_use_stable_pagination(
    db_client: TestClient,
    platform_users,
    admissions_api_scenario: AdmissionsApiScenario,
    db: Session,
) -> None:
    _new_pending_case(db, admin_id=admissions_api_scenario.admin.id)
    sign_in(db_client, platform_users, "admin")

    first = db_client.get(
        "/api/v1/admission-cases",
        params={"page": 1, "page_size": 1},
    )
    second = db_client.get(
        "/api/v1/admission-cases",
        params={"page": 2, "page_size": 1},
    )

    assert first.status_code == second.status_code == 200
    assert first.json()["total"] == second.json()["total"] == 2
    assert first.json()["items"][0]["id"] != second.json()["items"][0]["id"]
