from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy import Connection, Engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests.db.test_migrations import _alembic_config, _delete_test_account, _insert_test_account

OLD_HEAD = "0065_invited_housing_visibility"
NEW_HEAD = "0066_assisted_personnel"


@pytest.fixture
def rows(postgres_engine: Engine) -> Iterator[tuple[Connection, UUID, UUID]]:
    person_id, account_id = _insert_test_account(postgres_engine)
    try:
        with postgres_engine.connect() as connection:
            transaction = connection.begin()
            yield connection, person_id, account_id
            transaction.rollback()
    finally:
        _delete_test_account(postgres_engine, person_id=person_id, account_id=account_id)


def request(connection: Connection, actor: UUID) -> UUID:
    identifier = uuid4()
    connection.execute(
        text(
            "INSERT INTO personnel_intake_requests (id, submitted_by_user_id, version, state, kind) "
            "VALUES (:id, :actor, 1, 'pending', 'student')"
        ),
        {"id": identifier, "actor": actor},
    )
    connection.execute(
        text(
            "INSERT INTO personnel_intake_revisions "
            "(id, request_id, version, payload_ciphertext, payload_digest, identity_digest, "
            "email_digest, created_by_user_id) VALUES "
            "(:id, :request, 1, 'encrypted-payload', :digest, :digest, :digest, :actor)"
        ),
        {"id": uuid4(), "request": identifier, "digest": "a" * 64, "actor": actor},
    )
    return identifier


def approval(
    connection: Connection,
    request_id: UUID,
    person: UUID,
    actor: UUID,
    *,
    version: int = 1,
    supersedes: UUID | None = None,
) -> UUID:
    identifier = uuid4()
    connection.execute(
        text(
            "INSERT INTO assisted_approvals (id, request_id, revision_version, person_id, "
            "approved_by_user_id, snapshot_ciphertext, snapshot_digest, "
            "guardian_relationship_version, guardian_snapshot_ciphertext, guardian_snapshot_digest, supersedes_approval_id) "
            "VALUES (:id, :request, :version, :person, :actor, 'encrypted-snapshot', :digest, "
            ":version, 'encrypted-guardian-snapshot', :digest, :supersedes)"
        ),
        {
            "id": identifier,
            "request": request_id,
            "version": version,
            "person": person,
            "actor": actor,
            "digest": "b" * 64,
            "supersedes": supersedes,
        },
    )
    return identifier


def seed_business_rows(engine: Engine, person: UUID, actor: UUID) -> list[tuple[str, UUID]]:
    """Real pre-upgrade player/team, housing, invitation, and revenue facts."""
    from app.admissions.models import Payment
    from app.housing.models import HousingBuilding, HousingFloor, HousingRoom, HousingStay
    from app.revenue_share.models import (
        ReferralCode,
        RevenueShareLedgerEntry,
        RevenueShareOrganization,
        StudentReferralBinding,
    )
    from app.teaching.models import GameProject, ProfessionalTeam
    from app.team_management.models import TeamMembership

    inserted: list[tuple[str, UUID]] = []
    with engine.begin() as connection:

        def add(model, **values):
            identifier = uuid4()
            connection.execute(model.__table__.insert().values(id=identifier, **values))
            inserted.append((model.__tablename__, identifier))
            return identifier

        project = add(
            GameProject,
            name=f"Synthetic division {uuid4()}",
            sort_order=100,
            is_active=True,
            is_team_division=True,
            created_by_user_id=actor,
        )
        team = add(
            ProfessionalTeam,
            project_id=project,
            name="Synthetic team",
            is_active=True,
            created_by_user_id=actor,
        )
        add(
            TeamMembership,
            person_id=person,
            team_id=team,
            joined_on=date(2026, 10, 1),
            created_by_user_id=actor,
        )
        building = add(HousingBuilding, code=uuid4().hex[:12], name=f"Synthetic {uuid4()}")
        floor = add(HousingFloor, building_id=building, number=1, name="First floor")
        room = add(
            HousingRoom,
            building_id=building,
            floor_id=floor,
            room_number="101",
            normalized_key="101",
            room_type="single",
            capacity=1,
        )
        add(
            HousingStay,
            person_id=person,
            room_id=room,
            stay_purpose="permanent",
            duration_mode="long_term",
            planned_check_in_at=datetime(2026, 10, 1, tzinfo=UTC),
            status="planned",
            room_name_snapshot="101",
            floor_name_snapshot="First floor",
            building_name_snapshot="Synthetic",
            created_by_user_id=actor,
        )
        org = add(RevenueShareOrganization, name="Synthetic referral", created_by_user_id=actor)
        code = add(
            ReferralCode,
            code=uuid4().hex[:20],
            label="Synthetic invitation",
            source_kind="organization",
            referrer_organization_id=org,
            created_by_user_id=actor,
        )
        add(
            StudentReferralBinding,
            student_person_id=person,
            referral_code_id=code,
            source_kind="organization",
            referrer_organization_id=org,
        )
        now = datetime.now(UTC)
        payment = add(
            Payment,
            source_kind="manual",
            amount_cents=10000,
            paid_at=now,
            manual_note="Synthetic only",
            created_by_user_id=actor,
        )
        add(
            RevenueShareLedgerEntry,
            event_key=f"synthetic-{uuid4()}",
            payment_id=payment,
            entry_kind="earning",
            role_kind="inviter",
            beneficiary_kind="organization",
            beneficiary_organization_id=org,
            calculation_type="fixed",
            base_amount_cents=10000,
            amount_cents=1000,
            occurred_at=now,
        )
    return inserted


def test_upgrade_preserves_legacy_and_provenance_is_immutable(postgres_engine: Engine) -> None:
    config = _alembic_config(postgres_engine)
    command.downgrade(config, OLD_HEAD)
    person_id, account_id = _insert_test_account(postgres_engine)
    business_rows = seed_business_rows(postgres_engine, person_id, account_id)
    try:

        def snapshot():
            with postgres_engine.connect() as connection:
                result = {
                    table: connection.execute(
                        text(f'SELECT * FROM "{table}" WHERE id=:id'), {"id": identifier}
                    ).one()
                    for table, identifier in business_rows
                }
                result["account"] = connection.execute(
                    text(
                        "SELECT id, person_id, password_hash, status, login_phone_digest, "
                        "email_normalized, created_at, updated_at FROM user_accounts WHERE id=:id"
                    ),
                    {"id": account_id},
                ).one()
                return result

        before_business_snapshot = snapshot()
        command.upgrade(config, NEW_HEAD)
        after_business_snapshot = snapshot()
        assert after_business_snapshot == before_business_snapshot
        with postgres_engine.begin() as connection:
            assert (
                connection.scalar(
                    text("SELECT account_access_origin FROM user_accounts WHERE id=:id"),
                    {"id": account_id},
                )
                == "legacy"
            )
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    text(
                        "UPDATE user_accounts SET account_access_origin='assisted_activation' WHERE id=:id"
                    ),
                    {"id": account_id},
                )
            connection.execute(
                text(
                    "UPDATE user_accounts SET password_hash='new-hash', status='disabled' WHERE id=:id"
                ),
                {"id": account_id},
            )
    finally:
        command.upgrade(config, "head")
        with postgres_engine.begin() as connection:
            for table, identifier in reversed(business_rows):
                if table == "payments":
                    connection.execute(
                        text("DELETE FROM revenue_share_reconcile_queue WHERE payment_id=:id"),
                        {"id": identifier},
                    )
                connection.execute(text(f'DELETE FROM "{table}" WHERE id=:id'), {"id": identifier})
        _delete_test_account(postgres_engine, person_id=person_id, account_id=account_id)


def test_active_reservations_and_approval_are_unique(rows) -> None:
    connection, person, actor = rows
    request_id = request(connection, actor)
    reservation_sql = text(
        "INSERT INTO personnel_credential_reservations (id, kind, digest, request_id, person_id) "
        "VALUES (:id, 'email', :digest, :request, :person)"
    )
    params = {"id": uuid4(), "digest": "c" * 64, "request": request_id, "person": person}
    connection.execute(reservation_sql, params)
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(reservation_sql, {**params, "id": uuid4()})
    connection.execute(
        text("UPDATE personnel_credential_reservations SET ended_at=now() WHERE id=:id"),
        {"id": params["id"]},
    )
    connection.execute(reservation_sql, {**params, "id": uuid4()})
    approved = approval(connection, request_id, person, actor)
    with pytest.raises(IntegrityError), connection.begin_nested():
        approval(connection, request_id, person, actor)
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(
            text("UPDATE assisted_approvals SET snapshot_digest=:digest WHERE id=:id"),
            {"id": approved, "digest": "d" * 64},
        )


def test_downgrade_refuses_existing_assisted_data(postgres_engine: Engine) -> None:
    config = _alembic_config(postgres_engine)
    command.downgrade(config, OLD_HEAD)
    assert "assisted_approvals" not in inspect(postgres_engine).get_table_names()
    command.upgrade(config, NEW_HEAD)
    person, actor = _insert_test_account(postgres_engine)
    try:
        with postgres_engine.begin() as connection:
            request_id = uuid4()
            connection.execute(
                text(
                    "INSERT INTO personnel_intake_requests (id, submitted_by_user_id, version, state, kind) "
                    "VALUES (:id, :actor, 1, 'pending', 'player')"
                ),
                {"id": request_id, "actor": actor},
            )
        with pytest.raises(RuntimeError, match="assisted personnel"):
            command.downgrade(config, OLD_HEAD)
        command.upgrade(config, NEW_HEAD)
        with postgres_engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT version FROM personnel_intake_requests WHERE id=:id"),
                    {"id": request_id},
                )
                == 1
            )
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM personnel_intake_requests WHERE id=:id"), {"id": request_id}
            )
        _delete_test_account(postgres_engine, person_id=person, account_id=actor)


def test_correction_changes_profile_not_original_account_provenance(rows) -> None:
    connection, person, actor = rows
    request_id = request(connection, actor)
    original = approval(connection, request_id, person, actor)
    connection.execute(
        text(
            "INSERT INTO assisted_person_profiles (person_id, current_approval_id) VALUES (:person, :id)"
        ),
        {"person": person, "id": original},
    )
    connection.execute(
        text(
            "INSERT INTO personnel_intake_revisions "
            "(id, request_id, version, payload_ciphertext, payload_digest, identity_digest, "
            "email_digest, created_by_user_id) VALUES (:id, :request, 2, 'corrected-encrypted-payload', "
            ":digest, :digest, :digest, :actor)"
        ),
        {"id": uuid4(), "request": request_id, "digest": "d" * 64, "actor": actor},
    )
    corrected = approval(connection, request_id, person, actor, version=2, supersedes=original)
    connection.execute(
        text("UPDATE assisted_person_profiles SET current_approval_id=:id WHERE person_id=:person"),
        {"id": corrected, "person": person},
    )
    assert (
        connection.scalar(
            text(
                "SELECT current_approval_id FROM assisted_person_profiles WHERE person_id=:person"
            ),
            {"person": person},
        )
        == corrected
    )
    assert (
        connection.scalar(
            text("SELECT snapshot_ciphertext FROM assisted_approvals WHERE id=:id"),
            {"id": original},
        )
        == "encrypted-snapshot"
    )
    # Existing accounts stay legacy even after an approved profile is attached.
    assert (
        connection.scalar(
            text("SELECT account_access_origin FROM user_accounts WHERE id=:actor"),
            {"actor": actor},
        )
        == "legacy"
    )
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(
            text(
                "UPDATE user_accounts SET assisted_approval_id=:id, "
                "account_access_origin='assisted_activation' WHERE id=:actor"
            ),
            {"id": original, "actor": actor},
        )


def test_new_assisted_account_binds_person_and_preserves_origin_on_correction(rows) -> None:
    connection, _person, actor = rows
    assisted_person = uuid4()
    connection.execute(
        text(
            "INSERT INTO people (id, full_name, identity_ciphertext, identity_digest, sex, date_of_birth) "
            "VALUES (:id, 'Synthetic player', 'encrypted', :digest, 'female', DATE '2000-01-01')"
        ),
        {"id": assisted_person, "digest": assisted_person.hex},
    )
    request_id = request(connection, actor)
    original = approval(connection, request_id, assisted_person, actor)
    unrelated_person = uuid4()
    connection.execute(
        text(
            "INSERT INTO people (id, full_name, identity_ciphertext, identity_digest, sex, date_of_birth) "
            "VALUES (:id, 'Synthetic unrelated', 'encrypted', :digest, 'female', DATE '2000-01-01')"
        ),
        {"id": unrelated_person, "digest": unrelated_person.hex},
    )
    sql = text(
        "INSERT INTO user_accounts (id, person_id, password_hash, status, force_password_change, "
        "failed_login_count, account_access_origin, assisted_approval_id) "
        "VALUES (:id, :person, 'hash', 'active', false, 0, 'assisted_activation', :approval)"
    )
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(sql, {"id": uuid4(), "person": unrelated_person, "approval": original})
    account = uuid4()
    connection.execute(sql, {"id": account, "person": assisted_person, "approval": original})
    connection.execute(
        text(
            "INSERT INTO personnel_intake_revisions "
            "(id, request_id, version, payload_ciphertext, payload_digest, identity_digest, "
            "email_digest, created_by_user_id) VALUES (:id, :request, 2, 'corrected-payload', "
            ":digest, :digest, :digest, :actor)"
        ),
        {"id": uuid4(), "request": request_id, "digest": "a" * 64, "actor": actor},
    )
    corrected = approval(
        connection, request_id, assisted_person, actor, version=2, supersedes=original
    )
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(
            text("UPDATE user_accounts SET assisted_approval_id=:approval WHERE id=:id"),
            {"approval": corrected, "id": account},
        )
    connection.execute(
        text(
            "UPDATE user_accounts SET password_hash='reset-hash', locked_until=now(), "
            "email_normalized=NULL, status='disabled' WHERE id=:id"
        ),
        {"id": account},
    )
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(
            text(
                "UPDATE user_accounts SET assisted_approval_id=NULL, "
                "account_access_origin='legacy' WHERE id=:id"
            ),
            {"id": account},
        )
    assert (
        connection.scalar(
            text("SELECT assisted_approval_id FROM user_accounts WHERE id=:id"), {"id": account}
        )
        == original
    )


def test_activation_challenge_binding_and_uniqueness(rows) -> None:
    connection, person, actor = rows
    approved = approval(connection, request(connection, actor), person, actor)
    challenge = uuid4()
    connection.execute(
        text(
            "INSERT INTO registration_verification_challenges "
            "(id, purpose, identifier_kind, identifier_digest, code_digest, request_source_digest, "
            "expires_at, attempts, last_sent_at) VALUES (:id, 'assisted_activation', 'email', :digest, "
            ":digest, :digest, now()+interval '10 minutes', 0, now())"
        ),
        {"id": challenge, "digest": "e" * 64},
    )
    sql = text(
        "INSERT INTO assisted_activation_sessions "
        "(id, token_digest, challenge_id, person_id, approval_id, revision_version, email_digest, "
        "state, expires_at) VALUES (:id, :token, :challenge, :person, :approval, :version, :email, "
        "'requested', now()+interval '10 minutes')"
    )
    params = {
        "id": uuid4(),
        "token": "f" * 64,
        "challenge": challenge,
        "person": person,
        "approval": approved,
        "version": 1,
        "email": "e" * 64,
    }
    connection.execute(sql, params)
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(sql, {**params, "id": uuid4(), "token": "a" * 64})
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(
            text("UPDATE assisted_activation_sessions SET revision_version=2 WHERE id=:id"),
            {"id": params["id"]},
        )
    assert (
        connection.scalar(
            text("SELECT application_id FROM registration_verification_challenges WHERE id=:id"),
            {"id": challenge},
        )
        is None
    )


def test_approval_rejects_nonexistent_or_other_person_guardian_relationship(rows) -> None:
    connection, person, actor = rows
    request_id = request(connection, actor)
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(
            text(
                "INSERT INTO assisted_approvals "
                "(id, request_id, revision_version, person_id, approved_by_user_id, snapshot_ciphertext, "
                "snapshot_digest, guardian_relationship_version, guardian_relationship_ids, "
                "guardian_snapshot_ciphertext, guardian_snapshot_digest) "
                "VALUES (:id, :request, 1, :person, :actor, 'cipher', :digest, 1, :ids, 'cipher', :digest)"
            ),
            {
                "id": uuid4(),
                "request": request_id,
                "person": person,
                "actor": actor,
                "digest": "a" * 64,
                "ids": [uuid4()],
            },
        )
