from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.auth.models import UserAccount
from app.auth.service import AuthContext
from app.core.transactions import atomic_write_transaction
from app.housing.access import HousingAccessService
from app.housing.errors import (
    HousingInvalidStayTransition,
    HousingResourceNotFound,
    HousingSensitiveDataUnavailable,
    HousingStaleVersion,
    HousingStayAlreadyVoided,
    HousingVoidOperationConflict,
    HousingVoidReasonInvalid,
)
from app.housing.history_void_access import require_history_void_authority
from app.housing.history_void_models import HousingStayVoidOperation
from app.housing.history_void_schemas import StayHistoryVoidRequest, StayHistoryVoidResult
from app.housing.models import HousingRoom, HousingStay
from app.identity.document_identity import normalize_hk_id, normalize_hk_mo_permit
from app.identity.models import Person
from app.identity.normalization import IdentityValidationError
from app.security.encryption import EncryptedFieldError, EnvelopeCipher, LookupDigest


class HousingHistoryVoidService:
    def __init__(
        self,
        db: Session,
        cipher: EnvelopeCipher,
        lookup: LookupDigest,
        *,
        occurred_at: datetime,
        request_id: str,
    ) -> None:
        self.db = db
        self.cipher = cipher
        self.lookup = lookup
        self.occurred_at = occurred_at
        self.request_id = request_id

    def void(
        self, actor: AuthContext, stay_id: UUID, command: StayHistoryVoidRequest
    ) -> StayHistoryVoidResult:
        actor_id = actor.account.id
        with atomic_write_transaction(self.db):
            lock_material = f"housing-history-void:{actor_id}:{command.idempotency_key}"
            lock_id = int.from_bytes(
                hashlib.sha256(lock_material.encode()).digest()[:8], byteorder="big", signed=True
            )
            self.db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_id})

            # Only a currently visible target may supply the participant/room lock set.
            access = HousingAccessService(self.db, actor_id, datetime.now(UTC))
            preliminary = self.db.execute(
                select(HousingStay.person_id, HousingStay.room_id)
                .join(HousingRoom, HousingRoom.id == HousingStay.room_id)
                .where(HousingStay.id == stay_id, access.room_permission_criteria("housing.read"))
            ).one_or_none()
            if preliminary is None:
                raise HousingResourceNotFound("housing stay not found")
            room = self.db.get(HousingRoom, preliminary.room_id)
            if room is None:
                raise HousingResourceNotFound("housing room not found")
            require_history_void_authority(self.db, actor, room)
            actor_person_id = self.db.scalar(
                select(UserAccount.person_id).where(UserAccount.id == actor_id)
            )
            if actor_person_id is None:
                raise HousingResourceNotFound("housing actor not found")
            person_ids = sorted({actor_person_id, preliminary.person_id})
            people = {
                person.id: person
                for person in self.db.scalars(
                    select(Person)
                    .where(Person.id.in_(person_ids))
                    .order_by(Person.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            }
            # Account mutations and role management use the same people -> accounts order.
            accounts = list(
                self.db.scalars(
                    select(UserAccount)
                    .where(UserAccount.person_id.in_(person_ids))
                    .order_by(UserAccount.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            current_actor = next((account for account in accounts if account.id == actor_id), None)
            if current_actor is None or current_actor.person_id != actor_person_id:
                raise HousingVoidOperationConflict("housing participants changed")
            room = self.db.scalar(
                select(HousingRoom)
                .where(HousingRoom.id == preliminary.room_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if room is None:
                raise HousingResourceNotFound("housing room not found")
            stay = self.db.scalar(
                select(HousingStay)
                .where(HousingStay.id == stay_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if stay is None:
                raise HousingResourceNotFound("housing stay not found")
            if (stay.person_id, stay.room_id) != (preliminary.person_id, preliminary.room_id):
                raise HousingVoidOperationConflict("housing participants changed")
            require_history_void_authority(self.db, actor, room)

            reason = command.reason.get_secret_value().strip()
            canonical_json = json.dumps(
                {
                    "stay_id": str(stay_id),
                    "expected_version": command.expected_version,
                    "reason": reason,
                    "confirmed": command.confirmed,
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
            fingerprint = self.lookup.digest_domain("housing-history-void:v1", canonical_json)
            previous = self.db.scalar(
                select(HousingStayVoidOperation).where(
                    HousingStayVoidOperation.actor_account_id == actor_id,
                    HousingStayVoidOperation.idempotency_key == command.idempotency_key,
                )
            )
            if previous is not None:
                if previous.request_fingerprint != fingerprint:
                    raise HousingVoidOperationConflict("housing void operation conflicts")
                return self._result(previous, stay)
            if stay.voided_at is not None:
                raise HousingStayAlreadyVoided("已作废，无需重复操作")
            if stay.status != "checked_out":
                raise HousingInvalidStayTransition("only checked-out history can be voided")
            if stay.version != command.expected_version:
                raise HousingStaleVersion("housing stay version changed")
            resident = people.get(stay.person_id)
            if resident is None:
                raise HousingResourceNotFound("housing resident not found")
            if resident.document_type in {"passport", "hk_id", "hk_mo_permit"}:
                try:
                    number = self.cipher.decrypt(
                        resident.identity_ciphertext, field_name="identity_number"
                    )
                    if resident.document_type == "hk_id":
                        number = normalize_hk_id(number)
                        # Compare only the known target. Strip presentation differences
                        # in free text too, so brackets/case/spacing cannot leak the ID.
                        compared_reason = (
                            "".join(reason.split())
                            .upper()
                            .translate(str.maketrans("", "", "()（）"))
                        )
                    elif resident.document_type == "hk_mo_permit":
                        number = normalize_hk_mo_permit(number)
                        # Disclosure aliases are broader than accepted identity input.
                        # Only compare the known target; do not classify arbitrary text.
                        compared_reason = "".join(
                            unicodedata.normalize("NFKC", reason).split()
                        ).upper()
                    else:
                        compared_reason = reason
                except (EncryptedFieldError, IdentityValidationError):
                    raise HousingSensitiveDataUnavailable("identity data unavailable") from None
                # Exact known target document only, not an unreliable universal passport regex.
                if number.strip() and number.strip() in compared_reason:
                    raise HousingVoidReasonInvalid("reason contains a complete document number")
            old_version = stay.version
            stay.voided_at = self.occurred_at
            stay.voided_by_user_id = actor_id
            stay.void_reason_ciphertext = self.cipher.encrypt(
                reason, field_name="housing_stay_void_reason"
            )
            stay.version += 1
            operation = HousingStayVoidOperation(
                id=uuid4(),
                actor_account_id=actor_id,
                idempotency_key=command.idempotency_key,
                stay_id=stay.id,
                request_fingerprint=fingerprint,
                result_version=stay.version,
                created_at=self.occurred_at,
            )
            self.db.add(operation)
            self.db.flush()
            AuditService(self.db).append(
                actor_user_id=actor_id,
                action="housing.stay.void_history",
                resource_type="housing_stay",
                resource_id=stay.id,
                purpose=None,
                outcome="allowed",
                request_id=self.request_id,
                occurred_at=self.occurred_at,
                metadata={
                    "operation_id": str(operation.id),
                    "room_id": str(room.id),
                    "person_id": str(stay.person_id),
                    "old_version": old_version,
                    "new_version": stay.version,
                },
            )
            return self._result(operation, stay)

    @staticmethod
    def _result(operation: HousingStayVoidOperation, stay: HousingStay) -> StayHistoryVoidResult:
        return StayHistoryVoidResult(
            operation_id=operation.id,
            stay_id=stay.id,
            room_id=stay.room_id,
            person_id=stay.person_id,
            version=operation.result_version,
            voided_at=operation.created_at,
        )
