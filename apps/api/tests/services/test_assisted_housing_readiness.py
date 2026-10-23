from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.auth.models import UserAccount
from app.housing.errors import (
    HousingAccountRequired,
    HousingCapacityConflict,
    HousingGenderConflict,
    HousingPersonTimeConflict,
    HousingProfileIncomplete,
    HousingResourceNotFound,
    HousingVerifiedEmailRequired,
    MinorGuardianRequired,
)
from app.housing.occupancy_service import HousingOccupancyService
from app.housing.schemas import (
    CheckoutConfirmation,
    CheckoutRequest,
    ConfirmCheckInRequest,
    StayUpdate,
    TransferStayRequest,
)
from app.security.encryption import EnvelopeCipher, LookupDigest
from tests.db.test_housing_stay_lifecycle import _allow, _input, _room
from tests.services.test_assisted_eligibility import approved_minor
from tests.services.test_assisted_eligibility import platform_users as _platform_users
from tests.services.test_personnel_approval import approvals
from tests.services.test_personnel_intake import actor, intake
from tests.services.test_personnel_intake import service as intake_service

NOW = datetime(2026, 10, 18, 12, tzinfo=UTC)
CIPHER = EnvelopeCipher(bytes(range(32)))
LOOKUP = LookupDigest(bytes(range(32)))


@pytest.fixture
def platform_users(db):
    return _platform_users.__wrapped__(db)


def test_legacy_draft_candidate_is_truthfully_not_ready_and_cannot_create_stay(db, platform_users):
    from app.housing.models import HousingStay
    from app.identity.models import Person, StudentProfile
    from tests.api.test_housing_occupancy import verified_housing_emails

    verified_housing_emails.__wrapped__(db, platform_users)
    person_id = platform_users["people"]["self_student"].id
    person = db.get(Person, person_id)
    assert person is not None
    assert db.get(StudentProfile, person_id).status == "draft"
    room = _room(db)
    db.commit()
    service = HousingOccupancyService(db)
    options = service.list_resident_options(
        room.id,
        person.full_name,
        cipher=CIPHER,
        lookup=LOOKUP,
        authorize_room=_allow,
        reference_date=NOW.date(),
    )
    assert len(options) == 1
    assert options[0].account_ready is True
    assert options[0].guardian_ready is True
    assert options[0].profile_ready is False
    assert options[0].housing_ready is False
    with pytest.raises(HousingProfileIncomplete):
        service.create_stay(
            room.id,
            _input(person_id, start=NOW),
            actor_user_id=platform_users["accounts"]["admin"].id,
            occurred_at=NOW,
            authorize_room=_allow,
        )
    assert list(db.scalars(select(HousingStay).where(HousingStay.person_id == person_id))) == []


@pytest.mark.parametrize(
    "kind,person_type", [("student", "student"), ("player", "player"), ("both", "student_player")]
)
def test_approved_minor_without_accounts_can_complete_housing_lifecycle(
    db, platform_users, kind, person_type
):
    room, target = _room(db), _room(db)
    result, _, edges = approved_minor(db, platform_users, kind=kind)
    actor_id = platform_users["accounts"]["admin"].id
    service = HousingOccupancyService(db)
    # Search the actual approved name, not a fixture-dependent assumed label.
    from app.identity.models import Person

    name = db.get(Person, result.person_id).full_name
    options = service.list_resident_options(
        room.id,
        name,
        cipher=CIPHER,
        lookup=LOOKUP,
        authorize_room=_allow,
        reference_date=date(2026, 10, 18),
    )
    assert len(options) == 1
    assert options[0].housing_ready is True
    assert options[0].account_ready is False
    assert options[0].account_status == "not_activated"
    assert options[0].person_type == person_type
    from app.housing.resident_readiness import HousingResidentReadinessPolicy

    service = HousingOccupancyService(
        db, resident_readiness=HousingResidentReadinessPolicy(db, cipher=CIPHER, lookup=LOOKUP)
    )
    stay = service.create_stay(
        room.id,
        _input(result.person_id, start=NOW + timedelta(days=1)),
        actor_user_id=actor_id,
        occurred_at=NOW,
        authorize_room=_allow,
    )
    assert stay.status == "planned"
    stay = service.update_reservation(
        stay.id,
        StayUpdate(
            version=stay.version,
            planned_check_in_at=NOW + timedelta(days=1),
        ),
        actor_user_id=actor_id,
        occurred_at=NOW,
        authorize_room=_allow,
    )
    stay = service.confirm_check_in(
        stay.id,
        ConfirmCheckInRequest(version=stay.version, actual_check_in_at=NOW + timedelta(days=1)),
        actor_user_id=actor_id,
        occurred_at=NOW + timedelta(days=1),
        authorize_room=_allow,
    )
    assert stay.status == "checked_in"
    _, moved, _ = service.transfer(
        stay.id,
        TransferStayRequest(
            version=stay.version,
            target_room_id=target.id,
            transferred_at=NOW + timedelta(days=2),
            reason="调整房间",
        ),
        actor_user_id=actor_id,
        occurred_at=NOW + timedelta(days=2),
        authorize_source=_allow,
        authorize_target=_allow,
    )
    moved = service.request_checkout(
        moved.id,
        CheckoutRequest(version=moved.version, actual_check_out_at=NOW + timedelta(days=3)),
        actor_user_id=actor_id,
        occurred_at=NOW + timedelta(days=3),
        authorize_room=_allow,
    )
    assert moved.status == "checkout_pending"
    assert (
        service.room_projections([target.id], NOW + timedelta(days=3))[target.id]
        .current_stays[0]
        .person_id
        == result.person_id
    )
    moved = service.confirm_checkout(
        moved.id,
        CheckoutConfirmation(version=moved.version),
        actor_user_id=actor_id,
        occurred_at=NOW + timedelta(days=3),
        authorize_room=_allow,
    )
    assert moved.status == "checked_out"
    backfilled = service.backfill_existing_stay(
        room.id,
        _input(result.person_id, start=NOW + timedelta(days=4)),
        actor_user_id=actor_id,
        occurred_at=NOW + timedelta(days=4),
        authorize_room_and_stay=_allow,
    )
    assert backfilled.status == "checked_in"
    from app.housing.reporting_projection import HousingReportProjectionBuilder, ProjectionRequest
    reported = HousingReportProjectionBuilder(db, CIPHER, lookup=LOOKUP).build(ProjectionRequest(generated_at=NOW + timedelta(days=4), range_start=NOW, range_end=NOW + timedelta(days=5), floor_ids=(room.floor_id,)))
    assert reported.issues == ()
    assert reported.projection.resident_count == 1
    assert reported.projection.stay_row_count == 2
    assert {row.person_type for row in reported.projection.residents} == {person_type}
    assert not list(
        db.scalars(
            select(UserAccount).where(
                UserAccount.person_id.in_([result.person_id, edges[0].guardian_person_id])
            )
        )
    )


def test_invalid_profile_keeps_actual_occupancy_and_checkout(db, platform_users):
    from app.housing.reporting_projection import HousingReportProjectionBuilder, ProjectionRequest
    from app.housing.resident_readiness import HousingResidentReadinessPolicy

    room, target = _room(db), _room(db)
    result, _, edges = approved_minor(db, platform_users, kind="player")
    actor_id = platform_users["accounts"]["admin"].id
    service = HousingOccupancyService(
        db, resident_readiness=HousingResidentReadinessPolicy(db, cipher=CIPHER, lookup=LOOKUP)
    )
    stay = service.create_stay(
        room.id,
        _input(result.person_id, start=NOW),
        actor_user_id=actor_id,
        occurred_at=NOW,
        authorize_room=_allow,
    )
    planned_person, _, planned_edges = approved_minor(db, platform_users, sequence=325)
    planned = service.create_stay(
        target.id,
        _input(planned_person.person_id, start=NOW + timedelta(days=1)),
        actor_user_id=actor_id,
        occurred_at=NOW,
        authorize_room=_allow,
    )
    edges[0].ended_at = NOW + timedelta(hours=1)
    planned_edges[0].ended_at = NOW + timedelta(hours=1)
    db.flush()
    db.commit()
    for operation in (
        lambda: service.backfill_existing_stay(
            target.id,
            _input(result.person_id, start=NOW),
            actor_user_id=actor_id,
            occurred_at=NOW + timedelta(hours=2),
            authorize_room_and_stay=_allow,
        ),
        lambda: service.update_reservation(
            planned.id,
            StayUpdate(version=planned.version, planned_check_in_at=NOW + timedelta(days=2)),
            actor_user_id=actor_id,
            occurred_at=NOW + timedelta(hours=2),
            authorize_room=_allow,
        ),
        lambda: service.confirm_check_in(
            planned.id,
            ConfirmCheckInRequest(
                version=planned.version, actual_check_in_at=NOW + timedelta(hours=2)
            ),
            actor_user_id=actor_id,
            occurred_at=NOW + timedelta(hours=2),
            authorize_room=_allow,
        ),
        lambda: service.create_stay(
            target.id,
            _input(result.person_id, start=NOW + timedelta(days=1)),
            actor_user_id=actor_id,
            occurred_at=NOW + timedelta(hours=2),
            authorize_room=_allow,
        ),
        lambda: service.transfer(
            stay.id,
            TransferStayRequest(
                version=stay.version,
                target_room_id=target.id,
                transferred_at=NOW + timedelta(hours=2),
                reason="测试失效",
            ),
            actor_user_id=actor_id,
            occurred_at=NOW + timedelta(hours=2),
            authorize_source=_allow,
            authorize_target=_allow,
        ),
    ):
        with pytest.raises((HousingProfileIncomplete, MinorGuardianRequired)):
            operation()
    visible_stay_person_ids = [
        row.person_id
        for row in service.room_projections([room.id], NOW + timedelta(hours=2))[
            room.id
        ].current_stays
    ]
    assert result.person_id in visible_stay_person_ids
    report = HousingReportProjectionBuilder(db, CIPHER, lookup=LOOKUP).build(
        ProjectionRequest(
            generated_at=NOW + timedelta(hours=2),
            range_start=NOW - timedelta(hours=1),
            range_end=NOW + timedelta(days=1),
            floor_ids=(room.floor_id,),
        )
    )
    assert report.issues == ()
    assert report.projection.residents[0].person_id == result.person_id
    assert report.projection.residents[0].phone_verified is False
    from app.housing.access import HousingAccessService
    from app.housing.occupancy_export_service import (
        HousingOccupancyExportService,
        render_occupancy_export,
    )
    from app.housing.schemas import HousingOccupancyExportRequest

    request = HousingOccupancyExportRequest(purpose="隔离住宿核对", floor_id=room.floor_id)
    exported = HousingOccupancyExportService(
        db, cipher=CIPHER, access=HousingAccessService(db, actor_id, NOW + timedelta(hours=2))
    ).rows(request, as_of=NOW + timedelta(hours=2))
    assert len(exported) == 1
    assert exported[0].person_type == "player"
    assert exported[0].account_status == "not_activated"
    assert exported[0].profile_source == "approved_profile"
    for format in ("csv", "xlsx"):
        content, _, _ = render_occupancy_export(exported, format=format, occurred_at=NOW)
        assert content
    # The fixture's coach also holds actual admin for approval review, so use student.
    denied_rows = HousingOccupancyExportService(
        db,
        cipher=CIPHER,
        access=HousingAccessService(db, platform_users["accounts"]["self_student"].id, NOW),
    ).rows(request, as_of=NOW)
    assert denied_rows == []
    pending = service.request_checkout(
        stay.id,
        CheckoutRequest(version=stay.version, actual_check_out_at=NOW + timedelta(hours=3)),
        actor_user_id=actor_id,
        occurred_at=NOW + timedelta(hours=3),
        authorize_room=_allow,
    )
    assert (
        len(service.room_projections([room.id], NOW + timedelta(hours=3))[room.id].current_stays)
        == 1
    )
    service.confirm_checkout(
        stay.id,
        CheckoutConfirmation(version=pending.version),
        actor_user_id=actor_id,
        occurred_at=NOW + timedelta(hours=3),
        authorize_room=_allow,
    )
    assert (
        service.room_projections([room.id], NOW + timedelta(hours=3))[room.id].current_stays == ()
    )


def test_pending_profile_never_enters_resident_candidates(db, platform_users):
    room = _room(db)
    data = intake(platform_users, full_name="未审住房人员")
    pending = intake_service(db).submit(actor(platform_users, "admin"), data)
    service = HousingOccupancyService(db)

    def options():
        return service.list_resident_options(
            room.id,
            data.full_name,
            cipher=CIPHER,
            lookup=LOOKUP,
            authorize_room=_allow,
            reference_date=date(2026, 10, 18),
        )

    assert options() == []
    approvals(db).approve(actor(platform_users, "super_admin"), pending.id, 1)
    assert len(options()) == 1
    assert options()[0].housing_ready


@pytest.mark.parametrize(
    "origin,status,allowed",
    [
        ("legacy", "active", False),
        ("legacy", "disabled", False),
        ("assisted_activation", "active", True),
        ("assisted_activation", "disabled", True),
    ],
)
def test_housing_eligibility_respects_immutable_account_origin(
    db, platform_users, origin, status, allowed
):
    from app.housing.resident_readiness import HousingResidentReadinessPolicy

    result, _, _ = approved_minor(db, platform_users)
    account = UserAccount(
        person_id=result.person_id,
        password_hash="test-only",
        status=status,
        force_password_change=False,
        email_normalized=f"origin-{uuid4().hex}@example.com",
        account_access_origin=origin,
        assisted_approval_id=result.approval_id if origin == "assisted_activation" else None,
    )
    db.add(account)
    db.flush()
    policy = HousingResidentReadinessPolicy(db, cipher=CIPHER, lookup=LOOKUP)
    value = policy.evaluate_many([result.person_id], reference_date=date(2026, 10, 18))[
        result.person_id
    ]
    assert value.housing_ready is allowed
    assert account.status == status
    service = HousingOccupancyService(db, resident_readiness=policy)
    room = _room(db)
    db.commit()
    if allowed:
        from app.identity.models import Person

        candidates = service.list_resident_options(
            room.id,
            db.get(Person, result.person_id).full_name,
            cipher=CIPHER,
            lookup=LOOKUP,
            authorize_room=_allow,
            reference_date=date(2026, 10, 18),
        )
        assert candidates[0].account_status == status
        assert candidates[0].account_ready is False  # No verified email identifier was fabricated.
        assert (
            service.create_stay(
                room.id,
                _input(result.person_id, start=NOW),
                actor_user_id=platform_users["accounts"]["admin"].id,
                occurred_at=NOW,
                authorize_room=_allow,
            ).status
            == "checked_in"
        )
    else:
        with pytest.raises(
            (
                HousingAccountRequired,
                HousingProfileIncomplete,
                HousingResourceNotFound,
                HousingVerifiedEmailRequired,
                MinorGuardianRequired,
            )
        ):
            service.create_stay(
                room.id,
                _input(result.person_id, start=NOW),
                actor_user_id=platform_users["accounts"]["admin"].id,
                occurred_at=NOW,
                authorize_room=_allow,
            )
    db.refresh(account)
    assert account.status == status


@pytest.mark.parametrize("conflict", ["capacity", "gender", "overlap"])
def test_accountless_allocation_keeps_physical_conflict_checks(db, platform_users, conflict):
    from app.housing.resident_readiness import HousingResidentReadinessPolicy
    from tests.api.conftest import make_cn_id
    from tests.services.test_assisted_eligibility import correction

    first, first_data, _ = approved_minor(db, platform_users, kind="both")
    # Reuse the first approval's authorized team.
    data = intake(
        platform_users,
        kind="player",
        team_id=first_data.team_id,
        coach_person_ids=[],
        identity_number=make_cn_id("20150102", 323),
        date_of_birth=date(2015, 1, 2),
        email="second-housing@example.com",
        guardians=first_data.guardians,
    )
    second = approvals(db).direct_create(actor(platform_users, "admin"), data, uuid4())
    if conflict == "gender":
        correction(
            db,
            platform_users,
            second.person_id,
            data,
            identity_number=make_cn_id("20150102", 324),
            sex="female",
        )
    room = _room(db, capacity=1 if conflict == "capacity" else 2)
    other_room = _room(db)
    service = HousingOccupancyService(
        db, resident_readiness=HousingResidentReadinessPolicy(db, cipher=CIPHER, lookup=LOOKUP)
    )
    kwargs = {
        "actor_user_id": platform_users["accounts"]["admin"].id,
        "occurred_at": NOW,
        "authorize_room": _allow,
    }
    service.create_stay(room.id, _input(first.person_id, start=NOW), **kwargs)
    expected = {
        "capacity": HousingCapacityConflict,
        "gender": HousingGenderConflict,
        "overlap": HousingPersonTimeConflict,
    }[conflict]
    with pytest.raises(expected):
        service.create_stay(
            other_room.id if conflict == "overlap" else room.id,
            _input(first.person_id if conflict == "overlap" else second.person_id, start=NOW),
            **kwargs,
        )
    assert len(service.room_projections([room.id], NOW)[room.id].current_stays) == 1


def test_eighteenth_birthday_uses_planned_shanghai_business_date(db, platform_users):
    from app.housing.resident_readiness import HousingResidentReadinessPolicy

    result, _, edges = approved_minor(db, platform_users, kind="player")
    edges[0].ended_at = NOW
    db.flush()
    policy = HousingResidentReadinessPolicy(db, cipher=CIPHER, lookup=LOOKUP)
    assert not policy.evaluate_many([result.person_id], reference_date=date(2026, 10, 18))[
        result.person_id
    ].housing_ready
    service = HousingOccupancyService(db, resident_readiness=policy)
    room = _room(db)
    db.commit()
    kwargs = {
        "actor_user_id": platform_users["accounts"]["admin"].id,
        "occurred_at": NOW,
        "authorize_room": _allow,
    }
    with pytest.raises(MinorGuardianRequired):
        service.create_stay(
            room.id,
            _input(result.person_id, start=datetime(2033, 1, 1, 15, 59, tzinfo=UTC)),
            **kwargs,
        )
    stay = service.create_stay(
        room.id, _input(result.person_id, start=datetime(2033, 1, 1, 16, 0, tzinfo=UTC)), **kwargs
    )
    assert stay.status == "planned"


def test_explicit_nondefault_keys_reach_housing_and_historical_report(db, platform_users):
    from app.housing.reporting_projection import HousingReportProjectionBuilder, ProjectionRequest
    from app.housing.resident_readiness import HousingResidentReadinessPolicy
    from app.personnel_intake.approval_service import PersonnelApprovalService
    from app.personnel_intake.guardian_records import ApprovalEvidenceError

    cipher, lookup = EnvelopeCipher(b"q" * 32), LookupDigest(b"r" * 32)
    result, _, _ = approved_minor(
        db,
        platform_users,
        kind="player",
        approval_service=PersonnelApprovalService(db, cipher=cipher, lookup=lookup),
    )
    room = _room(db)
    db.commit()
    kwargs = {
        "actor_user_id": platform_users["accounts"]["admin"].id,
        "occurred_at": NOW,
        "authorize_room": _allow,
    }
    wrong = HousingOccupancyService(
        db, resident_readiness=HousingResidentReadinessPolicy(db, cipher=CIPHER, lookup=LOOKUP)
    )
    with pytest.raises(ApprovalEvidenceError):
        wrong.create_stay(room.id, _input(result.person_id, start=NOW), **kwargs)
    service = HousingOccupancyService(
        db, resident_readiness=HousingResidentReadinessPolicy(db, cipher=cipher, lookup=lookup)
    )
    stay = service.create_stay(room.id, _input(result.person_id, start=NOW), **kwargs)
    assert stay.status == "checked_in"
    report = HousingReportProjectionBuilder(db, cipher, lookup=lookup).build(
        ProjectionRequest(
            generated_at=NOW,
            range_start=NOW - timedelta(hours=1),
            range_end=NOW + timedelta(hours=1),
            floor_ids=(room.floor_id,),
        )
    )
    assert report.issues == ()
    assert report.projection.residents[0].phone_evidence == "approved_profile"


def test_assisted_sealed_download_uses_immutable_snapshot_not_current_eligibility(postgres_engine):
    from sqlalchemy.orm import Session

    from app.housing.reporting_service import GenerateReportCommand, HousingReportingService
    from app.housing.resident_readiness import HousingResidentReadinessPolicy
    from app.identity.models import GuardianRelationship

    cipher, lookup = EnvelopeCipher(b"q" * 32), LookupDigest(b"r" * 32)
    from app.personnel_intake.approval_service import PersonnelApprovalService

    connection = postgres_engine.connect().execution_options(isolation_level="REPEATABLE READ")
    outer = connection.begin()
    try:
        with Session(
            bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
        ) as seed:
            users = _platform_users.__wrapped__(seed)
            result, _, edges = approved_minor(
                seed,
                users,
                kind="player",
                approval_service=PersonnelApprovalService(seed, cipher=cipher, lookup=lookup),
            )
            actor_id = users["accounts"]["admin"].id
            room = _room(seed)
            HousingOccupancyService(
                seed,
                resident_readiness=HousingResidentReadinessPolicy(
                    seed, cipher=cipher, lookup=lookup
                ),
            ).create_stay(
                room.id,
                _input(result.person_id, start=NOW),
                actor_user_id=actor_id,
                occurred_at=NOW,
                authorize_room=_allow,
            )
            floor_id, edge_id = room.floor_id, edges[0].id
        with Session(bind=connection, join_transaction_mode="create_savepoint") as generation:
            generated = HousingReportingService(
                generation,
                cipher,
                actor_id,
                NOW + timedelta(hours=1),
                "assisted-sealed-generation",
                lookup=lookup,
            ).generate(
                GenerateReportCommand(
                    range_start=NOW - timedelta(hours=1),
                    range_end=NOW + timedelta(days=1),
                    scope_mode="floors",
                    purpose="隔离报表核对",
                    floor_ids=(floor_id,),
                    person_types=("player",),
                )
            )
            assert generated.resident_count == 1
        with Session(bind=connection, join_transaction_mode="create_savepoint") as mutation:
            mutation.get(GuardianRelationship, edge_id).ended_at = NOW + timedelta(hours=2)
            mutation.commit()
        for format in ("csv", "xlsx"):
            with Session(bind=connection, join_transaction_mode="create_savepoint") as download:
                # No lookup injected: sealed downloads must not query live approved facts.
                artifact = HousingReportingService(
                    download,
                    cipher,
                    actor_id,
                    NOW + timedelta(hours=3),
                    f"assisted-sealed-{format}",
                ).download(generated.id, format)
                assert artifact.content
    finally:
        outer.rollback()
        connection.close()


def test_legacy_account_with_approved_profile_still_needs_verified_report_phone(db, platform_users):
    from app.housing.reporting_projection import HousingReportProjectionBuilder, ProjectionRequest
    from app.housing.resident_readiness import HousingResidentReadinessPolicy

    result, _, _ = approved_minor(db, platform_users)
    room = _room(db)
    HousingOccupancyService(
        db, resident_readiness=HousingResidentReadinessPolicy(db, cipher=CIPHER, lookup=LOOKUP)
    ).create_stay(
        room.id,
        _input(result.person_id, start=NOW),
        actor_user_id=platform_users["accounts"]["admin"].id,
        occurred_at=NOW,
        authorize_room=_allow,
    )
    db.add(
        UserAccount(
            person_id=result.person_id,
            password_hash="test-only",
            status="active",
            force_password_change=False,
            email_normalized="legacy-report@example.com",
        )
    )
    db.flush()
    output = HousingReportProjectionBuilder(db, CIPHER, lookup=LOOKUP).build(
        ProjectionRequest(
            generated_at=NOW,
            range_start=NOW - timedelta(hours=1),
            range_end=NOW + timedelta(hours=1),
            floor_ids=(room.floor_id,),
        )
    )
    assert output.projection is None
    assert output.issues[0].missing_fields == ("phone",)


def test_housing_http_uses_injected_keys_and_safe_integrity_failure(db, db_client, platform_users):
    from app.housing.dependencies import get_housing_cipher, get_housing_lookup
    from app.housing.models import HousingStay
    from app.identity.models import Person
    from app.personnel_intake.approval_service import PersonnelApprovalService
    from tests.api.test_housing_occupancy import _sign_in

    cipher, lookup = EnvelopeCipher(b"q" * 32), LookupDigest(b"r" * 32)
    result, _, _ = approved_minor(
        db,
        platform_users,
        kind="player",
        approval_service=PersonnelApprovalService(db, cipher=cipher, lookup=lookup),
    )
    room = _room(db)
    db.commit()
    csrf = _sign_in(db_client, platform_users, "admin")
    db_client.app.dependency_overrides[get_housing_cipher] = lambda: cipher
    db_client.app.dependency_overrides[get_housing_lookup] = lambda: LOOKUP
    url = f"/api/v1/housing/rooms/{room.id}"
    payload = _input(result.person_id, start=NOW + timedelta(days=1)).model_dump(mode="json")
    failed = db_client.post(url + "/stays", json=payload, headers={"X-CSRF-Token": csrf})
    assert failed.status_code == 503, failed.text
    assert failed.json()["code"] == "assisted_evidence_unavailable"
    assert failed.headers["Cache-Control"] == "no-store, private"
    assert (
        list(db.scalars(select(HousingStay).where(HousingStay.person_id == result.person_id))) == []
    )
    db_client.app.dependency_overrides[get_housing_lookup] = lambda: lookup
    candidates = db_client.get(
        url + "/resident-options", params={"query": db.get(Person, result.person_id).full_name}
    )
    assert candidates.status_code == 200
    assert candidates.json()["items"][0]["housing_ready"] is True
    assert candidates.json()["items"][0]["account_ready"] is False
    created = db_client.post(url + "/stays", json=payload, headers={"X-CSRF-Token": csrf})
    assert created.status_code == 201
    assert created.json()["person_type"] == "player"
