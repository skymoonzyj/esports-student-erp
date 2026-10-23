from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests.db.test_assisted_personnel_migration import (
    approval,
    request,
    seed_business_rows,
)
from tests.db.test_migrations import _alembic_config, _delete_test_account, _insert_test_account

OLD_HEAD = "0067_person_profile_notes"
NEW_HEAD = "0068_deferred_email_claims"


@pytest.fixture
def rows(postgres_engine):
    person, actor = _insert_test_account(postgres_engine)
    try:
        with postgres_engine.connect() as connection:
            transaction = connection.begin()
            yield connection, person, actor
            transaction.rollback()
    finally:
        _delete_test_account(postgres_engine, person_id=person, account_id=actor)


def test_claim_dtos_normalize_secrets_and_forbid_extra_fields():
    from pydantic import ValidationError

    from app.personnel_intake.claims.schemas import (
        ClaimPublicStatus,
        ClaimReasonInput,
        ClaimStartInput,
    )

    value = ClaimStartInput(
        full_name="  测试 人  ", identity_number="11010519491231002x", email=" Alice@EXAMPLE.COM "
    )
    assert value.full_name.get_secret_value() == "测试 人"
    assert value.identity_number.get_secret_value() == "11010519491231002X"
    assert value.email.get_secret_value() == "alice@example.com"
    assert "alice" not in repr(value)
    with pytest.raises(ValidationError):
        ClaimPublicStatus(status="waiting_review", reason=None, person_id=uuid4())
    with pytest.raises(ValidationError):
        ClaimReasonInput(expected_version=1, idempotency_key=uuid4(), reason=" ")


def claim_session(
    connection, *, challenge=None, suffix="c", payload_suffix=None, email_suffix=None
):
    now = datetime.now(UTC)
    challenge = challenge or uuid4()
    connection.execute(
        text(
            "INSERT INTO registration_verification_challenges "
            "(id,purpose,identifier_kind,identifier_digest,code_digest,request_source_digest,"
            "expires_at,attempts,last_sent_at,consumed_at) VALUES "
            "(:id,'profile_claim','email',:digest,:digest,:digest,:expiry,0,:now,:now)"
        ),
        {
            "id": challenge,
            "digest": (email_suffix or suffix) * 64,
            "expiry": now + timedelta(minutes=10),
            "now": now,
        },
    )
    identifier = uuid4()
    connection.execute(
        text(
            "INSERT INTO profile_claim_sessions "
            "(id,token_digest,challenge_id,payload_ciphertext,payload_digest,identity_digest,"
            "email_digest,state,created_at,expires_at,verified_at) VALUES "
            "(:id,:token,:challenge,'encrypted-input',:payload,:digest,:email,'verified',:now,:expiry,:now)"
        ),
        {
            "id": identifier,
            "token": uuid4().hex * 2,
            "challenge": challenge,
            "digest": suffix * 64,
            "payload": (payload_suffix or suffix) * 64,
            "email": (email_suffix or suffix) * 64,
            "now": now,
            "expiry": now + timedelta(minutes=10),
        },
    )
    return identifier, challenge, now


def claim(connection, *, person=None, approved=None, suffix="c"):
    session, challenge, now = claim_session(connection, suffix=suffix)
    identifier = uuid4()
    connection.execute(
        text(
            "INSERT INTO profile_claims (id,session_id,challenge_id,payload_ciphertext,payload_digest,"
            "submission_version,identity_digest,email_digest,verified_at,challenge_consumed_at,"
            "target_person_id,target_approval_id,status,version,expires_at) VALUES "
            "(:id,:session,:challenge,'encrypted-submission',:digest,1,:digest,:digest,:now,:now,"
            ":person,:approved,'pending',1,:expiry)"
        ),
        {
            "id": identifier,
            "session": session,
            "challenge": challenge,
            "digest": suffix * 64,
            "now": now,
            "person": person,
            "approved": approved,
            "expiry": now + timedelta(days=7),
        },
    )
    return identifier, session, challenge


def test_upgrade_preserves_people_accounts_stays_and_old_snapshots(postgres_engine: Engine):
    config = _alembic_config(postgres_engine)
    command.downgrade(config, OLD_HEAD)
    person, actor = _insert_test_account(postgres_engine)
    business = seed_business_rows(postgres_engine, person, actor)
    # Freeze every old column before upgrading; additive columns must not hide
    # changes to historical values or make unchanged rows compare unequal.
    baseline_columns = {
        table: ", ".join(
            postgres_engine.dialect.identifier_preparer.quote(column["name"])
            for column in inspect(postgres_engine).get_columns(table)
        )
        for table in (
            *(table for table, _ in business),
            "people",
            "user_accounts",
            "assisted_approvals",
            "personnel_intake_revisions",
        )
    }
    with postgres_engine.begin() as connection:
        req = request(connection, actor)
        approved = approval(connection, req, person, actor)
        connection.execute(
            text(
                "INSERT INTO assisted_person_profiles(person_id,current_approval_id) VALUES (:p,:a)"
            ),
            {"p": person, "a": approved},
        )

    def snapshot():
        with postgres_engine.connect() as connection:
            result = {
                table: connection.execute(
                    text(f'SELECT {baseline_columns[table]} FROM "{table}" WHERE id=:id'),
                    {"id": identifier},
                ).one()
                for table, identifier in business
            }
            for table, identifier in (
                ("people", person),
                ("user_accounts", actor),
                ("assisted_approvals", approved),
            ):
                result[table] = connection.execute(
                    text(f'SELECT {baseline_columns[table]} FROM "{table}" WHERE id=:id'),
                    {"id": identifier},
                ).one()
            result["revision"] = connection.execute(
                text(
                    f"SELECT {baseline_columns['personnel_intake_revisions']} "
                    "FROM personnel_intake_revisions WHERE request_id=:r"
                ),
                {"r": req},
            ).one()
            return result

    try:
        before_business = snapshot()
        with postgres_engine.connect() as connection:
            legacy_stay_ids = (
                connection.execute(text("SELECT id FROM housing_stays ORDER BY id")).scalars().all()
            )

        def assert_legacy_void_fields_null():
            with postgres_engine.connect() as connection:
                assert connection.execute(
                    text(
                        "SELECT id,voided_at,voided_by_user_id,void_reason_ciphertext "
                        "FROM housing_stays ORDER BY id"
                    )
                ).all() == [(stay_id, None, None, None) for stay_id in legacy_stay_ids]

        command.upgrade(config, "head")
        after_business = snapshot()
        assert after_business == before_business
        assert_legacy_void_fields_null()
        command.downgrade(config, OLD_HEAD)
        assert snapshot() == before_business
        command.upgrade(config, "head")
        assert snapshot() == before_business
        assert_legacy_void_fields_null()
        with postgres_engine.begin() as connection:
            assert connection.execute(
                text(
                    "SELECT document_type,document_issuing_country,passport_number_digest,"
                    "intake_mode,team_allocation_status,team_allocation_version "
                    "FROM people WHERE id=:p"
                ),
                {"p": person},
            ).one() == ("cn_id", None, None, "strict", "untracked", 1)
            assert (
                connection.scalar(
                    text(
                        "SELECT activation_requirement FROM assisted_person_profiles WHERE person_id=:p"
                    ),
                    {"p": person},
                )
                == "reserved_email"
            )
            connection.execute(
                text(
                    "INSERT INTO personnel_intake_revisions(id,request_id,version,payload_ciphertext,payload_digest,identity_digest,email_digest,created_by_user_id) VALUES (:id,:r,2,'new-encrypted',:d,:d,NULL,:a)"
                ),
                {"id": uuid4(), "r": req, "d": "d" * 64, "a": actor},
            )
            assert (
                connection.scalar(
                    text("SELECT account_access_origin FROM user_accounts WHERE id=:a"),
                    {"a": actor},
                )
                == "legacy"
            )
    finally:
        command.upgrade(config, "head")
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM assisted_person_profiles WHERE person_id=:p"), {"p": person}
            )
            for table in ("assisted_approvals", "personnel_intake_revisions"):
                connection.execute(
                    text(f"ALTER TABLE {table} DISABLE TRIGGER trg_{table}_immutable")
                )
                connection.execute(text(f"DELETE FROM {table} WHERE request_id=:r"), {"r": req})
                connection.execute(
                    text(f"ALTER TABLE {table} ENABLE TRIGGER trg_{table}_immutable")
                )
            connection.execute(
                text("DELETE FROM personnel_intake_requests WHERE id=:r"), {"r": req}
            )
            for table, identifier in reversed(business):
                if table == "payments":
                    connection.execute(
                        text("DELETE FROM revenue_share_reconcile_queue WHERE payment_id=:id"),
                        {"id": identifier},
                    )
                connection.execute(text(f'DELETE FROM "{table}" WHERE id=:id'), {"id": identifier})
        _delete_test_account(postgres_engine, person_id=person, account_id=actor)


def test_claim_constraints_and_owner_xor(rows):
    connection, person, actor = rows
    assert "profile_claims" in inspect(connection).get_table_names()
    req = request(connection, actor)
    approved = approval(connection, req, person, actor)
    first, session, _challenge = claim(connection, person=person, approved=approved)
    second, _, _ = claim(connection, person=person, approved=approved, suffix="d")
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(
            text("UPDATE profile_claim_sessions SET claim_id=:claim WHERE id=:id"),
            {"claim": second, "id": session},
        )
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(text("DELETE FROM profile_claims WHERE id=:id"), {"id": second})
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(
            text(
                "INSERT INTO profile_claim_sessions SELECT :id, :token, challenge_id, payload_ciphertext, payload_digest, identity_digest, email_digest, state, expires_at, verified_at, claim_id, created_at, updated_at FROM profile_claim_sessions WHERE id=:old"
            ),
            {"id": uuid4(), "old": session, "token": uuid4().hex * 2},
        )
    for request_id, claim_id, kind, person_id in (
        (req, first, "email", None),
        (None, None, "email", None),
        (None, first, "identity", None),
        (None, first, "email", person),
    ):
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                text(
                    "INSERT INTO personnel_credential_reservations(id,kind,digest,request_id,profile_claim_id,person_id) VALUES (:id,:k,:d,:r,:c,:p)"
                ),
                {
                    "id": uuid4(),
                    "k": kind,
                    "d": uuid4().hex * 2,
                    "r": request_id,
                    "c": claim_id,
                    "p": person_id,
                },
            )
    connection.execute(
        text(
            "INSERT INTO personnel_credential_reservations(id,kind,digest,profile_claim_id) VALUES (:id,'email',:d,:c)"
        ),
        {"id": uuid4(), "d": "e" * 64, "c": first},
    )
    binding = uuid4()
    connection.execute(
        text(
            "INSERT INTO profile_claim_bindings(id,claim_id,person_id,email_digest,approval_id) VALUES (:id,:c,:p,:d,:a)"
        ),
        {"id": binding, "c": first, "p": person, "d": "c" * 64, "a": approved},
    )
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(
            text(
                "INSERT INTO profile_claim_bindings(id,claim_id,person_id,email_digest,approval_id) VALUES (:id,:c,:p,:d,:a)"
            ),
            {"id": uuid4(), "c": second, "p": person, "d": "d" * 64, "a": approved},
        )
    for field, value in (
        ("payload_ciphertext", "changed"),
        ("email_digest", "f" * 64),
        ("challenge_id", str(uuid4())),
    ):
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                text(f"UPDATE profile_claims SET {field}=:v WHERE id=:id"),
                {"v": value, "id": first},
            )
    connection.execute(
        text("UPDATE profile_claim_sessions SET payload_ciphertext=NULL WHERE id=:id"),
        {"id": session},
    )
    connection.execute(
        text(
            "INSERT INTO assisted_person_profiles(person_id,current_approval_id,activation_requirement) VALUES (:p,:a,'reviewed_claim')"
        ),
        {"p": person, "a": approved},
    )
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(
            text(
                "UPDATE assisted_person_profiles SET activation_requirement='reserved_email' WHERE person_id=:p"
            ),
            {"p": person},
        )


def test_decision_append_only_and_binding_evidence(rows):
    connection, person, actor = rows
    req = request(connection, actor)
    approved = approval(connection, req, person, actor)
    first, _, _ = claim(connection, person=person, approved=approved)
    binding = uuid4()
    connection.execute(
        text(
            "INSERT INTO profile_claim_bindings(id,claim_id,person_id,email_digest,approval_id) VALUES (:id,:c,:p,:d,:a)"
        ),
        {"id": binding, "c": first, "p": person, "d": "c" * 64, "a": approved},
    )
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(text("DELETE FROM profile_claim_bindings WHERE id=:id"), {"id": binding})
    key = uuid4()
    decision = uuid4()
    sql = text(
        "INSERT INTO profile_claim_decisions(id,claim_id,actor_user_id,action,idempotency_key,request_fingerprint,before_version,after_version,result_approval_id,result_binding_id) VALUES (:id,:c,:actor,'approve',:key,:d,1,2,:a,:b)"
    )
    params = {
        "id": decision,
        "c": first,
        "actor": actor,
        "key": key,
        "d": "a" * 64,
        "a": approved,
        "b": binding,
    }
    connection.execute(sql, params)
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(sql, {**params, "id": uuid4()})
    for statement in (
        "UPDATE profile_claim_decisions SET action='revoke' WHERE id=:id",
        "DELETE FROM profile_claim_decisions WHERE id=:id",
    ):
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(text(statement), {"id": decision})
    connection.execute(
        text("UPDATE profile_claim_bindings SET revoked_at=now() WHERE id=:id"), {"id": binding}
    )
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(
            text("UPDATE profile_claim_bindings SET revoked_at=NULL WHERE id=:id"), {"id": binding}
        )


def test_fresh_verified_session_reopens_same_input_without_rewriting_original_proof(rows):
    connection, person, actor = rows
    req = request(connection, actor)
    approved = approval(connection, req, person, actor)
    original, _, _ = claim(connection, person=person, approved=approved)
    evidence = text(
        "SELECT session_id,challenge_id,payload_ciphertext,payload_digest,email_digest,verified_at,challenge_consumed_at,expires_at FROM profile_claims WHERE id=:id"
    )
    before = connection.execute(evidence, {"id": original}).one()
    for status in ("pending", "approved", "completed"):
        connection.execute(
            text(
                "UPDATE profile_claims SET status=:status,completed_account_id=:account,completed_at=:at WHERE id=:id"
            ),
            {
                "status": status,
                "account": actor if status == "completed" else None,
                "at": datetime.now(UTC) if status == "completed" else None,
                "id": original,
            },
        )
        fresh, fresh_challenge, verified = claim_session(connection)
        connection.execute(
            text("UPDATE profile_claim_sessions SET claim_id=:claim WHERE id=:session"),
            {"claim": original, "session": fresh},
        )
        assert (
            connection.scalar(
                text("SELECT claim_id FROM profile_claim_sessions WHERE id=:id"), {"id": fresh}
            )
            == original
        )
        assert fresh_challenge != before.challenge_id
        assert connection.execute(evidence, {"id": original}).one() == before
        for overrides in ({"payload_suffix": "d"}, {"email_suffix": "e"}):
            different, _, _ = claim_session(connection, **overrides)
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    text("UPDATE profile_claim_sessions SET claim_id=:claim WHERE id=:session"),
                    {"claim": original, "session": different},
                )
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                text("UPDATE profile_claim_sessions SET verified_at=:at WHERE id=:id"),
                {"at": verified + timedelta(seconds=1), "id": fresh},
            )


def test_downgrade_refuses_claim_or_null_email_data(postgres_engine: Engine):
    config = _alembic_config(postgres_engine)
    command.downgrade(config, OLD_HEAD)
    command.upgrade(config, NEW_HEAD)
    person, actor = _insert_test_account(postgres_engine)
    try:
        with postgres_engine.begin() as connection:
            session, challenge, _ = claim_session(connection)
        with pytest.raises(RuntimeError, match="claim"):
            command.downgrade(config, OLD_HEAD)
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM profile_claim_sessions WHERE id=:id"), {"id": session}
            )
            connection.execute(
                text("DELETE FROM registration_verification_challenges WHERE id=:id"),
                {"id": challenge},
            )
            req = request(connection, actor)
            connection.execute(
                text(
                    "INSERT INTO personnel_intake_revisions(id,request_id,version,payload_ciphertext,payload_digest,identity_digest,email_digest,created_by_user_id) VALUES (:id,:r,2,'encrypted',:d,:d,NULL,:a)"
                ),
                {"id": uuid4(), "r": req, "d": "f" * 64, "a": actor},
            )
        with pytest.raises(RuntimeError, match="claim|null email"):
            command.downgrade(config, OLD_HEAD)
    finally:
        try:
            with postgres_engine.begin() as connection:
                connection.execute(
                    text(
                        "ALTER TABLE personnel_intake_revisions DISABLE TRIGGER trg_personnel_intake_revisions_immutable"
                    )
                )
                connection.execute(
                    text("DELETE FROM personnel_intake_revisions WHERE request_id=:r"), {"r": req}
                )
                connection.execute(
                    text(
                        "ALTER TABLE personnel_intake_revisions ENABLE TRIGGER trg_personnel_intake_revisions_immutable"
                    )
                )
                connection.execute(
                    text("DELETE FROM personnel_intake_requests WHERE id=:r"), {"r": req}
                )
            _delete_test_account(postgres_engine, person_id=person, account_id=actor)
        finally:
            # Keep the legacy 0068 assertions, but never leak its schema into
            # later current-model fixtures in the same leased test session.
            command.upgrade(config, "head")
