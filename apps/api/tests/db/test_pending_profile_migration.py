from __future__ import annotations

import importlib
from datetime import date
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.identity.normalization import IdentityValidationError
from app.security.encryption import LookupDigest
from tests.db.test_migrations import _alembic_config, _delete_test_account, _insert_test_account


def document_api():
    spec = importlib.util.find_spec("app.identity.document_identity")
    assert spec is not None, "typed document identity contract is missing"
    return importlib.import_module("app.identity.document_identity")


def test_cn_digest_and_old_data_unchanged(postgres_engine):
    api = document_api()
    lookup = LookupDigest(bytes(range(32)))
    normalized = api.normalize_document_identity(
        "cn_id",
        "11010519491231002x",
        issuing_country=None,
        sex="female",
        date_of_birth=date(1949, 12, 31),
        allow_pending=False,
    )
    assert api.document_identity_keys(normalized, lookup).identity_digest == lookup.digest(
        "11010519491231002X"
    )
    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0068_deferred_email_claims")
    person, account = _insert_test_account(postgres_engine)
    from tests.db.test_assisted_personnel_migration import approval, request, seed_business_rows

    business = seed_business_rows(postgres_engine, person, account)
    # Capture the complete legacy schema once, before additive migrations.
    baseline_columns = {
        table: ", ".join(
            postgres_engine.dialect.identifier_preparer.quote(column["name"])
            for column in inspect(postgres_engine).get_columns(table)
        )
        for table in (
            "people",
            "user_accounts",
            "housing_stays",
            "assisted_approvals",
            "personnel_intake_revisions",
        )
    }
    with postgres_engine.begin() as connection:
        intake_request = request(connection, account)
        approval(connection, intake_request, person, account)

    def snapshot():
        with postgres_engine.connect() as connection:
            return (
                connection.execute(
                    text(f"SELECT {baseline_columns['people']} FROM people WHERE id=:p"),
                    {"p": person},
                ).one(),
                connection.execute(
                    text(
                        f"SELECT {baseline_columns['user_accounts']} FROM user_accounts WHERE id=:a"
                    ),
                    {"a": account},
                ).one(),
                connection.execute(
                    text(
                        f"SELECT {baseline_columns['housing_stays']} FROM housing_stays ORDER BY id"
                    )
                ).all(),
                connection.execute(
                    text(
                        f"SELECT {baseline_columns['assisted_approvals']} FROM assisted_approvals ORDER BY id"
                    )
                ).all(),
                connection.execute(
                    text(
                        f"SELECT {baseline_columns['personnel_intake_revisions']} "
                        "FROM personnel_intake_revisions ORDER BY id"
                    )
                ).all(),
            )

    try:
        before_people_accounts_stays = snapshot()
        legacy_stay_ids = [row.id for row in before_people_accounts_stays[2]]

        def assert_legacy_void_fields_null():
            with postgres_engine.connect() as connection:
                assert connection.execute(
                    text(
                        "SELECT id,voided_at,voided_by_user_id,void_reason_ciphertext "
                        "FROM housing_stays ORDER BY id"
                    )
                ).all() == [(stay_id, None, None, None) for stay_id in legacy_stay_ids]

        command.upgrade(config, "head")
        after_people_accounts_stays = snapshot()
        assert after_people_accounts_stays == before_people_accounts_stays
        assert_legacy_void_fields_null()
        command.downgrade(config, "0068_deferred_email_claims")
        assert snapshot() == before_people_accounts_stays
        command.upgrade(config, "head")
        assert snapshot() == before_people_accounts_stays
        assert_legacy_void_fields_null()
        with postgres_engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT document_type,intake_mode,team_allocation_status,team_allocation_version FROM people WHERE id=:p"
                ),
                {"p": person},
            ).one() == ("cn_id", "strict", "untracked", 1)
    finally:
        command.upgrade(config, "head")
        with postgres_engine.begin() as connection:
            for table in ("assisted_approvals", "personnel_intake_revisions"):
                connection.execute(
                    text(f"ALTER TABLE {table} DISABLE TRIGGER trg_{table}_immutable")
                )
                connection.execute(
                    text(f"DELETE FROM {table} WHERE request_id=:r"), {"r": intake_request}
                )
                connection.execute(
                    text(f"ALTER TABLE {table} ENABLE TRIGGER trg_{table}_immutable")
                )
            connection.execute(
                text("DELETE FROM personnel_intake_requests WHERE id=:r"), {"r": intake_request}
            )
            for table, identifier in reversed(business):
                if table == "payments":
                    connection.execute(
                        text("DELETE FROM revenue_share_reconcile_queue WHERE payment_id=:id"),
                        {"id": identifier},
                    )
                connection.execute(text(f'DELETE FROM "{table}" WHERE id=:id'), {"id": identifier})
        _delete_test_account(postgres_engine, person_id=person, account_id=account)


def test_only_pending_passport_allows_unknown_facts(postgres_engine):
    api = document_api()
    from app.identity.readiness import age_status_for

    value = api.normalize_document_identity(
        "passport",
        " ab-123 ",
        issuing_country=None,
        sex=None,
        date_of_birth=None,
        allow_pending=True,
    )
    assert (value.number, value.sex, value.date_of_birth, value.issuing_country) == (
        "AB-123",
        None,
        None,
        None,
    )
    assert age_status_for(None, date(2026, 10, 21)) == "unknown"
    assert age_status_for(date(2008, 10, 22), date(2026, 10, 21)) == "minor"
    assert age_status_for(date(2008, 10, 21), date(2026, 10, 21)) == "adult"
    with pytest.raises(IdentityValidationError):
        api.normalize_document_identity(
            "passport",
            "AB123",
            issuing_country=None,
            sex=None,
            date_of_birth=None,
            allow_pending=False,
        )
    columns = {column["name"] for column in inspect(postgres_engine).get_columns("people")}
    assert "intake_mode" in columns
    with postgres_engine.connect() as connection:
        transaction = connection.begin()
        for mode, document, allowed in (
            ("strict", "cn_id", False),
            ("strict", "passport", False),
            ("pending", "cn_id", False),
            ("pending", "passport", True),
        ):
            statement = text(
                "INSERT INTO people(id,full_name,identity_ciphertext,identity_digest,document_type,intake_mode,passport_number_digest,sex,date_of_birth) VALUES (:id,'Synthetic','encrypted',:digest,:doc,:mode,:bare,NULL,NULL)"
            )
            params = {
                "id": uuid4(),
                "digest": uuid4().hex * 2,
                "doc": document,
                "mode": mode,
                "bare": uuid4().hex * 2 if document == "passport" else None,
            }
            if allowed:
                connection.execute(statement, params)
            else:
                with pytest.raises(IntegrityError), connection.begin_nested():
                    connection.execute(statement, params)
        transaction.rollback()


def test_incomplete_passport_cannot_have_account_at_transaction_end(postgres_engine):
    api = document_api()
    assert api is not None
    person, account = _insert_test_account(postgres_engine)
    try:
        with pytest.raises(IntegrityError), postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE people SET document_type='passport',intake_mode='pending',passport_number_digest=:d,sex=NULL,date_of_birth=NULL WHERE id=:p"
                ),
                {"p": person, "d": "f" * 64},
            )
        with postgres_engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT document_type FROM people WHERE id=:p"), {"p": person}
                )
                == "cn_id"
            )
    finally:
        _delete_test_account(postgres_engine, person_id=person, account_id=account)


def test_account_write_cannot_attach_incomplete_passport(postgres_engine):
    person, account = _insert_test_account(postgres_engine)
    try:
        with pytest.raises(IntegrityError), postgres_engine.begin() as connection:
            pending = uuid4()
            connection.execute(
                text(
                    "INSERT INTO people(id,full_name,identity_ciphertext,identity_digest,document_type,intake_mode,passport_number_digest) VALUES (:p,'Synthetic','encrypted',:d,'passport','pending',:d)"
                ),
                {"p": pending, "d": uuid4().hex * 2},
            )
            connection.execute(
                text("UPDATE user_accounts SET person_id=:p WHERE id=:a"),
                {"p": pending, "a": account},
            )
        with postgres_engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT person_id FROM user_accounts WHERE id=:a"), {"a": account}
                )
                == person
            )
    finally:
        _delete_test_account(postgres_engine, person_id=person, account_id=account)


def test_downgrade_refuses_to_discard_pending_business_facts(postgres_engine):
    identifier = uuid4()
    with postgres_engine.begin() as connection:
        before_revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        connection.execute(
            text(
                "INSERT INTO people(id,full_name,identity_ciphertext,identity_digest,document_type,intake_mode,passport_number_digest) VALUES (:p,'Synthetic','encrypted',:d,'passport','pending',:d)"
            ),
            {"p": identifier, "d": uuid4().hex * 2},
        )
    try:
        with pytest.raises(RuntimeError, match="discard business facts"):
            command.downgrade(_alembic_config(postgres_engine), "0068_deferred_email_claims")
        with postgres_engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT id FROM people WHERE id=:p"), {"p": identifier})
                == identifier
            )
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == before_revision
            )
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(text("DELETE FROM people WHERE id=:p"), {"p": identifier})


@pytest.mark.parametrize("country", ["ZZ", "USA", "1A", "", "中国"])
def test_passport_rejects_unrecognized_issuing_country(country):
    api = document_api()
    with pytest.raises(IdentityValidationError):
        api.normalize_document_identity(
            "passport",
            "AB123",
            issuing_country=country,
            sex="male",
            date_of_birth=date(1990, 1, 1),
            allow_pending=True,
        )


def test_passport_domains_country_and_masking():
    api = document_api()
    lookup = LookupDigest(bytes(range(32)))
    keys = []
    for country in (None, "br", "US"):
        document = api.normalize_document_identity(
            "passport",
            " Ab-123 ",
            issuing_country=country,
            sex=None,
            date_of_birth=None,
            allow_pending=True,
        )
        keys.append(api.document_identity_keys(document, lookup))
    assert len({key.identity_digest for key in keys}) == 3
    assert len({key.passport_number_digest for key in keys}) == 1
    assert keys[1].issuing_country == "BR"
    assert keys[0].identity_digest != lookup.digest("AB-123")
    assert api.mask_document_identity("cn_id", "11010519491231002X") == "110105********002X"
    assert "AB-123" not in api.mask_document_identity("passport", "AB-123")
    assert api.mask_document_identity("passport", "AB") == "**"


def test_passport_creation_and_nickname_edit_preserve_original_identity(db):
    from app.identity.schemas import PersonUpdate
    from app.identity.service import IdentityService
    from app.security.encryption import EnvelopeCipher

    cipher = EnvelopeCipher(bytes(range(32)))
    service = IdentityService(db, cipher=cipher, lookup=LookupDigest(bytes(range(32))))
    person = service._new_person(
        full_name="Synthetic Passport",
        identity_number=" Ab-123 ",
        document_type="passport",
        document_issuing_country="BR",
        intake_mode="pending",
        sex=None,
        birth_date=None,
        phone=None,
        email=None,
    )
    identifier = person.id
    original = person.identity_ciphertext
    view = service.update_person(
        identifier,
        PersonUpdate(nickname="Test"),
        actor_user_id=None,
        request_id="passport-nickname",
    )
    assert view.nickname == "Test"
    assert view.age_status == "unknown"
    assert view.sex is None and view.date_of_birth is None
    assert cipher.decrypt(person.identity_ciphertext, field_name="identity_number") == " Ab-123 "
    assert person.identity_ciphertext == original
    assert person.id == identifier


def test_strict_create_uses_explicit_passport_type(db):
    from app.identity.schemas import PersonBaseInput
    from app.identity.service import IdentityService
    from app.security.encryption import EnvelopeCipher

    service = IdentityService(
        db, cipher=EnvelopeCipher(bytes(range(32))), lookup=LookupDigest(bytes(range(32)))
    )
    result = service.create_base_person(
        PersonBaseInput(
            full_name="Synthetic Passport",
            identity_number="Ab-321",
            document_type="passport",
            document_issuing_country="BR",
            sex="male",
            date_of_birth=date(1990, 1, 1),
        )
    )
    assert result.document_type == "passport"
    assert result.document_issuing_country == "BR"
