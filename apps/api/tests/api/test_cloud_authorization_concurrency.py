from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.auth.models import AuthSession, UserAccount
from app.core.errors import ProblemError
from app.identity.models import Person
from app.operations.cloud_auth_models import (
    CloudAuthApplication,
    CloudAuthOperation,
    CloudAuthSession,
    CloudConnectorState,
)
from app.operations.cloud_auth_requests import CloudImportCommand
from app.operations.cloud_auth_schemas import AuthActor, CloudAppUpdate
from app.operations.models import OperationsServiceHeartbeat
from app.rbac.models import UserRole
from app.rbac.seed import ROLES
from app.security.encryption import EnvelopeCipher


@pytest.mark.parametrize(
    "modes",
    [
        ("own_app", "own_app"),
        ("own_app", "openlist_builtin_import"),
        ("openlist_builtin_import", "openlist_builtin_import"),
    ],
)
def test_concurrent_create_has_one_live_session_and_one_queue(postgres_engine, modes):
    # Independent committed connections test the actual advisory lock/unique fence.
    from app.operations.cloud_auth_service import CloudAuthorizationService
    from tests.api.test_cloud_authorization import prerequisites

    with Session(postgres_engine) as setup:
        marker = uuid4().hex
        person = Person(
            full_name="扫码并发测试",
            identity_ciphertext=marker,
            identity_digest=marker,
            sex="male",
            date_of_birth=date(1990, 1, 1),
        )
        setup.add(person)
        setup.flush()
        account = UserAccount(
            person_id=person.id,
            password_hash="unused",
            status="active",
            force_password_change=False,
        )
        setup.add(account)
        setup.flush()
        setup.add(UserRole(user_id=account.id, role_id=ROLES["super_admin"].id))
        login = AuthSession(
            user_id=account.id,
            token_digest=uuid4().hex,
            csrf_digest=uuid4().hex,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        setup.add(login)
        setup.flush()
        owner = AuthActor(account_id=account.id, login_session_id=login.id)
        connector = prerequisites(setup)
        connector_id, account_id, person_id = connector.id, account.id, person.id
        service = CloudAuthorizationService(setup, cipher=EnvelopeCipher(bytes(range(32))))
        service.save_application(
            "baidu",
            owner,
            CloudAppUpdate(client_id="app", client_secret="secret", expected_revision=0),
        )
        setup.commit()
    barrier = Barrier(2)

    def create(index):
        with Session(postgres_engine) as connection:
            barrier.wait(10)
            try:
                result = CloudAuthorizationService(
                    connection, cipher=EnvelopeCipher(bytes(range(32)))
                ).create_session("baidu", owner, uuid4(), mode=modes[index])
                return str(result.id)
            except ProblemError as error:
                return error.status

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create, range(2)))
        assert sum(isinstance(result, str) for result in results) == 1
        assert results.count(409) == 1
        with Session(postgres_engine) as check:
            assert len(list(check.scalars(select(CloudAuthSession)))) == 1
            assert len(list(check.scalars(select(CloudAuthOperation)))) == 1
            session = check.scalar(select(CloudAuthSession))
            session_id, revision, mode = session.id, session.revision, session.mode
        if mode == "openlist_builtin_import":
            command = CloudImportCommand(
                expected_revision=revision,
                operation_id=uuid4(),
                access_token="concurrent-synthetic-access",
                refresh_token="concurrent-synthetic-refresh",
            )
            barrier = Barrier(2)

            def submit(_):
                with Session(postgres_engine) as connection:
                    barrier.wait(10)
                    result = CloudAuthorizationService(
                        connection, cipher=EnvelopeCipher(bytes(range(32)))
                    ).submit_import(session_id, owner, command)
                    return result.model_dump(mode="json")

            with ThreadPoolExecutor(max_workers=2) as pool:
                submitted = list(pool.map(submit, range(2)))
            assert submitted[0] == submitted[1]
            with Session(postgres_engine) as check:
                assert (
                    len(
                        list(
                            check.scalars(
                                select(CloudAuthOperation).where(
                                    CloudAuthOperation.action == "verify_import"
                                )
                            )
                        )
                    )
                    == 1
                )
    finally:
        with Session(postgres_engine) as cleanup:
            cleanup.execute(
                delete(CloudAuthOperation).where(CloudAuthOperation.owner_account_id == account_id)
            )
            cleanup.execute(
                delete(CloudAuthSession).where(CloudAuthSession.owner_account_id == account_id)
            )
            cleanup.execute(
                delete(CloudAuthApplication).where(CloudAuthApplication.provider == "baidu")
            )
            cleanup.execute(
                delete(CloudConnectorState).where(CloudConnectorState.id == connector_id)
            )
            cleanup.execute(
                delete(OperationsServiceHeartbeat).where(
                    OperationsServiceHeartbeat.service_key == "cloud_authorization"
                )
            )
            cleanup.execute(delete(AuthSession).where(AuthSession.user_id == account_id))
            cleanup.execute(delete(UserRole).where(UserRole.user_id == account_id))
            cleanup.execute(delete(UserAccount).where(UserAccount.id == account_id))
            cleanup.execute(delete(Person).where(Person.id == person_id))
            cleanup.commit()
