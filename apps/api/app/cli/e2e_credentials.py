from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class E2ECredential:
    identifier: str
    password: str


def _load_credentials() -> Mapping[str, E2ECredential]:
    path = Path(__file__).with_name("e2e_credentials.json")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError("E2E credentials must be a JSON object")

    credentials: dict[str, E2ECredential] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            raise TypeError("E2E credential entries must be named objects")
        identifier = value.get("identifier")
        password = value.get("password")
        if not isinstance(identifier, str) or not identifier.strip():
            raise RuntimeError(f"E2E credential {key!r} has an invalid identifier")
        if not isinstance(password, str) or not re.fullmatch(
            r"(?=.*[A-Z])(?=.*[a-z])(?=.*\d).{8,16}",
            password,
        ):
            raise RuntimeError(f"E2E credential {key!r} violates password policy")
        credentials[key] = E2ECredential(
            identifier=identifier,
            password=password,
        )
    return MappingProxyType(credentials)


E2E_CREDENTIALS = _load_credentials()
