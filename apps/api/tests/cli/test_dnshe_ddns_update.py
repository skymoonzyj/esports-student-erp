from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any, Self

import pytest

MODULE_PATH = (
    Path(__file__).resolve().parents[2] / "app/cli/dnshe_ddns_update.py"
)


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def test_update_posts_private_token_without_exposing_it(tmp_path: Path) -> None:
    assert MODULE_PATH.is_file(), "DDNS updater module is missing"
    updater = importlib.import_module("app.cli.dnshe_ddns_update")
    token = "ddns_" + "a" * 64
    token_file = tmp_path / "dnshe_ddns_token"
    token_file.write_text(token, encoding="utf-8")
    token_file.chmod(0o600)
    captured: dict[str, object] = {}

    def opener(request: Any, *, timeout: float) -> _Response:
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["token"] = request.get_header("X-ddns-token")
        captured["timeout"] = timeout
        return _Response(
            {"success": True, "message": "IP address has not changed"}
        )

    result = updater.update_dnshe(
        token_file,
        endpoint="https://ddns.example.test/update",
        timeout=7.0,
        opener=opener,
    )

    assert captured == {
        "url": "https://ddns.example.test/update",
        "method": "POST",
        "token": token,
        "timeout": 7.0,
    }
    assert result == "unchanged"
    assert token not in result


def test_update_rejects_non_private_token_file_before_network(
    tmp_path: Path,
) -> None:
    assert MODULE_PATH.is_file(), "DDNS updater module is missing"
    updater = importlib.import_module("app.cli.dnshe_ddns_update")
    token_file = tmp_path / "dnshe_ddns_token"
    token_file.write_text("ddns_" + "b" * 64, encoding="utf-8")
    token_file.chmod(0o640)
    called = False

    def opener(_request: Any, *, timeout: float) -> _Response:
        nonlocal called
        called = True
        return _Response({"success": True})

    with pytest.raises(updater.DdnsUpdateError, match="private"):
        updater.update_dnshe(token_file, opener=opener)

    assert called is False


def test_cli_hides_provider_payload_on_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert MODULE_PATH.is_file(), "DDNS updater module is missing"
    updater = importlib.import_module("app.cli.dnshe_ddns_update")
    token = "ddns_" + "c" * 64
    token_file = tmp_path / "dnshe_ddns_token"
    token_file.write_text(token, encoding="utf-8")
    token_file.chmod(0o600)

    def opener(_request: Any, *, timeout: float) -> _Response:
        return _Response(
            {"success": False, "message": f"invalid secret {token}"}
        )

    exit_code = updater.main(
        ["--token-file", str(token_file)],
        opener=opener,
    )
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.out == ""
    assert captured.err == "dnshe-ddns update failed\n"
    assert token not in captured.out
    assert token not in captured.err
