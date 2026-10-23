from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError


@pytest.fixture
def team_rows(postgres_engine: Engine):
    person_ids = [uuid4(), uuid4()]
    account_ids = [uuid4(), uuid4()]
    project_id = uuid4()
    team_ids = [uuid4(), uuid4()]
    with postgres_engine.begin() as connection:
        for person_id, account_id in zip(person_ids, account_ids, strict=True):
            connection.execute(
                text(
                    "INSERT INTO people (id, full_name, identity_ciphertext, identity_digest, "
                    "sex, date_of_birth) VALUES (:id, '队伍测试', 'cipher', :digest, 'female', :dob)"
                ),
                {"id": person_id, "digest": person_id.hex, "dob": date(2000, 1, 1)},
            )
            connection.execute(
                text(
                    "INSERT INTO user_accounts (id, person_id, password_hash, status, "
                    "force_password_change, failed_login_count) "
                    "VALUES (:id, :person_id, 'hash', 'active', false, 0)"
                ),
                {"id": account_id, "person_id": person_id},
            )
        connection.execute(
            text(
                "INSERT INTO game_projects (id, name, sort_order, is_active, "
                "is_team_division, created_by_user_id) "
                "VALUES (:id, :name, 100, true, true, :creator)"
            ),
            {"id": project_id, "name": f"测试分部-{project_id.hex}", "creator": account_ids[0]},
        )
        for index, team_id in enumerate(team_ids):
            connection.execute(
                text(
                    "INSERT INTO professional_teams (id, project_id, name, is_active, "
                    "created_by_user_id) VALUES (:id, :project_id, :name, true, :creator)"
                ),
                {
                    "id": team_id,
                    "project_id": project_id,
                    "name": f"测试队伍{index}",
                    "creator": account_ids[0],
                },
            )
    yield person_ids, account_ids, team_ids
    with postgres_engine.begin() as connection:
        connection.execute(
            text("DELETE FROM team_manager_assignments WHERE team_id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": team_ids},
        )
        connection.execute(
            text("DELETE FROM team_memberships WHERE team_id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": team_ids},
        )
        connection.execute(
            text("DELETE FROM team_operations WHERE actor_account_id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": account_ids},
        )
        connection.execute(
            text("DELETE FROM professional_teams WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": team_ids},
        )
        connection.execute(text("DELETE FROM game_projects WHERE id = :id"), {"id": project_id})
        connection.execute(
            text("DELETE FROM user_accounts WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": account_ids},
        )
        connection.execute(
            text("DELETE FROM people WHERE id = ANY(CAST(:ids AS uuid[]))"), {"ids": person_ids}
        )


def _membership(
    connection,
    *,
    person_id: UUID,
    team_id: UUID,
    creator: UUID,
    joined_on: date,
    left_on: date | None = None,
    ended_at: str | None = None,
    end_kind: str | None = None,
    transfer_reason: str | None = None,
) -> UUID:
    membership_id = uuid4()
    connection.execute(
        text(
            "INSERT INTO team_memberships (id, person_id, team_id, joined_on, left_on, "
            "created_by_user_id, ended_at, end_kind, transfer_reason) "
            "VALUES (:id, :person_id, :team_id, :joined_on, :left_on, :creator, "
            "CAST(:ended_at AS timestamptz), :end_kind, :transfer_reason)"
        ),
        {
            "id": membership_id,
            "person_id": person_id,
            "team_id": team_id,
            "joined_on": joined_on,
            "left_on": left_on,
            "creator": creator,
            "ended_at": ended_at,
            "end_kind": end_kind,
            "transfer_reason": transfer_reason,
        },
    )
    return membership_id


def test_one_open_membership_across_all_teams_and_closed_history(
    postgres_engine: Engine,
    team_rows,
) -> None:
    person_ids, account_ids, team_ids = team_rows
    with postgres_engine.begin() as connection:
        # Two closed rows, including a same-day transfer, remain legitimate history.
        for team_id, joined, left in (
            (team_ids[0], date(2026, 1, 1), date(2026, 1, 2)),
            (team_ids[1], date(2026, 1, 2), date(2026, 1, 2)),
        ):
            _membership(
                connection,
                person_id=person_ids[0],
                team_id=team_id,
                creator=account_ids[0],
                joined_on=joined,
                left_on=left,
                ended_at="2099-01-02T12:00:00+08:00",
                end_kind="left",
            )
        _membership(
            connection,
            person_id=person_ids[0],
            team_id=team_ids[0],
            creator=account_ids[0],
            joined_on=date(2026, 1, 2),
        )
        with connection.begin_nested() as nested:
            with pytest.raises(IntegrityError):
                _membership(
                    connection,
                    person_id=person_ids[0],
                    team_id=team_ids[1],
                    creator=account_ids[0],
                    joined_on=date(2026, 1, 3),
                )
            nested.rollback()
        open_memberships_for_person = connection.scalar(
            text(
                "SELECT count(*) FROM team_memberships "
                "WHERE person_id = :person_id AND ended_at IS NULL"
            ),
            {"person_id": person_ids[0]},
        )
        assert open_memberships_for_person == 1
        assert (
            connection.scalar(
                text("SELECT count(*) FROM team_memberships WHERE person_id = :person_id"),
                {"person_id": person_ids[0]},
            )
            == 3
        )


@pytest.mark.parametrize(
    "left_on,ended_at,end_kind",
    [
        (date(2025, 12, 31), "2099-01-02T12:00:00+08:00", "left"),
        (date(2026, 1, 2), None, "left"),
        (None, "2099-01-02T12:00:00+08:00", "left"),
        (None, None, "left"),
    ],
)
def test_membership_rejects_invalid_date_or_open_end_pair(
    postgres_engine: Engine,
    team_rows,
    left_on,
    ended_at,
    end_kind,
) -> None:
    person_ids, account_ids, team_ids = team_rows
    with postgres_engine.begin() as connection, pytest.raises(IntegrityError):
        _membership(
            connection,
            person_id=person_ids[0],
            team_id=team_ids[0],
            creator=account_ids[0],
            joined_on=date(2026, 1, 1),
            left_on=left_on,
            ended_at=ended_at,
            end_kind=end_kind,
        )


def test_manager_bindings_allow_many_to_many_but_reject_duplicate_open(
    postgres_engine: Engine,
    team_rows,
) -> None:
    _, account_ids, team_ids = team_rows
    with postgres_engine.begin() as connection:
        for account_id, team_id in (
            (account_ids[0], team_ids[0]),
            (account_ids[0], team_ids[1]),
            (account_ids[1], team_ids[0]),
        ):
            connection.execute(
                text(
                    "INSERT INTO team_manager_assignments "
                    "(id, account_id, team_id, created_by_user_id) "
                    "VALUES (:id, :account_id, :team_id, :creator)"
                ),
                {
                    "id": uuid4(),
                    "account_id": account_id,
                    "team_id": team_id,
                    "creator": account_ids[0],
                },
            )
        assert connection.scalar(text("SELECT count(*) FROM team_manager_assignments")) == 3
        with connection.begin_nested() as nested:
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO team_manager_assignments "
                        "(id, account_id, team_id, created_by_user_id) "
                        "VALUES (:id, :account_id, :team_id, :creator)"
                    ),
                    {
                        "id": uuid4(),
                        "account_id": account_ids[0],
                        "team_id": team_ids[0],
                        "creator": account_ids[0],
                    },
                )
            nested.rollback()


def test_operation_key_is_unique_per_actor_across_actions(
    postgres_engine: Engine,
    team_rows,
) -> None:
    _, account_ids, _ = team_rows
    key = uuid4()
    with postgres_engine.begin() as connection:
        for account_id in account_ids:
            connection.execute(
                text(
                    "INSERT INTO team_operations "
                    "(id, actor_account_id, idempotency_key, action, request_fingerprint, result_ids) "
                    "VALUES (:id, :account_id, :key, 'join', :fingerprint, CAST('{}' AS jsonb))"
                ),
                {"id": uuid4(), "account_id": account_id, "key": key, "fingerprint": "a" * 64},
            )
        with connection.begin_nested() as nested:
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO team_operations "
                        "(id, actor_account_id, idempotency_key, action, request_fingerprint, result_ids) "
                        "VALUES (:id, :account_id, :key, 'leave', :fingerprint, CAST('{}' AS jsonb))"
                    ),
                    {
                        "id": uuid4(),
                        "account_id": account_ids[0],
                        "key": key,
                        "fingerprint": "b" * 64,
                    },
                )
            nested.rollback()


def test_assigned_team_override_requires_scope_uuid(postgres_engine: Engine, team_rows) -> None:
    _, account_ids, team_ids = team_rows
    with postgres_engine.begin() as connection:
        with connection.begin_nested() as nested:
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO user_permission_overrides "
                        "(id, user_id, permission_id, effect, scope_kind) "
                        "VALUES (:id, :user_id, :permission_id, 'allow', 'assigned_team')"
                    ),
                    {
                        "id": uuid4(),
                        "user_id": account_ids[0],
                        "permission_id": UUID("20000000-0000-4000-8000-000000000082"),
                    },
                )
            nested.rollback()
        override_id = uuid4()
        connection.execute(
            text(
                "INSERT INTO user_permission_overrides "
                "(id, user_id, permission_id, effect, scope_kind, scope_id) "
                "VALUES (:id, :user_id, :permission_id, 'allow', 'assigned_team', :team_id)"
            ),
            {
                "id": override_id,
                "user_id": account_ids[0],
                "permission_id": UUID("20000000-0000-4000-8000-000000000082"),
                "team_id": team_ids[0],
            },
        )
        assert (
            connection.scalar(
                text("SELECT scope_id FROM user_permission_overrides WHERE id = :id"),
                {"id": override_id},
            )
            == team_ids[0]
        )
        connection.execute(
            text("DELETE FROM user_permission_overrides WHERE id = :id"), {"id": override_id}
        )
