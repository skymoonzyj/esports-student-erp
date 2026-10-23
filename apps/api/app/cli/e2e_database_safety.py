from __future__ import annotations

import os
import re
import sys
from collections.abc import Callable
from ipaddress import IPv6Address
from urllib.parse import unquote_to_bytes

from sqlalchemy import Engine, create_engine, make_url, text

_DATABASE_PATH_MARKERS = ("/", "\\", "%", "\x00")
_DATABASE_SELECTOR_QUERY_KEYS = frozenset(
    {"dbname", "database", "service", "servicefile"}
)
_ALLOWED_QUERY_KEYS = frozenset({"application_name", "sslmode"})
_ALLOWED_SSLMODES = frozenset(
    {"allow", "disable", "prefer", "require", "verify-ca", "verify-full"}
)
_ALLOWED_SCHEMES = frozenset({"postgresql", "postgresql+psycopg"})
_REG_NAME_CHARACTER = re.compile(r"[A-Za-z0-9._-]")
_USERINFO_CHARACTER = re.compile(r"[A-Za-z0-9._~!$&'()*+,;=:%-]")
_INVALID_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_REFUSAL_MESSAGE = "E2E database URL is refused"


class E2EDatabaseRefused(RuntimeError):
    pass


def _validate_raw_postgresql_url(database_url: str) -> None:
    if (
        not database_url
        or "\\" in database_url
        or "#" in database_url
        or not database_url.isprintable()
        or any(character.isspace() for character in database_url)
    ):
        raise ValueError

    scheme, separator, remainder = database_url.partition("://")
    if not separator or scheme not in _ALLOWED_SCHEMES:
        raise ValueError

    raw_authority, path_separator, raw_path_and_query = remainder.partition("/")
    if not path_separator or not raw_path_and_query:
        raise ValueError
    if _INVALID_PERCENT_ESCAPE.search(raw_authority):
        raise ValueError

    raw_at_count = raw_authority.count("@")
    if raw_at_count > 1:
        raise ValueError
    if raw_at_count == 1:
        raw_userinfo, raw_host_and_port = raw_authority.split("@")
        if not raw_userinfo or not raw_host_and_port:
            raise ValueError
        _validate_raw_userinfo(raw_userinfo)
    else:
        raw_host_and_port = raw_authority
        if not raw_host_and_port:
            return

    _validate_raw_host_and_port(raw_host_and_port)


def _validate_raw_userinfo(raw_userinfo: str) -> None:
    raw_username = raw_userinfo.partition(":")[0]
    if not raw_username or any(
        _USERINFO_CHARACTER.fullmatch(character) is None for character in raw_userinfo
    ):
        raise ValueError
    decoded_userinfo = unquote_to_bytes(raw_userinfo).decode("utf-8")
    if (
        not decoded_userinfo.isprintable()
        or any(character.isspace() for character in decoded_userinfo)
        or "\\" in decoded_userinfo
    ):
        raise ValueError


def _validate_raw_host_and_port(raw_host_and_port: str) -> None:
    if raw_host_and_port.startswith("["):
        closing_bracket = raw_host_and_port.find("]")
        if closing_bracket == -1:
            raise ValueError
        raw_host = raw_host_and_port[1:closing_bracket]
        raw_port = raw_host_and_port[closing_bracket + 1 :]
        if not raw_host or "%" in raw_host or "[" in raw_host or "]" in raw_host:
            raise ValueError
        IPv6Address(raw_host)
        if raw_port:
            if not raw_port.startswith(":"):
                raise ValueError
            _validate_raw_port(raw_port[1:])
        return

    if "[" in raw_host_and_port or "]" in raw_host_and_port:
        raise ValueError
    if raw_host_and_port.count(":") > 1:
        raise ValueError
    raw_host, port_separator, raw_port = raw_host_and_port.partition(":")
    if (
        not raw_host
        or "%" in raw_host
        or any(_REG_NAME_CHARACTER.fullmatch(character) is None for character in raw_host)
    ):
        raise ValueError
    if port_separator:
        _validate_raw_port(raw_port)


def _validate_raw_port(raw_port: str) -> None:
    if not raw_port or not raw_port.isascii() or not raw_port.isdigit():
        raise ValueError
    if not 1 <= int(raw_port) <= 65535:
        raise ValueError


def _validate_raw_query(database_url: str) -> None:
    _, separator, raw_query = database_url.partition("?")
    if not separator:
        return
    if not raw_query or _INVALID_PERCENT_ESCAPE.search(raw_query):
        raise ValueError
    for field in raw_query.split("&"):
        raw_key, equals, raw_value = field.partition("=")
        if not raw_key or not equals or not raw_value:
            raise ValueError


def _effective_database_name(database_url: str) -> str:
    _validate_raw_postgresql_url(database_url)
    _validate_raw_query(database_url)
    url = make_url(database_url)
    query_keys = set(url.query)
    normalized_query_keys = {key.casefold() for key in query_keys}
    if normalized_query_keys & _DATABASE_SELECTOR_QUERY_KEYS:
        raise ValueError
    if query_keys - _ALLOWED_QUERY_KEYS:
        raise ValueError
    application_name = url.query.get("application_name")
    if application_name is not None and (
        not isinstance(application_name, str)
        or not application_name.isprintable()
        or "\\" in application_name
        or any(character.isspace() for character in application_name)
    ):
        raise ValueError
    sslmode = url.query.get("sslmode")
    if sslmode is not None and (
        not isinstance(sslmode, str) or sslmode not in _ALLOWED_SSLMODES
    ):
        raise ValueError

    dialect = url.get_dialect()()
    if dialect.name != "postgresql":
        raise ValueError
    positional_args, keyword_args = dialect.create_connect_args(url)
    if positional_args:
        raise ValueError
    database_name = keyword_args.get("dbname")
    if not isinstance(database_name, str):
        raise TypeError
    return database_name


def require_e2e_database_url(database_url: str) -> None:
    try:
        database_name = _effective_database_name(database_url)
    except Exception:  # noqa: BLE001 - fail closed for every dialect/parser error.
        raise E2EDatabaseRefused(_REFUSAL_MESSAGE) from None
    if (
        any(marker in database_name for marker in _DATABASE_PATH_MARKERS)
        or not (database_name == "erp_e2e" or database_name.endswith("_test"))
    ):
        raise E2EDatabaseRefused(_REFUSAL_MESSAGE)


def reset_e2e_database_schema(
    database_url: str,
    *,
    engine_factory: Callable[[str], Engine] = create_engine,
) -> Engine:
    require_e2e_database_url(database_url)
    engine = engine_factory(database_url)
    try:
        with engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
    except Exception:
        engine.dispose()
        raise
    return engine


def main() -> int:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if database_url is None:
        print("TEST_DATABASE_URL is required", file=sys.stderr)
        return 2
    try:
        require_e2e_database_url(database_url)
    except E2EDatabaseRefused:
        print(
            "只允许数据库名严格为 erp_e2e 或以 _test 结尾的隔离测试库",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
