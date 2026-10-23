from __future__ import annotations

import builtins
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.asset_management.models import Asset, AssetAssignment
from app.asset_management.schemas import (
    AssetAssignRequest,
    AssetCreate,
    AssetReturnRequest,
    AssetUpdate,
    AssetWarrantyDetails,
)
from app.core.transactions import write_transaction
from app.identity.models import Person


class AssetServiceError(ValueError):
    code = "asset_service_error"


class AssetNotFound(AssetServiceError):
    code = "asset_not_found"


class AssetAlreadyAssigned(AssetServiceError):
    code = "asset_already_assigned"


class AssetNotAssignable(AssetServiceError):
    code = "asset_not_assignable"


class AssetAssignmentNotFound(AssetServiceError):
    code = "asset_assignment_not_found"


class AssetPersonNotFound(AssetServiceError):
    code = "asset_person_not_found"


class AssetStatusConflict(AssetServiceError):
    code = "asset_status_conflict"


class AssetWarrantyRangeInvalid(AssetServiceError):
    code = "asset_warranty_range_invalid"


class AssetCodeGenerationFailed(AssetServiceError):
    code = "asset_code_generation_failed"


@dataclass(frozen=True)
class AssetListResult:
    items: list[tuple[Asset, AssetAssignment | None, str | None]]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True)
class AssetSelfItem:
    asset: Asset
    assignment: AssetAssignment


class AssetService:
    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def _warranty_values(input: AssetWarrantyDetails) -> dict[str, str | None]:
        values = {key: getattr(input, key) for key in AssetWarrantyDetails.model_fields}
        offline = ("warranty_service_location", "warranty_contact_name", "warranty_contact_phone")
        online = ("warranty_product_url", "warranty_order_number")
        for key in offline + online:
            if (key in offline and input.warranty_channel != "offline") or (
                key in online and input.warranty_channel != "online"
            ):
                values[key] = None
        return values

    @staticmethod
    def _new_code() -> str:
        # The random suffix keeps creation independent of a shared counter and
        # remains short enough for a printed label.
        return f"AST-{datetime.now(UTC):%Y%m%d}-{secrets.token_hex(4).upper()}"

    def _unique_code(self) -> str:
        for _ in range(8):
            code = self._new_code()
            if self.db.scalar(select(Asset.id).where(Asset.asset_code == code)) is None:
                return code
        raise AssetCodeGenerationFailed("could not generate a unique asset code")

    def create_in_transaction(
        self,
        input: AssetCreate,
        *,
        actor_user_id: UUID,
        source_purchase_id: UUID | None = None,
    ) -> Asset:
        asset = Asset(
            asset_code=self._unique_code(),
            name=input.name,
            category=input.category,
            brand=input.brand,
            model=input.model,
            serial_number=input.serial_number,
            location=input.location,
            purchase_date=input.purchase_date,
            warranty_expires_on=input.warranty_expires_on,
            **self._warranty_values(input),
            source_purchase_id=source_purchase_id,
            note=input.note,
            status="available",
            created_by_user_id=actor_user_id,
            updated_by_user_id=actor_user_id,
        )
        self.db.add(asset)
        self.db.flush()
        return asset

    def create(self, input: AssetCreate, *, actor_user_id: UUID) -> Asset:
        try:
            with write_transaction(self.db):
                return self.create_in_transaction(
                    input,
                    actor_user_id=actor_user_id,
                )
        except IntegrityError as exc:
            raise AssetCodeGenerationFailed("asset code already exists") from exc

    def get(self, asset_id: UUID) -> Asset:
        asset = self.db.get(Asset, asset_id)
        if asset is None:
            raise AssetNotFound("asset not found")
        return asset

    def current_assignment(self, asset_id: UUID) -> tuple[AssetAssignment, str] | None:
        row = self.db.execute(
            select(AssetAssignment, Person.full_name)
            .join(Person, Person.id == AssetAssignment.person_id)
            .where(
                AssetAssignment.asset_id == asset_id,
                AssetAssignment.returned_at.is_(None),
            )
        ).one_or_none()
        if row is None:
            return None
        assignment, person_name = row
        return assignment, person_name

    def list(
        self,
        *,
        query: str | None,
        status: str | None,
        category: str | None,
        page: int,
        page_size: int,
    ) -> AssetListResult:
        base = select(Asset)
        if status is not None:
            base = base.where(Asset.status == status)
        if category:
            base = base.where(Asset.category == category)
        if query:
            pattern = f"%{query.strip()}%"
            base = base.where(
                or_(
                    Asset.asset_code.ilike(pattern),
                    Asset.name.ilike(pattern),
                    Asset.category.ilike(pattern),
                    Asset.serial_number.ilike(pattern),
                )
            )
        total = self.db.scalar(select(func.count()).select_from(base.subquery())) or 0
        assets = list(
            self.db.scalars(
                base.options(selectinload(Asset.photo))
                .order_by(Asset.updated_at.desc(), Asset.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        items: list[tuple[Asset, AssetAssignment | None, str | None]] = []
        if assets:
            rows = self.db.execute(
                select(AssetAssignment, Person.full_name)
                .join(Person, Person.id == AssetAssignment.person_id)
                .where(
                    AssetAssignment.asset_id.in_([asset.id for asset in assets]),
                    AssetAssignment.returned_at.is_(None),
                )
            )
            assignments = {assignment.asset_id: (assignment, name) for assignment, name in rows}
            for asset in assets:
                assignment, name = assignments.get(asset.id, (None, None))
                items.append((asset, assignment, name))
        return AssetListResult(items=items, total=int(total), page=page, page_size=page_size)

    def list_for_person(self, person_id: UUID) -> builtins.list[AssetSelfItem]:
        rows = self.db.execute(
            select(Asset, AssetAssignment)
            .options(selectinload(Asset.photo))
            .join(AssetAssignment, AssetAssignment.asset_id == Asset.id)
            .where(
                AssetAssignment.person_id == person_id,
                AssetAssignment.returned_at.is_(None),
                Asset.status != "retired",
            )
            .order_by(Asset.name, Asset.asset_code)
        )
        return [AssetSelfItem(asset=asset, assignment=assignment) for asset, assignment in rows]

    def history(self, asset_id: UUID) -> builtins.list[tuple[AssetAssignment, str]]:
        self.get(asset_id)
        return list(
            self.db.execute(
                select(AssetAssignment, Person.full_name)
                .join(Person, Person.id == AssetAssignment.person_id)
                .where(AssetAssignment.asset_id == asset_id)
                .order_by(AssetAssignment.assigned_at.desc(), AssetAssignment.id.desc())
            )
            .tuples()
            .all()
        )

    def update(self, asset_id: UUID, input: AssetUpdate, *, actor_user_id: UUID) -> Asset:
        try:
            with write_transaction(self.db):
                asset = self.db.scalar(select(Asset).where(Asset.id == asset_id).with_for_update())
                if asset is None:
                    raise AssetNotFound("asset not found")
                active = self.current_assignment(asset_id)
                if input.status == "assigned" and active is None:
                    raise AssetStatusConflict("assigned status requires an active assignment")
                if input.status in {"available", "maintenance", "retired"} and active is not None:
                    raise AssetStatusConflict(
                        "assigned asset must be returned before changing status"
                    )
                next_purchase_date = (
                    input.purchase_date
                    if "purchase_date" in input.model_fields_set
                    else asset.purchase_date
                )
                next_warranty_expires_on = (
                    input.warranty_expires_on
                    if "warranty_expires_on" in input.model_fields_set
                    else asset.warranty_expires_on
                )
                if (
                    next_purchase_date is not None
                    and next_warranty_expires_on is not None
                    and next_warranty_expires_on < next_purchase_date
                ):
                    raise AssetWarrantyRangeInvalid("warranty expiry cannot precede purchase date")
                for field in (
                    "name",
                    "category",
                    "brand",
                    "model",
                    "serial_number",
                    "status",
                    "location",
                    "purchase_date",
                    "warranty_expires_on",
                    "note",
                ):
                    if field in input.model_fields_set:
                        setattr(asset, field, getattr(input, field))
                asset.updated_by_user_id = actor_user_id
                if set(AssetWarrantyDetails.model_fields) & input.model_fields_set:
                    warranty = AssetWarrantyDetails.model_validate(
                        {
                            key: getattr(input, key)
                            if key in input.model_fields_set
                            else getattr(asset, key)
                            for key in AssetWarrantyDetails.model_fields
                        }
                    )
                    for key, value in self._warranty_values(warranty).items():
                        setattr(asset, key, value)
                self.db.flush()
                return asset
        except IntegrityError as exc:
            raise AssetStatusConflict("asset update conflicted with current state") from exc

    def assign(
        self,
        asset_id: UUID,
        input: AssetAssignRequest,
        *,
        actor_user_id: UUID,
    ) -> AssetAssignment:
        with write_transaction(self.db):
            asset = self.db.scalar(select(Asset).where(Asset.id == asset_id).with_for_update())
            if asset is None:
                raise AssetNotFound("asset not found")
            if asset.status in {"maintenance", "retired"}:
                raise AssetNotAssignable("maintenance or retired assets cannot be assigned")
            if self.current_assignment(asset_id) is not None:
                raise AssetAlreadyAssigned("asset already has an active assignment")
            person = self.db.scalar(
                select(Person).where(Person.id == input.person_id, Person.deleted_at.is_(None))
            )
            if person is None:
                raise AssetPersonNotFound("person not found")
            assignment = AssetAssignment(
                asset_id=asset_id,
                person_id=person.id,
                assigned_at=input.assigned_at,
                expected_return_at=input.expected_return_at,
                condition_on_assign=input.condition_on_assign,
                note=input.note,
                assigned_by_user_id=actor_user_id,
            )
            self.db.add(assignment)
            asset.status = "assigned"
            asset.updated_by_user_id = actor_user_id
            self.db.flush()
            return assignment

    def return_asset(
        self,
        asset_id: UUID,
        input: AssetReturnRequest,
        *,
        actor_user_id: UUID,
    ) -> AssetAssignment:
        with write_transaction(self.db):
            asset = self.db.scalar(select(Asset).where(Asset.id == asset_id).with_for_update())
            if asset is None:
                raise AssetNotFound("asset not found")
            assignment = self.db.scalar(
                select(AssetAssignment)
                .where(
                    AssetAssignment.asset_id == asset_id,
                    AssetAssignment.returned_at.is_(None),
                )
                .with_for_update()
            )
            if assignment is None:
                raise AssetAssignmentNotFound("asset has no active assignment")
            if input.returned_at < assignment.assigned_at:
                raise AssetStatusConflict("return time cannot precede assignment")
            assignment.returned_at = input.returned_at
            assignment.condition_on_return = input.condition_on_return
            if input.note:
                assignment.note = input.note
            assignment.returned_by_user_id = actor_user_id
            asset.status = "available"
            asset.updated_by_user_id = actor_user_id
            self.db.flush()
            return assignment
