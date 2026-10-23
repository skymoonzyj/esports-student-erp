from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.identity.models import Person
from app.security.encryption import LookupDigest
from tests.db.test_pending_profile_migration import document_api


def passport(api, number, country):
    return api.normalize_document_identity(
        "passport",
        number,
        issuing_country=country,
        sex="male",
        date_of_birth=date(1990, 1, 1),
        allow_pending=True,
    )


def stored_person(document, keys):
    return Person(
        full_name="Synthetic Passport",
        document_type="passport",
        document_issuing_country=document.issuing_country,
        intake_mode="pending",
        identity_ciphertext="encrypted",
        identity_digest=keys.identity_digest,
        passport_number_digest=keys.passport_number_digest,
        sex=document.sex,
        date_of_birth=document.date_of_birth,
    )


@pytest.mark.parametrize(
    "countries,expected",
    [((None, "BR"), 1), (("BR", None), 1), (("BR", "US"), 2), (("BR", "BR"), 1)],
)
def test_unknown_and_known_passport_race_is_serialized(postgres_engine, countries, expected):
    api = document_api()
    number = uuid4().hex
    lookup = LookupDigest(bytes(range(32)))
    barrier = Barrier(2)

    def create(country):
        document = passport(api, number, country)
        keys = api.document_identity_keys(document, lookup)
        with Session(postgres_engine) as session:
            barrier.wait(timeout=10)
            try:
                with session.begin():
                    api.require_document_available(session, keys)
                    session.add(stored_person(document, keys))
                return True
            except api.DocumentIdentityConflict:
                return False

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(create, countries))
        assert sum(outcomes) == expected
        with Session(postgres_engine) as session:
            assert (
                len(
                    session.scalars(
                        select(Person).where(
                            Person.passport_number_digest
                            == lookup.digest_domain("passport-number", number.upper())
                        )
                    ).all()
                )
                == expected
            )
    finally:
        with Session(postgres_engine) as session, session.begin():
            session.execute(
                delete(Person).where(
                    Person.passport_number_digest
                    == lookup.digest_domain("passport-number", number.upper())
                )
            )


def test_country_completion_conflict_rolls_back_without_replacing_person(postgres_engine):
    api = document_api()
    lookup = LookupDigest(bytes(range(32)))
    number = uuid4().hex
    documents = [passport(api, number, country) for country in ("BR", "US")]
    people = [
        stored_person(document, api.document_identity_keys(document, lookup))
        for document in documents
    ]
    with Session(postgres_engine) as session, session.begin():
        session.add_all(people)
        session.flush()
        ids = [person.id for person in people]
    try:
        keys = api.document_identity_keys(passport(api, number, "BR"), lookup)
        with (
            Session(postgres_engine) as session,
            pytest.raises(api.DocumentIdentityConflict),
            session.begin(),
        ):
            api.require_document_available(session, keys, exclude_person_id=ids[1])
            person = session.get(Person, ids[1])
            person.document_issuing_country = "BR"
            person.identity_digest = keys.identity_digest
        with Session(postgres_engine) as session:
            person = session.get(Person, ids[1])
            assert person.id == ids[1]
            assert person.document_issuing_country == "US"
    finally:
        with Session(postgres_engine) as session, session.begin():
            session.execute(delete(Person).where(Person.id.in_(ids)))
