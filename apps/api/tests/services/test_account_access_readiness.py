from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from app.auth.models import UserAccount
from app.security.encryption import EnvelopeCipher, LookupDigest
from tests.services.test_assisted_eligibility import (
    approved_minor,
    fixed_approval_clock,  # noqa: F401 - Pytest discovers the imported autouse fixture.
)
from tests.services.test_personnel_approval import platform_users as _platform_users


@pytest.fixture
def platform_users(db):
    return _platform_users.__wrapped__(db)


def account_policy(db):
    from app.auth.account_readiness import AccountAccessReadinessPolicy

    return AccountAccessReadinessPolicy(
        db, cipher=EnvelopeCipher(bytes(range(32))), lookup=LookupDigest(bytes(range(32)))
    )


def assisted_account(db, result, *, status="active"):
    account = UserAccount(
        person_id=result.person_id,
        password_hash="synthetic",
        status=status,
        force_password_change=False,
        account_access_origin="assisted_activation",
        assisted_approval_id=result.approval_id,
    )
    db.add(account)
    db.flush()
    return account


def test_pending_unknown_age_access_is_pending_without_type_error(db):
    from tests.services.test_profile_claims import incomplete_pending_target

    target, _ = incomplete_pending_target(db, email="claim@example.com")
    account = UserAccount(
        id=uuid4(),
        person_id=target.person_id,
        password_hash="synthetic",
        account_access_origin="assisted_activation",
        assisted_approval_id=target.approval_id,
    )
    state = account_policy(db).evaluate_one(account, reference_date=date(2026, 10, 21))
    assert state.age_status == "unknown"
    assert state.guardian_status == "pending" and state.missing


def test_legacy_account_cannot_inherit_assisted_guardian_exception(db, platform_users):
    assisted, _, _ = approved_minor(db, platform_users)
    legacy, _, _ = approved_minor(db, platform_users, sequence=323)
    account = assisted_account(db, assisted)
    old = UserAccount(
        person_id=legacy.person_id,
        password_hash="synthetic",
        status="disabled",
        force_password_change=False,
    )
    db.add(old)
    db.flush()
    results = account_policy(db).evaluate_many([account, old], reference_date=date(2026, 10, 18))
    assert results[account.id].missing == ()
    assert results[old.id].missing == ("guardian",)
    assert old.account_access_origin == "legacy" and old.status == "disabled"


def test_assisted_player_and_adult_fact_drift_fail_closed(db, platform_users):
    from app.identity.models import Person

    result, _, _ = approved_minor(db, platform_users, kind="player")
    account = assisted_account(db, result)
    db.get(Person, result.person_id).full_name = "未批准变更"
    db.flush()
    for day in (date(2026, 10, 18), date(2033, 1, 2)):
        assert account_policy(db).evaluate_one(account, reference_date=day).missing


def services(db, *, cipher=None, lookup=None):
    from app.auth.service import AccountService, AuthService
    from app.identity.service import IdentityService
    from app.security.passwords import PasswordService

    identity = IdentityService(
        db,
        cipher=cipher or EnvelopeCipher(bytes(range(32))),
        lookup=lookup or LookupDigest(bytes(range(32))),
    )
    accounts = AccountService(
        db,
        identity_service=identity,
        passwords=PasswordService(),
        reference_date_provider=lambda: date(2026, 10, 18),
    )
    auth = AuthService(
        db,
        cipher=identity.cipher,
        lookup=identity.lookup,
        passwords=PasswordService(),
        session_token_pepper=bytes(range(32)),
        session_ttl_minutes=60,
        clock=lambda: datetime(2026, 10, 18, 8, tzinfo=UTC),
    )
    return identity, accounts, auth


def sessions(db, account, auth):
    from app.auth.models import AuthSession

    tokens = [f"isolated-{uuid4()}" for _ in range(2)]
    rows = [
        AuthSession(
            user_id=account.id,
            token_digest=auth._token_digest(token),
            csrf_digest=auth._token_digest(token + "csrf"),
            expires_at=datetime(2035, 1, 1, tzinfo=UTC),
        )
        for token in tokens
    ]
    db.add_all(rows)
    db.flush()
    return tokens, rows


@pytest.mark.parametrize("kind", ["student", "player"])
def test_identity_update_invalidates_own_assisted_account(db, platform_users, kind):
    from app.identity.schemas import PersonUpdate

    result, _, _ = approved_minor(db, platform_users, kind=kind)
    account = assisted_account(db, result)
    identity, _, auth = services(db)
    _, rows = sessions(db, account, auth)
    identity.update_person(
        result.person_id,
        PersonUpdate(full_name="需要重新审批"),
        actor_user_id=platform_users["accounts"]["admin"].id,
        request_id="self-drift",
    )
    assert account.status == "disabled"
    assert all(row.revoked_at is not None for row in rows)


def test_explicit_enable_uses_account_origin_and_does_not_rewrite_it(db, platform_users):
    result, _, _ = approved_minor(db, platform_users)
    account = assisted_account(db, result, status="disabled")
    _, accounts, _ = services(db)
    accounts.enable_account(account.id, actor_user_id=platform_users["accounts"]["admin"].id)
    assert account.status == "active" and account.assisted_approval_id == result.approval_id


@pytest.mark.parametrize("operation", ["disable", "delete", "role", "missing_policy"])
def test_guardian_account_changes_respect_source_and_missing_policy_is_atomic(
    db, platform_users, operation
):
    from app.rbac.models import UserRole
    from app.rbac.seed import ROLES
    from app.rbac.service import RbacService

    result, _, edges = approved_minor(db, platform_users)
    account = assisted_account(db, result)
    guardian = UserAccount(
        person_id=edges[0].guardian_person_id,
        password_hash="synthetic",
        status="active",
        force_password_change=False,
    )
    db.add(guardian)
    db.flush()
    db.add(UserRole(user_id=guardian.id, role_id=ROLES["guardian"].id))
    _, accounts, auth = services(db)
    _, rows = sessions(db, account, auth)
    db.commit()
    kwargs = {
        "actor_user_id": platform_users["accounts"]["admin"].id,
        "request_id": "guardian-mutation",
    }
    if operation == "disable":
        accounts.disable_account(guardian.id, **kwargs)
    elif operation == "delete":
        accounts.delete_account(guardian.id, **kwargs)
    elif operation == "role":
        RbacService(db).remove_role(
            account_id=guardian.id,
            role_id=ROLES["guardian"].id,
            account_readiness=account_policy(db),
            **kwargs,
        )
    else:
        from app.personnel_intake.guardian_records import ApprovalEvidenceError

        with pytest.raises(ApprovalEvidenceError):
            RbacService(db).remove_role(
                account_id=guardian.id, role_id=ROLES["guardian"].id, **kwargs
            )
        assert db.get(UserRole, (guardian.id, ROLES["guardian"].id)) is not None
    db.refresh(account)
    assert account.status == ("disabled" if operation == "delete" else "active")
    assert all((row.revoked_at is not None) == (operation == "delete") for row in rows)


def test_wrong_key_rolls_back_role_removal_and_account_changes(db, platform_users):
    from app.auth.account_readiness import AccountAccessReadinessPolicy
    from app.personnel_intake.guardian_records import ApprovalEvidenceError
    from app.rbac.models import UserRole
    from app.rbac.seed import ROLES
    from app.rbac.service import RbacService

    result, _, edges = approved_minor(db, platform_users)
    account = assisted_account(db, result)
    guardian = UserAccount(
        person_id=edges[0].guardian_person_id, password_hash="synthetic", status="active"
    )
    db.add(guardian)
    db.flush()
    db.add(UserRole(user_id=guardian.id, role_id=ROLES["guardian"].id))
    _, _, auth = services(db)
    _, rows = sessions(db, account, auth)
    db.commit()
    with pytest.raises(ApprovalEvidenceError):
        RbacService(db).remove_role(
            account_id=guardian.id,
            role_id=ROLES["guardian"].id,
            actor_user_id=platform_users["accounts"]["admin"].id,
            request_id="wrong-key",
            account_readiness=AccountAccessReadinessPolicy(
                db, cipher=EnvelopeCipher(b"x" * 32), lookup=LookupDigest(b"y" * 32)
            ),
        )
    assert db.get(UserRole, (guardian.id, ROLES["guardian"].id)) is not None
    assert account.status == "active" and all(row.revoked_at is None for row in rows)


@pytest.mark.parametrize("initial_status", ["active", "disabled"])
def test_valid_player_correction_preserves_original_origin_and_status(
    db, platform_users, initial_status
):
    from tests.services.test_assisted_eligibility import correction

    result, data, edges = approved_minor(db, platform_users, kind="player")
    account = assisted_account(db, result, status=initial_status)
    _, _, auth = services(db)
    _, rows = sessions(db, account, auth)
    replacement = [data.guardians[0].model_copy(update={"relationship": "亲属"})]
    successor = correction(
        db, platform_users, result.person_id, data, full_name="更正后人员", guardians=replacement
    )
    assert successor.approval_id != result.approval_id
    assert account.assisted_approval_id == result.approval_id and account.status == initial_status
    assert all(row.revoked_at is None for row in rows)
    assert edges[0].ended_at is not None
    assert account_policy(db).evaluate_one(account, reference_date=date(2026, 10, 18)).missing == ()


def test_correction_rechecks_other_dependents_of_changed_person(db, platform_users):
    from tests.api.conftest import make_cn_id
    from tests.services.test_assisted_eligibility import correction
    from tests.services.test_personnel_approval import approvals
    from tests.services.test_personnel_intake import actor, intake

    guardian_data = intake(
        platform_users,
        identity_number=make_cn_id("19800102", 701),
        date_of_birth=date(1980, 1, 2),
        full_name="监护人员0",
        phone="13900000880",
        email="guardian-approved@example.com",
    )
    guardian_result = approvals(db).direct_create(
        actor(platform_users, "admin"), guardian_data, uuid4()
    )
    guardian_account = assisted_account(db, guardian_result)
    # Reuse that exact approved person as dependent's guardian (email is optional but must match).
    dependent_data = intake(
        platform_users,
        identity_number=make_cn_id("20150102", 321),
        date_of_birth=date(2015, 1, 2),
        guardians=[
            {
                "full_name": guardian_data.full_name,
                "identity_number": guardian_data.identity_number,
                "phone": guardian_data.phone,
                "email": guardian_data.email,
                "sex": "male",
                "date_of_birth": guardian_data.date_of_birth,
                "relationship": "父亲",
            }
        ],
    )
    dependent = approvals(db).direct_create(actor(platform_users, "admin"), dependent_data, uuid4())
    account = assisted_account(db, dependent)
    _, _, auth = services(db)
    _, rows = sessions(db, account, auth)
    correction(
        db, platform_users, guardian_result.person_id, guardian_data, full_name="监护资料已修订"
    )
    assert account.status == "disabled" and all(row.revoked_at is not None for row in rows)
    assert (
        guardian_account.status == "active"
        and guardian_account.assisted_approval_id == guardian_result.approval_id
    )


def test_assisted_eighteenth_birthday_uses_china_date_and_never_reenables(db, platform_users):
    from app.auth.service import InvalidSession
    from app.identity.models import Person

    first, _, edges = approved_minor(db, platform_users, kind="player")
    second, _, _ = approved_minor(db, platform_users, sequence=323)
    disabled = assisted_account(db, first)
    active = assisted_account(db, second)
    _, _, auth = services(db)
    first_tokens, _ = sessions(db, disabled, auth)
    second_tokens, _ = sessions(db, active, auth)
    db.get(Person, edges[0].guardian_person_id).full_name = "监护资料变动"
    db.commit()
    auth.clock = lambda: datetime(2033, 1, 1, 15, 59, 59, tzinfo=UTC)
    with pytest.raises(InvalidSession):
        auth.authenticate(first_tokens[0], request_id="before-birthday")
    assert disabled.status == "disabled"
    auth.clock = lambda: datetime(2033, 1, 1, 16, tzinfo=UTC)
    assert auth.authenticate(second_tokens[0], request_id="after-birthday").account.id == active.id
    with pytest.raises(InvalidSession):
        auth.authenticate(first_tokens[0], request_id="no-auto-enable")
    assert disabled.status == "disabled"


@pytest.mark.parametrize("entry", ["login", "enable", "identity", "role"])
def test_all_account_consumers_use_injected_nondefault_crypto(db, platform_users, entry):
    from app.auth.account_readiness import AccountAccessReadinessPolicy
    from app.auth.identifiers import login_identifier_digest, normalize_login_identifier
    from app.auth.models import AccountLoginIdentifier
    from app.identity.schemas import PersonUpdate
    from app.personnel_intake.approval_service import PersonnelApprovalService
    from app.rbac.models import UserRole
    from app.rbac.seed import ROLES
    from app.rbac.service import RbacService
    from app.security.passwords import PasswordService

    cipher, lookup = EnvelopeCipher(b"q" * 32), LookupDigest(b"r" * 32)
    result, data, edges = approved_minor(
        db,
        platform_users,
        approval_service=PersonnelApprovalService(db, cipher=cipher, lookup=lookup),
    )
    account = assisted_account(db, result, status="disabled" if entry == "enable" else "active")
    identity, accounts, auth = services(db, cipher=cipher, lookup=lookup)
    actor_id = platform_users["accounts"]["admin"].id
    if entry == "login":
        account.password_hash = PasswordService().hash("CustomKeys123")
        email = data.email.get_secret_value()
        db.add(
            AccountLoginIdentifier(
                account_id=account.id,
                kind="email",
                identifier_digest=login_identifier_digest(
                    lookup, normalize_login_identifier(email)
                ),
                verified_at=datetime.now(UTC),
                verification_source="test",
                is_primary=True,
            )
        )
        db.flush()
        auth.login(
            identifier=email,
            password="CustomKeys123",
            ip_address="127.0.0.1",
            request_id="custom-keys",
        )
    elif entry == "enable":
        accounts.enable_account(account.id, actor_user_id=actor_id)
    elif entry == "identity":
        identity.update_person(
            result.person_id,
            PersonUpdate(full_name="需要再审批"),
            actor_user_id=actor_id,
            request_id="custom-keys",
        )
    else:
        guardian = UserAccount(
            person_id=edges[0].guardian_person_id, password_hash="synthetic", status="active"
        )
        db.add(guardian)
        db.flush()
        db.add(UserRole(user_id=guardian.id, role_id=ROLES["guardian"].id))
        db.flush()
        RbacService(db).remove_role(
            account_id=guardian.id,
            role_id=ROLES["guardian"].id,
            actor_user_id=actor_id,
            request_id="custom-keys",
            account_readiness=AccountAccessReadinessPolicy(db, cipher=cipher, lookup=lookup),
        )
        assert db.get(UserRole, (guardian.id, ROLES["guardian"].id)) is None
    assert account.status == ("disabled" if entry == "identity" else "active")
