"""Real PostgreSQL guards for personnel staff evidence, including ended history."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import Engine, event, inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.db.test_team_staff_migration import config_for

TABLES = {
    "intake_identity_selections",
    "person_team_staff_positions",
    "person_team_staff_authorizations",
}
HEAD = "0087_intake_identity_teams"


@pytest.fixture
def committed_db(postgres_engine):
    from tests.services.test_profile_claims import claim_db

    yield from claim_db.__wrapped__(postgres_engine)


@pytest.fixture
def target(committed_db):
    from tests.api.conftest import platform_users
    from tests.services.test_personnel_approval import approvals
    from tests.services.test_personnel_intake import actor, intake

    users = platform_users.__wrapped__(committed_db)
    result = approvals(committed_db).direct_create(
        actor(users, "admin"), intake(users), uuid4(), activation_role_keys=["coach"]
    )
    account = actor(users, "admin").account.id
    project, team = uuid4(), uuid4()
    committed_db.execute(
        text(
            "INSERT INTO game_projects (id,name,sort_order,is_active,is_team_division,created_by_user_id) VALUES (:id,:name,1,true,true,:actor)"
        ),
        {"id": project, "name": f"隔离分部{project.hex}", "actor": account},
    )
    committed_db.execute(
        text(
            "INSERT INTO professional_teams (id,project_id,name,is_active,created_by_user_id) VALUES (:id,:project,'隔离工作人员队伍',true,:actor)"
        ),
        {"id": team, "project": project, "actor": account},
    )
    committed_db.execute(
        text(
            "INSERT INTO team_staff_assignments (id,account_id,team_id,staff_role,created_by_user_id) VALUES (:id,:coach,:team,'coach',:actor)"
        ),
        {"id": uuid4(), "coach": users["accounts"]["coach"].id, "team": team, "actor": account},
    )
    committed_db.commit()
    return {
        "person": result.person_id,
        "approval": result.approval_id,
        "actor": account,
        "team": team,
    }


def insert_position(db, target, *, role="coach", ended=False, **changes):
    values = {"id": uuid4(), **target, "role": role, **changes}
    db.execute(
        text(
            "INSERT INTO person_team_staff_positions (id,person_id,team_id,staff_role,origin_approval_id,created_by_user_id,started_at,version,ended_at,ended_by_user_id,end_reason) VALUES (:id,:person,:team,:role,:approval,:actor,now(),1,"
            + ("now(),:actor,'unassigned'" if ended else "NULL,NULL,NULL")
            + ")"
        ),
        values,
    )
    return values["id"]


def test_empty_roundtrip_preserves_every_0086_row(postgres_engine, committed_db, target):
    config = config_for(postgres_engine)
    assert TABLES <= set(inspect(postgres_engine).get_table_names()), (
        "identity/team migration is missing"
    )
    command.downgrade(config, "0086_intake_activation_roles")
    with postgres_engine.connect() as db:
        baseline = {
            table: db.execute(
                text(f'SELECT to_jsonb(t)::text FROM "{table}" t ORDER BY to_jsonb(t)::text')
            )
            .scalars()
            .all()
            for table in inspect(db).get_table_names()
            if table != "alembic_version"
        }
    try:
        command.upgrade(config, "head")
        with postgres_engine.connect() as db:
            for table, rows in baseline.items():
                assert (
                    db.execute(
                        text(
                            f'SELECT to_jsonb(t)::text FROM "{table}" t ORDER BY to_jsonb(t)::text'
                        )
                    )
                    .scalars()
                    .all()
                    == rows
                )
            for table in TABLES:
                assert db.scalar(text(f'SELECT count(*) FROM "{table}"')) == 0
    finally:
        command.upgrade(config, "head")


def test_current_position_is_unique_but_ended_position_can_be_replaced(committed_db, target):
    db = committed_db
    insert_position(db, target, ended=True)
    insert_position(db, target)
    with pytest.raises(IntegrityError), db.begin_nested():
        insert_position(db, target)
    insert_position(db, target, role="assistant_coach")
    other = uuid4()
    db.execute(
        text(
            "INSERT INTO professional_teams (id,project_id,name,is_active,created_by_user_id) SELECT :id,project_id,'另一隔离队伍',true,:actor FROM professional_teams WHERE id=:team"
        ),
        {**target, "id": other},
    )
    insert_position(db, target, team=other)
    assert (
        db.scalar(
            text("SELECT count(*) FROM person_team_staff_positions WHERE person_id=:person"), target
        )
        == 4
    )


def test_origin_approval_must_belong_to_person(committed_db, target):
    other_person = committed_db.scalar(
        text("SELECT person_id FROM user_accounts WHERE id=:actor"), target
    )
    with pytest.raises(IntegrityError), committed_db.begin_nested():
        insert_position(committed_db, target, person=other_person)


@pytest.mark.parametrize("history", ["identity", "position", "authorization"])
def test_downgrade_refuses_any_evidence_history(postgres_engine, committed_db, target, history):
    db = committed_db
    if history == "identity":
        db.execute(
            text(
                "INSERT INTO intake_identity_selections (person_id,origin_approval_id,primary_identity,concurrent_identities,resolved_staff_type,created_by_user_id,operation_summary,operation_digest) VALUES (:person,:approval,'coach','{}','coach',:actor,'{}',:digest)"
            ),
            {**target, "digest": "a" * 64},
        )
    else:
        position = insert_position(db, target, ended=True)
        if history == "authorization":
            db.execute(
                text(
                    "INSERT INTO person_team_staff_authorizations (id,position_id,version,authorizer_user_id,authorized_role_key,operation_id,evidence_digest) VALUES (:id,:position,1,:actor,'coach',:approval,:digest)"
                ),
                {**target, "id": uuid4(), "position": position, "digest": "b" * 64},
            )
    db.commit()
    with pytest.raises(RuntimeError, match="历史.*拒绝降级"):
        command.downgrade(config_for(postgres_engine), "0086_intake_activation_roles")
    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == HEAD
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM "
                    + (
                        "intake_identity_selections"
                        if history == "identity"
                        else "person_team_staff_positions"
                    )
                )
            )
            == 1
        )


def test_authorization_role_and_version_are_enforced(committed_db, target):
    db = committed_db
    position = insert_position(db, target)
    query = text(
        "INSERT INTO person_team_staff_authorizations (id,position_id,version,authorizer_user_id,authorized_role_key,operation_id,evidence_digest) VALUES (:id,:position,:version,:actor,:role,:approval,:digest)"
    )
    params = {
        **target,
        "id": uuid4(),
        "position": position,
        "digest": "c" * 64,
        "role": "coach",
        "version": 1,
    }
    db.execute(query, params)
    with pytest.raises(IntegrityError), db.begin_nested():
        db.execute(query, {**params, "id": uuid4()})
    with pytest.raises(IntegrityError), db.begin_nested():
        db.execute(query, {**params, "id": uuid4(), "version": 2, "role": "team_manager"})


@pytest.mark.parametrize(
    "table", ["intake_identity_selections", "person_team_staff_authorizations"]
)
@pytest.mark.parametrize("action", ["UPDATE", "DELETE"])
def test_creation_and_authorization_evidence_are_append_only(committed_db, target, table, action):
    db = committed_db
    if table == "intake_identity_selections":
        db.execute(
            text(
                "INSERT INTO intake_identity_selections (person_id,origin_approval_id,primary_identity,concurrent_identities,resolved_staff_type,created_by_user_id,operation_summary,operation_digest) VALUES (:person,:approval,'coach','{}','coach',:actor,'{}',:digest)"
            ),
            {**target, "digest": "a" * 64},
        )
    else:
        position = insert_position(db, target)
        db.execute(
            text(
                "INSERT INTO person_team_staff_authorizations (id,position_id,version,authorizer_user_id,authorized_role_key,operation_id,evidence_digest) VALUES (:id,:position,1,:actor,'coach',:approval,:digest)"
            ),
            {**target, "id": uuid4(), "position": position, "digest": "b" * 64},
        )
    query = f"DELETE FROM {table}" if action == "DELETE" else f"UPDATE {table} SET created_at=now()"
    with pytest.raises(IntegrityError, match="immutable"), db.begin_nested():
        db.execute(text(query))
    assert db.scalar(text(f"SELECT count(*) FROM {table}")) == 1


@pytest.fixture
def position_history(committed_db, target):
    """Real alternative rows so missing guards cannot hide behind invalid FKs."""
    from app.auth.dependencies import AuthContext
    from app.auth.models import UserAccount
    from app.identity.models import Person
    from app.personnel_intake.models import AssistedApproval, PersonnelIntakeRequest
    from app.personnel_intake.schemas import AssistedProfileCorrection, IntakeInput
    from tests.api.conftest import make_cn_id
    from tests.services.test_personnel_approval import approvals

    db = committed_db
    account = db.get(UserAccount, target["actor"])
    who = AuthContext(account=account, person=db.get(Person, account.person_id), session=None)
    old = db.get(AssistedApproval, target["approval"])
    payload = approvals(db)._payload(db.get(PersonnelIntakeRequest, old.request_id))
    fields = payload.model_dump()
    corrected = approvals(db).correct_profile(
        who,
        target["person"],
        AssistedProfileCorrection(
            **{key: fields[key] for key in AssistedProfileCorrection.model_fields if key in fields},
            expected_version=1,
            reason="隔离来源历史核验",
            idempotency_key=uuid4(),
        ),
    )
    other = approvals(db).direct_create(
        who,
        IntakeInput.model_validate(
            {
                **fields,
                "identity_number": make_cn_id("20000102", 323),
                "email": "other-history@example.com",
                "full_name": "隔离历史对照人员",
            }
        ),
        uuid4(),
    )
    other_actor = db.scalar(
        text("SELECT id FROM user_accounts WHERE person_id=:person"),
        {"person": payload.coach_person_ids[0]},
    )
    other_team = uuid4()
    db.execute(
        text(
            "INSERT INTO professional_teams (id,project_id,name,is_active,created_by_user_id) SELECT :id,project_id,'隔离历史对照队伍',true,:actor FROM professional_teams WHERE id=:team"
        ),
        {**target, "id": other_team},
    )
    return {
        **target,
        "other_person": other.person_id,
        "other_approval": other.approval_id,
        "successor_approval": corrected.approval_id,
        "other_team": other_team,
        "other_actor": other_actor,
    }


def append_position_proof(db, target, position, version=1):
    from app.security.encryption import LookupDigest
    from app.team_management.person_staff_schemas import (
        StaffAuthorizationEvidence,
        staff_authorization_digest,
    )

    evidence = StaffAuthorizationEvidence(
        position_id=position,
        person_id=target["person"],
        team_id=target["team"],
        origin_approval_id=target["approval"],
        staff_role="coach",
        version=version,
        authorizer_user_id=target["actor"],
        authorized_role_key="coach",
        role_plan_id=None,
        operation_id=uuid4(),
    )
    db.execute(
        text(
            "INSERT INTO person_team_staff_authorizations (id,position_id,version,authorizer_user_id,authorized_role_key,operation_id,evidence_digest) VALUES (:id,:position,:version,:actor,'coach',:operation,:digest)"
        ),
        {
            "id": uuid4(),
            "position": position,
            "version": version,
            "actor": target["actor"],
            "operation": evidence.operation_id,
            "digest": staff_authorization_digest(LookupDigest(bytes(range(32))), evidence),
        },
    )


@pytest.mark.parametrize("with_proof", [False, True])
@pytest.mark.parametrize(
    "change",
    [
        "id",
        "person_id",
        "team_id",
        "staff_role",
        "origin_approval_id",
        "created_by_user_id",
        "started_at",
    ],
)
def test_position_source_facts_cannot_be_rewritten(
    committed_db, position_history, change, with_proof
):
    db, target = committed_db, position_history
    position = insert_position(db, target)
    if with_proof:
        append_position_proof(db, target, position)
    original = (
        db.execute(text("SELECT * FROM person_team_staff_positions WHERE id=:id"), {"id": position})
        .mappings()
        .one()
    )
    updates = {
        "id": ("id=:replacement", uuid4()),
        "person_id": (
            "person_id=:replacement, origin_approval_id=:other_approval",
            target["other_person"],
        ),
        "team_id": ("team_id=:replacement", target["other_team"]),
        "staff_role": ("staff_role=:replacement", "assistant_coach"),
        "origin_approval_id": ("origin_approval_id=:replacement", target["successor_approval"]),
        "created_by_user_id": ("created_by_user_id=:replacement", target["other_actor"]),
        "started_at": ("started_at=started_at + interval '1 day'", None),
    }
    clause, replacement = updates[change]
    with pytest.raises(IntegrityError, match="position history immutable"), db.begin_nested():
        db.execute(
            text(f"UPDATE person_team_staff_positions SET {clause} WHERE id=:id"),
            {**target, "id": position, "replacement": replacement},
        )
    assert (
        db.execute(text("SELECT * FROM person_team_staff_positions WHERE id=:id"), {"id": position})
        .mappings()
        .one()
        == original
    )


@pytest.mark.parametrize("ended", [False, True])
def test_position_history_cannot_be_deleted_even_without_proof(committed_db, target, ended):
    position = insert_position(committed_db, target, ended=ended)
    with (
        pytest.raises(IntegrityError, match="position history immutable"),
        committed_db.begin_nested(),
    ):
        committed_db.execute(
            text("DELETE FROM person_team_staff_positions WHERE id=:id"), {"id": position}
        )
    assert (
        committed_db.scalar(
            text("SELECT count(*) FROM person_team_staff_positions WHERE id=:id"), {"id": position}
        )
        == 1
    )


@pytest.mark.parametrize("change", ["reopen", "ended_at", "ended_by_user_id", "end_reason"])
def test_ended_position_audit_cannot_be_rewritten_or_reopened(
    committed_db, position_history, change
):
    db, target = committed_db, position_history
    position = insert_position(db, target, ended=True)
    clauses = {
        "reopen": "ended_at=NULL, ended_by_user_id=NULL, end_reason=NULL",
        "ended_at": "ended_at=ended_at + interval '1 day'",
        "ended_by_user_id": "ended_by_user_id=:other_actor",
        "end_reason": "end_reason='rewritten'",
    }
    with pytest.raises(IntegrityError, match="position end audit immutable"), db.begin_nested():
        db.execute(
            text(f"UPDATE person_team_staff_positions SET {clauses[change]} WHERE id=:id"),
            {**target, "id": position},
        )


def test_position_reconfirmation_realization_and_end_transitions_remain_supported(
    committed_db, position_history
):
    db, target = committed_db, position_history
    position = insert_position(db, target)
    append_position_proof(db, target, position)
    account, assignment = uuid4(), uuid4()
    db.execute(
        text(
            "INSERT INTO user_accounts (id,person_id,password_hash,status,force_password_change,failed_login_count) VALUES (:id,:person,'hash','active',false,0)"
        ),
        {"id": account, "person": target["person"]},
    )
    db.execute(
        text(
            "INSERT INTO team_staff_assignments (id,account_id,team_id,staff_role,created_by_user_id) VALUES (:id,:account,:team,'coach',:actor)"
        ),
        {**target, "id": assignment, "account": account},
    )
    db.execute(
        text(
            "UPDATE person_team_staff_positions SET version=2, account_assignment_id=:assignment WHERE id=:id"
        ),
        {"id": position, "assignment": assignment},
    )
    append_position_proof(db, target, position, version=2)
    db.execute(
        text(
            "UPDATE person_team_staff_positions SET version=3, ended_at=now(), ended_by_user_id=:actor, end_reason='unassigned' WHERE id=:id"
        ),
        {**target, "id": position},
    )
    ended = db.execute(
        text(
            "SELECT ended_at,ended_by_user_id,end_reason FROM person_team_staff_positions WHERE id=:id"
        ),
        {"id": position},
    ).one()
    db.execute(
        text(
            "UPDATE person_team_staff_positions SET version=4, account_assignment_id=NULL WHERE id=:id"
        ),
        {"id": position},
    )
    assert (
        db.execute(
            text(
                "SELECT ended_at,ended_by_user_id,end_reason FROM person_team_staff_positions WHERE id=:id"
            ),
            {"id": position},
        ).one()
        == ended
    )
    assert (
        db.scalar(
            text("SELECT count(*) FROM person_team_staff_authorizations WHERE position_id=:id"),
            {"id": position},
        )
        == 2
    )
    replacement = insert_position(db, target, team=target["other_team"], role="assistant_coach")
    assert replacement != position
    assert (
        db.scalar(
            text("SELECT version FROM person_team_staff_positions WHERE id=:id"), {"id": position}
        )
        == 4
    )


def test_downgrade_lock_excludes_writers_before_history_check(postgres_engine):
    guarded, release = Event(), Event()

    def pause(connection, cursor, statement, parameters, context, executemany):
        if statement == "SELECT EXISTS (SELECT 1 FROM intake_identity_selections)":
            guarded.set()
            if not release.wait(10):
                raise TimeoutError("identity downgrade guard not released")

    event.listen(Engine, "after_cursor_execute", pause)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                command.downgrade, config_for(postgres_engine), "0086_intake_activation_roles"
            )
            try:
                assert guarded.wait(5)
                for table in TABLES:
                    with postgres_engine.connect() as writer, writer.begin():
                        writer.execute(text("SET LOCAL lock_timeout = '150ms'"))
                        with pytest.raises(DBAPIError, match="lock timeout"):
                            writer.execute(
                                text(
                                    f'UPDATE "{table}" SET created_at=created_at WHERE false'
                                    if table != "person_team_staff_positions"
                                    else f'UPDATE "{table}" SET version=version WHERE false'
                                )
                            )
            finally:
                release.set()
            future.result(timeout=10)
    finally:
        event.remove(Engine, "after_cursor_execute", pause)
        command.upgrade(config_for(postgres_engine), "head")
