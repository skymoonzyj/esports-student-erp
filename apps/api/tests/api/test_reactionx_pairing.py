from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, update

from app.auth.models import AuthSession, UserAccount
from app.identity.models import Person
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES

WEB = "/api/v1/reactionx"
MACHINE = "/api/v1/reactionx-client"
ORIGIN = "https://skymoonzyj.l.cd:18080"
SECRET = "A" * 43
DIGEST = sha256(SECRET.encode("ascii")).hexdigest()


def browser_headers(login):
    return {"X-CSRF-Token": login.csrf_token}


def create(client, login):
    response = client.post(f"{WEB}/pairings", headers=browser_headers(login))
    assert response.status_code == 200, response.text
    return response.json()


def claim(client, pairing, **extra):
    return client.post(
        f"{MACHINE}/pairings/claim",
        json={
            "challenge": pairing["challenge"],
            "credential_digest": DIGEST,
            "client_info": {"origin": ORIGIN, "name": "synthetic-windows"},
            **extra,
        },
    )


def machine_headers(value, secret=SECRET):
    return {"Authorization": f"ReactionX {value['connector_id']}.{secret}"}


def test_anonymous_browser_and_cookie_only_machine_are_rejected(db_client):
    assert db_client.post(f"{WEB}/pairings").status_code == 401
    assert db_client.get(f"{WEB}/people").status_code == 401
    assert db_client.get(f"{MACHINE}/status").status_code == 401


def test_browser_mutations_require_csrf(db_client, login_as_admin):
    response = db_client.post(f"{WEB}/pairings")
    assert response.status_code == 403 and response.json()["code"] == "csrf_invalid"


def test_claim_returns_stored_owner_and_trusted_site_without_personnel_details(
    db_client, login_as_admin
):
    value = claim(db_client, create(db_client, login_as_admin)).json()
    assert value["actor_account_id"] == str(login_as_admin.account_id)
    assert value["actor_name"] == "平台管理员"
    assert value["origin"] == ORIGIN
    assert set(value) == {
        "pairing_id",
        "connector_id",
        "verification_phrase",
        "expires_at",
        "status",
        "browser_confirmed",
        "device_confirmed",
        "actor_account_id",
        "actor_name",
        "origin",
    }


def test_configured_development_browser_origin_keeps_normal_csrf(db_client, login_as_admin):
    origin = db_client.app.state.settings.erp_web_base_url
    assert db_client.post(f"{WEB}/pairings", headers={"Origin": origin}).status_code == 403
    response = db_client.post(
        f"{WEB}/pairings", headers={**browser_headers(login_as_admin), "Origin": origin}
    )
    assert response.status_code == 200
    assert (
        db_client.post(
            f"{WEB}/pairings",
            headers={**browser_headers(login_as_admin), "Origin": "http://attacker.invalid"},
        ).status_code
        == 403
    )
    db_client.app.state.settings.app_env = "production"
    assert (
        db_client.post(
            f"{WEB}/pairings", headers={**browser_headers(login_as_admin), "Origin": origin}
        ).status_code
        == 403
    )
    assert (
        db_client.post(
            f"{WEB}/pairings", headers={**browser_headers(login_as_admin), "Origin": ORIGIN}
        ).status_code
        == 200
    )


@pytest.mark.parametrize("denial", ["role", "manage", "conduct"])
def test_pilot_permissions_are_live_superadmin_and_deny_first(
    db_client, db, login_as_admin, denial
):
    actor = login_as_admin.account_id
    if denial == "role":
        db.execute(delete(UserRole).where(UserRole.user_id == actor))
        db.add(UserRole(user_id=actor, role_id=ROLES["admin"].id))
        db.add(
            UserPermissionOverride(
                user_id=actor,
                permission_id=PERMISSIONS["reactionx.manage"].id,
                effect="allow",
                scope_kind="global",
                created_by_user_id=actor,
            )
        )
    else:
        db.add(
            UserPermissionOverride(
                user_id=actor,
                permission_id=PERMISSIONS[f"reactionx.{denial}"].id,
                effect="deny",
                scope_kind="global",
                created_by_user_id=actor,
            )
        )
    db.commit()
    route = "/people" if denial == "conduct" else "/pairings"
    response = (
        db_client.get(WEB + route)
        if denial == "conduct"
        else db_client.post(WEB + route, headers=browser_headers(login_as_admin))
    )
    assert response.status_code == 403


def test_two_ended_confirmation_and_secret_never_returned_or_stored(db_client, db, login_as_admin):
    from app.audit.models import AuditEvent
    from app.reactionx.models import ReactionxConnector, ReactionxPairing

    pairing = create(db_client, login_as_admin)
    assert len(pairing["challenge"]) == 43
    assert pairing["pair_uri"] == f"reactionx-erp://pair?challenge={pairing['challenge']}"
    response = claim(db_client, pairing)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["status"] == "pending"
    assert db_client.get(f"{MACHINE}/status", headers=machine_headers(value)).status_code == 403
    assert (
        db_client.post(
            f"{WEB}/pairings/{pairing['pairing_id']}/confirm",
            json={"verification_phrase": value["verification_phrase"]},
            headers=browser_headers(login_as_admin),
        ).json()["status"]
        == "pending"
    )
    assert db_client.get(f"{MACHINE}/status", headers=machine_headers(value)).status_code == 403
    confirmed = db_client.post(
        f"{MACHINE}/pairings/{pairing['pairing_id']}/confirm",
        json={"verification_phrase": value["verification_phrase"]},
        headers=machine_headers(value),
    )
    assert confirmed.status_code == 200 and confirmed.json()["status"] == "active"
    assert db_client.get(f"{MACHINE}/status", headers=machine_headers(value)).status_code == 200
    assert db_client.get(f"{WEB}/people", headers=machine_headers(value)).status_code == 401
    assert db_client.get(f"{MACHINE}/people", headers=machine_headers(value)).status_code == 404
    connector = db.get(ReactionxConnector, value["connector_id"])
    stored = db.get(ReactionxPairing, pairing["pairing_id"])
    assert connector.credential_digest == DIGEST and stored.challenge_digest != pairing["challenge"]
    assert SECRET not in confirmed.text
    events = list(db.scalars(select(AuditEvent).where(AuditEvent.action.like("reactionx.%"))))
    assert {e.action for e in events} >= {
        "reactionx.pairing.create",
        "reactionx.pairing.claim",
        "reactionx.pairing.activate",
    }
    assert all(
        SECRET not in str(e.event_metadata) and pairing["challenge"] not in str(e.event_metadata)
        for e in events
    )


def test_claim_once_and_expiry_fail_closed(db_client, db, login_as_admin):
    from app.reactionx.models import ReactionxPairing

    pairing = create(db_client, login_as_admin)
    assert claim(db_client, pairing).status_code == 200
    assert claim(db_client, pairing).status_code == 409
    expired = create(db_client, login_as_admin)
    db.execute(
        update(ReactionxPairing)
        .where(ReactionxPairing.id == expired["pairing_id"])
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    db.commit()
    assert claim(db_client, expired).status_code == 410


def test_other_origin_bad_digest_and_unknown_fields_are_safe(db_client, login_as_admin):
    pairing = create(db_client, login_as_admin)
    for extra in (
        {"client_info": {"origin": "https://attacker.invalid", "name": "synthetic"}},
        {"credential_digest": "not-a-digest"},
        {"secret-marker": "private-marker"},
    ):
        response = claim(db_client, pairing, **extra)
        assert response.status_code in (403, 422)
        assert "private-marker" not in response.text
        assert "no-store" in response.headers["cache-control"]
    assert claim(db_client, pairing).status_code == 200


def test_credential_wrong_and_pending_device_cannot_read_person_data(db_client, login_as_admin):
    pairing = create(db_client, login_as_admin)
    value = claim(db_client, pairing).json()
    assert (
        db_client.get(
            f"{MACHINE}/pairings/{pairing['pairing_id']}", headers=machine_headers(value, "B" * 43)
        ).status_code
        == 401
    )
    assert (
        db_client.get(
            f"{MACHINE}/pairings/{pairing['pairing_id']}", headers=machine_headers(value)
        ).status_code
        == 200
    )
    assert (
        db_client.get(f"{MACHINE}/pairings/{uuid4()}", headers=machine_headers(value)).status_code
        == 404
    )


@pytest.mark.parametrize("revocation", ["account", "session", "role", "permission"])
def test_pending_confirmation_rechecks_original_actor(db_client, db, login_as_admin, revocation):
    pairing = create(db_client, login_as_admin)
    value = claim(db_client, pairing).json()
    actor = login_as_admin.account_id
    if revocation == "account":
        db.execute(update(UserAccount).where(UserAccount.id == actor).values(status="disabled"))
    elif revocation == "session":
        db.execute(
            update(AuthSession)
            .where(AuthSession.user_id == actor)
            .values(revoked_at=datetime.now(UTC))
        )
    elif revocation == "role":
        db.execute(delete(UserRole).where(UserRole.user_id == actor))
    else:
        db.add(
            UserPermissionOverride(
                user_id=actor,
                permission_id=PERMISSIONS["reactionx.manage"].id,
                effect="deny",
                scope_kind="global",
                created_by_user_id=actor,
            )
        )
    db.commit()
    assert db_client.post(
        f"{MACHINE}/pairings/{pairing['pairing_id']}/confirm",
        headers=machine_headers(value),
        json={"verification_phrase": value["verification_phrase"]},
    ).status_code in (401, 403)


@pytest.mark.parametrize("identity", ["same", "other"])
def test_wrong_browser_session_and_wrong_phrase_rejected(db_client, db, login_as_admin, identity):
    from app.auth.service import AuthContext
    from app.core.errors import ProblemError
    from app.reactionx.pairing import PairingService

    pairing = create(db_client, login_as_admin)
    value = claim(db_client, pairing).json()
    account = db.get(UserAccount, login_as_admin.account_id)
    if identity == "other":
        account = db.scalar(select(UserAccount).where(UserAccount.id != account.id).limit(1))
        account.force_password_change = False
        db.add(UserRole(user_id=account.id, role_id=ROLES["super_admin"].id))
    other_session = AuthSession(
        user_id=account.id,
        token_digest=uuid4().hex,
        csrf_digest=uuid4().hex,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db.add(other_session)
    db.commit()
    actor = AuthContext(
        account=account, person=db.get(Person, account.person_id), session=other_session
    )
    with pytest.raises(ProblemError) as caught:
        PairingService(db).confirm_browser(
            pairing["pairing_id"],
            actor,
            value["verification_phrase"],
            request_id="synthetic-session-mismatch",
        )
    assert caught.value.status == 403
    response = db_client.post(
        f"{WEB}/pairings/{pairing['pairing_id']}/confirm",
        headers=browser_headers(login_as_admin),
        json={"verification_phrase": "WRONG"},
    )
    assert response.status_code == 403


def test_minimal_selection_includes_unactivated_profiles(db_client, db, login_as_admin):
    person = Person(
        full_name="synthetic-unactivated",
        nickname="nick",
        identity_ciphertext="cipher",
        identity_digest=uuid4().hex,
        sex="male",
        date_of_birth=date(2000, 1, 1),
    )
    db.add(person)
    db.commit()
    response = db_client.get(f"{WEB}/people", params={"query": "synthetic-unactivated"})
    assert response.status_code == 200
    assert response.json() == [
        {"id": str(person.id), "name": "synthetic-unactivated", "nickname": "nick"}
    ]
    from app.auth.service import AuthContext
    from app.reactionx.access import ReactionxAccess

    account = db.get(UserAccount, login_as_admin.account_id)
    session = db.scalar(select(AuthSession).where(AuthSession.user_id == account.id))
    ReactionxAccess(
        db, AuthContext(account=account, person=db.get(Person, account.person_id), session=session)
    ).require_conduct(person.id)


def test_rate_limits_are_persisted_with_retry_after(db_client, login_as_admin):
    for _ in range(5):
        create(db_client, login_as_admin)
    response = db_client.post(f"{WEB}/pairings", headers=browser_headers(login_as_admin))
    assert response.status_code == 429 and 1 <= int(response.headers["retry-after"]) <= 60
    for _ in range(20):
        response = claim(db_client, {"challenge": "X" * 43})
        assert response.status_code == 404
    response = claim(db_client, {"challenge": "X" * 43})
    assert response.status_code == 429 and "retry-after" in response.headers


@pytest.mark.parametrize(
    "payload",
    [
        "{",
        '{"challenge":"short"}',
        '{"challenge":"'
        + "A" * 43
        + '","credential_digest":"'
        + DIGEST
        + '","client_info":{"origin":"'
        + ORIGIN
        + '","name":"synthetic"},"extra":"private-marker"}',
    ],
)
def test_malformed_claims_are_throttled_before_json_and_schema_validation(db_client, payload):
    for _ in range(20):
        response = db_client.post(
            f"{MACHINE}/pairings/claim",
            content=payload,
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422
        assert "private-marker" not in response.text
    response = db_client.post(
        f"{MACHINE}/pairings/claim", content=payload, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 429
    assert 1 <= int(response.headers["retry-after"]) <= 60
    assert "no-store" in response.headers["cache-control"]


def test_direct_pairing_service_calls_cannot_bypass_claim_limit(db):
    from app.core.errors import ProblemError
    from app.reactionx.pairing import PairingService

    service = PairingService(db)
    for index in range(21):
        with pytest.raises(ProblemError) as caught:
            service.claim(
                "short", DIGEST, {"origin": ORIGIN, "name": "synthetic"}, ip="synthetic-direct"
            )
        assert caught.value.status == (422 if index < 20 else 429)


def test_claim_limit_precedes_middleware_body_cap(db_client, monkeypatch):
    from app.reactionx import http_support

    monkeypatch.setattr(http_support, "MAX_BATCH_BYTES", 64)
    for _ in range(20):
        response = db_client.post(f"{MACHINE}/pairings/claim", content="x" * 65)
        assert response.status_code == 413
    response = db_client.post(f"{MACHINE}/pairings/claim", content="x" * 65)
    assert response.status_code == 429
    assert 1 <= int(response.headers["retry-after"]) <= 60


def test_matching_digest_does_not_authorize_noncanonical_secret(db_client, db, login_as_admin):
    from app.reactionx.models import ReactionxConnector

    value = claim(db_client, create(db_client, login_as_admin)).json()
    noncanonical = "A" * 42 + "B"
    db.execute(
        update(ReactionxConnector)
        .where(ReactionxConnector.id == value["connector_id"])
        .values(credential_digest=sha256(noncanonical.encode("ascii")).hexdigest())
    )
    db.commit()
    response = db_client.get(
        f"{MACHINE}/pairings/{value['pairing_id']}", headers=machine_headers(value, noncanonical)
    )
    assert response.status_code == 401


def test_concurrent_other_accounts_sharing_digest_get_specific_conflict_and_rollback(
    postgres_engine,
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlalchemy import event, func, text
    from sqlalchemy.orm import Session

    from app.audit.models import AuditEvent
    from app.auth.models import AuthRateLimit
    from app.auth.service import AuthContext
    from app.core.errors import ProblemError
    from app.reactionx.models import ReactionxConnector, ReactionxPairing
    from app.reactionx.pairing import PairingService

    actors = [(uuid4(), uuid4(), uuid4()) for _ in range(2)]
    challenges = []
    digest = sha256(uuid4().bytes).hexdigest()
    barrier = Barrier(2)

    def simultaneous_preflight(connection, cursor, statement, parameters, context, executemany):
        if (
            statement.startswith("SELECT")
            and "FROM reactionx_connectors" in statement
            and "credential_digest =" in statement
        ):
            barrier.wait(timeout=10)

    def invoke(index):
        with Session(postgres_engine) as session:
            try:
                PairingService(session).claim(
                    challenges[index],
                    digest,
                    {"origin": ORIGIN, "name": "synthetic-collision"},
                    ip=f"synthetic-{actors[index][0]}",
                )
            except ProblemError as error:
                return error.status, error.code
            return 200, None

    try:
        with Session(postgres_engine) as seed, seed.begin():
            for account_id, person_id, session_id in actors:
                seed.add(
                    Person(
                        id=person_id,
                        full_name="synthetic-collision-owner",
                        identity_ciphertext="synthetic",
                        identity_digest=uuid4().hex,
                        sex="male",
                        date_of_birth=date(2000, 1, 1),
                    )
                )
                seed.flush()
                seed.add(
                    UserAccount(
                        id=account_id,
                        person_id=person_id,
                        password_hash="synthetic-unused",
                        status="active",
                        force_password_change=False,
                    )
                )
                seed.flush()
                seed.add_all(
                    [
                        UserRole(user_id=account_id, role_id=ROLES["super_admin"].id),
                        AuthSession(
                            id=session_id,
                            user_id=account_id,
                            token_digest=uuid4().hex * 2,
                            csrf_digest=uuid4().hex * 2,
                            expires_at=datetime.now(UTC) + timedelta(hours=1),
                        ),
                    ]
                )
        for account_id, person_id, session_id in actors:
            with Session(postgres_engine) as session:
                actor = AuthContext(
                    account=session.get(UserAccount, account_id),
                    person=session.get(Person, person_id),
                    session=session.get(AuthSession, session_id),
                )
                challenges.append(
                    PairingService(session).create(actor, "synthetic-collision-create").challenge
                )
        event.listen(postgres_engine, "after_cursor_execute", simultaneous_preflight)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(invoke, index) for index in range(2)]
                outcomes = [future.result(timeout=20) for future in futures]
        finally:
            event.remove(postgres_engine, "after_cursor_execute", simultaneous_preflight)
        assert sorted(outcomes, key=lambda item: item[0]) == [
            (200, None),
            (409, "reactionx_credential_conflict"),
        ]
        with Session(postgres_engine) as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(ReactionxConnector)
                    .where(ReactionxConnector.credential_digest == digest)
                )
                == 1
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(ReactionxPairing)
                    .where(
                        ReactionxPairing.actor_account_id.in_([actor[0] for actor in actors]),
                        ReactionxPairing.connector_id.is_(None),
                    )
                )
                == 1
            )
    finally:
        account_ids = [actor[0] for actor in actors]
        with Session(postgres_engine) as cleanup, cleanup.begin():
            database = cleanup.scalar(text("SELECT current_database()"))
            assert database == "erp_e2e" or database.endswith("_test")
            cleanup.execute(text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update"))
            cleanup.execute(delete(AuditEvent).where(AuditEvent.actor_user_id.in_(account_ids)))
            cleanup.execute(text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update"))
            cleanup.execute(
                delete(ReactionxPairing).where(ReactionxPairing.actor_account_id.in_(account_ids))
            )
            cleanup.execute(
                delete(ReactionxConnector).where(
                    ReactionxConnector.actor_account_id.in_(account_ids)
                )
            )
            keys = [
                sha256(f"reactionx:{namespace}:{identity}".encode()).hexdigest()
                for account_id in account_ids
                for namespace, identity in (
                    ("create", str(account_id)),
                    ("claim", f"synthetic-{account_id}"),
                )
            ]
            cleanup.execute(delete(AuthRateLimit).where(AuthRateLimit.key_digest.in_(keys)))
            cleanup.execute(delete(UserRole).where(UserRole.user_id.in_(account_ids)))
            cleanup.execute(delete(AuthSession).where(AuthSession.user_id.in_(account_ids)))
            cleanup.execute(delete(UserAccount).where(UserAccount.id.in_(account_ids)))
            cleanup.execute(delete(Person).where(Person.id.in_([actor[1] for actor in actors])))


def test_claim_does_not_mask_unrelated_database_constraint_error(db_client, db, login_as_admin):
    from sqlalchemy import event
    from sqlalchemy.exc import IntegrityError

    from app.reactionx.models import ReactionxConnector
    from app.reactionx.pairing import PairingService

    pairing = create(db_client, login_as_admin)

    def invalid_status(session, flush_context, instances):
        for row in session.new:
            if isinstance(row, ReactionxConnector):
                row.status = "invalid"

    event.listen(db, "before_flush", invalid_status)
    try:
        with pytest.raises(IntegrityError) as caught:
            PairingService(db).claim(
                pairing["challenge"],
                DIGEST,
                {"origin": ORIGIN, "name": "synthetic"},
                ip="synthetic-constraint",
            )
        assert caught.value.orig.sqlstate == "23514"
    finally:
        event.remove(db, "before_flush", invalid_status)


def test_ordinary_admin_has_no_default_reactionx_grants(db, platform_users):
    from app.rbac.models import Permission, RolePermission

    grants = list(
        db.execute(
            select(Permission.key, RolePermission.scope_kind)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == ROLES["admin"].id, Permission.key.like("reactionx.%"))
        )
    )
    assert grants == []
    assert {key for key in PERMISSIONS if key.startswith("reactionx.")} == {
        "reactionx.manage",
        "reactionx.conduct",
    }


def test_configured_site_change_invalidates_pending_challenges_and_credentials(
    db_client, db, login_as_admin
):
    from app.core.errors import ProblemError
    from app.reactionx.pairing import PairingService

    old = create(db_client, login_as_admin)
    value = claim(db_client, old).json()
    new_site = "https://replacement.example.test:18080"
    db_client.app.state.settings.reactionx_public_origin = new_site
    with pytest.raises(ProblemError) as caught:
        PairingService(db, origin=new_site).authenticate(
            machine_headers(value)["Authorization"], allow_pending=True
        )
    assert caught.value.status == 401
    response = claim(db_client, old, client_info={"origin": new_site, "name": "synthetic-new-site"})
    assert response.status_code == 403
    new = create(db_client, login_as_admin)
    response = claim(
        db_client,
        new,
        credential_digest="b" * 64,
        client_info={"origin": new_site, "name": "synthetic-new-site"},
    )
    assert response.status_code == 200


def test_person_denial_is_not_exposed_by_selector(db_client, db, platform_users, login_as_admin):
    person = platform_users["people"]["coach"]
    db.add(
        UserPermissionOverride(
            user_id=login_as_admin.account_id,
            permission_id=PERMISSIONS["reactionx.conduct"].id,
            effect="deny",
            scope_kind="person",
            scope_id=person.id,
            created_by_user_id=login_as_admin.account_id,
        )
    )
    db.commit()
    response = db_client.get(f"{WEB}/people")
    assert response.status_code == 200
    assert str(person.id) not in {row["id"] for row in response.json()}


def test_cancelled_pairing_cannot_be_claimed(db_client, login_as_admin):
    pairing = create(db_client, login_as_admin)
    response = db_client.delete(
        f"{WEB}/pairings/{pairing['pairing_id']}", headers=browser_headers(login_as_admin)
    )
    assert response.status_code == 204
    assert claim(db_client, pairing).status_code == 410


def test_revocation_invalidates_active_credential(db_client, login_as_admin):
    pairing = create(db_client, login_as_admin)
    value = claim(db_client, pairing).json()
    payload = {"verification_phrase": value["verification_phrase"]}
    assert (
        db_client.post(
            f"{MACHINE}/pairings/{pairing['pairing_id']}/confirm",
            headers=machine_headers(value),
            json=payload,
        ).json()["status"]
        == "pending"
    )
    assert (
        db_client.post(
            f"{WEB}/pairings/{pairing['pairing_id']}/confirm",
            headers=browser_headers(login_as_admin),
            json=payload,
        ).json()["status"]
        == "active"
    )
    assert (
        db_client.delete(
            f"{WEB}/connectors/{value['connector_id']}", headers=browser_headers(login_as_admin)
        ).status_code
        == 204
    )
    assert db_client.get(f"{MACHINE}/status", headers=machine_headers(value)).status_code == 401


def test_foundation_uniqueness_enforces_run_event_receipt_operation(
    db, platform_users, login_as_admin
):
    from sqlalchemy.exc import IntegrityError

    from app.reactionx.models import (
        ReactionxCommand,
        ReactionxConnector,
        ReactionxEvent,
        ReactionxResultReceipt,
        ReactionxRun,
    )

    account = platform_users["accounts"]["admin"]
    session = db.scalar(select(AuthSession).where(AuthSession.user_id == account.id))
    connector = ReactionxConnector(
        actor_account_id=account.id,
        browser_session_id=session.id,
        credential_digest=DIGEST,
        origin=ORIGIN,
        client_name="synthetic",
        status="active",
    )
    db.add(connector)
    db.flush()
    common = {
        "actor_account_id": account.id,
        "subject_person_id": account.person_id,
        "connector_id": connector.id,
        "browser_session_id": session.id,
        "browser_tab_id": uuid4(),
        "manifest": {},
        "manifest_digest": "a" * 64,
        "lease_expires_at": datetime.now(UTC) + timedelta(seconds=15),
    }
    run = ReactionxRun(**common)
    db.add(run)
    db.flush()
    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(ReactionxRun(**common))
        db.flush()
    db.add(ReactionxEvent(run_id=run.id, seq=1, event={"kind": "hit"}))
    db.flush()
    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(ReactionxEvent(run_id=run.id, seq=1, event={"kind": "timeout"}))
        db.flush()
    receipt = {
        "run_id": run.id,
        "digest": "b" * 64,
        "envelope": {},
        "metrics": {},
        "eligible": False,
        "saved_at": datetime.now(UTC),
    }
    db.add(ReactionxResultReceipt(**receipt))
    db.flush()
    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(ReactionxResultReceipt(**receipt))
        db.flush()
    operation = uuid4()
    command = {
        "operation_id": operation,
        "run_id": run.id,
        "connector_id": connector.id,
        "actor_account_id": account.id,
        "subject_person_id": account.person_id,
        "sequence": 1,
        "kind": "start",
        "payload": {},
        "payload_fingerprint": "c" * 64,
        "expires_at": datetime.now(UTC) + timedelta(seconds=10),
    }
    db.add(ReactionxCommand(**command))
    db.flush()
    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(ReactionxCommand(**{**command, "sequence": 2}))
        db.flush()


@pytest.mark.parametrize("permission", ["reactionx.manage", "reactionx.conduct"])
@pytest.mark.parametrize("operation", ["preview", "add", "remove"])
def test_ordinary_permission_manager_cannot_delegate_reactionx(
    db, platform_users, permission, operation
):
    from app.rbac.schemas import PermissionOverrideCreate
    from app.rbac.service import PermissionDelegationDenied, RbacService

    actor = platform_users["accounts"]["admin"]
    target = platform_users["accounts"]["coach"]
    db.execute(delete(UserRole).where(UserRole.user_id == actor.id))
    db.add(UserRole(user_id=actor.id, role_id=ROLES["admin"].id))
    for key in (permission, "permissions.manage"):
        db.add(
            UserPermissionOverride(
                user_id=actor.id,
                permission_id=PERMISSIONS[key].id,
                effect="allow",
                scope_kind="global",
            )
        )
    existing = UserPermissionOverride(
        user_id=target.id,
        permission_id=PERMISSIONS[permission].id,
        effect="allow",
        scope_kind="global",
    )
    db.add(existing)
    db.commit()
    service = RbacService(db)
    request = PermissionOverrideCreate(
        permission_id=PERMISSIONS[permission].id, effect="deny", scope_kind="global"
    )
    with pytest.raises(PermissionDelegationDenied):
        if operation == "preview":
            service.preview_override(actor_user_id=actor.id, account_id=target.id, input=request)
        elif operation == "add":
            service.add_override(actor_user_id=actor.id, account_id=target.id, input=request)
        else:
            service.remove_override(
                actor_user_id=actor.id,
                account_id=target.id,
                override_id=existing.id,
                before_delete=lambda _: pytest.fail("unauthorized removal"),
            )
    assert db.get(UserPermissionOverride, existing.id) is not None


@pytest.mark.parametrize(
    "permission,scope",
    [
        ("reactionx.manage", "person"),
        ("reactionx.conduct", "family"),
        ("reactionx.conduct", "assigned_class"),
    ],
)
def test_reactionx_override_rejects_unrecognized_scope(permission, scope):
    from app.rbac.models import Permission
    from app.rbac.schemas import PermissionOverrideCreate
    from app.rbac.service import InvalidPermissionOverride, RbacService

    with pytest.raises(InvalidPermissionOverride):
        RbacService._validate_override_input(
            permission=Permission(key=permission),
            input=PermissionOverrideCreate(
                permission_id=uuid4(),
                effect="allow",
                scope_kind=scope,
                scope_id=uuid4() if scope in {"person", "assigned_class"} else None,
            ),
        )
