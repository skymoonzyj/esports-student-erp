from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from io import BytesIO
from threading import Barrier, Event, Lock
from uuid import UUID, uuid4

import pytest
from openpyxl import load_workbook
from sqlalchemy import Engine, delete, event, select, text
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.auth.models import AccountLoginIdentifier, UserAccount
from app.housing.errors import HousingResourceNotFound
from app.housing.models import HousingBuilding, HousingFloor, HousingRoom, HousingStay
from app.housing.reporting_models import HousingReportBatch, HousingReportBatchScope
from app.housing.reporting_service import (
    GenerateReportCommand,
    HousingReportingService,
    HousingReportStateConflict,
    MarkSubmittedCommand,
    ReportBatchQuery,
    ReportBatchSummary,
    VoidReportCommand,
)
from app.housing.reporting_snapshot import SealedSnapshot, open_projection
from app.identity.models import (
    GuardianRelationship,
    Person,
    StudentProfile,
)
from app.rbac.models import Permission, Role, UserPermissionOverride, UserRole
from app.security.encryption import EnvelopeCipher
from tests.account_identifiers import add_test_primary_login_identifier

NOW = datetime(2026, 8, 7, 12, tzinfo=UTC)
RANGE_START = datetime(2026, 8, 6, tzinfo=UTC)
RANGE_END = datetime(2026, 8, 8, tzinfo=UTC)


def _identity_for(birth_date: date, *, male: bool) -> str:
    body = f"110105{birth_date:%Y%m%d}00{1 if male else 2}"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    checks = ("1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2")
    weighted = sum(int(digit) * weight for digit, weight in zip(body, weights))
    return body + checks[weighted % 11]


@dataclass(frozen=True, slots=True)
class ConcurrentContext:
    engine: Engine
    cipher: EnvelopeCipher
    actor_id: UUID
    resident_id: UUID
    resident_account_id: UUID
    guardian_id: UUID
    building_id: UUID
    floor_id: UUID


def _seed_context(postgres_engine: Engine) -> ConcurrentContext:
    cipher = EnvelopeCipher(b"c" * 32)
    actor_id = uuid4()
    resident_id = uuid4()
    resident_account_id = uuid4()
    guardian_id = uuid4()
    building_id = uuid4()
    floor_id = uuid4()
    room_id = uuid4()
    marker = uuid4().hex[:8].upper()
    resident_birth = date(2010, 1, 2)
    guardian_birth = date(1980, 2, 3)
    with Session(postgres_engine) as db:
        actor_person = Person(
            id=uuid4(),
            full_name="并发报送操作员",
            identity_ciphertext=f"actor-{uuid4().hex}",
            identity_digest=f"actor-{uuid4().hex}",
            sex="female",
            date_of_birth=date(1988, 1, 2),
        )
        actor = UserAccount(
            id=actor_id,
            person_id=actor_person.id,
            login_phone_digest=f"login-{uuid4().hex}",
            email_normalized=f"{uuid4().hex}@concurrency.example.test",
            password_hash="concurrency-password-hash",
            status="active",
            force_password_change=False,
            failed_login_count=0,
        )
        db.add(actor_person)
        db.flush()
        db.add(actor)
        add_test_primary_login_identifier(db, actor)
        admin_role_id = db.scalar(select(Role.id).where(Role.key == "admin"))
        assert admin_role_id is not None
        db.add(UserRole(user_id=actor_id, role_id=admin_role_id))
        db.flush()

        building = HousingBuilding(
            id=building_id,
            code=f"C4{marker}",
            name=f"并发报送楼{marker}",
            display_order=1,
            is_active=True,
        )
        floor = HousingFloor(
            id=floor_id,
            building_id=building_id,
            number=2,
            name=f"并发二层{marker}",
            display_order=2,
            is_active=True,
        )
        room = HousingRoom(
            id=room_id,
            building_id=building_id,
            floor_id=floor_id,
            room_number=f"{marker}-201",
            normalized_key=f"{marker}-201",
            room_type="custom",
            capacity=10,
            verification_status="verified",
            service_status="available",
            version=1,
        )
        db.add(building)
        db.flush()
        db.add(floor)
        db.flush()
        db.add(room)
        db.flush()

        resident = Person(
            id=resident_id,
            full_name="修改前住户",
            identity_ciphertext=cipher.encrypt(
                _identity_for(resident_birth, male=True),
                field_name="identity_number",
            ),
            identity_digest=f"resident-{uuid4().hex}",
            phone_ciphertext=cipher.encrypt("13800138000", field_name="phone"),
            phone_digest=f"resident-phone-{uuid4().hex}",
            sex="male",
            date_of_birth=resident_birth,
        )
        guardian = Person(
            id=guardian_id,
            full_name="修改前监护人",
            identity_ciphertext=cipher.encrypt(
                _identity_for(guardian_birth, male=False),
                field_name="identity_number",
            ),
            identity_digest=f"guardian-{uuid4().hex}",
            phone_ciphertext=cipher.encrypt("13900139000", field_name="phone"),
            phone_digest=f"guardian-phone-{uuid4().hex}",
            sex="female",
            date_of_birth=guardian_birth,
        )
        db.add_all([resident, guardian])
        db.flush()
        db.add(
            UserAccount(
                id=resident_account_id,
                person_id=resident_id,
                login_phone_digest=None,
                email_normalized=f"{uuid4().hex}@resident.concurrency.example.test",
                password_hash="resident-concurrency-password-hash",
                status="active",
                force_password_change=False,
                failed_login_count=0,
            )
        )
        db.flush()
        db.add(
            AccountLoginIdentifier(
                account_id=resident_account_id,
                kind="phone",
                identifier_digest=uuid4().hex + uuid4().hex,
                verified_at=NOW,
                verification_source="test_verified",
                is_primary=True,
            )
        )
        db.add(StudentProfile(person_id=resident_id, student_number=f"S{marker}"))
        db.add(
            GuardianRelationship(
                id=uuid4(),
                guardian_person_id=guardian_id,
                student_person_id=resident_id,
                relationship="母亲",
                is_primary=True,
                valid_from=date(2010, 1, 2),
            )
        )
        db.flush()
        db.add(
            HousingStay(
                id=uuid4(),
                person_id=resident_id,
                room_id=room_id,
                stay_purpose="permanent",
                duration_mode="long_term",
                planned_check_in_at=RANGE_START - timedelta(days=1),
                actual_check_in_at=RANGE_START,
                status="checked_in",
                room_name_snapshot=room.room_number,
                floor_name_snapshot=floor.name,
                building_name_snapshot=building.name,
                created_by_user_id=actor_id,
                checked_in_by_user_id=actor_id,
                version=1,
            )
        )
        db.commit()
    return ConcurrentContext(
        engine=postgres_engine,
        cipher=cipher,
        actor_id=actor_id,
        resident_id=resident_id,
        resident_account_id=resident_account_id,
        guardian_id=guardian_id,
        building_id=building_id,
        floor_id=floor_id,
    )


def _cleanup_context(context: ConcurrentContext) -> None:
    with Session(context.engine) as db, db.begin():
        database_name = db.scalar(select(text("current_database()")))
        assert isinstance(database_name, str) and (
            database_name == "erp_e2e" or database_name.endswith("_test")
        )

        actor_person_id = db.scalar(
            select(UserAccount.person_id).where(UserAccount.id == context.actor_id)
        )
        batch_ids = set(
            db.scalars(
                select(HousingReportBatch.id).where(
                    HousingReportBatch.created_by_user_id == context.actor_id
                )
            )
        )

        db.execute(text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update"))
        db.execute(delete(AuditEvent).where(AuditEvent.actor_user_id == context.actor_id))
        db.execute(text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update"))

        if batch_ids:
            db.execute(
                text(
                    "ALTER TABLE housing_report_batch_scopes DISABLE TRIGGER "
                    "trg_housing_report_batch_scopes_immutable"
                )
            )
            db.execute(
                delete(HousingReportBatchScope).where(
                    HousingReportBatchScope.batch_id.in_(batch_ids)
                )
            )
            db.execute(
                text(
                    "ALTER TABLE housing_report_batch_scopes ENABLE TRIGGER "
                    "trg_housing_report_batch_scopes_immutable"
                )
            )

            db.execute(
                text(
                    "ALTER TABLE housing_report_batches DISABLE TRIGGER "
                    "trg_housing_report_batches_no_delete"
                )
            )
            remaining_batch_ids = set(batch_ids)
            while remaining_batch_ids:
                referenced_batch_ids = set(
                    db.scalars(
                        select(HousingReportBatch.corrects_batch_id).where(
                            HousingReportBatch.id.in_(remaining_batch_ids),
                            HousingReportBatch.corrects_batch_id.is_not(None),
                        )
                    )
                )
                leaf_batch_ids = remaining_batch_ids - referenced_batch_ids
                assert leaf_batch_ids, "housing report correction cycle in test data"
                db.execute(
                    delete(HousingReportBatch).where(HousingReportBatch.id.in_(leaf_batch_ids))
                )
                remaining_batch_ids -= leaf_batch_ids
            db.execute(
                text(
                    "ALTER TABLE housing_report_batches ENABLE TRIGGER "
                    "trg_housing_report_batches_no_delete"
                )
            )

        db.execute(
            delete(UserPermissionOverride).where(UserPermissionOverride.user_id == context.actor_id)
        )
        db.execute(
            text("ALTER TABLE housing_stays DISABLE TRIGGER trg_housing_stays_void_fact_immutable")
        )
        db.execute(delete(HousingStay).where(HousingStay.person_id == context.resident_id))
        db.execute(
            text("ALTER TABLE housing_stays ENABLE TRIGGER trg_housing_stays_void_fact_immutable")
        )
        db.execute(
            delete(GuardianRelationship).where(
                (GuardianRelationship.student_person_id == context.resident_id)
                | (GuardianRelationship.guardian_person_id == context.guardian_id)
            )
        )
        db.execute(delete(StudentProfile).where(StudentProfile.person_id == context.resident_id))
        db.execute(
            delete(AccountLoginIdentifier).where(
                AccountLoginIdentifier.account_id == context.resident_account_id
            )
        )
        db.execute(delete(UserRole).where(UserRole.user_id == context.actor_id))
        db.execute(
            delete(UserAccount).where(
                UserAccount.id.in_((context.actor_id, context.resident_account_id))
            )
        )
        db.execute(delete(HousingRoom).where(HousingRoom.floor_id == context.floor_id))
        db.execute(delete(HousingFloor).where(HousingFloor.id == context.floor_id))
        db.execute(delete(HousingBuilding).where(HousingBuilding.id == context.building_id))
        person_ids = {context.resident_id, context.guardian_id}
        if actor_person_id is not None:
            person_ids.add(actor_person_id)
        db.execute(delete(Person).where(Person.id.in_(person_ids)))


@pytest.fixture
def concurrent_context(postgres_engine: Engine) -> Iterator[ConcurrentContext]:
    context = _seed_context(postgres_engine)
    try:
        yield context
    finally:
        _cleanup_context(context)


@pytest.fixture
def reporting_db(postgres_engine: Engine) -> Iterator[Session]:
    connection = postgres_engine.connect().execution_options(isolation_level="REPEATABLE READ")
    outer_transaction = connection.begin()
    with Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as session:
        try:
            yield session
        finally:
            if outer_transaction.is_active:
                outer_transaction.rollback()
    connection.close()


def _service(db: Session, context: ConcurrentContext) -> HousingReportingService:
    return HousingReportingService(
        db,
        context.cipher,
        context.actor_id,
        NOW,
        f"concurrency-{uuid4().hex}",
    )


def _generate(context: ConcurrentContext, **overrides: object) -> ReportBatchSummary:
    values: dict[str, object] = {
        "range_start": RANGE_START,
        "range_end": RANGE_END,
        "scope_mode": "floors",
        "floor_ids": (context.floor_id,),
        "purpose": "并发实名报送",
    }
    values.update(overrides)
    with Session(context.engine, expire_on_commit=False) as db:
        return _service(db, context).generate(
            GenerateReportCommand(**values)  # type: ignore[arg-type]
        )


def _add_voidable_history(context: ConcurrentContext) -> UUID:
    with Session(context.engine) as db, db.begin():
        current = db.scalar(select(HousingStay).where(HousingStay.person_id == context.resident_id))
        current.actual_check_in_at = RANGE_START + timedelta(hours=2)
        history = HousingStay(
            person_id=current.person_id,
            room_id=current.room_id,
            stay_purpose="permanent",
            duration_mode="long_term",
            planned_check_in_at=RANGE_START - timedelta(days=2),
            actual_check_in_at=RANGE_START - timedelta(days=2),
            actual_check_out_at=RANGE_START + timedelta(hours=1),
            status="checked_out",
            room_name_snapshot=current.room_name_snapshot,
            floor_name_snapshot=current.floor_name_snapshot,
            building_name_snapshot=current.building_name_snapshot,
            created_by_user_id=context.actor_id,
            checked_in_by_user_id=context.actor_id,
            checked_out_by_user_id=context.actor_id,
        )
        db.add(history)
        db.flush()
        return history.id


def _void_history_fact(context: ConcurrentContext, stay_id: UUID) -> None:
    with Session(context.engine) as db, db.begin():
        history = db.get(HousingStay, stay_id)
        history.voided_at = NOW
        history.voided_by_user_id = context.actor_id
        history.void_reason_ciphertext = context.cipher.encrypt(
            "测试历史无效", field_name="housing_stay_void_reason"
        )
        history.version += 1


def _report_facts_and_projection(db, context, batch_id):
    batch = db.get(HousingReportBatch, batch_id)
    return (
        (
            batch.snapshot_ciphertext,
            batch.snapshot_digest,
            batch.snapshot_key_version,
            batch.resident_count,
            batch.stay_row_count,
            batch.guardian_row_count,
        ),
        open_projection(
            SealedSnapshot(
                ciphertext=batch.snapshot_ciphertext,
                key_version=batch.snapshot_key_version,
                digest=batch.snapshot_digest,
            ),
            cipher=context.cipher,
        ),
    )


def _parsed_download(db, context, batch_id):
    download = _service(db, context).download(batch_id, "xlsx")
    workbook = load_workbook(BytesIO(download.content), read_only=True, data_only=True)
    try:
        return {sheet.title: tuple(sheet.iter_rows(values_only=True)) for sheet in workbook}
    finally:
        workbook.close()


def test_report_snapshot_survives_void_and_new_generation_excludes_it(concurrent_context):
    context = concurrent_context
    voided_stay_id = _add_voidable_history(context)
    old = _generate(context)
    with Session(context.engine) as db:
        before, projection_before = _report_facts_and_projection(db, context, old.id)
        before_download = _parsed_download(db, context, old.id)
        assert voided_stay_id in {row.stay_id for row in projection_before.residents}
        assert before[3:] == (1, 2, 2)
    _void_history_fact(context, voided_stay_id)
    new = _generate(context)
    with Session(context.engine) as db:
        old_ciphertext_digest_counts, _ = _report_facts_and_projection(db, context, old.id)
        parsed_old_download = _parsed_download(db, context, old.id)
        assert old_ciphertext_digest_counts == before
        assert parsed_old_download == before_download
        new_facts, new_projection = _report_facts_and_projection(db, context, new.id)
        assert voided_stay_id not in {row.stay_id for row in new_projection.residents}
        assert new_facts[3:] == (1, 1, 1)
        assert len(new_projection.residents) == new_facts[4]
        assert len(new_projection.guardians) == new_facts[5]


def test_generation_repeatable_read_snapshot_boundary_survives_concurrent_void(
    concurrent_context,
    monkeypatch,
):
    context = concurrent_context
    voided_stay_id = _add_voidable_history(context)
    snapshot_read, void_committed = Event(), Event()
    from app.housing.reporting_projection import HousingReportProjectionBuilder

    original = HousingReportProjectionBuilder._guardian_candidates

    def pause_after_candidates(builder, student_ids):
        snapshot_read.set()
        assert void_committed.wait(10), "void transaction did not complete"
        return original(builder, student_ids)

    monkeypatch.setattr(
        HousingReportProjectionBuilder, "_guardian_candidates", pause_after_candidates
    )

    def void_after_snapshot():
        assert snapshot_read.wait(10), "report snapshot was not established"
        try:
            _void_history_fact(context, voided_stay_id)
        finally:
            void_committed.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        generation = pool.submit(_generate, context)
        mutation = pool.submit(void_after_snapshot)
        old = generation.result(timeout=20)
        mutation.result(timeout=20)
    monkeypatch.setattr(HousingReportProjectionBuilder, "_guardian_candidates", original)
    new = _generate(context)
    with Session(context.engine) as db:
        old_facts, old_projection = _report_facts_and_projection(db, context, old.id)
        new_facts, new_projection = _report_facts_and_projection(db, context, new.id)
        assert voided_stay_id in {row.stay_id for row in old_projection.residents}
        assert voided_stay_id not in {row.stay_id for row in new_projection.residents}
        assert old_facts[3:] == (1, 2, 2)
        assert new_facts[3:] == (1, 1, 1)


def test_generation_snapshot_is_complete_before_or_after_concurrent_identity_change(
    concurrent_context: ConcurrentContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = concurrent_context
    resident_read = Event()
    allow_guardian_read = Event()
    original = __import__(
        "app.housing.reporting_service",
        fromlist=["HousingReportProjectionBuilder"],
    ).HousingReportProjectionBuilder._guardian_candidates

    def pause_before_guardians(
        builder: object,
        student_ids: tuple[UUID, ...],
    ) -> object:
        resident_read.set()
        assert allow_guardian_read.wait(10), "identity mutation did not finish"
        return original(builder, student_ids)

    reporting_module = __import__(
        "app.housing.reporting_service",
        fromlist=["HousingReportProjectionBuilder"],
    )
    monkeypatch.setattr(
        reporting_module.HousingReportProjectionBuilder,
        "_guardian_candidates",
        pause_before_guardians,
    )

    def generate() -> ReportBatchSummary:
        return _generate(context)

    def mutate_identity() -> None:
        assert resident_read.wait(10), "generation never read the resident"
        try:
            with Session(context.engine) as db:
                resident = db.get(Person, context.resident_id)
                guardian = db.get(Person, context.guardian_id)
                assert resident is not None and guardian is not None
                resident.full_name = "修改后住户"
                resident.phone_ciphertext = context.cipher.encrypt(
                    "13700137000",
                    field_name="phone",
                )
                guardian.full_name = "修改后监护人"
                guardian.phone_ciphertext = context.cipher.encrypt(
                    "13600136000",
                    field_name="phone",
                )
                db.commit()
        finally:
            allow_guardian_read.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        generated_future = pool.submit(generate)
        mutation_future = pool.submit(mutate_identity)
        generated = generated_future.result(timeout=20)
        mutation_future.result(timeout=20)

    with Session(context.engine) as db:
        batch = db.get(HousingReportBatch, generated.id)
        assert batch is not None
        assert batch.snapshot_ciphertext is not None
        assert batch.snapshot_key_version is not None
        assert batch.snapshot_digest is not None
        projection = open_projection(
            SealedSnapshot(
                ciphertext=batch.snapshot_ciphertext,
                key_version=batch.snapshot_key_version,
                digest=batch.snapshot_digest,
            ),
            cipher=context.cipher,
        )
    observed = (
        projection.residents[0].full_name,
        projection.guardians[0].guardian_name,
    )
    assert observed in {
        ("修改前住户", "修改前监护人"),
        ("修改后住户", "修改后监护人"),
    }


def test_concurrent_submit_and_void_have_one_success_and_one_usable_conflict(
    concurrent_context: ConcurrentContext,
) -> None:
    context = concurrent_context
    postgres_engine = context.engine
    generated = _generate(context)
    start = Barrier(2)
    attempt_lock = Lock()
    second_lock_attempt = Event()
    attempts: dict[int, int] = {}
    attempt_count = 0

    def before_cursor_execute(
        connection: object,
        cursor: object,
        statement: str,
        parameters: object,
        execution_context: object,
        executemany: bool,
    ) -> None:
        nonlocal attempt_count
        del connection, cursor, parameters, executemany
        if "FOR UPDATE" not in statement.upper():
            return
        with attempt_lock:
            attempt_count += 1
            attempts[id(execution_context)] = attempt_count
            if attempt_count == 2:
                second_lock_attempt.set()

    def after_cursor_execute(
        connection: object,
        cursor: object,
        statement: str,
        parameters: object,
        execution_context: object,
        executemany: bool,
    ) -> None:
        del connection, cursor, parameters, executemany
        if "FOR UPDATE" in statement.upper() and attempts[id(execution_context)] == 1:
            assert second_lock_attempt.wait(10), "second state action did not contend"

    event.listen(postgres_engine, "before_cursor_execute", before_cursor_execute)
    event.listen(postgres_engine, "after_cursor_execute", after_cursor_execute)

    def submit() -> tuple[str, bool]:
        with Session(postgres_engine, expire_on_commit=False) as db:
            start.wait(timeout=10)
            try:
                summary = _service(db, context).mark_submitted(
                    generated.id,
                    MarkSubmittedCommand(
                        submitted_at=NOW - timedelta(minutes=1),
                        note=None,
                        expected_version=1,
                    ),
                )
                return summary.status, True
            except HousingReportStateConflict:
                return "conflict", db.scalar(select(1)) == 1

    def void() -> tuple[str, bool]:
        with Session(postgres_engine, expire_on_commit=False) as db:
            start.wait(timeout=10)
            try:
                summary = _service(db, context).void(
                    generated.id,
                    VoidReportCommand(reason="并发作废", expected_version=1),
                )
                return summary.status, True
            except HousingReportStateConflict:
                return "conflict", db.scalar(select(1)) == 1

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(submit), pool.submit(void)]
            results = [future.result(timeout=20) for future in futures]
    finally:
        event.remove(postgres_engine, "before_cursor_execute", before_cursor_execute)
        event.remove(postgres_engine, "after_cursor_execute", after_cursor_execute)

    assert attempt_count == 2
    assert sorted(result[0] for result in results) in (
        ["conflict", "marked_submitted"],
        ["conflict", "voided"],
    )
    assert all(usable for _, usable in results)
    with Session(postgres_engine) as db:
        persisted = db.get(HousingReportBatch, generated.id)
        assert persisted is not None
        assert persisted.version == 2


def test_concurrent_corrections_create_at_most_one_and_conflict_session_is_usable(
    concurrent_context: ConcurrentContext,
) -> None:
    context = concurrent_context
    postgres_engine = context.engine
    predecessor = _generate(context)
    with Session(postgres_engine, expire_on_commit=False) as db:
        _service(db, context).void(
            predecessor.id,
            VoidReportCommand(reason="原名单有误", expected_version=1),
        )

    start = Barrier(2)
    attempt_lock = Lock()
    second_lock_attempt = Event()
    attempts: dict[int, int] = {}
    attempt_count = 0

    def before_cursor_execute(
        connection: object,
        cursor: object,
        statement: str,
        parameters: object,
        execution_context: object,
        executemany: bool,
    ) -> None:
        nonlocal attempt_count
        del connection, cursor, parameters, executemany
        if "FOR UPDATE" not in statement.upper():
            return
        with attempt_lock:
            attempt_count += 1
            attempts[id(execution_context)] = attempt_count
            if attempt_count == 2:
                second_lock_attempt.set()

    def after_cursor_execute(
        connection: object,
        cursor: object,
        statement: str,
        parameters: object,
        execution_context: object,
        executemany: bool,
    ) -> None:
        del connection, cursor, parameters, executemany
        if "FOR UPDATE" in statement.upper() and attempts[id(execution_context)] == 1:
            assert second_lock_attempt.wait(10), "second correction did not contend"

    event.listen(postgres_engine, "before_cursor_execute", before_cursor_execute)
    event.listen(postgres_engine, "after_cursor_execute", after_cursor_execute)

    def correct() -> tuple[str, bool]:
        with Session(postgres_engine, expire_on_commit=False) as db:
            start.wait(timeout=10)
            try:
                summary = _service(db, context).generate(
                    GenerateReportCommand(
                        range_start=RANGE_START,
                        range_end=RANGE_END,
                        scope_mode="floors",
                        floor_ids=(context.floor_id,),
                        purpose="并发更正",
                        corrects_batch_id=predecessor.id,
                    )
                )
                return str(summary.id), True
            except HousingReportStateConflict:
                return "conflict", db.scalar(select(1)) == 1

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(correct), pool.submit(correct)]
            results = [future.result(timeout=20) for future in futures]
    finally:
        event.remove(postgres_engine, "before_cursor_execute", before_cursor_execute)
        event.remove(postgres_engine, "after_cursor_execute", after_cursor_execute)

    assert attempt_count == 2
    assert [result for result, _ in results].count("conflict") == 1
    assert all(usable for _, usable in results)
    with Session(postgres_engine) as db:
        correction_ids = list(
            db.scalars(
                select(HousingReportBatch.id).where(
                    HousingReportBatch.corrects_batch_id == predecessor.id
                )
            )
        )
    assert len(correction_ids) == 1


def test_business_identity_change_does_not_change_historical_service_download(
    concurrent_context: ConcurrentContext,
    reporting_db: Session,
) -> None:
    context = concurrent_context
    postgres_engine = context.engine
    generated = _generate(context)
    with Session(postgres_engine) as mutation_db:
        resident = mutation_db.get(Person, context.resident_id)
        guardian = mutation_db.get(Person, context.guardian_id)
        assert resident is not None and guardian is not None
        resident.full_name = "下载前修改住户"
        guardian.full_name = "下载前修改监护人"
        mutation_db.commit()

    download = _service(reporting_db, context).download(generated.id, "xlsx")
    workbook = load_workbook(BytesIO(download.content), read_only=True, data_only=True)
    residents = list(workbook["入住名单"].iter_rows(values_only=True))
    guardians = list(workbook["未成年监护人"].iter_rows(values_only=True))
    resident_name_index = residents[0].index("姓名")
    guardian_name_index = guardians[0].index("监护人姓名")
    assert residents[1][resident_name_index] == "修改前住户"
    assert guardians[1][guardian_name_index] == "修改前监护人"
    workbook.close()


def test_list_then_revoke_in_other_session_blocks_next_download(
    concurrent_context: ConcurrentContext,
) -> None:
    context = concurrent_context
    postgres_engine = context.engine
    generated = _generate(context)
    with Session(postgres_engine, expire_on_commit=False) as list_db:
        page = _service(list_db, context).list(ReportBatchQuery())
    assert generated.id in {item.id for item in page.items}

    with Session(postgres_engine) as revoke_db:
        permission_id = revoke_db.scalar(
            select(Permission.id).where(Permission.key == "housing.reports.export_sensitive")
        )
        assert permission_id is not None
        revoke_db.add(
            UserPermissionOverride(
                user_id=context.actor_id,
                permission_id=permission_id,
                effect="deny",
                scope_kind="floor",
                scope_id=context.floor_id,
                created_by_user_id=context.actor_id,
                grant_reason="并发验收撤权",
            )
        )
        revoke_db.commit()

    with (
        Session(postgres_engine, expire_on_commit=False) as download_db,
        pytest.raises(HousingResourceNotFound) as caught,
    ):
        _service(download_db, context).download(generated.id, "xlsx")
    assert caught.value.code == "housing_resource_not_found"


def test_locked_action_refreshes_batch_cached_before_competing_commit(
    concurrent_context: ConcurrentContext,
) -> None:
    context = concurrent_context
    postgres_engine = context.engine
    generated = _generate(context)

    with Session(postgres_engine, expire_on_commit=False) as cached_db:
        cached = cached_db.get(HousingReportBatch, generated.id)
        assert cached is not None
        assert (cached.status, cached.version) == ("generated", 1)

        with Session(postgres_engine, expire_on_commit=False) as competing_db:
            submitted = _service(competing_db, context).mark_submitted(
                generated.id,
                MarkSubmittedCommand(
                    submitted_at=NOW - timedelta(minutes=1),
                    note=None,
                    expected_version=1,
                ),
            )
        assert submitted.version == 2

        with pytest.raises(HousingReportStateConflict):
            _service(cached_db, context).void(
                generated.id,
                VoidReportCommand(
                    reason="缓存版本不得覆盖竞争提交",
                    expected_version=1,
                ),
            )
        assert cached_db.scalar(select(1)) == 1
        cached_db.expire_all()
        refreshed = cached_db.get(HousingReportBatch, generated.id)
        assert refreshed is not None
        assert (refreshed.status, refreshed.version) == ("marked_submitted", 2)
