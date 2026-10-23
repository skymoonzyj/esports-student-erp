"""Real HTTP contract; set ERP_WEB_TEST_URL to an exactly-owned candidate container.

No HTTP mocks or config-text assertions. Normal backend runs skip the opt-in HTTP
checks; release acceptance must explicitly run them against its owned container.
"""
from __future__ import annotations

import json
import os
import re
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import urlopen

import pytest


def configured_endpoint() -> str:
    value = os.environ.get("ERP_WEB_TEST_URL", "")
    if not value:
        pytest.skip("HTTP delivery requires an explicitly owned ERP_WEB_TEST_URL")
    parsed = urlsplit(value)
    assert parsed.scheme == "http" and parsed.hostname == "127.0.0.1" and parsed.port, (
        "ERP_WEB_TEST_URL must identify an owned disposable localhost HTTP container"
    )
    assert not parsed.username and not parsed.password and not parsed.query
    return value.rstrip("/")


@pytest.fixture(scope="module")
def endpoint() -> str:
    return configured_endpoint()


def request(endpoint: str, route: str) -> tuple[int, dict[str, str], bytes]:
    try:
        response = urlopen(endpoint + route, timeout=10)
    except HTTPError as error:
        response = error
    with response:
        headers = dict(response.headers)
        # Multiple HTTP header fields are semantically one comma-joined value.
        cache_control = response.headers.get_all("Cache-Control")
        if cache_control:
            headers["Cache-Control"] = ", ".join(cache_control)
        return response.status, headers, response.read()


def test_build_id_matches_document(endpoint: str) -> None:
    status, _, document = request(endpoint, "/index.html")
    assert status == 200
    match = re.search(rb'<meta name="erp-build-id" content="([A-Za-z0-9_-]+)"', document)
    assert match, "HTML must expose the identity compiled into this document"
    compiled_id = match.group(1).decode()
    status, _, body = request(endpoint, "/build-info.json")
    assert status == 200
    public_info = json.loads(body)
    assert public_info == {"schema_version": 1, "build_id": compiled_id}


@pytest.mark.parametrize("route", ["/assets-manifest.json", "/.build-metadata/assets-manifest.json", "/etc/erp-web/assets-manifest.json", "/50x.html", "/.env", "/src/main.tsx", "/assets/app.js.map"])
def test_private_inventory_not_served(endpoint: str, route: str) -> None:
    status, headers, body = request(endpoint, route)
    assert status == 404
    assert b"predecessor_image_id" not in body
    assert "immutable" not in headers.get("Cache-Control", "")


@pytest.mark.parametrize("route", ["/assets/missing.js", "/assets/missing.css", "/assets/missing.json", "/assets/nested/missing", "/assets/"])
def test_missing_asset_is_404(endpoint: str, route: str) -> None:
    status, headers, body = request(endpoint, route)
    assert status == 404
    assert b'<div id="root">' not in body
    assert "immutable" not in headers.get("Cache-Control", "")
    assert "max-age=604800" not in headers.get("Cache-Control", "")


@pytest.mark.parametrize("route", ["/", "/index.html", "/login", "/build-info.json"])
def test_html_and_build_info_revalidate(endpoint: str, route: str) -> None:
    status, headers, _ = request(endpoint, route)
    assert status == 200
    assert headers.get("Cache-Control") == "no-cache"


def test_existing_hashed_javascript_is_immutable(endpoint: str) -> None:
    _, _, document = request(endpoint, "/index.html")
    asset = re.search(rb'src="(/assets/[^\"]+\.js)"', document)
    assert asset
    status, headers, body = request(endpoint, asset.group(1).decode())
    assert status == 200 and body
    cache_control = headers.get("Cache-Control", "")
    assert "public" in cache_control and "max-age=604800" in cache_control
    assert "immutable" in cache_control


def test_http_probe_skips_without_explicit_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ERP_WEB_TEST_URL", raising=False)
    with pytest.raises(pytest.skip.Exception):
        configured_endpoint()
