from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Never

import pytest

from app.cli import e2e_database_safety
from app.cli.e2e_database_safety import (
    E2EDatabaseRefused,
    _validate_raw_postgresql_url,
    require_e2e_database_url,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
LAUNCHERS = (
    REPOSITORY_ROOT / "scripts/run-housing-room-status-e2e.sh",
    REPOSITORY_ROOT / "scripts/run-housing-occupancy-e2e.sh",
    REPOSITORY_ROOT / "scripts/run-evaluation-page-e2e.sh",
)


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql://localhost:5432/foo_test",
        "postgresql+psycopg://user:encoded%40secret@localhost:5432/foo_test",
        (
            "postgresql+psycopg://erp_e2e:encoded%40secret@"
            "127.0.0.1:55434/housing_test"
        ),
        "postgresql+psycopg://user:secret@[::1]:5432/foo_test",
        "postgresql+psycopg://user:secret@[2001:db8::1]:65535/foo_test",
        "postgresql+psycopg:///foo_test",
    ],
)
def test_raw_postgresql_url_guard_accepts_unambiguous_authorities(
    database_url: str,
) -> None:
    assert _validate_raw_postgresql_url(database_url) is None


@pytest.mark.parametrize(
    "database_url",
    [
        (
            "postgresql+psycopg://user:do-not-leak@sensitive-token@"
            "127.0.0.1:55434/foo_test"
        ),
        "postgresql+psycopg://user:secret@/foo_test",
        "postgresql+psycopg://:5432/foo_test",
        "postgresql+psycopg://localhost:5432:55434/foo_test",
        "postgresql+psycopg://[::1/foo_test",
        "postgresql+psycopg://[]:5432/foo_test",
        "postgresql+psycopg://[::1]extra/foo_test",
        "postgresql+psycopg://[::1]:/foo_test",
        "postgresql+psycopg://[::1]:5432:55434/foo_test",
        "postgresql+psycopg://[::1]]:5432/foo_test",
        "postgresql+psycopg://::1/foo_test",
        "postgresql+psycopg://user:bad%@localhost:5432/foo_test",
        "postgresql+psycopg://user:bad password@localhost:5432/foo_test",
        "postgresql+psycopg://user:bad%5Cpassword@localhost:5432/foo_test",
        "postgresql+psycopg://user:%C2%80secret@localhost:5432/foo_test",
        "postgresql+psycopg://us[er:secret@localhost:5432/foo_test",
        "postgresql+psycopg://:secret@localhost:5432/foo_test",
        "postgresql+psycopg://user:bad\\password@localhost:5432/foo_test",
        "postgresql+psycopg://local\thost:5432/foo_test",
        "postgresql+psycopg://host-a,host-b:55434/foo_test",
        "postgresql+psycopg://,host-b:55434/foo_test",
        "postgresql+psycopg://localhost:/foo_test",
        "postgresql+psycopg://localhost:0/foo_test",
        "postgresql+psycopg://localhost:65536/foo_test",
        "postgresql+psycopg://localhost:+5432/foo_test",
        "postgresql+psycopg://localhost:５４３２/foo_test",
        "postgresql+psycopg://localhost:5432/foo_test#unsafe-fragment",
        "postgresql+psycopg://localhost:5432/foo_test\n",
        "postgresql+psycopg://localhost:5432/foo\N{NO-BREAK SPACE}_test",
        "postgresql+psycopg://localhost:5432/foo\N{ZERO WIDTH SPACE}_test",
        "postgresql+psycopg://localhost:5432/foo\x80_test",
    ],
)
def test_raw_postgresql_url_guard_refuses_ambiguous_or_malformed_authority(
    database_url: str,
) -> None:
    with pytest.raises(ValueError):
        _validate_raw_postgresql_url(database_url)


@pytest.mark.parametrize(
    "database_url",
    [
        (
            "postgresql+psycopg://erp:secret@localhost:5432/production"
            "?application_name=_test"
        ),
        "postgresql+psycopg://erp:secret@localhost:5432/production/_test",
        "postgresql+psycopg://erp:secret@localhost:5432/%2Ffoo_test",
        "postgresql+psycopg://erp:secret@localhost:5432/foo%5Ftest",
        "postgresql+psycopg://erp:secret@localhost:5432/production%3Fname%3D_test",
        "postgresql+psycopg://erp:secret@localhost:do-not-leak/foo_test",
        "not a database url ending _test",
        "postgresql+unknown://erp:secret@localhost:5432/foo_test",
        "postgresql+pg8000://erp:secret@localhost:5432/foo_test",
        "postgresql+psycopg://erp:secret@localhost:5432/foo_test?unknown=x",
        "postgresql+psycopg://erp:secret@localhost:5432/foo_test?application_name=%",
        "postgresql+psycopg://erp:secret@localhost:5432/foo_test?application_name=%20",
        "postgresql+psycopg://erp:secret@localhost:5432/foo_test?application_name=x+y",
        "postgresql+psycopg://erp:secret@localhost:5432/foo_test?application_name=%5C",
        "postgresql+psycopg://erp:secret@localhost:5432/foo_test?sslmode=%ZZ",
        "postgresql+psycopg://erp:secret@localhost:5432/foo_test?sslmode",
        "postgresql+psycopg://erp:secret@localhost:5432/foo_test?sslmode=invalid",
        (
            "postgresql+psycopg://user:do-not-leak@sensitive-token@"
            "127.0.0.1:55434/foo_test"
        ),
        "postgresql+psycopg://user:secret@/foo_test",
        "postgresql+psycopg://[::1]extra/foo_test",
        "postgresql+psycopg://localhost:5432/foo_test#unsafe-fragment",
    ],
)
def test_e2e_database_guard_refuses_query_and_encoded_path_tricks(
    database_url: str,
) -> None:
    with pytest.raises(E2EDatabaseRefused):
        require_e2e_database_url(database_url)


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql+psycopg://erp:secret@localhost:5432/erp_e2e",
        (
            "postgresql+psycopg://erp:secret@localhost:5432/housing_test"
            "?application_name=x&sslmode=require"
        ),
        (
            "postgresql+psycopg://erp_e2e:encoded%40secret@"
            "127.0.0.1:55434/housing_test"
        ),
        "postgresql+psycopg:///housing_test",
        "postgresql+psycopg://erp:secret@localhost:5432/住房_test",
    ],
)
def test_e2e_database_guard_accepts_safe_effective_database_name(
    database_url: str,
) -> None:
    assert require_e2e_database_url(database_url) is None


@pytest.mark.parametrize(
    "unsafe_url",
    [
        (
            "postgresql+psycopg://production-user:sensitive-token@localhost:5432/"
            "foo_test?dbname=production"
        ),
        (
            "postgresql+psycopg://user:do-not-leak@sensitive-token@"
            "127.0.0.1:55434/foo_test"
        ),
    ],
)
def test_schema_reset_refuses_unsafe_url_before_creating_an_engine(
    unsafe_url: str,
) -> None:
    engine_factory_called = False

    def unexpected_engine_factory(_database_url: str) -> Never:
        nonlocal engine_factory_called
        engine_factory_called = True
        raise AssertionError("unsafe URL reached create_engine")

    with pytest.raises(E2EDatabaseRefused, match="^E2E database URL is refused$"):
        e2e_database_safety.reset_e2e_database_schema(
            unsafe_url,
            engine_factory=unexpected_engine_factory,
        )

    assert engine_factory_called is False


@pytest.mark.parametrize(
    "query",
    [
        "dbname=production",
        "DBNAME=production",
        "%64bname=production",
        "dbname=production&dbname=erp_e2e",
        "database=production",
        "DaTaBaSe=production",
        "service=production",
        "SERVICE=production",
        "servicefile=%2Ftmp%2Fproduction.conf",
        "%73ervicefile=%2Ftmp%2Fproduction.conf",
    ],
)
def test_e2e_database_guard_refuses_effective_target_selectors(query: str) -> None:
    with pytest.raises(E2EDatabaseRefused, match="^E2E database URL is refused$"):
        require_e2e_database_url(
            f"postgresql+psycopg://erp:sensitive-token@localhost:5432/foo_test?{query}"
        )


@pytest.mark.parametrize(
    "unsafe_url",
    [
        (
            "postgresql+psycopg://production-user:do-not-leak@localhost:5432/production"
            "?application_name=_test"
        ),
        "postgresql+psycopg://production-user:do-not-leak@localhost:5432/%2Ffoo_test",
        "postgresql+psycopg://production-user:secret@localhost:do-not-leak/foo_test",
        (
            "postgresql+psycopg://production-user:sensitive-token@localhost:5432/"
            "foo_test?dbname=production"
        ),
        (
            "postgresql+psycopg://production-user:sensitive-token@localhost:5432/"
            "foo_test?sslmode=%ZZ"
        ),
        (
            "postgresql+psycopg://user:do-not-leak@sensitive-token@"
            "127.0.0.1:55434/foo_test"
        ),
        "postgresql+psycopg://user:do-not-leak@/foo_test",
        "postgresql+psycopg://[::1]sensitive-token/foo_test",
        (
            "postgresql+psycopg://production-user:sensitive-token@"
            ",127.0.0.1:55434/foo_test"
        ),
        (
            "postgresql+psycopg://production-user:sensitive-token@localhost:5432/"
            "foo_test?application_name=%20"
        ),
    ],
)
@pytest.mark.parametrize("launcher", LAUNCHERS, ids=lambda path: path.stem)
def test_launcher_refuses_invalid_database_before_alembic_without_leaking_url(
    tmp_path: Path,
    unsafe_url: str,
    launcher: Path,
) -> None:
    invocation_log = tmp_path / "python-invocations.log"
    python_wrapper = tmp_path / "python-wrapper"
    python_wrapper.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {shlex.quote(str(invocation_log))}\n"
        f"exec {shlex.quote(os.fsdecode(os.fsencode(sys.executable)))} \"$@\"\n",
        encoding="utf-8",
    )
    python_wrapper.chmod(0o700)
    result = subprocess.run(
        [str(launcher)],
        cwd=REPOSITORY_ROOT,
        env={
            **os.environ,
            "E2E_PYTHON_BIN": str(python_wrapper),
            "TEST_DATABASE_URL": unsafe_url,
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "alembic" not in invocation_log.read_text(encoding="utf-8")
    assert unsafe_url not in result.stdout
    assert unsafe_url not in result.stderr
    assert "do-not-leak" not in result.stdout
    assert "do-not-leak" not in result.stderr
    assert "sensitive-token" not in result.stdout
    assert "sensitive-token" not in result.stderr
    assert "production" not in result.stdout
    assert "production" not in result.stderr
    assert result.stdout == ""
    assert result.stderr == "只允许数据库名严格为 erp_e2e 或以 _test 结尾的隔离测试库\n"


@pytest.mark.parametrize("launcher", LAUNCHERS, ids=lambda path: path.stem)
def test_launcher_accepts_safe_name_with_query_at_guard_stage(launcher: Path) -> None:
    result = subprocess.run(
        [str(launcher)],
        cwd=REPOSITORY_ROOT,
        env={
            **os.environ,
            "TEST_DATABASE_URL": (
                "postgresql+psycopg://erp:encoded%40secret@localhost:5432/housing_test"
                "?application_name=x"
            ),
            "FIELD_ENCRYPTION_MASTER_KEY": "",
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "FIELD_ENCRYPTION_MASTER_KEY" in result.stderr
    assert "只允许" not in result.stderr
