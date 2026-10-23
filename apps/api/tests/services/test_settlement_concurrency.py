from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, delete, func, select, text
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.auth.models import UserAccount
from app.identity.models import Person
from app.revenue_share.errors import RevenueShareSettlementReferenceConflict
from app.revenue_share.models import RevenueShareOrganization, RevenueShareSettlement
from app.revenue_share.schemas import RevenueShareSettlementCreate
from app.revenue_share.service import Balance, ResolvedBeneficiary, RevenueShareService


@pytest.mark.parametrize("same_operation", [True, False])
def test_concurrent_settlement_requests_create_one_record(
    postgres_engine: Engine, monkeypatch: pytest.MonkeyPatch, same_operation: bool
) -> None:
    """Use independent committed DB transactions; only the unrelated earnings setup is stubbed."""
    with Session(postgres_engine, expire_on_commit=False) as db:
        marker = uuid4().hex
        person = Person(
            full_name="结算并发测试",
            identity_ciphertext=marker,
            identity_digest=marker,
            sex="male",
            date_of_birth=date(1990, 1, 1),
        )
        db.add(person)
        db.flush()
        actor = UserAccount(
            person_id=person.id,
            email_normalized=f"{marker}@example.test",
            password_hash="unused",
            status="active",
            force_password_change=False,
            failed_login_count=0,
        )
        db.add(actor)
        db.flush()
        organization = RevenueShareOrganization(name=marker, created_by_user_id=actor.id)
        db.add(organization)
        db.commit()
        person_id, actor_id, organization_id = person.id, actor.id, organization.id

    def funded_balance(self: RevenueShareService, kind: str, id: UUID) -> Balance:
        return Balance(ResolvedBeneficiary(kind, None, id), 10_000, 0)

    monkeypatch.setattr(RevenueShareService, "balance_for", funded_balance)
    barrier = Barrier(2)
    input = RevenueShareSettlementCreate(
        idempotency_key=uuid4(),
        beneficiary_kind="organization",
        beneficiary_id=organization_id,
        amount_cents=2_000,
        settled_on=date(2026, 9, 11),
        reference="CONCURRENT-REF",
    )

    def submit(index: int) -> UUID | str:
        request = (
            input
            if same_operation or index == 0
            else input.model_copy(update={"idempotency_key": uuid4()})
        )
        with Session(postgres_engine, expire_on_commit=False) as db:
            barrier.wait(timeout=10)
            try:
                return (
                    RevenueShareService(db)
                    .create_settlement(
                        request, actor_user_id=actor_id, request_id=f"concurrent-{index}"
                    )
                    .id
                )
            except RevenueShareSettlementReferenceConflict:
                return "duplicate_reference"

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(submit, index) for index in range(2)]
            results = [future.result(timeout=20) for future in futures]
        if same_operation:
            assert results[0] == results[1]
        else:
            assert results.count("duplicate_reference") == 1
        with Session(postgres_engine) as db:
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(RevenueShareSettlement)
                    .where(RevenueShareSettlement.created_by_user_id == actor_id)
                )
                == 1
            )
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(AuditEvent.actor_user_id == actor_id)
                )
                == 1
            )
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update")
            )
            connection.execute(delete(AuditEvent).where(AuditEvent.actor_user_id == actor_id))
            connection.execute(
                text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update")
            )
            connection.execute(
                delete(RevenueShareSettlement).where(
                    RevenueShareSettlement.created_by_user_id == actor_id
                )
            )
            connection.execute(
                delete(RevenueShareOrganization).where(
                    RevenueShareOrganization.id == organization_id
                )
            )
            connection.execute(delete(UserAccount).where(UserAccount.id == actor_id))
            connection.execute(delete(Person).where(Person.id == person_id))
