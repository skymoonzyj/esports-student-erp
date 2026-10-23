from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from urllib.request import Request

import pytest


MODULE_PATH = Path(__file__).with_name("dnshe_certbot_hook.py")
SPEC = importlib.util.spec_from_file_location("dnshe_certbot_hook", MODULE_PATH)
assert SPEC and SPEC.loader
HOOK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HOOK)


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    key = tmp_path / "key"
    secret = tmp_path / "secret"
    key.write_text("key-value\n", encoding="utf-8")
    secret.write_text("secret-value\n", encoding="utf-8")
    key.chmod(0o600)
    secret.chmod(0o600)
    monkeypatch.setenv("DNSHE_API_KEY_FILE", str(key))
    monkeypatch.setenv("DNSHE_API_SECRET_FILE", str(secret))
    monkeypatch.setenv("CERTBOT_DOMAIN", "skymoonzyj.l.cd")
    monkeypatch.setenv("CERTBOT_VALIDATION", "challenge-value")
    monkeypatch.setenv("DNSHE_PROPAGATION_SECONDS", "0")


def test_add_record_creates_only_the_expected_txt_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _secrets(tmp_path, monkeypatch)
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def opener(request: Request, *, timeout: float) -> _Response:
        query = parse_qs(urlparse(request.full_url).query)
        body = json.loads(request.data.decode()) if request.data else None
        calls.append((query["endpoint"][0], query["action"][0], body))
        assert request.get_header("X-api-key") == "key-value"
        assert request.get_header("X-api-secret") == "secret-value"
        if query["endpoint"][0] == "subdomains":
            return _Response(
                {
                    "success": True,
                    "subdomains": [
                        {"id": 123, "full_domain": "skymoonzyj.l.cd"}
                    ],
                }
            )
        return _Response({"success": True, "id": 456})

    HOOK.add_record(opener=opener, sleeper=lambda _seconds: None)

    assert calls == [
        ("subdomains", "list", None),
        (
            "dns_records",
            "create",
            {
                "subdomain_id": 123,
                "type": "TXT",
                "name": "_acme-challenge",
                "content": "challenge-value",
                "ttl": 60,
            },
        ),
    ]


def test_cleanup_deletes_only_matching_txt_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _secrets(tmp_path, monkeypatch)
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def opener(request: Request, *, timeout: float) -> _Response:
        query = parse_qs(urlparse(request.full_url).query)
        body = json.loads(request.data.decode()) if request.data else None
        calls.append((query["endpoint"][0], query["action"][0], body))
        if query["endpoint"][0] == "subdomains":
            return _Response(
                {
                    "success": True,
                    "subdomains": [{"id": 123, "full_domain": "skymoonzyj.l.cd"}],
                }
            )
        if query["action"][0] == "list":
            return _Response(
                {
                    "success": True,
                    "records": [
                        {
                            "id": 456,
                            "name": "_acme-challenge.skymoonzyj.l.cd.",
                            "type": "TXT",
                            "content": "challenge-value",
                        },
                        {
                            "id": 789,
                            "name": "_acme-challenge.skymoonzyj.l.cd",
                            "type": "TXT",
                            "content": "keep-this-value",
                        },
                    ],
                }
            )
        return _Response({"success": True})

    HOOK.cleanup_record(opener=opener)

    assert calls[-1] == ("dns_records", "delete", {"id": 456})


def test_private_secret_file_is_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = tmp_path / "key"
    key.write_text("key-value\n", encoding="utf-8")
    key.chmod(0o644)
    monkeypatch.setenv("DNSHE_API_KEY_FILE", str(key))
    with pytest.raises(HOOK.HookError, match="private"):
        HOOK._private_secret(str(key))
