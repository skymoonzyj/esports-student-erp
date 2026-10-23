from __future__ import annotations

import hashlib
import json
import re
import secrets
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy import delete, func, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, SessionTransactionOrigin

from app.admissions.models import (
    AdmissionCase,
    AdmissionCasePayment,
    Enrollment,
    Payment,
    PaymentRefund,
)
from app.admissions.money import InvalidMoney, parse_cny
from app.audit.service import AuditService
from app.auth.models import UserAccount
from app.core.time import business_date_at
from app.core.transactions import write_transaction
from app.identity.models import Person
from app.registration.models import RegistrationApplication
from app.revenue_share.errors import (
    ReferralCodeInvalid,
    RevenueShareConflict,
    RevenueShareNotFound,
    RevenueShareReconcileBusy,
    RevenueShareSettlementExceedsBalance,
    RevenueShareSettlementOperationConflict,
    RevenueShareSettlementReferenceConflict,
)
from app.revenue_share.models import (
    ReferralCode,
    RevenueShareLedgerEntry,
    RevenueShareOrganization,
    RevenueSharePaymentStatus,
    RevenueShareReconcileQueue,
    RevenueShareRule,
    RevenueShareSettlement,
    StudentReferralBinding,
)
from app.revenue_share.pagination import (
    LedgerCursor,
    ReconcileCursor,
    decode_cursor,
    encode_cursor,
)
from app.revenue_share.reconciliation_locking import (
    ReconciliationContextChanged,
    lock_reconciliation_context,
)
from app.revenue_share.schemas import (
    ReferralCodeCreate,
    RevenueShareOrganizationCreate,
    RevenueShareRuleCreate,
    RevenueShareSettlementCreate,
)
from app.teaching.models import CoachAssignment, GameProject, TeachingClass

_CODE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9_-]{3,23}$")
_AUTO_ROLES = frozenset({"inviter", "subject_coach", "assistant"})
_RULE_CONFIGURATION_LOCK_KEY = int.from_bytes(
    hashlib.sha256(b"revenue-share-rule-configuration").digest()[:8], "big", signed=True
)


@dataclass(frozen=True)
class ResolvedBeneficiary:
    kind: str
    person_id: UUID | None
    organization_id: UUID | None

    @property
    def id(self) -> UUID:
        value = self.person_id if self.kind == "person" else self.organization_id
        if value is None:  # pragma: no cover - construction invariant
            raise RuntimeError("revenue-share beneficiary is incomplete")
        return value


@dataclass(frozen=True)
class PaymentContext:
    payment: Payment
    student_person_id: UUID
    enrollment: Enrollment
    teaching_class: TeachingClass


@dataclass(frozen=True)
class ReconcileResult:
    state: str
    reason_code: str | None


@dataclass(frozen=True)
class ReconcileBatchResult:
    processed: int
    allocated: int
    pending: int
    has_more: bool
    next_cursor: str | None


@dataclass(frozen=True)
class LedgerPage:
    items: list[RevenueShareLedgerEntry]
    total: int
    has_more: bool
    next_cursor: str | None


@dataclass(frozen=True)
class Balance:
    beneficiary: ResolvedBeneficiary
    earned_cents: int
    settled_cents: int

    @property
    def outstanding_cents(self) -> int:
        return self.earned_cents - self.settled_cents


class RevenueShareService:
    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def normalize_code(value: str) -> str:
        code = value.strip().upper()
        if not _CODE_PATTERN.fullmatch(code):
            raise ReferralCodeInvalid("invitation code is invalid")
        return code

    def resolve_active_referral_code(self, value: str) -> ReferralCode:
        code = self.normalize_code(value)
        row = self.db.scalar(
            select(ReferralCode).where(
                ReferralCode.code == code,
                ReferralCode.is_active.is_(True),
            )
        )
        if row is None:
            raise ReferralCodeInvalid("invitation code is unavailable")
        return row

    def create_organization(
        self,
        input: RevenueShareOrganizationCreate,
        *,
        actor_user_id: UUID,
        request_id: str,
    ) -> RevenueShareOrganization:
        try:
            with write_transaction(self.db):
                row = RevenueShareOrganization(
                    name=input.name,
                    is_active=True,
                    created_by_user_id=actor_user_id,
                    updated_by_user_id=actor_user_id,
                )
                self.db.add(row)
                self.db.flush()
                self._audit(
                    actor_user_id,
                    "revenue_share.organization.create",
                    "revenue_share_organization",
                    row.id,
                    request_id,
                )
                return row
        except IntegrityError as exc:
            raise RevenueShareConflict("organization already exists") from exc

    def _generated_code(self) -> str:
        for _ in range(12):
            candidate = f"INV-{secrets.token_hex(4).upper()}"
            if (
                self.db.scalar(select(ReferralCode.id).where(ReferralCode.code == candidate))
                is None
            ):
                return candidate
        raise RevenueShareConflict("could not generate invitation code")

    def create_referral_code(
        self,
        input: ReferralCodeCreate,
        *,
        actor_user_id: UUID,
        request_id: str,
    ) -> ReferralCode:
        code = self.normalize_code(input.code) if input.code else self._generated_code()
        if input.source_kind == "person":
            person = self.db.scalar(
                select(Person).where(
                    Person.id == input.referrer_person_id,
                    Person.deleted_at.is_(None),
                )
            )
            if person is None:
                raise RevenueShareNotFound("referrer person was not found")
        else:
            organization = self.db.scalar(
                select(RevenueShareOrganization).where(
                    RevenueShareOrganization.id == input.referrer_organization_id,
                    RevenueShareOrganization.is_active.is_(True),
                )
            )
            if organization is None:
                raise RevenueShareNotFound("referrer organization was not found")
        try:
            with write_transaction(self.db):
                row = ReferralCode(
                    code=code,
                    label=input.label,
                    source_kind=input.source_kind,
                    referrer_person_id=input.referrer_person_id,
                    referrer_organization_id=input.referrer_organization_id,
                    is_active=True,
                    created_by_user_id=actor_user_id,
                    updated_by_user_id=actor_user_id,
                )
                self.db.add(row)
                self.db.flush()
                self._audit(
                    actor_user_id,
                    "revenue_share.referral_code.create",
                    "referral_code",
                    row.id,
                    request_id,
                )
                return row
        except IntegrityError as exc:
            raise RevenueShareConflict("invitation code already exists") from exc

    def ensure_person_referral_code(
        self,
        *,
        person_id: UUID,
        actor_user_id: UUID,
        request_id: str,
    ) -> ReferralCode:
        """Return a person's active code, creating one exactly once when needed."""
        try:
            with write_transaction(self.db):
                person = self.db.scalar(
                    select(Person)
                    .where(
                        Person.id == person_id,
                        Person.deleted_at.is_(None),
                    )
                    .with_for_update()
                )
                if person is None:
                    raise RevenueShareNotFound("referrer person was not found")
                existing = self.db.scalar(
                    select(ReferralCode)
                    .where(
                        ReferralCode.referrer_person_id == person.id,
                        ReferralCode.is_active.is_(True),
                    )
                    .order_by(ReferralCode.created_at.desc(), ReferralCode.id)
                    .limit(1)
                )
                if existing is not None:
                    return existing
                row = ReferralCode(
                    code=self._generated_code(),
                    label=f"{person.full_name}专属邀请"[:120],
                    source_kind="person",
                    referrer_person_id=person.id,
                    referrer_organization_id=None,
                    is_active=True,
                    created_by_user_id=actor_user_id,
                    updated_by_user_id=actor_user_id,
                )
                self.db.add(row)
                self.db.flush()
                self._audit(
                    actor_user_id,
                    "revenue_share.referral_code.create",
                    "referral_code",
                    row.id,
                    request_id,
                )
                return row
        except IntegrityError as exc:
            raise RevenueShareConflict("invitation code already exists") from exc

    @staticmethod
    def _percentage_basis_points(value: str) -> int:
        try:
            percent = Decimal(value)
        except (InvalidOperation, ValueError):
            raise RevenueShareConflict("percentage is invalid") from None
        if not percent.is_finite() or percent <= 0 or percent > 100:
            raise RevenueShareConflict("percentage must be between 0 and 100")
        basis_points = (percent * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        if basis_points / Decimal(100) != percent:
            raise RevenueShareConflict("percentage supports at most two decimals")
        return int(basis_points)

    def create_rule(
        self,
        input: RevenueShareRuleCreate,
        *,
        actor_user_id: UUID,
        request_id: str,
    ) -> RevenueShareRule:
        if input.project_id is not None and self.db.get(GameProject, input.project_id) is None:
            raise RevenueShareNotFound("project was not found")
        if input.recipient_person_id is not None:
            person = self.db.scalar(
                select(Person).where(
                    Person.id == input.recipient_person_id,
                    Person.deleted_at.is_(None),
                )
            )
            if person is None:
                raise RevenueShareNotFound("recipient person was not found")
        if input.recipient_organization_id is not None:
            organization = self.db.get(RevenueShareOrganization, input.recipient_organization_id)
            if organization is None or not organization.is_active:
                raise RevenueShareNotFound("recipient organization was not found")

        if input.calculation_type == "percentage":
            assert input.percentage is not None
            percentage_basis_points = self._percentage_basis_points(input.percentage)
            fixed_amount_cents = None
        else:
            assert input.fixed_amount is not None
            try:
                fixed_amount_cents = parse_cny(input.fixed_amount)
            except InvalidMoney as exc:
                raise RevenueShareConflict("fixed amount is invalid") from exc
            percentage_basis_points = None

        with write_transaction(self.db):
            self._lock_rule_configuration()
            active_rules = self._active_configuration_rules()
            overlapping = [
                row
                for row in active_rules
                if row.project_id == input.project_id
                and (row.effective_to is None or row.effective_to >= input.effective_from)
                and (input.effective_to is None or row.effective_from <= input.effective_to)
            ]
            if input.role_kind in _AUTO_ROLES and any(
                row.role_kind == input.role_kind for row in overlapping
            ):
                raise RevenueShareConflict("automatic role already has an active rule")
            row = RevenueShareRule(
                project_id=input.project_id,
                role_kind=input.role_kind,
                recipient_person_id=input.recipient_person_id,
                recipient_organization_id=input.recipient_organization_id,
                label=input.label,
                calculation_type=input.calculation_type,
                percentage_basis_points=percentage_basis_points,
                fixed_amount_cents=fixed_amount_cents,
                effective_from=input.effective_from,
                effective_to=input.effective_to,
                is_active=True,
                created_by_user_id=actor_user_id,
                updated_by_user_id=actor_user_id,
            )
            self._validate_effective_percentages([*active_rules, row], input.project_id)
            self.db.add(row)
            self.db.flush()
            self._audit(
                actor_user_id,
                "revenue_share.rule.create",
                "revenue_share_rule",
                row.id,
                request_id,
            )
            return row

    def disable_rule(
        self,
        rule_id: UUID,
        *,
        actor_user_id: UUID,
        request_id: str,
    ) -> RevenueShareRule:
        with write_transaction(self.db):
            self._lock_rule_configuration()
            row = self.db.scalar(
                select(RevenueShareRule)
                .where(RevenueShareRule.id == rule_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if row is None:
                raise RevenueShareNotFound("rule was not found")
            if row.is_active:
                remaining = [
                    item for item in self._active_configuration_rules() if item.id != row.id
                ]
                self._validate_effective_percentages(remaining, row.project_id)
            row.is_active = False
            row.updated_by_user_id = actor_user_id
            self._audit(
                actor_user_id,
                "revenue_share.rule.disable",
                "revenue_share_rule",
                row.id,
                request_id,
            )
            return row

    def _lock_rule_configuration(self) -> None:
        # Global and project rules combine, so all configuration writes use the
        # same transaction lock, even before the first rule exists. Always take
        # this before row locks; payment reconciliation does not take it.
        self.db.execute(select(func.pg_advisory_xact_lock(_RULE_CONFIGURATION_LOCK_KEY)))

    def _active_configuration_rules(self) -> list[RevenueShareRule]:
        return list(
            self.db.scalars(
                select(RevenueShareRule)
                .where(RevenueShareRule.is_active.is_(True))
                .order_by(RevenueShareRule.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )

    @staticmethod
    def _resolve_effective_rules(
        rows: list[RevenueShareRule],
        project_id: UUID | None,
        on_date: date,
    ) -> list[RevenueShareRule]:
        by_role: dict[str, list[RevenueShareRule]] = defaultdict(list)
        for row in rows:
            if (
                row.project_id in (None, project_id)
                and row.effective_from <= on_date
                and (row.effective_to is None or row.effective_to >= on_date)
            ):
                by_role[row.role_kind].append(row)
        resolved: list[RevenueShareRule] = []
        for role_rows in by_role.values():
            specific = [row for row in role_rows if row.project_id == project_id]
            resolved.extend(specific or [row for row in role_rows if row.project_id is None])
        return resolved

    @classmethod
    def _validate_effective_percentages(
        cls,
        rows: list[RevenueShareRule],
        changed_project_id: UUID | None,
    ) -> None:
        projects = (
            {None, *(row.project_id for row in rows)}
            if changed_project_id is None
            else {changed_project_id}
        )
        for project_id in projects:
            relevant = [row for row in rows if row.project_id in (None, project_id)]
            boundaries = {row.effective_from for row in relevant}
            boundaries.update(
                row.effective_to + timedelta(days=1)
                for row in relevant
                if row.effective_to is not None and row.effective_to < date.max
            )
            # The active set changes only on starts and after inclusive ends.
            # Checking these intervals also catches an expiring project override
            # exposing an excessive global percentage without any new rule start.
            for on_date in boundaries:
                effective = cls._resolve_effective_rules(relevant, project_id, on_date)
                if sum(row.percentage_basis_points or 0 for row in effective) > 10_000:
                    raise RevenueShareConflict("effective percentage rules exceed 100 percent")

    def bind_registration_referral(
        self,
        *,
        application: RegistrationApplication,
        student_person_id: UUID,
        bound_at: datetime,
    ) -> StudentReferralBinding | None:
        if application.primary_purpose != "student" or application.referral_code_id is None:
            return None
        existing = self.db.scalar(
            select(StudentReferralBinding).where(
                StudentReferralBinding.student_person_id == student_person_id
            )
        )
        if existing is not None:
            if existing.referral_code_id != application.referral_code_id:
                raise RevenueShareConflict("student already has a different referrer")
            return existing
        code = self.db.get(ReferralCode, application.referral_code_id)
        if code is None:
            raise RevenueShareConflict("registration referral code is unavailable")
        row = StudentReferralBinding(
            student_person_id=student_person_id,
            referral_code_id=code.id,
            registration_application_id=application.id,
            source_kind=code.source_kind,
            referrer_person_id=code.referrer_person_id,
            referrer_organization_id=code.referrer_organization_id,
            bound_at=bound_at,
            created_by_user_id=None,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def _payment_context(self, payment_id: UUID) -> PaymentContext | None:
        payment = self.db.get(Payment, payment_id)
        if payment is None:
            raise RevenueShareNotFound("payment was not found")
        row = self.db.execute(
            select(AdmissionCase, Enrollment, TeachingClass)
            .join(
                AdmissionCasePayment,
                AdmissionCasePayment.admission_case_id == AdmissionCase.id,
            )
            .join(
                Enrollment,
                Enrollment.id == AdmissionCase.current_enrollment_id,
            )
            .join(TeachingClass, TeachingClass.id == Enrollment.class_id)
            .where(
                AdmissionCasePayment.payment_id == payment_id,
                AdmissionCase.student_person_id.is_not(None),
            )
        ).one_or_none()
        if row is None:
            return None
        admission_case, enrollment, teaching_class = row
        assert admission_case.student_person_id is not None
        return PaymentContext(
            payment=payment,
            student_person_id=admission_case.student_person_id,
            enrollment=enrollment,
            teaching_class=teaching_class,
        )

    def _effective_rules(self, project_id: UUID, on_date: date) -> list[RevenueShareRule]:
        rows = list(
            self.db.scalars(
                select(RevenueShareRule)
                .where(
                    RevenueShareRule.is_active.is_(True),
                    or_(
                        RevenueShareRule.project_id == project_id,
                        RevenueShareRule.project_id.is_(None),
                    ),
                    RevenueShareRule.effective_from <= on_date,
                    or_(
                        RevenueShareRule.effective_to.is_(None),
                        RevenueShareRule.effective_to >= on_date,
                    ),
                )
                .order_by(RevenueShareRule.created_at, RevenueShareRule.id)
            )
        )
        return self._resolve_effective_rules(rows, project_id, on_date)

    @staticmethod
    def _beneficiary_from_rule(rule: RevenueShareRule) -> ResolvedBeneficiary:
        if rule.recipient_person_id is not None:
            return ResolvedBeneficiary("person", rule.recipient_person_id, None)
        if rule.recipient_organization_id is not None:
            return ResolvedBeneficiary("organization", None, rule.recipient_organization_id)
        raise RuntimeError("explicit revenue-share rule has no recipient")

    def _role_beneficiaries(
        self,
        context: PaymentContext,
        rule: RevenueShareRule,
    ) -> tuple[list[ResolvedBeneficiary], str | None]:
        if rule.role_kind == "inviter":
            binding = self.db.scalar(
                select(StudentReferralBinding).where(
                    StudentReferralBinding.student_person_id == context.student_person_id
                )
            )
            if binding is None:
                return [], None
            return [
                ResolvedBeneficiary(
                    binding.source_kind,
                    binding.referrer_person_id,
                    binding.referrer_organization_id,
                )
            ], None
        if rule.role_kind in {"subject_coach", "assistant"}:
            assignment_type = "primary" if rule.role_kind == "subject_coach" else "assistant"
            occurred_at = context.payment.paid_at
            assignments = list(
                self.db.scalars(
                    select(CoachAssignment)
                    .where(
                        CoachAssignment.class_id == context.teaching_class.id,
                        CoachAssignment.assignment_type == assignment_type,
                        CoachAssignment.valid_from <= occurred_at,
                        or_(
                            CoachAssignment.valid_until.is_(None),
                            CoachAssignment.valid_until > occurred_at,
                        ),
                        or_(
                            CoachAssignment.ended_at.is_(None),
                            CoachAssignment.ended_at > occurred_at,
                        ),
                    )
                    .order_by(CoachAssignment.created_at, CoachAssignment.id)
                )
            )
            if not assignments:
                return [], f"missing_{rule.role_kind}"
            return [
                ResolvedBeneficiary("person", row.coach_person_id, None) for row in assignments
            ], None
        return [self._beneficiary_from_rule(rule)], None

    @staticmethod
    def _rounded_ratio(numerator: int, denominator: int) -> int:
        if denominator <= 0:
            return 0
        return int(
            (Decimal(numerator) / Decimal(denominator)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        )

    @classmethod
    def _refund_reversal_targets(
        cls,
        *,
        payment_amount_cents: int,
        cumulative_refunded_cents: int,
        earnings: list[RevenueShareLedgerEntry],
        reversed_by_entry: dict[UUID, int],
    ) -> dict[UUID, int]:
        total_earnings = sum(row.amount_cents for row in earnings)
        refunded_cents = min(cumulative_refunded_cents, payment_amount_cents)
        if refunded_cents >= payment_amount_cents:
            pool_target = total_earnings
        else:
            pool_target = cls._rounded_ratio(
                total_earnings * refunded_cents,
                payment_amount_cents,
            )
        unconstrained_pool_target = pool_target

        # Persisted rows are append-only lower bounds. This also makes the
        # allocation monotone when largest-remainder priorities change between
        # successive partial refunds.
        lower_bounds = {row.id: max(reversed_by_entry.get(row.id, 0), 0) for row in earnings}
        upper_bounds = {row.id: max(lower_bounds[row.id], row.amount_cents) for row in earnings}
        if sum(lower_bounds.values()) >= pool_target:
            return lower_bounds

        numerators = {row.id: row.amount_cents * refunded_cents for row in earnings}
        remainders = {row.id: numerators[row.id] % payment_amount_cents for row in earnings}
        ordered = sorted(earnings, key=lambda row: (-remainders[row.id], row.id))
        ideal_targets = {row.id: numerators[row.id] // payment_amount_cents for row in earnings}
        remainder_count = unconstrained_pool_target - sum(ideal_targets.values())
        for row in ordered[:remainder_count]:
            ideal_targets[row.id] += 1
        targets = {
            row.id: min(
                max(ideal_targets[row.id], lower_bounds[row.id]),
                upper_bounds[row.id],
            )
            for row in earnings
        }

        # Append-only lower bounds can displace ideal seats. Redistribute the
        # remaining amount in bulk; never loop per cent.
        missing = pool_target - sum(targets.values())
        for row in ordered:
            if missing <= 0:
                break
            available = upper_bounds[row.id] - targets[row.id]
            added = min(available, missing)
            targets[row.id] += added
            missing -= added

        excess = sum(targets.values()) - pool_target
        for row in reversed(ordered):
            if excess <= 0:
                break
            removable = targets[row.id] - lower_bounds[row.id]
            removed = min(removable, excess)
            targets[row.id] -= removed
            excess -= removed
        return targets

    @staticmethod
    def _refund_adjustment(
        payment: Payment,
        refund: PaymentRefund,
        earning: RevenueShareLedgerEntry,
        delta: int,
    ) -> RevenueShareLedgerEntry:
        return RevenueShareLedgerEntry(
            event_key=f"refund:{refund.id}:{earning.id}",
            payment_id=payment.id,
            refund_id=refund.id,
            parent_entry_id=earning.id,
            rule_id=earning.rule_id,
            entry_kind="refund_adjustment",
            role_kind=earning.role_kind,
            beneficiary_kind=earning.beneficiary_kind,
            beneficiary_person_id=earning.beneficiary_person_id,
            beneficiary_organization_id=earning.beneficiary_organization_id,
            calculation_type=earning.calculation_type,
            percentage_basis_points_snapshot=earning.percentage_basis_points_snapshot,
            fixed_amount_cents_snapshot=earning.fixed_amount_cents_snapshot,
            base_amount_cents=refund.amount_cents,
            amount_cents=-delta,
            occurred_at=refund.refunded_at,
            created_by_user_id=refund.created_by_user_id,
        )

    @staticmethod
    def _percentage_amounts(
        payment_amount_cents: int,
        rules: list[RevenueShareRule],
    ) -> dict[UUID, int]:
        numerators = {
            rule.id: payment_amount_cents * (rule.percentage_basis_points or 0) for rule in rules
        }
        amounts = {rule_id: numerator // 10_000 for rule_id, numerator in numerators.items()}
        # Round the eligible percentage pool once, not each independent rule.
        pool = (sum(numerators.values()) + 5_000) // 10_000
        remainder = pool - sum(amounts.values())
        ordered = sorted(
            rules,
            key=lambda rule: (
                -(numerators[rule.id] % 10_000),
                rule.created_at,
                rule.id,
            ),
        )
        for rule in ordered[:remainder]:
            amounts[rule.id] += 1
        return amounts

    def _fixed_earning_amount(
        self,
        context: PaymentContext,
        rule: RevenueShareRule,
    ) -> int:
        assert rule.fixed_amount_cents is not None
        tuition = context.enrollment.agreed_tuition_cents
        if tuition is None or tuition <= 0:
            return 0
        calculated = min(
            rule.fixed_amount_cents,
            self._rounded_ratio(
                rule.fixed_amount_cents * context.payment.amount_cents,
                tuition,
            ),
        )
        already_allocated = int(
            self.db.scalar(
                select(
                    func.coalesce(
                        func.sum(RevenueShareLedgerEntry.amount_cents),
                        0,
                    )
                )
                .join(
                    AdmissionCasePayment,
                    AdmissionCasePayment.payment_id == RevenueShareLedgerEntry.payment_id,
                )
                .where(
                    AdmissionCasePayment.admission_case_id == context.enrollment.admission_case_id,
                    RevenueShareLedgerEntry.rule_id == rule.id,
                    RevenueShareLedgerEntry.entry_kind.in_(("earning", "refund_adjustment")),
                    RevenueShareLedgerEntry.payment_id != context.payment.id,
                )
            )
            or 0
        )
        return max(min(calculated, rule.fixed_amount_cents - already_allocated), 0)

    def _status(
        self,
        payment_id: UUID,
        *,
        state: str,
        reason_code: str | None,
        context: PaymentContext | None,
        has_earnings: bool = False,
    ) -> RevenueSharePaymentStatus:
        row = self.db.get(RevenueSharePaymentStatus, payment_id)
        if row is None:
            row = RevenueSharePaymentStatus(
                payment_id=payment_id,
                state=state,
                reason_code=reason_code,
                project_id=(context.teaching_class.project_id if context else None),
                enrollment_id=(context.enrollment.id if context else None),
                reconciled_at=datetime.now(UTC),
            )
            self.db.add(row)
        else:
            row.state = state
            row.reason_code = reason_code
            row.project_id = context.teaching_class.project_id if context else None
            row.enrollment_id = context.enrollment.id if context else None
            row.reconciled_at = datetime.now(UTC)
        if has_earnings:
            self.db.execute(
                delete(RevenueShareReconcileQueue).where(
                    RevenueShareReconcileQueue.payment_id == payment_id
                )
            )
        else:
            # Pending and zero-earning payments must remain eligible: a later
            # coach/referrer/rule change can make them allocatable.
            self.db.execute(
                pg_insert(RevenueShareReconcileQueue)
                .from_select(
                    ["payment_id", "paid_at"],
                    select(Payment.id, Payment.paid_at).where(Payment.id == payment_id),
                )
                .on_conflict_do_nothing(index_elements=["payment_id"])
            )
        return row

    def _reconcile_refunds(self, payment: Payment) -> None:
        earnings = list(
            self.db.scalars(
                select(RevenueShareLedgerEntry)
                .where(
                    RevenueShareLedgerEntry.payment_id == payment.id,
                    RevenueShareLedgerEntry.entry_kind == "earning",
                )
                .order_by(RevenueShareLedgerEntry.id)
            )
        )
        if not earnings:
            return
        refunds = list(
            self.db.scalars(
                select(PaymentRefund)
                .where(PaymentRefund.payment_id == payment.id)
                .order_by(PaymentRefund.created_at, PaymentRefund.id)
            )
        )
        if not refunds:
            return
        existing_adjustments = list(
            self.db.scalars(
                select(RevenueShareLedgerEntry).where(
                    RevenueShareLedgerEntry.payment_id == payment.id,
                    RevenueShareLedgerEntry.entry_kind == "refund_adjustment",
                )
            )
        )
        existing_events = {row.event_key for row in existing_adjustments}
        processed_refund_ids = {
            row.refund_id for row in existing_adjustments if row.refund_id is not None
        }
        reversed_by_entry = {
            earning.id: -sum(
                row.amount_cents
                for row in existing_adjustments
                if row.parent_entry_id == earning.id
            )
            for earning in earnings
        }
        cumulative_refunded = sum(
            refund.amount_cents for refund in refunds if refund.id in processed_refund_ids
        )
        pending_refunds = [refund for refund in refunds if refund.id not in processed_refund_ids]
        for refund in pending_refunds:
            cumulative_refunded += refund.amount_cents
            targets = self._refund_reversal_targets(
                payment_amount_cents=payment.amount_cents,
                cumulative_refunded_cents=cumulative_refunded,
                earnings=earnings,
                reversed_by_entry=reversed_by_entry,
            )
            for earning in earnings:
                delta = targets[earning.id] - reversed_by_entry[earning.id]
                if delta <= 0:
                    continue
                event_key = f"refund:{refund.id}:{earning.id}"
                if event_key in existing_events:  # pragma: no cover - defensive invariant
                    continue
                reversed_by_entry[earning.id] += delta
                self.db.add(self._refund_adjustment(payment, refund, earning, delta))
                existing_events.add(event_key)

        # Older versions rounded each earning independently. If that left the
        # combined pool short, append the attainable deficit using an unused
        # legacy event key; never update a persisted row or invent a new key.
        available_refund_by_entry: dict[UUID, PaymentRefund] = {}
        for earning in earnings:
            available_refund_candidate = next(
                (
                    refund
                    for refund in reversed(refunds)
                    if f"refund:{refund.id}:{earning.id}" not in existing_events
                ),
                None,
            )
            if available_refund_candidate is not None:
                available_refund_by_entry[earning.id] = available_refund_candidate
        targets = self._refund_reversal_targets(
            payment_amount_cents=payment.amount_cents,
            cumulative_refunded_cents=sum(refund.amount_cents for refund in refunds),
            earnings=earnings,
            reversed_by_entry=reversed_by_entry,
        )
        for earning in earnings:
            delta = targets[earning.id] - reversed_by_entry[earning.id]
            if delta <= 0:
                continue
            source_refund = available_refund_by_entry.get(earning.id)
            if source_refund is None:
                continue
            adjustment = self._refund_adjustment(payment, source_refund, earning, delta)
            self.db.add(adjustment)
            reversed_by_entry[earning.id] += delta
            existing_events.add(adjustment.event_key)

    def reconcile_payment_in_transaction(self, payment_id: UUID) -> ReconcileResult:
        payment = self.db.scalar(
            select(Payment)
            .where(Payment.id == payment_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if payment is None:
            raise RevenueShareNotFound("payment was not found")
        existing_earnings = list(
            self.db.scalars(
                select(RevenueShareLedgerEntry).where(
                    RevenueShareLedgerEntry.payment_id == payment_id,
                    RevenueShareLedgerEntry.entry_kind == "earning",
                )
            )
        )
        context = self._payment_context(payment_id)
        if existing_earnings:
            self._reconcile_refunds(payment)
            self._status(
                payment_id,
                state="allocated",
                reason_code=None,
                context=context,
                has_earnings=True,
            )
            return ReconcileResult("allocated", None)
        if context is None:
            self._status(
                payment_id,
                state="pending",
                reason_code="missing_enrollment",
                context=None,
            )
            return ReconcileResult("pending", "missing_enrollment")
        self.db.scalar(
            select(Enrollment)
            .where(Enrollment.id == context.enrollment.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        rules = self._effective_rules(
            context.teaching_class.project_id,
            business_date_at(context.payment.paid_at),
        )
        if not rules:
            self._status(
                payment_id,
                state="pending",
                reason_code="missing_rules",
                context=context,
            )
            return ReconcileResult("pending", "missing_rules")

        eligible: list[tuple[RevenueShareRule, list[ResolvedBeneficiary]]] = []
        for rule in rules:
            beneficiaries, reason = self._role_beneficiaries(context, rule)
            if reason is not None:
                self._status(
                    payment_id,
                    state="pending",
                    reason_code=reason,
                    context=context,
                )
                return ReconcileResult("pending", reason)
            if beneficiaries:
                eligible.append((rule, beneficiaries))
        percentage_amounts = self._percentage_amounts(
            context.payment.amount_cents,
            [rule for rule, _ in eligible if rule.calculation_type == "percentage"],
        )
        resolved: list[tuple[RevenueShareRule, ResolvedBeneficiary, int]] = []
        for rule, beneficiaries in eligible:
            amount = (
                percentage_amounts[rule.id]
                if rule.calculation_type == "percentage"
                else self._fixed_earning_amount(context, rule)
            )
            if amount <= 0:
                continue
            per_beneficiary, remainder = divmod(amount, len(beneficiaries))
            for index, beneficiary in enumerate(beneficiaries):
                allocated_amount = per_beneficiary + (1 if index < remainder else 0)
                if allocated_amount > 0:
                    resolved.append((rule, beneficiary, allocated_amount))

        if sum(amount for _, _, amount in resolved) > context.payment.amount_cents:
            self._status(
                payment_id,
                state="pending",
                reason_code="allocation_exceeds_payment",
                context=context,
            )
            return ReconcileResult("pending", "allocation_exceeds_payment")

        for rule, beneficiary, amount in resolved:
            event_key = f"earning:{payment_id}:{rule.id}:{beneficiary.kind}:{beneficiary.id}"
            self.db.add(
                RevenueShareLedgerEntry(
                    event_key=event_key,
                    payment_id=payment_id,
                    refund_id=None,
                    parent_entry_id=None,
                    rule_id=rule.id,
                    entry_kind="earning",
                    role_kind=rule.role_kind,
                    beneficiary_kind=beneficiary.kind,
                    beneficiary_person_id=beneficiary.person_id,
                    beneficiary_organization_id=beneficiary.organization_id,
                    calculation_type=rule.calculation_type,
                    percentage_basis_points_snapshot=rule.percentage_basis_points,
                    fixed_amount_cents_snapshot=rule.fixed_amount_cents,
                    base_amount_cents=context.payment.amount_cents,
                    amount_cents=amount,
                    occurred_at=context.payment.paid_at,
                    created_by_user_id=context.payment.created_by_user_id,
                )
            )
        self.db.flush()
        self._reconcile_refunds(context.payment)
        self._status(
            payment_id,
            state="allocated",
            reason_code=None,
            context=context,
            has_earnings=bool(resolved),
        )
        return ReconcileResult("allocated", None)

    def reconcile_payment(self, payment_id: UUID) -> ReconcileResult:
        with write_transaction(self.db):
            lock_reconciliation_context(self.db, payment_id)
            return self.reconcile_payment_in_transaction(payment_id)

    def reconcile_batch(
        self,
        *,
        batch_size: int = 50,
        cursor: str | None = None,
        actor_user_id: UUID,
        request_id: str,
    ) -> ReconcileBatchResult:
        if not 1 <= batch_size <= 100:
            raise ValueError("batch size must be between 1 and 100")
        transaction = self.db.get_transaction()
        if transaction is not None and transaction.origin is not SessionTransactionOrigin.AUTOBEGIN:
            raise RuntimeError("batch reconciliation requires independent transactions")
        after = decode_cursor(cursor, ReconcileCursor) if cursor is not None else None
        # Use the queue's database clock, including when API and DB live on
        # different hosts. New arrivals belong to the next reconciliation run.
        cutoff = (
            after.cutoff if after is not None else self.db.scalar(select(func.clock_timestamp()))
        )
        assert cutoff is not None
        query = select(
            RevenueShareReconcileQueue.payment_id, RevenueShareReconcileQueue.paid_at
        ).where(RevenueShareReconcileQueue.queued_at <= cutoff)
        if after is not None:
            query = query.where(
                tuple_(RevenueShareReconcileQueue.paid_at, RevenueShareReconcileQueue.payment_id)
                > (after.paid_at, after.payment_id)
            )
        candidates = list(
            self.db.execute(
                query.order_by(
                    RevenueShareReconcileQueue.paid_at, RevenueShareReconcileQueue.payment_id
                ).limit(batch_size + 1)
            )
        )
        has_more = len(candidates) > batch_size
        candidates = candidates[:batch_size]
        allocated = 0
        pending = 0
        for payment_id, _ in candidates:
            result: ReconcileResult | None = None
            for attempt in range(3):
                try:
                    # A batch only bounds the request's work. Each payment has
                    # its own commit, so locks never span unrelated payments.
                    with write_transaction(self.db):
                        self.db.execute(select(func.set_config("lock_timeout", "3s", True)))
                        lock_reconciliation_context(self.db, payment_id)
                        queued = self.db.scalar(
                            select(RevenueShareReconcileQueue.payment_id).where(
                                RevenueShareReconcileQueue.payment_id == payment_id
                            )
                        )
                        if queued is not None:
                            result = self.reconcile_payment_in_transaction(payment_id)
                            self._audit(
                                actor_user_id,
                                "revenue_share.reconcile_all",
                                "revenue_share_payment_status",
                                payment_id,
                                request_id,
                                metadata={
                                    "item_count": 1,
                                    "filled_count": int(result.state == "allocated"),
                                    "result_count": int(result.state == "pending"),
                                },
                            )
                    break
                except ReconciliationContextChanged:
                    if attempt == 2:
                        raise RevenueShareReconcileBusy(
                            "payment context is changing; retry"
                        ) from None
                except OperationalError as exc:
                    if getattr(exc.orig, "sqlstate", None) == "55P03":
                        raise RevenueShareReconcileBusy("payment is being updated; retry") from None
                    raise
            if result is not None:
                if result.state == "allocated":
                    allocated += 1
                else:
                    pending += 1
        next_cursor = None
        if has_more:
            payment_id, paid_at = candidates[-1]
            next_cursor = encode_cursor(
                ReconcileCursor(cutoff=cutoff, paid_at=paid_at, payment_id=payment_id)
            )
        return ReconcileBatchResult(allocated + pending, allocated, pending, has_more, next_cursor)

    def _balances(self) -> list[Balance]:
        earned: dict[tuple[str, UUID], int] = defaultdict(int)
        for kind, person_id, organization_id, amount in self.db.execute(
            select(
                RevenueShareLedgerEntry.beneficiary_kind,
                RevenueShareLedgerEntry.beneficiary_person_id,
                RevenueShareLedgerEntry.beneficiary_organization_id,
                func.sum(RevenueShareLedgerEntry.amount_cents),
            ).group_by(
                RevenueShareLedgerEntry.beneficiary_kind,
                RevenueShareLedgerEntry.beneficiary_person_id,
                RevenueShareLedgerEntry.beneficiary_organization_id,
            )
        ):
            beneficiary_id = person_id if kind == "person" else organization_id
            if beneficiary_id is not None:
                earned[(kind, beneficiary_id)] = int(amount or 0)
        settled: dict[tuple[str, UUID], int] = defaultdict(int)
        for kind, person_id, organization_id, amount in self.db.execute(
            select(
                RevenueShareSettlement.beneficiary_kind,
                RevenueShareSettlement.beneficiary_person_id,
                RevenueShareSettlement.beneficiary_organization_id,
                func.sum(RevenueShareSettlement.amount_cents),
            ).group_by(
                RevenueShareSettlement.beneficiary_kind,
                RevenueShareSettlement.beneficiary_person_id,
                RevenueShareSettlement.beneficiary_organization_id,
            )
        ):
            beneficiary_id = person_id if kind == "person" else organization_id
            if beneficiary_id is not None:
                settled[(kind, beneficiary_id)] = int(amount or 0)
        keys = set(earned) | set(settled)
        return [
            Balance(
                beneficiary=ResolvedBeneficiary(
                    kind,
                    beneficiary_id if kind == "person" else None,
                    beneficiary_id if kind == "organization" else None,
                ),
                earned_cents=earned[(kind, beneficiary_id)],
                settled_cents=settled[(kind, beneficiary_id)],
            )
            for kind, beneficiary_id in keys
        ]

    def balances(self) -> list[Balance]:
        return self._balances()

    def balance_for(self, kind: str, beneficiary_id: UUID) -> Balance:
        earning_recipient = (
            RevenueShareLedgerEntry.beneficiary_person_id
            if kind == "person"
            else RevenueShareLedgerEntry.beneficiary_organization_id
        )
        settlement_recipient = (
            RevenueShareSettlement.beneficiary_person_id
            if kind == "person"
            else RevenueShareSettlement.beneficiary_organization_id
        )
        earned = int(
            self.db.scalar(
                select(func.coalesce(func.sum(RevenueShareLedgerEntry.amount_cents), 0)).where(
                    earning_recipient == beneficiary_id,
                    RevenueShareLedgerEntry.beneficiary_kind == kind,
                )
            )
            or 0
        )
        settled = int(
            self.db.scalar(
                select(func.coalesce(func.sum(RevenueShareSettlement.amount_cents), 0)).where(
                    settlement_recipient == beneficiary_id,
                    RevenueShareSettlement.beneficiary_kind == kind,
                )
            )
            or 0
        )
        return Balance(
            ResolvedBeneficiary(
                kind,
                beneficiary_id if kind == "person" else None,
                beneficiary_id if kind == "organization" else None,
            ),
            earned,
            settled,
        )

    def create_settlement(
        self,
        input: RevenueShareSettlementCreate,
        *,
        actor_user_id: UUID,
        request_id: str,
    ) -> RevenueShareSettlement:
        with write_transaction(self.db):
            # Serialize the same actor/operation even if a conflicting retry changes payee.
            operation_digest = hashlib.sha256(
                f"settlement:{actor_user_id}:{input.idempotency_key}".encode()
            ).digest()
            lock_key = int.from_bytes(operation_digest[:8], "big", signed=True)
            self.db.execute(select(func.pg_advisory_xact_lock(lock_key)))
            fingerprint = hashlib.sha256(
                json.dumps(
                    input.model_dump(mode="json", exclude={"idempotency_key"}),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode()
            ).hexdigest()
            existing = self.db.scalar(
                select(RevenueShareSettlement).where(
                    RevenueShareSettlement.created_by_user_id == actor_user_id,
                    RevenueShareSettlement.idempotency_key == input.idempotency_key,
                )
            )
            if existing is not None:
                if existing.request_fingerprint != fingerprint:
                    raise RevenueShareSettlementOperationConflict("settlement retry changed")
                return existing
            if input.beneficiary_kind == "person":
                person = self.db.scalar(
                    select(Person).where(Person.id == input.beneficiary_id).with_for_update()
                )
                beneficiary_exists = person is not None
            else:
                organization = self.db.scalar(
                    select(RevenueShareOrganization)
                    .where(RevenueShareOrganization.id == input.beneficiary_id)
                    .with_for_update()
                )
                beneficiary_exists = organization is not None
            if not beneficiary_exists:
                raise RevenueShareNotFound("settlement beneficiary was not found")
            reference_key = self.settlement_reference_key(
                input.beneficiary_kind, input.beneficiary_id, input.reference
            )
            if (
                self.db.scalar(
                    select(RevenueShareSettlement.id).where(
                        RevenueShareSettlement.reference_key == reference_key
                    )
                )
                is not None
            ):
                raise RevenueShareSettlementReferenceConflict("settlement reference already used")
            balance = self.balance_for(input.beneficiary_kind, input.beneficiary_id)
            if input.amount_cents > balance.outstanding_cents:
                raise RevenueShareSettlementExceedsBalance("settlement exceeds outstanding balance")
            row = RevenueShareSettlement(
                beneficiary_kind=input.beneficiary_kind,
                beneficiary_person_id=(
                    input.beneficiary_id if input.beneficiary_kind == "person" else None
                ),
                beneficiary_organization_id=(
                    input.beneficiary_id if input.beneficiary_kind == "organization" else None
                ),
                amount_cents=input.amount_cents,
                settled_on=input.settled_on,
                reference=input.reference,
                note=input.note,
                idempotency_key=input.idempotency_key,
                request_fingerprint=fingerprint,
                reference_key=reference_key,
                created_by_user_id=actor_user_id,
            )
            self.db.add(row)
            self.db.flush()
            self._audit(
                actor_user_id,
                "revenue_share.settlement.create",
                "revenue_share_settlement",
                row.id,
                request_id,
            )
            return row

    @staticmethod
    def settlement_reference_key(kind: str, beneficiary_id: UUID, reference: str) -> str:
        normalized = "".join(reference.split()).upper()
        return hashlib.sha256(f"{kind}:{beneficiary_id}:{normalized}".encode()).hexdigest()

    def beneficiary_names(self, balances: list[Balance]) -> dict[tuple[str, UUID], str]:
        person_ids = {item.beneficiary.id for item in balances if item.beneficiary.kind == "person"}
        organization_ids = {
            item.beneficiary.id for item in balances if item.beneficiary.kind == "organization"
        }
        names = {
            ("person", person_id): name
            for person_id, name in self.db.execute(
                select(Person.id, Person.full_name).where(Person.id.in_(person_ids))
            )
        }
        names.update(
            {
                ("organization", organization_id): name
                for organization_id, name in self.db.execute(
                    select(
                        RevenueShareOrganization.id,
                        RevenueShareOrganization.name,
                    ).where(RevenueShareOrganization.id.in_(organization_ids))
                )
            }
        )
        return names

    def people_options(self) -> list[tuple[UUID, str]]:
        return list(
            self.db.execute(
                select(Person.id, Person.full_name)
                .join(UserAccount, UserAccount.person_id == Person.id)
                .where(
                    Person.deleted_at.is_(None),
                    UserAccount.status == "active",
                )
                .order_by(Person.full_name, Person.id)
            ).tuples()
        )

    def pending_payment_count(self) -> int:
        return int(
            self.db.scalar(
                select(func.count())
                .select_from(RevenueSharePaymentStatus)
                .where(RevenueSharePaymentStatus.state == "pending")
            )
            or 0
        )

    def self_ledger(
        self,
        person_id: UUID,
        *,
        page_size: int = 20,
        cursor: str | None = None,
    ) -> LedgerPage:
        if not 1 <= page_size <= 100:
            raise ValueError("page size must be between 1 and 100")
        after = decode_cursor(cursor, LedgerCursor) if cursor is not None else None
        owner_filter = RevenueShareLedgerEntry.beneficiary_person_id == person_id
        total = int(
            self.db.scalar(
                select(func.count()).select_from(RevenueShareLedgerEntry).where(owner_filter)
            )
            or 0
        )
        query = select(RevenueShareLedgerEntry).where(owner_filter)
        if after is not None:
            query = query.where(
                tuple_(RevenueShareLedgerEntry.occurred_at, RevenueShareLedgerEntry.id)
                < (after.occurred_at, after.entry_id)
            )
        items = list(
            self.db.scalars(
                query.order_by(
                    RevenueShareLedgerEntry.occurred_at.desc(), RevenueShareLedgerEntry.id.desc()
                ).limit(page_size + 1)
            )
        )
        has_more = len(items) > page_size
        items = items[:page_size]
        next_cursor = None
        if has_more:
            last = items[-1]
            next_cursor = encode_cursor(
                LedgerCursor(occurred_at=last.occurred_at, entry_id=last.id)
            )
        return LedgerPage(items, total, has_more, next_cursor)

    def _audit(
        self,
        actor_user_id: UUID,
        action: str,
        resource_type: str,
        resource_id: UUID,
        request_id: str,
        *,
        metadata: dict[str, object] | None = None,
    ) -> None:
        AuditService(self.db).append(
            actor_user_id=actor_user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            purpose=None,
            outcome="allowed",
            request_id=request_id,
            metadata=metadata or {},
        )
