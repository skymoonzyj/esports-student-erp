from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session

from app.asset_management.models import Asset
from app.asset_management.rental_models import AssetRentalOperation, AssetRentalRenewal
from app.asset_management.rental_schemas import RentalRenewRequest, RentalReturnRequest
from app.asset_management.rental_service import RentalConflict, RentalService
from app.asset_management.schemas import AssetCreate
from app.asset_management.service import AssetService
from app.audit.models import AuditEvent
from app.audit.service import AuditService
from app.auth.models import UserAccount
from app.core.errors import ProblemError
from app.rbac.models import UserRole
from tests.departure_helpers import committed_scene


@contextmanager
def rental_scene(engine):
    with committed_scene(engine) as (db, context, _subject, _stay):
        asset = AssetService(db).create(
            AssetCreate(
                name="并发租约",
                ownership_type="rented",
                rental_started_on=date(2026, 1, 1),
                rental_expires_on=date(2027, 1, 31),
            ),
            actor_user_id=context.account.id,
        )
        db.commit()
        asset_id = asset.id
        try:
            yield db, context, asset_id
        finally:
            db.rollback()
            operation_keys = list(
                db.scalars(
                    select(AssetRentalRenewal.operation_key).where(
                        AssetRentalRenewal.asset_id == asset_id
                    )
                )
            )
            db.execute(text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update"))
            try:
                db.execute(delete(AuditEvent).where(AuditEvent.resource_id == asset_id))
            finally:
                db.execute(text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update"))
            db.execute(delete(AssetRentalRenewal).where(AssetRentalRenewal.asset_id == asset_id))
            db.execute(
                delete(AssetRentalOperation).where(
                    AssetRentalOperation.operation_key.in_(operation_keys)
                )
            )
            db.execute(delete(Asset).where(Asset.id == asset_id))
            db.commit()


def request(asset_id, key=None, revision=0):
    return RentalRenewRequest(
        operation_key=key or uuid4(),
        period="month",
        assets=[{"asset_id": asset_id, "expected_rental_revision": revision}],
    )


def test_different_actors_lock_overlapping_batches_in_global_order(postgres_engine, monkeypatch):
    with (
        rental_scene(postgres_engine) as (db, first_actor, first_id),
        rental_scene(postgres_engine) as (_other_db, second_actor, second_id),
    ):
        actor_ids = [first_actor.account.id, second_actor.account.id]
        barrier = Barrier(2)
        authorize = RentalService._authorize

        def synchronize(service, actor_user_id):
            authorize(service, actor_user_id)
            if not service.db.info.get("batch_ready"):
                service.db.info["batch_ready"] = True
                barrier.wait(timeout=10)

        monkeypatch.setattr(RentalService, "_authorize", synchronize)

        def invoke(actor_id, asset_ids):
            with Session(postgres_engine) as connection:
                try:
                    return RentalService(connection).renew(
                        RentalRenewRequest(
                            operation_key=uuid4(),
                            period="month",
                            assets=[
                                {"asset_id": value, "expected_rental_revision": 0}
                                for value in asset_ids
                            ],
                        ),
                        actor_user_id=actor_id,
                        request_id="different-actors-batch",
                    )
                except RentalConflict:
                    return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(invoke, first_actor.account.id, [first_id, second_id])
            second = pool.submit(invoke, second_actor.account.id, [second_id, first_id])
            results = [first.result(timeout=20), second.result(timeout=20)]
        assert results.count("conflict") == 1
        db.expire_all()
        for asset_id in (first_id, second_id):
            asset = db.get(Asset, asset_id)
            assert asset.rental_expires_on == date(2027, 2, 28)
            assert asset.rental_revision == 1
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(AssetRentalRenewal)
                    .where(AssetRentalRenewal.asset_id == asset_id)
                )
                == 1
            )
        # Either actor may own both assets' renewal audits. Remove these exact
        # resources before the inner fixture deletes its actor, including the
        # cross-asset audit that the outer fixture would otherwise remove later.
        db.execute(text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update"))
        try:
            db.execute(
                delete(AuditEvent).where(AuditEvent.resource_id.in_([first_id, second_id]))
            )
        finally:
            db.execute(text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update"))
        # Both assets also share one operation; remove its linked rows before
        # either nested synthetic fixture removes its actor and operations.
        operation_keys = list(
            db.scalars(
                select(AssetRentalRenewal.operation_key).where(
                    AssetRentalRenewal.asset_id.in_([first_id, second_id])
                )
            )
        )
        db.execute(
            delete(AssetRentalRenewal).where(AssetRentalRenewal.asset_id.in_([first_id, second_id]))
        )
        db.execute(
            delete(AssetRentalOperation).where(
                AssetRentalOperation.operation_key.in_(operation_keys)
            )
        )
        db.execute(
            update(Asset)
            .where(Asset.id == first_id)
            .values(updated_by_user_id=first_actor.account.id)
        )
        db.execute(
            update(Asset)
            .where(Asset.id == second_id)
            .values(updated_by_user_id=second_actor.account.id)
        )
        db.commit()
        assert db.scalar(
            select(func.count()).select_from(AuditEvent).where(
                AuditEvent.resource_id.in_([first_id, second_id])
            )
        ) == 0
        assert db.scalar(
            select(func.count()).select_from(AssetRentalRenewal).where(
                AssetRentalRenewal.asset_id.in_([first_id, second_id])
            )
        ) == 0
        assert db.scalar(
            select(func.count()).select_from(AssetRentalOperation).where(
                AssetRentalOperation.operation_key.in_(operation_keys)
            )
        ) == 0
        assert db.scalar(text(
            "SELECT tgenabled FROM pg_trigger WHERE tgrelid = 'audit_events'::regclass "
            "AND tgname = 'audit_events_no_update'"
        )) == "O"
    with Session(postgres_engine) as fresh:
        assert fresh.scalar(
            select(func.count()).select_from(UserAccount).where(UserAccount.id.in_(actor_ids))
        ) == 0
        assert fresh.scalar(
            select(func.count()).select_from(Asset).where(Asset.id.in_([first_id, second_id]))
        ) == 0


@pytest.mark.parametrize("same_key", [True, False])
def test_two_connections_apply_same_revision_once(postgres_engine, same_key):
    with rental_scene(postgres_engine) as (db, context, asset_id):
        first = request(asset_id)
        second = first if same_key else request(asset_id)
        barrier = Barrier(2)

        def invoke(payload):
            with Session(postgres_engine) as connection:
                barrier.wait(timeout=10)
                try:
                    return RentalService(connection).renew(
                        payload,
                        actor_user_id=context.account.id,
                        request_id="rental-concurrent-test",
                    )
                except RentalConflict:
                    return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = list(pool.map(invoke, [first, second]))
        if same_key:
            assert sorted([a.replayed, b.replayed]) == [False, True]
            assert a.items == b.items
        else:
            assert [a, b].count("conflict") == 1
        db.expire_all()
        assert db.get(Asset, asset_id).rental_expires_on == date(2027, 2, 28)
        assert (
            db.scalar(
                select(func.count())
                .select_from(AssetRentalRenewal)
                .where(AssetRentalRenewal.asset_id == asset_id)
            )
            == 1
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.resource_id == asset_id, AuditEvent.action == "asset.rental.renew"
                )
            )
            == 1
        )


@pytest.mark.parametrize("change", ["revoke", "disable", "force_password"])
def test_replay_rechecks_live_authority_even_with_cached_actor(postgres_engine, change):
    with rental_scene(postgres_engine) as (db, context, asset_id):
        payload = request(asset_id)
        RentalService(db).renew(
            payload, actor_user_id=context.account.id, request_id="rental-authority-test"
        )
        db.get(UserAccount, context.account.id)
        db.commit()
        with Session(postgres_engine) as other:
            if change == "revoke":
                other.execute(delete(UserRole).where(UserRole.user_id == context.account.id))
            elif change == "disable":
                other.execute(
                    update(UserAccount)
                    .where(UserAccount.id == context.account.id)
                    .values(status="disabled")
                )
            else:
                other.execute(
                    update(UserAccount)
                    .where(UserAccount.id == context.account.id)
                    .values(force_password_change=True)
                )
            other.commit()
        with pytest.raises(ProblemError) as raised:
            RentalService(db).renew(
                payload, actor_user_id=context.account.id, request_id="rental-denied-replay-test"
            )
        assert raised.value.status == 403


@pytest.mark.parametrize("operation", ["renew", "return"])
def test_failed_required_audit_rolls_back_lease_and_operation(
    postgres_engine, monkeypatch, operation
):
    with rental_scene(postgres_engine) as (db, context, asset_id):

        def fail_audit(*args, **kwargs):
            raise RuntimeError("required audit unavailable")

        monkeypatch.setattr(AuditService, "append", fail_audit)
        with pytest.raises(RuntimeError, match="required audit unavailable"):
            if operation == "renew":
                RentalService(db).renew(
                    request(asset_id),
                    actor_user_id=context.account.id,
                    request_id="rental-audit-test",
                )
            else:
                RentalService(db).return_rental(
                    asset_id,
                    RentalReturnRequest(returned_on=date(2026, 1, 2), expected_rental_revision=0),
                    actor_user_id=context.account.id,
                    request_id="rental-audit-test",
                )
        with Session(postgres_engine) as fresh:
            asset = fresh.get(Asset, asset_id)
            assert asset.rental_revision == 0
            assert asset.rental_returned_on is None
            assert asset.rental_expires_on == date(2027, 1, 31)
            assert (
                fresh.scalar(
                    select(func.count())
                    .select_from(AssetRentalRenewal)
                    .where(AssetRentalRenewal.asset_id == asset_id)
                )
                == 0
            )
            assert (
                fresh.scalar(
                    select(func.count())
                    .select_from(AssetRentalOperation)
                    .where(AssetRentalOperation.actor_user_id == context.account.id)
                )
                == 0
            )


def test_replay_returns_saved_result_after_later_renewal(postgres_engine):
    with rental_scene(postgres_engine) as (db, context, asset_id):
        first_request = request(asset_id)
        first = RentalService(db).renew(
            first_request, actor_user_id=context.account.id, request_id="rental-first-test"
        )
        RentalService(db).renew(
            request(asset_id, revision=1),
            actor_user_id=context.account.id,
            request_id="rental-second-test",
        )
        replay = RentalService(db).renew(
            first_request, actor_user_id=context.account.id, request_id="rental-replay-test"
        )
        assert replay.items == first.items
        assert replay.items[0].rental_expires_on == date(2027, 2, 28)
        assert replay.items[0].rental_revision == 1
