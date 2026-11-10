"""Actor-scoped atomic rental creation with durable retry protection."""

import hashlib
import json
from collections.abc import Callable
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.asset_management.models import Asset, AssetBatchOperation
from app.asset_management.schemas import AssetBatchCreate
from app.asset_management.service import AssetService, AssetServiceError
from app.core.transactions import atomic_write_transaction


class AssetBatchConflict(AssetServiceError):
    code = "asset_batch_conflict"


class AssetBatchService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.replayed = False

    def create(
        self,
        input: AssetBatchCreate,
        *,
        actor_user_id: UUID,
        reauthorize: Callable[[], None],
        audit: Callable[[Asset], None],
    ) -> list[Asset]:
        self.replayed = False
        payload_hash = hashlib.sha256(
            json.dumps(
                input.model_dump(mode="json", exclude={"operation_key"}),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        lock_hash = hashlib.sha256(
            f"asset-batch:{actor_user_id}:{input.operation_key}".encode()
        ).digest()
        with atomic_write_transaction(self.db):
            reauthorize()
            self.db.execute(
                select(
                    func.pg_advisory_xact_lock(int.from_bytes(lock_hash[:8], "big", signed=True))
                )
            )
            # Permission or session may change while this request waits.
            reauthorize()
            operation = self.db.get(AssetBatchOperation, (actor_user_id, input.operation_key))
            if operation is not None:
                if operation.payload_hash != payload_hash:
                    raise AssetBatchConflict("操作键已用于其他参数，请核对原批次")
                self.replayed = True
                assets = {
                    asset.id: asset
                    for asset in self.db.scalars(
                        select(Asset).where(
                            Asset.id.in_([UUID(value) for value in operation.asset_ids])
                        )
                    )
                }
                return [assets[UUID(value)] for value in operation.asset_ids]
            service = AssetService(self.db)
            items = [
                service.create_in_transaction(input.asset, actor_user_id=actor_user_id)
                for _ in range(input.quantity)
            ]
            self.db.add(
                AssetBatchOperation(
                    actor_user_id=actor_user_id,
                    operation_key=input.operation_key,
                    payload_hash=payload_hash,
                    quantity=input.quantity,
                    asset_ids=[str(asset.id) for asset in items],
                )
            )
            for asset in items:
                audit(asset)
            self.db.flush()
            return items
