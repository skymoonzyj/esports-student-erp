from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text


def config_for(engine):
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    url = engine.url.render_as_string(hide_password=False)
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["database_url"] = url
    return config


def test_staff_migration_empty_roundtrip_preserves_legacy(postgres_engine):
    config = config_for(postgres_engine)
    with postgres_engine.connect() as connection:
        old = connection.execute(
            text(
                "SELECT id, account_id, team_id, ended_at FROM team_manager_assignments ORDER BY id"
            )
        ).all()
    try:
        command.downgrade(config, "0084_team_staff_roles")
        assert "team_staff_assignments" not in inspect(postgres_engine).get_table_names()
        command.upgrade(config, "0085_team_staff_assignments")
        assert "team_staff_assignments" in inspect(postgres_engine).get_table_names()
        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM team_staff_assignments")) == 0
            assert (
                connection.execute(
                    text(
                        "SELECT id, account_id, team_id, ended_at FROM team_manager_assignments ORDER BY id"
                    )
                ).all()
                == old
            )
    finally:
        command.upgrade(config, "head")


def test_staff_migration_refuses_to_discard_ended_history(postgres_engine):
    config = config_for(postgres_engine)
    person, account, project, team, assignment = [uuid4() for _ in range(5)]
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO people (id, full_name, identity_ciphertext, identity_digest, sex, date_of_birth) VALUES (:id, '合成工作人员', 'cipher', :digest, 'female', '2000-01-01')"
                ),
                {"id": person, "digest": person.hex},
            )
            connection.execute(
                text(
                    "INSERT INTO user_accounts (id, person_id, password_hash, status, force_password_change, failed_login_count) VALUES (:id, :person, 'hash', 'active', false, 0)"
                ),
                {"id": account, "person": person},
            )
            connection.execute(
                text(
                    "INSERT INTO game_projects (id,name,sort_order,is_active,is_team_division,created_by_user_id) VALUES (:id,:name,1,true,true,:account)"
                ),
                {"id": project, "name": f"合成分部{project.hex}", "account": account},
            )
            connection.execute(
                text(
                    "INSERT INTO professional_teams (id,project_id,name,is_active,created_by_user_id) VALUES (:id,:project,'合成队伍',true,:account)"
                ),
                {"id": team, "project": project, "account": account},
            )
            connection.execute(
                text(
                    "INSERT INTO team_staff_assignments (id,account_id,team_id,staff_role,created_by_user_id,ended_at,end_kind) VALUES (:id,:account,:team,'coach',:account,now(),'unassigned')"
                ),
                {"id": assignment, "account": account, "team": team},
            )
        with pytest.raises(RuntimeError, match="工作人员.*历史"):
            command.downgrade(config, "0084_team_staff_roles")
        with postgres_engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM team_staff_assignments WHERE id=:id"),
                    {"id": assignment},
                )
                == 1
            )
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM team_staff_assignments WHERE id=:id"), {"id": assignment}
            )
            connection.execute(text("DELETE FROM professional_teams WHERE id=:id"), {"id": team})
            connection.execute(text("DELETE FROM game_projects WHERE id=:id"), {"id": project})
            connection.execute(text("DELETE FROM user_accounts WHERE id=:id"), {"id": account})
            connection.execute(text("DELETE FROM people WHERE id=:id"), {"id": person})
        command.upgrade(config, "head")
