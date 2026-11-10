from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Select, case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, undefer
from sqlalchemy.sql.base import ExecutableOption

from app.asset_management.hardware import validate_computer_hardware
from app.asset_management.schemas import AssetCreate
from app.asset_management.service import AssetService
from app.core.transactions import write_transaction
from app.procurement.invoice_files import ValidatedInvoiceFile
from app.procurement.models import (
    DailyPurchaseRecord,
    ProcurementInvoice,
    ProcurementInvoicePurchase,
    ProcurementReserveFundEntry,
    ProcurementSettlementCenter,
)
from app.procurement.schemas import (
    PurchaseCreate,
    PurchaseReimbursementUpdate,
    PurchaseSettlementUpdate,
    PurchaseUpdate,
    ReserveFundEntryCreate,
    SettlementCenterCreate,
    SettlementCenterUpdate,
    calculate_purchase_total,
)


class ProcurementServiceError(ValueError):
    code = "procurement_service_error"


class SettlementCenterNotFound(ProcurementServiceError):
    code = "procurement_settlement_center_not_found"


class SettlementCenterInactive(ProcurementServiceError):
    code = "procurement_settlement_center_inactive"


class SettlementCenterDuplicate(ProcurementServiceError):
    code = "procurement_settlement_center_duplicate"


class PurchaseNotFound(ProcurementServiceError):
    code = "procurement_purchase_not_found"


class PurchaseLocked(ProcurementServiceError):
    code = "procurement_purchase_locked"


class PurchaseReimbursed(PurchaseLocked):
    code = "procurement_purchase_reimbursed"


class PurchaseConflict(ProcurementServiceError):
    code = "procurement_purchase_conflict"


class OperationConflict(PurchaseConflict):
    code = "procurement_operation_conflict"


class PurchaseHardwareInvalid(ProcurementServiceError):
    code = "procurement_asset_hardware_invalid"


class ReserveFundInsufficient(ProcurementServiceError):
    code = "procurement_reserve_fund_insufficient"


class InvoiceNotFound(ProcurementServiceError):
    code = "procurement_invoice_not_found"


class InvoiceSelectionRequired(ProcurementServiceError):
    code = "procurement_invoice_selection_required"


class InvoiceAmountMismatch(ProcurementServiceError):
    code = "procurement_invoice_amount_mismatch"


class InvoicePurchaseConflict(ProcurementServiceError):
    code = "procurement_invoice_purchase_conflict"


@dataclass(frozen=True)
class PurchaseFilters:
    query: str | None = None
    settlement_center_id: UUID | None = None
    reimbursement_status: str | None = None
    settlement_status: str | None = None
    invoice_status: str | None = None
    start_date: date | None = None
    end_date: date | None = None


@dataclass(frozen=True)
class PurchaseListResult:
    items: list[DailyPurchaseRecord]
    page: int
    page_size: int
    total: int
    total_amount_cents: int
    reimbursed_amount_cents: int
    unreimbursed_amount_cents: int
    settled_amount_cents: int
    unsettled_amount_cents: int


@dataclass(frozen=True)
class InvoiceMatchResult:
    candidates: list[DailyPurchaseRecord]
    suggested_purchase_ids: tuple[UUID, ...]
    matched_total_cents: int


@dataclass(frozen=True)
class ReserveFundBalance:
    total_cents: int
    used_cents: int
    remaining_cents: int


class ProcurementService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.operation_replayed = False

    def _lock_operation(self, kind: str, actor_user_id: UUID, key: UUID) -> None:
        digest = hashlib.sha256(f"procurement:{kind}:{actor_user_id}:{key}".encode()).digest()
        self.db.execute(
            select(func.pg_advisory_xact_lock(int.from_bytes(digest[:8], "big", signed=True)))
        )

    @staticmethod
    def _fingerprint(input: PurchaseCreate | ReserveFundEntryCreate, *, center_id: UUID) -> str:
        payload = input.model_dump(mode="json", exclude={"operation_key"})
        payload["settlement_center_id"] = str(center_id)
        if isinstance(input, PurchaseCreate):
            # Old persisted fingerprints predate this optional field. Omission
            # and explicit null must keep their original retry identity.
            if input.asset_hardware is None:
                payload.pop("asset_hardware", None)
            payload["quantity"] = format(input.quantity.normalize(), "f")
            if input.purchase_channel == "offline":
                payload["product_url"] = None
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()

    @staticmethod
    def calculate_total(quantity: Decimal, unit_price_cents: int) -> int:
        try:
            return calculate_purchase_total(quantity, unit_price_cents)
        except ValueError as exc:
            raise ProcurementServiceError(str(exc)) from exc

    @staticmethod
    def _purchase_load_options() -> tuple[ExecutableOption, ...]:
        return (
            joinedload(DailyPurchaseRecord.settlement_center),
            joinedload(DailyPurchaseRecord.invoice_link).joinedload(
                ProcurementInvoicePurchase.invoice
            ),
        )

    def list_centers(self, *, include_inactive: bool) -> list[ProcurementSettlementCenter]:
        statement = select(ProcurementSettlementCenter)
        if not include_inactive:
            statement = statement.where(ProcurementSettlementCenter.is_active.is_(True))
        return list(
            self.db.scalars(
                statement.order_by(
                    ProcurementSettlementCenter.is_active.desc(),
                    ProcurementSettlementCenter.name,
                    ProcurementSettlementCenter.id,
                )
            )
        )

    def create_center(
        self,
        input: SettlementCenterCreate,
        *,
        actor_user_id: UUID,
    ) -> ProcurementSettlementCenter:
        try:
            with write_transaction(self.db):
                center = ProcurementSettlementCenter(
                    name=input.name,
                    created_by_user_id=actor_user_id,
                    updated_by_user_id=actor_user_id,
                )
                self.db.add(center)
                self.db.flush()
                return center
        except IntegrityError as exc:
            raise SettlementCenterDuplicate("settlement center already exists") from exc

    def update_center(
        self,
        center_id: UUID,
        input: SettlementCenterUpdate,
        *,
        actor_user_id: UUID,
    ) -> ProcurementSettlementCenter:
        try:
            with write_transaction(self.db):
                center = self.db.scalar(
                    select(ProcurementSettlementCenter)
                    .where(ProcurementSettlementCenter.id == center_id)
                    .with_for_update()
                )
                if center is None:
                    raise SettlementCenterNotFound("settlement center not found")
                if "name" in input.model_fields_set:
                    center.name = input.name or ""
                if "is_active" in input.model_fields_set:
                    center.is_active = bool(input.is_active)
                center.updated_by_user_id = actor_user_id
                self.db.flush()
                return center
        except IntegrityError as exc:
            raise SettlementCenterDuplicate("settlement center already exists") from exc

    def _center(
        self,
        center_id: UUID,
        *,
        require_active: bool,
        lock: bool = False,
    ) -> ProcurementSettlementCenter:
        statement = select(ProcurementSettlementCenter).where(
            ProcurementSettlementCenter.id == center_id
        )
        if lock:
            statement = statement.with_for_update()
        center = self.db.scalar(statement)
        if center is None:
            raise SettlementCenterNotFound("settlement center not found")
        if require_active and not center.is_active:
            raise SettlementCenterInactive("settlement center is inactive")
        return center

    def reserve_fund_balance(
        self,
        center_id: UUID,
        *,
        exclude_purchase_id: UUID | None = None,
    ) -> ReserveFundBalance:
        total_cents = int(
            self.db.scalar(
                select(func.coalesce(func.sum(ProcurementReserveFundEntry.amount_cents), 0)).where(
                    ProcurementReserveFundEntry.settlement_center_id == center_id
                )
            )
            or 0
        )
        used_statement = select(
            func.coalesce(func.sum(DailyPurchaseRecord.total_price_cents), 0)
        ).where(
            DailyPurchaseRecord.settlement_center_id == center_id,
            DailyPurchaseRecord.uses_reserve_fund.is_(True),
        )
        if exclude_purchase_id is not None:
            used_statement = used_statement.where(DailyPurchaseRecord.id != exclude_purchase_id)
        used_cents = int(self.db.scalar(used_statement) or 0)
        return ReserveFundBalance(
            total_cents=total_cents,
            used_cents=used_cents,
            remaining_cents=total_cents - used_cents,
        )

    def add_reserve_fund(
        self,
        center_id: UUID,
        input: ReserveFundEntryCreate,
        *,
        actor_user_id: UUID,
    ) -> tuple[
        ProcurementReserveFundEntry,
        ProcurementSettlementCenter,
        ReserveFundBalance,
    ]:
        self.operation_replayed = False
        key = input.operation_key or uuid4()
        fingerprint = self._fingerprint(input, center_id=center_id)
        with write_transaction(self.db):
            self._lock_operation("reserve", actor_user_id, key)
            existing = self.db.scalar(
                select(ProcurementReserveFundEntry).where(
                    ProcurementReserveFundEntry.created_by_user_id == actor_user_id,
                    ProcurementReserveFundEntry.operation_key == key,
                )
            )
            if existing is not None:
                if existing.request_fingerprint != fingerprint:
                    raise OperationConflict("operation key payload mismatch")
                self.operation_replayed = True
                return (
                    existing,
                    self._center(center_id, require_active=False),
                    self.reserve_fund_balance(center_id),
                )
            center = self._center(center_id, require_active=True, lock=True)
            entry = ProcurementReserveFundEntry(
                settlement_center_id=center_id,
                amount_cents=input.amount_cents,
                operation_key=key,
                request_fingerprint=fingerprint,
                note=input.note,
                created_by_user_id=actor_user_id,
            )
            self.db.add(entry)
            self.db.flush()
            return entry, center, self.reserve_fund_balance(center_id)

    def _require_reserve_fund(
        self,
        center_id: UUID,
        amount_cents: int,
        *,
        require_active: bool,
        exclude_purchase_id: UUID | None = None,
    ) -> None:
        self._center(center_id, require_active=require_active, lock=True)
        balance = self.reserve_fund_balance(
            center_id,
            exclude_purchase_id=exclude_purchase_id,
        )
        if amount_cents > balance.remaining_cents:
            raise ReserveFundInsufficient("reserve fund balance is insufficient")

    def create_purchase(
        self,
        input: PurchaseCreate,
        *,
        actor_user_id: UUID,
    ) -> DailyPurchaseRecord:
        self.operation_replayed = False
        key = input.operation_key or uuid4()
        fingerprint = self._fingerprint(input, center_id=input.settlement_center_id)
        try:
            with write_transaction(self.db):
                self._lock_operation("purchase", actor_user_id, key)
                existing = self.db.scalar(
                    select(DailyPurchaseRecord)
                    .where(
                        DailyPurchaseRecord.created_by_user_id == actor_user_id,
                        DailyPurchaseRecord.operation_key == key,
                    )
                    .options(*self._purchase_load_options())
                )
                if existing is not None:
                    if existing.request_fingerprint != fingerprint:
                        raise OperationConflict("operation key payload mismatch")
                    self.operation_replayed = True
                    return existing
                if input.add_to_assets:
                    try:
                        validate_computer_hardware(
                            input.asset_category,
                            input.asset_hardware.model_dump() if input.asset_hardware is not None else {},
                        )
                    except ValueError as exc:
                        raise PurchaseHardwareInvalid(str(exc)) from exc
                total_price_cents = self.calculate_total(
                    input.quantity,
                    input.unit_price_cents,
                )
                if input.uses_reserve_fund:
                    self._require_reserve_fund(
                        input.settlement_center_id,
                        total_price_cents,
                        require_active=True,
                    )
                else:
                    self._center(input.settlement_center_id, require_active=True)
                record = DailyPurchaseRecord(
                    operation_key=key,
                    request_fingerprint=fingerprint,
                    purchase_date=input.purchase_date,
                    item_name=input.item_name,
                    purchase_channel=input.purchase_channel,
                    product_url=(input.product_url if input.purchase_channel == "online" else None),
                    quantity=input.quantity,
                    unit_price_cents=input.unit_price_cents,
                    total_price_cents=total_price_cents,
                    settlement_center_id=input.settlement_center_id,
                    uses_reserve_fund=input.uses_reserve_fund,
                    is_reimbursed=input.is_reimbursed,
                    is_settled=input.is_settled,
                    settled_on=input.settled_on,
                    settled_by_user_id=actor_user_id if input.is_settled else None,
                    note=input.note,
                    created_by_user_id=actor_user_id,
                    updated_by_user_id=actor_user_id,
                )
                self.db.add(record)
                self.db.flush()
                if input.add_to_assets:
                    asset_service = AssetService(self.db)
                    for _ in range(int(input.quantity)):
                        asset_service.create_in_transaction(
                            AssetCreate(
                                name=input.item_name,
                                ownership_type="owned",
                                acquisition_method="purchase",
                                acquisition_channel=input.purchase_channel,
                                category=input.asset_category,
                                **(input.asset_hardware.model_dump() if input.asset_hardware is not None else {}),
                                purchase_date=input.purchase_date,
                                warranty_expires_on=input.asset_warranty_expires_on,
                                note=input.note,
                            ),
                            actor_user_id=actor_user_id,
                            source_purchase_id=record.id,
                        )
                self.db.refresh(record, attribute_names=["settlement_center", "quantity"])
                return record
        except IntegrityError as exc:
            raise PurchaseConflict("purchase could not be created") from exc

    def get_purchase(self, purchase_id: UUID, *, lock: bool = False) -> DailyPurchaseRecord:
        statement = select(DailyPurchaseRecord).where(DailyPurchaseRecord.id == purchase_id)
        if lock:
            statement = statement.with_for_update()
        else:
            statement = statement.options(*self._purchase_load_options())
        record = self.db.scalar(statement)
        if record is None:
            raise PurchaseNotFound("purchase not found")
        return record

    def update_purchase(
        self,
        purchase_id: UUID,
        input: PurchaseUpdate,
        *,
        actor_user_id: UUID,
    ) -> DailyPurchaseRecord:
        try:
            with write_transaction(self.db):
                record = self.get_purchase(purchase_id, lock=True)
                if record.is_settled or record.invoice_link is not None:
                    raise PurchaseLocked(
                        "settled or invoiced purchase must be reopened before editing"
                    )
                if record.is_reimbursed:
                    raise PurchaseReimbursed("reimbursement must be revoked before editing")
                next_center_id = input.settlement_center_id or record.settlement_center_id
                next_quantity = input.quantity or record.quantity
                next_unit_price_cents = input.unit_price_cents or record.unit_price_cents
                next_total_price_cents = self.calculate_total(
                    next_quantity,
                    next_unit_price_cents,
                )
                next_uses_reserve_fund = (
                    bool(input.uses_reserve_fund)
                    if "uses_reserve_fund" in input.model_fields_set
                    else record.uses_reserve_fund
                )
                if next_uses_reserve_fund:
                    self._require_reserve_fund(
                        next_center_id,
                        next_total_price_cents,
                        require_active=input.settlement_center_id is not None,
                        exclude_purchase_id=record.id,
                    )
                elif input.settlement_center_id is not None:
                    self._center(input.settlement_center_id, require_active=True)
                purchase_channel = input.purchase_channel
                if purchase_channel is None:
                    purchase_channel = record.purchase_channel
                product_url = (
                    input.product_url
                    if "product_url" in input.model_fields_set
                    else record.product_url
                )
                for field in (
                    "purchase_date",
                    "item_name",
                    "quantity",
                    "unit_price_cents",
                    "settlement_center_id",
                    "note",
                ):
                    if field in input.model_fields_set:
                        setattr(record, field, getattr(input, field))
                record.purchase_channel = purchase_channel
                record.product_url = product_url if purchase_channel == "online" else None
                record.total_price_cents = next_total_price_cents
                record.uses_reserve_fund = next_uses_reserve_fund
                record.updated_by_user_id = actor_user_id
                self.db.flush()
                self.db.refresh(record, attribute_names=["settlement_center"])
                return record
        except IntegrityError as exc:
            raise PurchaseConflict("purchase update conflicted with current state") from exc

    def update_settlement(
        self,
        purchase_id: UUID,
        input: PurchaseSettlementUpdate,
        *,
        actor_user_id: UUID,
    ) -> DailyPurchaseRecord:
        with write_transaction(self.db):
            record = self.get_purchase(purchase_id, lock=True)
            record.is_settled = input.is_settled
            record.settled_on = input.settled_on
            record.settled_by_user_id = actor_user_id if input.is_settled else None
            record.updated_by_user_id = actor_user_id
            self.db.flush()
            return record

    def update_reimbursement(
        self,
        purchase_id: UUID,
        input: PurchaseReimbursementUpdate,
        *,
        actor_user_id: UUID,
    ) -> DailyPurchaseRecord:
        with write_transaction(self.db):
            record = self.get_purchase(purchase_id, lock=True)
            record.is_reimbursed = input.is_reimbursed
            record.updated_by_user_id = actor_user_id
            self.db.flush()
            return record

    @staticmethod
    def _filtered_statement(
        filters: PurchaseFilters,
    ) -> Select[tuple[DailyPurchaseRecord]]:
        statement = select(DailyPurchaseRecord)
        if filters.query:
            pattern = f"%{filters.query.strip()}%"
            statement = statement.where(
                or_(
                    DailyPurchaseRecord.item_name.ilike(pattern),
                    DailyPurchaseRecord.note.ilike(pattern),
                )
            )
        if filters.settlement_center_id is not None:
            statement = statement.where(
                DailyPurchaseRecord.settlement_center_id == filters.settlement_center_id
            )
        if filters.reimbursement_status == "reimbursed":
            statement = statement.where(DailyPurchaseRecord.is_reimbursed.is_(True))
        elif filters.reimbursement_status == "unreimbursed":
            statement = statement.where(DailyPurchaseRecord.is_reimbursed.is_(False))
        if filters.settlement_status == "settled":
            statement = statement.where(DailyPurchaseRecord.is_settled.is_(True))
        elif filters.settlement_status == "unsettled":
            statement = statement.where(DailyPurchaseRecord.is_settled.is_(False))
        if filters.invoice_status == "invoiced":
            statement = statement.where(DailyPurchaseRecord.invoice_link.has())
        elif filters.invoice_status == "uninvoiced":
            statement = statement.where(~DailyPurchaseRecord.invoice_link.has())
        if filters.start_date is not None:
            statement = statement.where(DailyPurchaseRecord.purchase_date >= filters.start_date)
        if filters.end_date is not None:
            statement = statement.where(DailyPurchaseRecord.purchase_date <= filters.end_date)
        return statement

    def list_purchases(
        self,
        filters: PurchaseFilters,
        *,
        page: int,
        page_size: int,
    ) -> PurchaseListResult:
        base = self._filtered_statement(filters)
        filtered = base.subquery()
        summary = self.db.execute(
            select(
                func.count(filtered.c.id),
                func.coalesce(func.sum(filtered.c.total_price_cents), 0),
                func.coalesce(
                    func.sum(
                        case(
                            (filtered.c.is_settled.is_(True), filtered.c.total_price_cents),
                            else_=0,
                        )
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(
                        case(
                            (filtered.c.is_reimbursed.is_(True), filtered.c.total_price_cents),
                            else_=0,
                        )
                    ),
                    0,
                ),
            )
        ).one()
        total, total_amount, settled_amount, reimbursed_amount = (int(value) for value in summary)
        records = list(
            self.db.scalars(
                base.options(*self._purchase_load_options())
                .order_by(
                    DailyPurchaseRecord.purchase_date.desc(),
                    DailyPurchaseRecord.created_at.desc(),
                    DailyPurchaseRecord.id,
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return PurchaseListResult(
            items=records,
            page=page,
            page_size=page_size,
            total=total,
            total_amount_cents=total_amount,
            reimbursed_amount_cents=reimbursed_amount,
            unreimbursed_amount_cents=total_amount - reimbursed_amount,
            settled_amount_cents=settled_amount,
            unsettled_amount_cents=total_amount - settled_amount,
        )

    def export_purchases(self, filters: PurchaseFilters) -> list[DailyPurchaseRecord]:
        return list(
            self.db.scalars(
                self._filtered_statement(filters)
                .options(*self._purchase_load_options())
                .order_by(
                    DailyPurchaseRecord.purchase_date,
                    DailyPurchaseRecord.created_at,
                    DailyPurchaseRecord.id,
                )
            )
        )

    @staticmethod
    def _exact_invoice_combination(
        candidates: list[DailyPurchaseRecord],
        amount_cents: int,
    ) -> tuple[UUID, ...]:
        for record in candidates:
            if record.total_price_cents == amount_cents:
                return (record.id,)

        max_states = 30_000
        states: dict[int, tuple[UUID, ...]] = {0: ()}
        for record in candidates[:80]:
            additions: dict[int, tuple[UUID, ...]] = {}
            for subtotal, selected in tuple(states.items()):
                total = subtotal + record.total_price_cents
                if total > amount_cents:
                    continue
                match = (*selected, record.id)
                if total == amount_cents:
                    return match
                existing = states.get(total) or additions.get(total)
                if existing is None or len(match) < len(existing):
                    additions[total] = match
            states.update(additions)
            if len(states) > max_states:
                closest = sorted(
                    states.items(),
                    key=lambda item: (amount_cents - item[0], len(item[1])),
                )[: max_states - 1]
                states = {0: ()}
                states.update(dict(closest))
        return ()

    def suggest_invoice_matches(
        self,
        amount_cents: int,
        *,
        settlement_center_id: UUID | None = None,
    ) -> InvoiceMatchResult:
        statement = (
            select(DailyPurchaseRecord)
            .where(
                ~DailyPurchaseRecord.invoice_link.has(),
                DailyPurchaseRecord.total_price_cents <= amount_cents,
            )
            .options(joinedload(DailyPurchaseRecord.settlement_center))
        )
        if settlement_center_id is not None:
            statement = statement.where(
                DailyPurchaseRecord.settlement_center_id == settlement_center_id
            )
        candidates = list(
            self.db.scalars(
                statement.order_by(
                    DailyPurchaseRecord.purchase_date.desc(),
                    DailyPurchaseRecord.created_at.desc(),
                    DailyPurchaseRecord.id,
                ).limit(200)
            )
        )
        suggested = self._exact_invoice_combination(candidates, amount_cents)
        return InvoiceMatchResult(
            candidates=candidates,
            suggested_purchase_ids=suggested,
            matched_total_cents=(amount_cents if suggested else 0),
        )

    def create_invoice(
        self,
        *,
        amount_cents: int,
        invoice_date: date,
        invoice_number: str | None,
        note: str | None,
        purchase_ids: list[UUID],
        file: ValidatedInvoiceFile,
        actor_user_id: UUID,
    ) -> ProcurementInvoice:
        unique_ids = sorted(set(purchase_ids), key=str)
        if not unique_ids:
            raise InvoiceSelectionRequired("invoice requires at least one purchase")
        if len(unique_ids) != len(purchase_ids):
            raise InvoicePurchaseConflict("purchase selection contains duplicates")
        try:
            with write_transaction(self.db):
                records = list(
                    self.db.scalars(
                        select(DailyPurchaseRecord)
                        .where(DailyPurchaseRecord.id.in_(unique_ids))
                        .order_by(DailyPurchaseRecord.id)
                        .with_for_update()
                    )
                )
                if len(records) != len(unique_ids):
                    raise PurchaseNotFound("one or more purchases do not exist")
                existing_link = self.db.scalar(
                    select(ProcurementInvoicePurchase).where(
                        ProcurementInvoicePurchase.purchase_id.in_(unique_ids)
                    )
                )
                if existing_link is not None:
                    raise InvoicePurchaseConflict("purchase is already invoiced")
                if sum(record.total_price_cents for record in records) != amount_cents:
                    raise InvoiceAmountMismatch("invoice amount does not match purchases")

                invoice = ProcurementInvoice(
                    amount_cents=amount_cents,
                    invoice_date=invoice_date,
                    invoice_number=invoice_number,
                    note=note,
                    original_filename=file.original_filename,
                    mime_type=file.mime_type,
                    extension=file.extension,
                    byte_size=file.byte_size,
                    sha256=file.sha256,
                    content=file.content,
                    uploaded_by_user_id=actor_user_id,
                )
                self.db.add(invoice)
                self.db.flush()
                self.db.add_all(
                    ProcurementInvoicePurchase(invoice_id=invoice.id, purchase_id=purchase_id)
                    for purchase_id in unique_ids
                )
                self.db.flush()
                self.db.refresh(invoice, attribute_names=["purchase_links"])
                return invoice
        except IntegrityError as exc:
            raise InvoicePurchaseConflict("purchase is already invoiced") from exc

    def get_invoice(
        self, invoice_id: UUID, *, lock: bool = False, include_content: bool = False
    ) -> ProcurementInvoice:
        statement = select(ProcurementInvoice).where(ProcurementInvoice.id == invoice_id)
        if include_content:
            statement = statement.options(undefer(ProcurementInvoice.content))
        if lock:
            statement = statement.with_for_update()
        else:
            statement = statement.options(joinedload(ProcurementInvoice.purchase_links))
        invoice = self.db.scalar(statement)
        if invoice is None:
            raise InvoiceNotFound("invoice not found")
        return invoice

    def delete_invoice(self, invoice_id: UUID) -> tuple[UUID, ...]:
        with write_transaction(self.db):
            invoice = self.get_invoice(invoice_id, lock=True)
            purchase_ids = tuple(
                self.db.scalars(
                    select(ProcurementInvoicePurchase.purchase_id).where(
                        ProcurementInvoicePurchase.invoice_id == invoice.id
                    )
                )
            )
            self.db.delete(invoice)
            self.db.flush()
            return purchase_ids
