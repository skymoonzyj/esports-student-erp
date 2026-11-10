import json
from collections.abc import Callable
from hashlib import sha256
from typing import cast
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.asset_management.models import Asset, AssetAssignment
from app.asset_management.rental_dates import RentalPeriod, renew_expiry
from app.asset_management.rental_models import AssetRentalOperation, AssetRentalRenewal
from app.asset_management.rental_schemas import (
    RentalFacts,
    RentalRenewalPage,
    RentalRenewalResponse,
    RentalRenewRequest,
    RentalRenewResponse,
    RentalRenewResultItem,
    RentalReturnRequest,
    validate_rental_facts,
)
from app.asset_management.schemas import AssetUpdate
from app.asset_management.service import AssetNotFound, AssetServiceError
from app.audit.service import AuditService
from app.auth.models import UserAccount
from app.core.errors import ProblemError
from app.core.time import business_today
from app.core.transactions import atomic_write_transaction
from app.identity.models import Person
from app.rbac.service import RbacService

RENTAL_ERROR_DETAILS = {
    "revision_conflict": "租期版本已变化，请刷新资产后重试",
    "already_returned": "租赁已退租，不能再次修改",
    "ownership_protected": "已开始的租赁不能改变资产归属",
    "history_protected": "已有续期历史，请通过续期操作延长租期",
    "facts_invalid": "资产归属或租期资料无效，请检查必填项和日期范围",
    "start_protected": "已开始的租赁不能改为未来开始日期",
    "operation_conflict": "操作编号已用于其他请求，请刷新后重新操作",
    "not_started": "租赁尚未开始，不能续期",
    "not_active": "该资产不是可续期的有效租赁资产",
    "date_overflow": "续期日期超出9999年，请选择较短周期",
    "not_rented": "该资产不是租用资产，不能退租",
    "return_date_invalid": "退租日期必须在租赁开始日期与今天之间",
    "custody_active": "请先完成使用人归还，再办理退租",
}


class RentalConflict(AssetServiceError):
    code = "asset_rental_conflict"

    def __init__(self, reason: str) -> None:
        if reason in RENTAL_ERROR_DETAILS:
            self.code = f"asset_rental_{reason}"
        super().__init__(reason)


class RentalInvalid(AssetServiceError):
    code = "asset_rental_invalid"

    def __init__(self, reason: str) -> None:
        if reason in RENTAL_ERROR_DETAILS:
            self.code = f"asset_rental_{reason}"
        super().__init__(reason)


def _correction_snapshot(asset: Asset) -> dict[str, object]:
    return {
        "ownership_type": asset.ownership_type,
        "acquisition_method": asset.acquisition_method,
        "acquisition_channel": asset.acquisition_channel,
        "rental_started_on": asset.rental_started_on.isoformat()
        if asset.rental_started_on
        else None,
        "rental_expires_on": asset.rental_expires_on.isoformat()
        if asset.rental_expires_on
        else None,
        "rental_returned_on": asset.rental_returned_on.isoformat()
        if asset.rental_returned_on
        else None,
        "rental_revision": asset.rental_revision,
    }


def validate_asset_rental_update(
    db: Session, asset: Asset, input: AssetUpdate
) -> dict[str, object]:
    fields = set(RentalFacts.model_fields) & input.model_fields_set
    if not fields:
        return {}
    changed = {key for key in fields if getattr(asset, key) != getattr(input, key)}
    if not changed:
        return {}
    if input.expected_rental_revision != asset.rental_revision:
        raise RentalConflict("revision_conflict")
    if asset.rental_returned_on is not None:
        raise RentalConflict("already_returned")
    if asset.ownership_type == "rented":
        if (
            "ownership_type" in changed
            and asset.rental_started_on is not None
            and asset.rental_started_on <= business_today()
        ):
            raise RentalConflict("ownership_protected")
        if db.scalar(
            select(AssetRentalRenewal.id).where(AssetRentalRenewal.asset_id == asset.id).limit(1)
        ):
            raise RentalConflict("history_protected")
    try:
        merged = RentalFacts.model_validate(
            {
                key: getattr(input, key) if key in fields else getattr(asset, key)
                for key in RentalFacts.model_fields
            }
        )
        validate_rental_facts(merged)
    except ValueError as exc:
        raise RentalInvalid("facts_invalid") from exc
    if (
        asset.ownership_type == "rented"
        and asset.rental_started_on is not None
        and asset.rental_started_on <= business_today()
        and merged.rental_started_on is not None
        and merged.rental_started_on > business_today()
    ):
        raise RentalConflict("start_protected")
    before = _correction_snapshot(asset)
    note_changed = asset.acquisition_note != merged.acquisition_note
    for key in fields:
        setattr(asset, key, getattr(merged, key))
    asset.rental_revision += 1
    return {
        "previous_rental_facts": before,
        "rental_facts": _correction_snapshot(asset),
        "acquisition_note_changed": note_changed,
    }


class RentalService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _authorize(self, actor_user_id: UUID) -> None:
        actor = self.db.scalar(
            select(UserAccount)
            .where(UserAccount.id == actor_user_id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        person = (
            self.db.scalar(
                select(Person)
                .where(Person.id == actor.person_id)
                .execution_options(populate_existing=True)
            )
            if actor
            else None
        )
        if (
            actor is None
            or actor.status != "active"
            or actor.force_password_change
            or person is None
            or person.deleted_at is not None
            or not RbacService(self.db)
            .decide(user_id=actor_user_id, permission="assets.manage", resource_tokens={"global"})
            .allowed
        ):
            raise ProblemError(status=403, code="permission_denied", detail="没有执行此操作的权限")

    def renew(
        self,
        input: RentalRenewRequest,
        *,
        actor_user_id: UUID,
        request_id: str,
        reauthorize: Callable[[], None] | None = None,
    ) -> RentalRenewResponse:
        with atomic_write_transaction(self.db):
            if reauthorize:
                reauthorize()
            self._authorize(actor_user_id)
            # Serialize identical keys before looking up their persisted response.
            lock_key = int.from_bytes(
                sha256(input.operation_key.bytes).digest()[:8], "big", signed=True
            )
            self.db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
            payload_hash = sha256(
                json.dumps(
                    {
                        "period": input.period,
                        "assets": sorted(
                            [
                                (str(item.asset_id), item.expected_rental_revision)
                                for item in input.assets
                            ]
                        ),
                    },
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            operation = self.db.get(
                AssetRentalOperation, input.operation_key, populate_existing=True
            )
            if operation is not None:
                self._authorize(actor_user_id)
                if (
                    operation.actor_user_id != actor_user_id
                    or operation.payload_hash != payload_hash
                ):
                    raise RentalConflict("operation_conflict")
                return RentalRenewResponse.model_validate({**operation.result, "replayed": True})
            ids = [item.asset_id for item in input.assets]
            assets = list(
                self.db.scalars(
                    select(Asset)
                    .where(Asset.id.in_(ids))
                    .order_by(Asset.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            if len(assets) != len(ids):
                raise AssetNotFound("asset not found")
            if reauthorize:
                reauthorize()
            self._authorize(actor_user_id)
            expected = {item.asset_id: item.expected_rental_revision for item in input.assets}
            results: dict[UUID, RentalRenewResultItem] = {}
            for asset in assets:
                if asset.rental_revision != expected[asset.id]:
                    raise RentalConflict("revision_conflict")
                if asset.rental_started_on and asset.rental_started_on > business_today():
                    raise RentalInvalid("not_started")
                if (
                    asset.ownership_type != "rented"
                    or asset.rental_returned_on is not None
                    or asset.status == "retired"
                    or asset.rental_expires_on is None
                    or asset.rental_started_on is None
                    or asset.rental_started_on > business_today()
                ):
                    raise RentalInvalid("not_active")
                previous = asset.rental_expires_on
                try:
                    next_expiry = renew_expiry(previous, input.period)
                except ValueError as exc:
                    raise RentalInvalid("date_overflow") from exc
                results[asset.id] = RentalRenewResultItem(
                    asset_id=asset.id,
                    previous_expires_on=previous,
                    rental_expires_on=next_expiry,
                    rental_revision=asset.rental_revision + 1,
                )
            response = RentalRenewResponse(
                operation_key=input.operation_key,
                period=input.period,
                replayed=False,
                items=[results[item.asset_id] for item in input.assets],
            )
            self.db.add(
                AssetRentalOperation(
                    operation_key=input.operation_key,
                    actor_user_id=actor_user_id,
                    payload_hash=payload_hash,
                    result=response.model_dump(mode="json"),
                )
            )
            self.db.flush()
            for asset in assets:
                item = results[asset.id]
                self.db.add(
                    AssetRentalRenewal(
                        operation_key=input.operation_key,
                        asset_id=asset.id,
                        actor_user_id=actor_user_id,
                        period=input.period,
                        previous_expires_on=item.previous_expires_on,
                        rental_expires_on=item.rental_expires_on,
                    )
                )
                asset.rental_expires_on = item.rental_expires_on
                asset.rental_revision = item.rental_revision
                asset.updated_by_user_id = actor_user_id
                AuditService(self.db).append(
                    actor_user_id=actor_user_id,
                    action="asset.rental.renew",
                    resource_type="asset",
                    resource_id=asset.id,
                    outcome="allowed",
                    request_id=request_id,
                    purpose=None,
                    metadata={
                        "operation_key": str(input.operation_key),
                        "rental_period": input.period,
                        "previous_expires_on": item.previous_expires_on.isoformat(),
                        "rental_expires_on": item.rental_expires_on.isoformat(),
                        "rental_revision": item.rental_revision,
                    },
                )
            self.db.flush()
            return response

    def return_rental(
        self,
        asset_id: UUID,
        input: RentalReturnRequest,
        *,
        actor_user_id: UUID,
        request_id: str,
        reauthorize: Callable[[], None] | None = None,
    ) -> Asset:
        with atomic_write_transaction(self.db):
            if reauthorize:
                reauthorize()
            self._authorize(actor_user_id)
            asset = self.db.scalar(
                select(Asset)
                .where(Asset.id == asset_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if asset is None:
                raise AssetNotFound("asset not found")
            if reauthorize:
                reauthorize()
            self._authorize(actor_user_id)
            if (
                asset.rental_revision != input.expected_rental_revision
                or asset.rental_returned_on is not None
            ):
                raise RentalConflict("revision_conflict")
            if asset.ownership_type != "rented" or asset.rental_started_on is None:
                raise RentalInvalid("not_rented")
            if not asset.rental_started_on <= input.returned_on <= business_today():
                raise RentalInvalid("return_date_invalid")
            if self.db.scalar(
                select(AssetAssignment.id)
                .where(AssetAssignment.asset_id == asset.id, AssetAssignment.returned_at.is_(None))
                .limit(1)
            ):
                raise RentalConflict("custody_active")
            asset.rental_returned_on = input.returned_on
            asset.rental_revision += 1
            asset.updated_by_user_id = actor_user_id
            AuditService(self.db).append(
                actor_user_id=actor_user_id,
                action="asset.rental.return",
                resource_type="asset",
                resource_id=asset.id,
                outcome="allowed",
                request_id=request_id,
                purpose=None,
                metadata={
                    "rental_returned_on": input.returned_on.isoformat(),
                    "rental_revision": asset.rental_revision,
                },
            )
            self.db.flush()
            return asset

    def history(self, asset_id: UUID, *, page: int, page_size: int) -> RentalRenewalPage:
        if self.db.get(Asset, asset_id) is None:
            raise AssetNotFound("asset not found")

        total = (
            self.db.scalar(
                select(func.count())
                .select_from(AssetRentalRenewal)
                .where(AssetRentalRenewal.asset_id == asset_id)
            )
            or 0
        )
        rows = self.db.execute(
            select(AssetRentalRenewal, Person.full_name)
            .outerjoin(UserAccount, UserAccount.id == AssetRentalRenewal.actor_user_id)
            .outerjoin(Person, Person.id == UserAccount.person_id)
            .where(AssetRentalRenewal.asset_id == asset_id)
            .order_by(AssetRentalRenewal.created_at.desc(), AssetRentalRenewal.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return RentalRenewalPage(
            items=[
                RentalRenewalResponse(
                    id=row.id,
                    period=cast(RentalPeriod, row.period),
                    previous_expires_on=row.previous_expires_on,
                    rental_expires_on=row.rental_expires_on,
                    created_at=row.created_at,
                    actor_name=name,
                )
                for row, name in rows
            ],
            total=total,
            page=page,
            page_size=page_size,
        )
