"""Test-only program injected into the exact owned, portless restore namespace.

No database connection occurs on import. The host supplies existing backup helper
definitions and the one pure legacy business seed function, never DB fixtures.
"""

import base64
import hashlib
import json
import os
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

OLD = "0065_invited_housing_visibility"
HEAD = "0066_assisted_personnel"
DSN = "postgresql://erp_verify@127.0.0.1:5432/erp_verify"
TABLES = (
    "personnel_intake_requests",
    "personnel_intake_revisions",
    "personnel_credential_reservations",
    "assisted_approvals",
    "assisted_person_profiles",
    "coach_person_responsibilities",
    "assisted_activation_sessions",
)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def row_fingerprints(db, tables, columns=None):
    from psycopg import sql

    result = {}
    for table in sorted(set(tables)):
        projection = (
            sql.SQL("*")
            if columns is None
            else sql.SQL(",").join(map(sql.Identifier, columns[table]))
        )
        rows = db.execute(
            sql.SQL(
                "SELECT to_jsonb(t)::text FROM (SELECT {} FROM {}) t ORDER BY to_jsonb(t)::text"
            ).format(projection, sql.Identifier(table))
        ).fetchall()
        result[table] = fingerprint(rows)
    return result


def trigger_fingerprint(db):
    definitions = db.execute("""
        SELECT t.tgname, pg_get_triggerdef(t.oid), pg_get_functiondef(t.tgfoid)
        FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND NOT t.tgisinternal ORDER BY c.relname,t.tgname
    """).fetchall()
    required = {
        "trg_assisted_account_origin_immutable",
        "trg_personnel_intake_revisions_immutable",
        "trg_assisted_approvals_immutable",
        "trg_assisted_approval_guardian_references",
    }
    assert required <= {row[0] for row in definitions}, "required_triggers_missing"
    return fingerprint(definitions)


def keys(config):
    from app.security.encryption import EnvelopeCipher, LookupDigest

    return (
        EnvelopeCipher(base64.urlsafe_b64decode(config["field_encryption_master_key"])),
        LookupDigest(base64.urlsafe_b64decode(config["field_lookup_key"])),
    )


def person_values(index, cipher, lookup):
    # Entirely synthetic identities, with internally consistent checksum/date/sex.
    birth = date(2010, 1, 2) if index == 2 else date(1988, 1, 2)
    stem = "110101" + birth.strftime("%Y%m%d") + f"{index * 2 + 1:03d}"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    identity = stem + "10X98765432"[sum(int(n) * w for n, w in zip(stem, weights)) % 11]
    phone, email = f"13900000{index:03d}", f"restore-{index}@example.test"
    return {
        "id": uuid4(),
        "full_name": f"Synthetic restore {index}",
        "sex": "male",
        "date_of_birth": birth,
        "identity_ciphertext": cipher.encrypt(identity, field_name="identity_number"),
        "identity_digest": lookup.digest(identity),
        "phone_ciphertext": cipher.encrypt(phone, field_name="phone"),
        "phone_digest": lookup.digest(phone),
        "email_ciphertext": cipher.encrypt(email, field_name="email"),
        "email_digest": lookup.digest_domain("person-email", email),
    }


def produce(config, seed_business_rows, database_manifest):
    import psycopg
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    from app.auth.models import AccountLoginIdentifier
    from app.identity.models import GuardianProfile, GuardianRelationship, Person
    from app.personnel_intake.activation_models import AssistedActivationSession
    from app.personnel_intake.guardian_records import (
        canonical_json,
        capture_current_snapshots,
        person_facts,
    )
    from app.personnel_intake.models import (
        AssistedApproval,
        AssistedPersonProfile,
        CoachPersonResponsibility,
        PersonnelCredentialReservation,
        PersonnelIntakeRequest,
        PersonnelIntakeRevision,
    )
    from app.registration.models import RegistrationVerificationChallenge
    from app.team_management.models import TeamMembership

    cipher, lookup = keys(config)
    for key, value in config.items():
        os.environ[key.upper()] = value
    migration = Config("/app/alembic.ini")
    migration.attributes["database_url"] = DSN.replace("postgresql://", "postgresql+psycopg://")
    command.upgrade(migration, OLD)
    engine = create_engine(migration.attributes["database_url"], hide_parameters=True)
    actor, legacy = uuid4(), person_values(1, cipher, lookup)
    with engine.begin() as connection:
        connection.execute(Person.__table__.insert().values(**legacy))
        connection.execute(
            text("""
            INSERT INTO user_accounts(id,person_id,login_phone_digest,password_hash,status,
                force_password_change,failed_login_count)
            VALUES (:id,:person,:phone,'synthetic-unusable-hash','active',false,0)
        """),
            {"id": actor, "person": legacy["id"], "phone": legacy["phone_digest"]},
        )
        connection.execute(
            AccountLoginIdentifier.__table__.insert().values(
                id=uuid4(),
                account_id=actor,
                kind="phone",
                identifier_digest=lookup.digest_domain("account-login-phone", "13900000001"),
                verified_at=datetime.now(UTC),
                verification_source="legacy_phone_login",
                is_primary=True,
            )
        )
        connection.execute(
            text(
                "INSERT INTO user_roles(user_id,role_id) SELECT :id,id FROM roles WHERE key='registered_user'"
            ),
            {"id": actor},
        )
    # Extracted pure function supplied by controller; no conftest/reset import.
    inserted = seed_business_rows(engine, legacy["id"], actor)
    old_tables = sorted(
        {table for table, _ in inserted}
        | {"people", "user_accounts", "account_login_identifiers", "user_roles"}
    )
    team_id = next(identifier for table, identifier in inserted if table == "professional_teams")
    with psycopg.connect(DSN) as db:
        columns = {
            table: [
                r[0]
                for r in db.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema='public' "
                    "AND table_name=%s ORDER BY ordinal_position",
                    (table,),
                )
            ]
            for table in old_tables
        }
        before = row_fingerprints(db, old_tables, columns)
    command.upgrade(migration, HEAD)
    with psycopg.connect(DSN) as db:
        assert row_fingerprints(db, old_tables, columns) == before, "legacy_upgrade_changed_values"

    evidence = {"legacy_account": str(actor), "legacy_person": str(legacy["id"])}
    expected_plain_hashes = {}
    now = datetime.now(UTC)
    with Session(engine) as session, session.begin():
        people = [Person(**person_values(n, cipher, lookup)) for n in (2, 3, 4)]
        session.add_all(people)
        session.flush()
        dependent, guardian, independent = people
        session.add(GuardianProfile(person_id=guardian.id))
        edge = GuardianRelationship(
            guardian_person_id=guardian.id,
            student_person_id=dependent.id,
            relationship="父亲",
            is_primary=True,
            valid_from=date(2026, 1, 1),
            requires_verification=True,
        )
        session.add(edge)
        session.flush()
        evidence["independent"] = str(independent.id)
        evidence["dependent"] = str(dependent.id)
        session.add_all(
            [
                TeamMembership(
                    person_id=person.id,
                    team_id=team_id,
                    joined_on=date(2026, 10, 1),
                    created_by_user_id=actor,
                )
                for person in (dependent, independent)
            ]
        )

        def approve(person, version=1, previous=None, request=None, relationships=()):
            if request is None:
                request = PersonnelIntakeRequest(
                    submitted_by_user_id=actor,
                    version=version,
                    state="approved",
                    kind="player",
                    person_id=person.id,
                    target_team_id=team_id,
                )
                session.add(request)
                session.flush()
            else:
                request.version = version
            facts = person_facts(person, cipher)
            person_raw, guardian_raw = capture_current_snapshots(
                session, person_id=person.id, relationship_ids=list(relationships), cipher=cipher
            )
            payload = {
                key: facts[key]
                for key in (
                    "full_name",
                    "identity_number",
                    "phone",
                    "email",
                    "sex",
                    "date_of_birth",
                )
            }
            guardians = []
            for record in json.loads(guardian_raw):
                guardian_values = {
                    key: record["guardian"][key]
                    for key in (
                        "full_name",
                        "identity_number",
                        "phone",
                        "email",
                        "sex",
                        "date_of_birth",
                    )
                }
                guardian_values.update(
                    relationship=record["relationship"], is_primary=record["is_primary"]
                )
                guardians.append(guardian_values)
            payload.update(
                kind="player", coach_person_ids=[], team_id=str(team_id), guardians=guardians
            )
            raw = canonical_json(payload)
            revision = PersonnelIntakeRevision(
                request_id=request.id,
                version=version,
                payload_ciphertext=cipher.encrypt(raw, field_name="personnel_intake_payload"),
                payload_digest=lookup.digest_domain("personnel-intake-payload", raw),
                identity_digest=person.identity_digest,
                email_digest=person.email_digest,
                created_by_user_id=actor,
            )
            session.add(revision)
            session.flush()
            approval = AssistedApproval(
                request_id=request.id,
                revision_version=version,
                person_id=person.id,
                approved_by_user_id=actor,
                snapshot_ciphertext=cipher.encrypt(
                    person_raw, field_name="assisted_person_snapshot"
                ),
                snapshot_digest=lookup.digest_domain("assisted-person-snapshot", person_raw),
                guardian_snapshot_ciphertext=cipher.encrypt(
                    guardian_raw, field_name="assisted_guardian_snapshot"
                ),
                guardian_snapshot_digest=lookup.digest_domain(
                    "assisted-guardian-snapshot", guardian_raw
                ),
                guardian_relationship_ids=list(relationships),
                guardian_relationship_version=version,
                supersedes_approval_id=previous.id if previous else None,
            )
            session.add(approval)
            session.flush()
            expected_plain_hashes[str(revision.id)] = fingerprint(raw)
            expected_plain_hashes[str(approval.id)] = [
                fingerprint(person_raw),
                fingerprint(guardian_raw),
            ]
            return request, approval

        request, original = approve(dependent, relationships=[edge.id])
        account = uuid4()
        session.execute(
            text("""
            INSERT INTO user_accounts(id,person_id,password_hash,status,force_password_change,
                failed_login_count,account_access_origin,assisted_approval_id)
            VALUES (:id,:person,'synthetic-unusable-hash','active',false,0,'assisted_activation',:approval)
        """),
            {"id": account, "person": dependent.id, "approval": original.id},
        )
        dependent.phone_ciphertext = cipher.encrypt("19990000002", field_name="phone")
        dependent.phone_digest = lookup.digest("19990000002")
        session.flush()
        _, corrected = approve(
            dependent, version=2, previous=original, request=request, relationships=[edge.id]
        )
        _, independent_approval = approve(independent)
        _, legacy_approval = approve(session.get(Person, legacy["id"]))
        session.add_all(
            [
                AssistedPersonProfile(
                    person_id=dependent.id, current_approval_id=corrected.id, version=2
                ),
                AssistedPersonProfile(
                    person_id=independent.id, current_approval_id=independent_approval.id
                ),
                PersonnelCredentialReservation(
                    kind="email",
                    digest=dependent.email_digest,
                    request_id=request.id,
                    person_id=dependent.id,
                ),
                CoachPersonResponsibility(
                    coach_person_id=legacy["id"], person_id=independent.id, created_by_user_id=actor
                ),
            ]
        )
        challenge = RegistrationVerificationChallenge(
            purpose="assisted_activation",
            identifier_kind="email",
            identifier_digest=dependent.email_digest,
            code_digest=lookup.digest_domain("test-code", "unused"),
            request_source_digest=lookup.digest_domain("test-source", "private"),
            expires_at=now + timedelta(minutes=10),
            last_sent_at=now,
        )
        session.add(challenge)
        session.flush()
        session.add(
            AssistedActivationSession(
                token_digest=lookup.digest_domain("test-token", "private"),
                challenge_id=challenge.id,
                person_id=dependent.id,
                approval_id=corrected.id,
                revision_version=2,
                email_digest=dependent.email_digest,
                state="requested",
                expires_at=now + timedelta(minutes=10),
            )
        )
        evidence.update(
            assisted_account=str(account),
            original=str(original.id),
            corrected=str(corrected.id),
            legacy_approval=str(legacy_approval.id),
            request=str(request.id),
            guardian_edge=str(edge.id),
        )
    engine.dispose()
    with psycopg.connect(DSN) as db:
        metadata = database_manifest(db)
        evidence.update(
            rows=row_fingerprints(
                db,
                old_tables
                + list(TABLES)
                + [
                    "guardian_relationships",
                    "guardian_profiles",
                    "student_profiles",
                    "registration_verification_challenges",
                ],
            ),
            triggers=trigger_fingerprint(db),
            plaintext=expected_plain_hashes,
            old_upgrade_preserved=True,
        )
    metadata.update(
        media=[],
        key_canary=cipher.encrypt("erp-backup-recovery-v1", field_name="backup"),
        lookup_canary=hashlib.sha256(
            ("erp-backup-lookup-v1:" + config["field_lookup_key"]).encode()
        ).hexdigest(),
    )
    return {"database": metadata, "evidence": evidence}


def probe(config, evidence):
    import psycopg
    from psycopg import sql
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from app.personnel_intake.guardian_records import authenticated_approval_facts, canonical_json
    from app.personnel_intake.models import AssistedApproval

    cipher, lookup = keys(config)
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchall() == [(HEAD,)]
        assert row_fingerprints(db, evidence["rows"]) == evidence["rows"], "restored_values_changed"
        assert trigger_fingerprint(db) == evidence["triggers"], "restored_trigger_function_changed"
        counts = {
            table: db.execute(
                sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
            ).fetchone()[0]
            for table in TABLES
        }
        assert all(counts.values()), "new_table_empty"
        assert (
            db.execute(
                "SELECT count(*) FROM student_profiles WHERE person_id=%s",
                (evidence["independent"],),
            ).fetchone()[0]
            == 0
        )
        assert (
            db.execute(
                "SELECT count(*) FROM user_accounts WHERE person_id=%s", (evidence["independent"],)
            ).fetchone()[0]
            == 0
        )
        assert db.execute(
            "SELECT account_access_origin,assisted_approval_id::text FROM user_accounts WHERE id=%s",
            (evidence["legacy_account"],),
        ).fetchone() == ("legacy", None)
        assert db.execute(
            "SELECT account_access_origin,assisted_approval_id::text FROM user_accounts WHERE id=%s",
            (evidence["assisted_account"],),
        ).fetchone() == ("assisted_activation", evidence["original"])

        def rejects(statement, args, state, constraint=None, trigger=None):
            db.execute("SAVEPOINT rejection")
            try:
                db.execute(statement, args)
                db.execute("SET CONSTRAINTS ALL IMMEDIATE")
            except psycopg.Error as exc:
                assert exc.sqlstate == state, "unexpected_rejection_state"
                if constraint:
                    assert exc.diag.constraint_name == constraint, "unexpected_rejection_constraint"
                if trigger:
                    assert trigger in (exc.diag.context or ""), "immutability_trigger_not_exercised"
            else:
                raise AssertionError("invalid_mutation_accepted")
            finally:
                db.execute("ROLLBACK TO SAVEPOINT rejection")
                db.execute("RELEASE SAVEPOINT rejection")

        # All replacements are CHECK-valid and refer to an approval for the SAME
        # person: a failing CHECK or composite FK cannot masquerade as immutability.
        rejects(
            "UPDATE user_accounts SET account_access_origin='assisted_activation',assisted_approval_id=%s WHERE id=%s",
            (evidence["legacy_approval"], evidence["legacy_account"]),
            "23514",
            trigger="assisted_account_origin_immutable",
        )
        rejects(
            "UPDATE user_accounts SET account_access_origin='legacy',assisted_approval_id=NULL WHERE id=%s",
            (evidence["assisted_account"],),
            "23514",
            trigger="assisted_account_origin_immutable",
        )
        rejects(
            "UPDATE user_accounts SET assisted_approval_id=%s WHERE id=%s",
            (evidence["corrected"], evidence["assisted_account"]),
            "23514",
            trigger="assisted_account_origin_immutable",
        )
        rejects(
            "UPDATE personnel_intake_requests SET state='invalid' WHERE id=%s",
            (evidence["request"],),
            "23514",
            constraint="ck_personnel_intake_requests_state",
        )
        rejects(
            "UPDATE assisted_person_profiles SET current_approval_id=%s WHERE person_id=%s",
            (evidence["legacy_approval"], evidence["dependent"]),
            "23503",
            constraint="fk_assisted_profile_approval_person",
        )
        rejects(
            "INSERT INTO personnel_credential_reservations(id,kind,digest,request_id,person_id) "
            "SELECT %s,kind,digest,request_id,person_id FROM personnel_credential_reservations LIMIT 1",
            (uuid4(),),
            "23505",
            constraint="uq_personnel_reservations_active",
        )
        for table in ("personnel_intake_revisions", "assisted_approvals"):
            rejects(
                sql.SQL("UPDATE {} SET id=id").format(sql.Identifier(table)),
                (),
                "23514",
                trigger="assisted_snapshot_immutable",
            )
            rejects(
                sql.SQL("DELETE FROM {}").format(sql.Identifier(table)),
                (),
                "23514",
                trigger="assisted_snapshot_immutable",
            )
        # Guardian reference trigger runs before other constraints on this duplicate
        # row and rejects a real relationship belonging to a different person.
        rejects(
            "INSERT INTO assisted_approvals SELECT id,request_id,revision_version, %s,approved_by_user_id,"
            "approved_at,snapshot_ciphertext,snapshot_digest,guardian_relationship_version,"
            "guardian_relationship_ids,guardian_snapshot_ciphertext,guardian_snapshot_digest,"
            "supersedes_approval_id,operation_key,operation_digest,operation_kind "
            "FROM assisted_approvals WHERE id=%s",
            (evidence["independent"], evidence["original"]),
            "23503",
            trigger="assisted_approval_guardian_references",
        )
        for identifier, encrypted, digest in db.execute(
            "SELECT id,payload_ciphertext,payload_digest FROM personnel_intake_revisions"
        ):
            raw = cipher.decrypt(encrypted, field_name="personnel_intake_payload")
            assert lookup.digest_domain("personnel-intake-payload", raw) == digest
            assert fingerprint(raw) == evidence["plaintext"][str(identifier)]
        assert row_fingerprints(db, evidence["rows"]) == evidence["rows"], "probe_changed_rows"
    engine = create_engine(
        DSN.replace("postgresql://", "postgresql+psycopg://"), hide_parameters=True
    )
    with Session(engine) as session:
        for approval in session.scalars(select(AssistedApproval)):
            person, guardians = authenticated_approval_facts(approval, cipher=cipher, lookup=lookup)
            assert [
                fingerprint(canonical_json(person)),
                fingerprint(canonical_json(guardians)),
            ] == evidence["plaintext"][str(approval.id)]
    engine.dispose()
    return counts
