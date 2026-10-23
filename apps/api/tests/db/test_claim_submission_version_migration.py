from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.personnel_intake.claims.models import ProfileClaimSession
from tests.db.test_migrations import _alembic_config
from tests.db.test_profile_claim_migration import claim, claim_session


def require_session_marker(connection):
    assert "submission_version" in {
        column["name"] for column in inspect(connection).get_columns("profile_claim_sessions")
    }, "anonymous claim proof has no persistent submission-version marker"


def insert_session(connection, *, version=1, state="verified", redacted=False):
    require_session_marker(connection)
    now = datetime.now(UTC)
    challenge, identifier = uuid4(), uuid4()
    digest = uuid4().hex * 2
    connection.execute(
        text(
            "INSERT INTO registration_verification_challenges(id,purpose,identifier_kind,identifier_digest,code_digest,request_source_digest,expires_at,attempts,last_sent_at) VALUES (:id,'profile_claim','email',:d,:d,:d,:expiry,0,:now)"
        ),
        {"id": challenge, "d": digest, "expiry": now + timedelta(minutes=10), "now": now},
    )
    connection.execute(
        text(
            "INSERT INTO profile_claim_sessions(id,token_digest,challenge_id,payload_ciphertext,payload_digest,identity_digest,email_digest,submission_version,state,created_at,expires_at,verified_at) VALUES (:id,:d,:challenge,:payload,:d,:d,:d,:version,:state,:now,:expiry,:verified)"
        ),
        {
            "id": identifier,
            "d": digest,
            "challenge": challenge,
            "payload": None if redacted else "opaque-encrypted-submission",
            "version": version,
            "state": state,
            "now": now,
            "expiry": now + timedelta(minutes=10),
            "verified": now if state == "verified" else None,
        },
    )
    return identifier, challenge, digest, now


def cleanup(connection, session, challenge, claim_id=None):
    if claim_id is not None:
        connection.execute(
            text("ALTER TABLE profile_claims DISABLE TRIGGER trg_profile_claims_no_delete")
        )
        connection.execute(text("DELETE FROM profile_claims WHERE id=:id"), {"id": claim_id})
        connection.execute(
            text("ALTER TABLE profile_claims ENABLE TRIGGER trg_profile_claims_no_delete")
        )
    connection.execute(text("DELETE FROM profile_claim_sessions WHERE id=:id"), {"id": session})
    connection.execute(
        text("DELETE FROM registration_verification_challenges WHERE id=:id"), {"id": challenge}
    )


def test_session_orm_accepts_explicit_authenticated_submission_version():
    session = ProfileClaimSession(submission_version=2)
    assert session.submission_version == 2


def test_session_marker_is_immutable_even_when_payload_is_pruned(postgres_engine):
    with postgres_engine.connect() as connection:
        transaction = connection.begin()
        session, _, _, _ = insert_session(connection, version=2)
        connection.execute(
            text("UPDATE profile_claim_sessions SET payload_ciphertext=NULL WHERE id=:id"),
            {"id": session},
        )
        for version in (1, 3):
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    text("UPDATE profile_claim_sessions SET submission_version=:v WHERE id=:id"),
                    {"id": session, "v": version},
                )
        assert connection.execute(
            text(
                "SELECT submission_version,payload_ciphertext FROM profile_claim_sessions WHERE id=:id"
            ),
            {"id": session},
        ).one() == (2, None)
        transaction.rollback()


@pytest.mark.parametrize("version", [None, 0, 3])
def test_session_rejects_missing_or_unsupported_version(postgres_engine, version):
    with postgres_engine.connect() as connection:
        transaction = connection.begin()
        require_session_marker(connection)
        with pytest.raises(IntegrityError), connection.begin_nested():
            insert_session(connection, version=version)
        transaction.rollback()


def test_claim_rejects_unsupported_submission_version(postgres_engine):
    with postgres_engine.connect() as connection:
        transaction = connection.begin()
        identifier, _, _ = claim(connection)
        session, challenge, _ = claim_session(connection)
        # UPDATE is already immutable in 0068; exercise INSERT independently so
        # this assertion detects the supported-version CHECK rather than that trigger.
        with pytest.raises(IntegrityError) as rejected, connection.begin_nested():
            connection.execute(
                text(
                    "INSERT INTO profile_claims SELECT (jsonb_populate_record(NULL::profile_claims,to_jsonb(c) || jsonb_build_object('id',CAST(:new_id AS text),'submission_version',3,'session_id',CAST(:session AS text),'challenge_id',CAST(:challenge AS text)))).* FROM profile_claims c WHERE c.id=:id"
                ),
                {
                    "id": identifier,
                    "new_id": str(uuid4()),
                    "session": str(session),
                    "challenge": str(challenge),
                },
            )
        assert rejected.value.orig.diag.constraint_name == "ck_profile_claim_versions"
        transaction.rollback()


@pytest.mark.parametrize("redacted", [False, True])
def test_unmatched_unverified_v2_session_blocks_downgrade(postgres_engine, redacted):
    config = _alembic_config(postgres_engine)
    with postgres_engine.begin() as connection:
        before_revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        session, challenge, _, _ = insert_session(
            connection, version=2, state="requested", redacted=redacted
        )
    try:
        with pytest.raises(RuntimeError, match="discard business facts"):
            command.downgrade(config, "0068_deferred_email_claims")
        with postgres_engine.connect() as connection:
            assert connection.execute(
                text("SELECT submission_version,claim_id FROM profile_claim_sessions WHERE id=:id"),
                {"id": session},
            ).one() == (2, None)
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == before_revision
            )
    finally:
        with postgres_engine.begin() as connection:
            cleanup(connection, session, challenge)


def test_v2_claim_independently_blocks_downgrade(postgres_engine):
    config = _alembic_config(postgres_engine)
    with postgres_engine.begin() as connection:
        session, challenge, digest, now = insert_session(connection, version=1)
        identifier = uuid4()
        connection.execute(
            text(
                "INSERT INTO profile_claims(id,session_id,challenge_id,payload_ciphertext,payload_digest,submission_version,identity_digest,email_digest,verified_at,challenge_consumed_at,status,version,expires_at) VALUES (:id,:session,:challenge,'encrypted',:d,2,:d,:d,:now,:now,'pending',1,:expiry)"
            ),
            {
                "id": identifier,
                "session": session,
                "challenge": challenge,
                "d": digest,
                "now": now,
                "expiry": now + timedelta(days=7),
            },
        )
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                text("UPDATE profile_claims SET submission_version=1 WHERE id=:id"),
                {"id": identifier},
            )
    try:
        with pytest.raises(RuntimeError, match="discard business facts"):
            command.downgrade(config, "0068_deferred_email_claims")
    finally:
        with postgres_engine.begin() as connection:
            cleanup(connection, session, challenge, identifier)


def test_v1_upgrade_and_downgrade_preserve_ciphertext_digests_and_claim(postgres_engine):
    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0068_deferred_email_claims")
    with postgres_engine.begin() as connection:
        identifier, session, challenge = claim(connection)
        original_session = dict(
            connection.execute(
                text("SELECT * FROM profile_claim_sessions WHERE id=:id"), {"id": session}
            )
            .mappings()
            .one()
        )
        original_claim = dict(
            connection.execute(
                text("SELECT * FROM profile_claims WHERE id=:id"), {"id": identifier}
            )
            .mappings()
            .one()
        )
    try:
        command.upgrade(config, "head")
        with postgres_engine.connect() as connection:
            after = dict(
                connection.execute(
                    text("SELECT * FROM profile_claim_sessions WHERE id=:id"), {"id": session}
                )
                .mappings()
                .one()
            )
            assert after.pop("submission_version", None) == 1
            assert after == original_session
            assert (
                dict(
                    connection.execute(
                        text("SELECT * FROM profile_claims WHERE id=:id"), {"id": identifier}
                    )
                    .mappings()
                    .one()
                )
                == original_claim
            )
        command.downgrade(config, "0068_deferred_email_claims")
        with postgres_engine.connect() as connection:
            assert (
                dict(
                    connection.execute(
                        text("SELECT * FROM profile_claim_sessions WHERE id=:id"), {"id": session}
                    )
                    .mappings()
                    .one()
                )
                == original_session
            )
    finally:
        command.upgrade(config, "head")
        with postgres_engine.begin() as connection:
            cleanup(connection, session, challenge, identifier)
