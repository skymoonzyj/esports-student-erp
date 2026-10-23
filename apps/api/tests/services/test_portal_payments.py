from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.admissions.models import (
    AdmissionCase,
    AdmissionCasePayment,
    Payment,
    PaymentRefund,
)
from app.auth.models import UserAccount
from app.identity.models import GuardianRelationship, Person, StudentProfile
from app.portal.errors import PortalPermissionDenied
from app.portal.payment_service import PortalPaymentService
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES


@dataclass
class PortalPaymentScenario:
    db: Session
    service: PortalPaymentService
    first_guardian: Person
    first_account: UserAccount
    second_guardian: Person
    second_account: UserAccount
    student: Person
    relationship: GuardianRelationship
    first_payment: Payment
    second_payment: Payment
    no_account_payment: Payment

    def end_relationship(self) -> None:
        self.relationship.ended_at = datetime(2026, 7, 29, 8, tzinfo=UTC)
        self.relationship.ended_by_user_id = self.first_account.id
        self.relationship.end_reason = "付款门户测试结束关系"
        self.db.flush()

    def deny(
        self,
        account: UserAccount,
        permission: str,
        scope_kind: str,
    ) -> None:
        self.db.add(
            UserPermissionOverride(
                user_id=account.id,
                permission_id=PERMISSIONS[permission].id,
                effect="deny",
                scope_kind=scope_kind,
                scope_id=None,
            )
        )
        self.db.flush()

    def allow(
        self,
        account: UserAccount,
        permission: str,
        scope_kind: str,
    ) -> None:
        self.db.add(
            UserPermissionOverride(
                user_id=account.id,
                permission_id=PERMISSIONS[permission].id,
                effect="allow",
                scope_kind=scope_kind,
                scope_id=None,
            )
        )
        self.db.flush()

    def deny_student_name(self) -> None:
        self.db.add(
            UserPermissionOverride(
                user_id=self.first_account.id,
                permission_id=PERMISSIONS["people.read"].id,
                effect="deny",
                scope_kind="person",
                scope_id=self.student.id,
            )
        )
        self.db.flush()


def _person(db: Session, *, name: str, marker: str, birth: date) -> Person:
    person = Person(
        full_name=name,
        identity_ciphertext=f"payment-cipher-{marker}",
        identity_digest=f"payment-id-{marker}",
        sex="female",
        date_of_birth=birth,
    )
    db.add(person)
    db.flush()
    return person


def _guardian_account(
    db: Session,
    *,
    person: Person,
    marker: str,
) -> UserAccount:
    account = UserAccount(
        person_id=person.id,
        login_phone_digest=f"payment-phone-{marker}",
        email_normalized=f"payment-{marker}@example.test",
        password_hash="test-password-hash",
        status="active",
        force_password_change=False,
        failed_login_count=0,
    )
    db.add(account)
    db.flush()
    db.add(UserRole(user_id=account.id, role_id=ROLES["guardian"].id))
    db.flush()
    return account


@pytest.fixture
def portal_payment_scenario(db: Session) -> PortalPaymentScenario:
    marker = uuid4().hex[:12]
    first_guardian = _person(
        db,
        name="第一付款监护人",
        marker=f"{marker}-first",
        birth=date(1980, 1, 2),
    )
    first_account = _guardian_account(
        db,
        person=first_guardian,
        marker=f"{marker}-first",
    )
    second_guardian = _person(
        db,
        name="第二付款监护人",
        marker=f"{marker}-second",
        birth=date(1982, 1, 2),
    )
    second_account = _guardian_account(
        db,
        person=second_guardian,
        marker=f"{marker}-second",
    )
    student = _person(
        db,
        name="共同绑定学员",
        marker=f"{marker}-student",
        birth=date(2012, 1, 2),
    )
    db.add(
        StudentProfile(
            person_id=student.id,
            student_number=f"PAY-{marker[:8]}",
            status="active",
        )
    )
    relationship = GuardianRelationship(
        guardian_person_id=first_guardian.id,
        student_person_id=student.id,
        relationship="mother",
        is_primary=True,
        valid_from=date(2020, 1, 1),
    )
    second_relationship = GuardianRelationship(
        guardian_person_id=second_guardian.id,
        student_person_id=student.id,
        relationship="father",
        is_primary=False,
        valid_from=date(2020, 1, 1),
    )
    db.add_all([relationship, second_relationship])
    admission_case = AdmissionCase(
        student_person_id=student.id,
        status="ready",
        agreed_tuition_cents=100_000,
        created_by_user_id=first_account.id,
    )
    db.add(admission_case)
    db.flush()

    paid_at = datetime(2026, 7, 1, 1, tzinfo=UTC)
    first_payment = Payment(
        id=UUID("a1000000-0000-4000-8000-000000000001"),
        source_kind="manual",
        payer_account_id=first_account.id,
        payer_person_id=first_guardian.id,
        amount_cents=100_000,
        currency="CNY",
        paid_at=paid_at + timedelta(days=2),
        manual_note="第一付款内部备注",
        created_by_user_id=first_account.id,
    )
    second_payment = Payment(
        id=UUID("a1000000-0000-4000-8000-000000000002"),
        source_kind="api",
        source_system="forbidden-source",
        external_order_id="forbidden-other-order",
        payer_account_id=second_account.id,
        payer_person_id=second_guardian.id,
        amount_cents=99_999,
        currency="CNY",
        paid_at=paid_at + timedelta(days=1),
        created_by_user_id=None,
    )
    no_account_payment = Payment(
        id=UUID("a1000000-0000-4000-8000-000000000003"),
        source_kind="manual",
        payer_account_id=None,
        payer_person_id=first_guardian.id,
        amount_cents=50_000,
        currency="CNY",
        paid_at=paid_at,
        manual_note="无账号付款内部备注",
        created_by_user_id=first_account.id,
    )
    db.add_all([first_payment, second_payment, no_account_payment])
    db.flush()
    db.add_all(
        [
            AdmissionCasePayment(
                admission_case_id=admission_case.id,
                payment_id=payment.id,
                attached_by_user_id=first_account.id,
            )
            for payment in (first_payment, second_payment, no_account_payment)
        ]
    )
    db.add(
        PaymentRefund(
            payment_id=first_payment.id,
            withdrawal_id=None,
            amount_cents=20_000,
            refunded_at=paid_at + timedelta(days=3),
            refund_type="manual",
            reason="不应返回的退款原因",
            created_by_user_id=first_account.id,
        )
    )
    db.flush()
    return PortalPaymentScenario(
        db=db,
        service=PortalPaymentService(db),
        first_guardian=first_guardian,
        first_account=first_account,
        second_guardian=second_guardian,
        second_account=second_account,
        student=student,
        relationship=relationship,
        first_payment=first_payment,
        second_payment=second_payment,
        no_account_payment=no_account_payment,
    )


def test_self_payments_filter_by_current_account(
    portal_payment_scenario: PortalPaymentScenario,
) -> None:
    result = portal_payment_scenario.service.list_payments(
        user_id=portal_payment_scenario.first_account.id,
        actor_person_id=portal_payment_scenario.first_guardian.id,
        page=1,
        page_size=20,
    )

    assert [item.payment_id for item in result.items] == [
        portal_payment_scenario.first_payment.id
    ]
    assert result.items[0].status == "partially_refunded"
    assert result.items[0].refunded_cents == 20_000
    assert result.items[0].net_cents == 80_000
    assert result.items[0].related_student_name == "共同绑定学员"
    assert [item.amount_cents for item in result.items[0].refunds] == [20_000]
    assert "退款原因" not in result.model_dump_json()
    assert "内部备注" not in result.model_dump_json()
    assert "forbidden-other-order" not in result.model_dump_json()


def test_second_guardian_sees_only_their_own_payment(
    portal_payment_scenario: PortalPaymentScenario,
) -> None:
    result = portal_payment_scenario.service.list_payments(
        user_id=portal_payment_scenario.second_account.id,
        actor_person_id=portal_payment_scenario.second_guardian.id,
        page=1,
        page_size=20,
    )

    assert [item.payment_id for item in result.items] == [
        portal_payment_scenario.second_payment.id
    ]
    assert result.items[0].amount_cents == 99_999
    assert result.items[0].status == "paid"


def test_full_refund_reports_zero_net_and_refunded_status(
    portal_payment_scenario: PortalPaymentScenario,
) -> None:
    refund = portal_payment_scenario.db.scalar(
        select(PaymentRefund).where(
            PaymentRefund.payment_id == portal_payment_scenario.first_payment.id
        )
    )
    assert refund is not None
    refund.amount_cents = portal_payment_scenario.first_payment.amount_cents
    portal_payment_scenario.db.flush()

    result = portal_payment_scenario.service.list_payments(
        user_id=portal_payment_scenario.first_account.id,
        actor_person_id=portal_payment_scenario.first_guardian.id,
        page=1,
        page_size=20,
    )

    assert result.items[0].status == "refunded"
    assert result.items[0].refunded_cents == 100_000
    assert result.items[0].net_cents == 0


def test_relationship_end_hides_student_name_but_keeps_payment(
    portal_payment_scenario: PortalPaymentScenario,
) -> None:
    portal_payment_scenario.allow(
        portal_payment_scenario.first_account,
        "people.read",
        "global",
    )
    portal_payment_scenario.end_relationship()

    result = portal_payment_scenario.service.list_payments(
        user_id=portal_payment_scenario.first_account.id,
        actor_person_id=portal_payment_scenario.first_guardian.id,
        page=1,
        page_size=20,
    )

    assert result.items[0].payment_id == portal_payment_scenario.first_payment.id
    assert result.items[0].related_student_name is None


def test_person_deny_hides_student_name_but_keeps_payment(
    portal_payment_scenario: PortalPaymentScenario,
) -> None:
    portal_payment_scenario.deny_student_name()

    result = portal_payment_scenario.service.list_payments(
        user_id=portal_payment_scenario.first_account.id,
        actor_person_id=portal_payment_scenario.first_guardian.id,
        page=1,
        page_size=20,
    )

    assert result.items[0].payment_id == portal_payment_scenario.first_payment.id
    assert result.items[0].related_student_name is None


def test_self_payment_permission_deny_is_rejected(
    portal_payment_scenario: PortalPaymentScenario,
) -> None:
    portal_payment_scenario.deny(
        portal_payment_scenario.first_account,
        "payments.read",
        "self",
    )

    with pytest.raises(PortalPermissionDenied):
        portal_payment_scenario.service.list_payments(
            user_id=portal_payment_scenario.first_account.id,
            actor_person_id=portal_payment_scenario.first_guardian.id,
            page=1,
            page_size=20,
        )


def test_self_payment_projection_uses_bounded_query_count(
    portal_payment_scenario: PortalPaymentScenario,
) -> None:
    bind = portal_payment_scenario.db.get_bind()
    selects = 0

    def record_select(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        nonlocal selects
        if statement.lstrip().upper().startswith("SELECT"):
            selects += 1

    event.listen(bind, "before_cursor_execute", record_select)
    try:
        portal_payment_scenario.service.list_payments(
            user_id=portal_payment_scenario.first_account.id,
            actor_person_id=portal_payment_scenario.first_guardian.id,
            page=1,
            page_size=20,
        )
    finally:
        event.remove(bind, "before_cursor_execute", record_select)

    assert selects <= 9
