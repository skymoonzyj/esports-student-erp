#!/usr/bin/env python3
"""Certbot manual DNS hook for DNSHE's v2 API.

The hook intentionally reads credentials from private files and never prints
provider responses.  Certbot invokes it as ``add`` for the auth hook and
``cleanup`` for the cleanup hook.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_API_BASE = "https://api005.dnshe.com/index.php"
DEFAULT_KEY_FILE = "/run/secrets/dnshe_api_key"
DEFAULT_SECRET_FILE = "/run/secrets/dnshe_api_secret"
DEFAULT_TTL = 60
DEFAULT_PROPAGATION_SECONDS = 60


class HookError(RuntimeError):
    """A non-disclosing DNS hook failure."""


def _private_secret(path: str) -> str:
    secret_path = Path(path)
    mode = stat.S_IMODE(secret_path.stat().st_mode)
    if mode & 0o077:
        raise HookError("credential file is not private")
    value = secret_path.read_text(encoding="utf-8").strip()
    if not value:
        raise HookError("credential file is empty")
    return value


def _domain() -> str:
    value = os.environ.get("CERTBOT_DOMAIN", "").strip().rstrip(".").lower()
    if not value or any(ch.isspace() for ch in value):
        raise HookError("CERTBOT_DOMAIN is missing")
    return value.removeprefix("*.")


def _validation() -> str:
    value = os.environ.get("CERTBOT_VALIDATION", "").strip()
    if not value or any(ch.isspace() for ch in value):
        raise HookError("CERTBOT_VALIDATION is missing")
    return value


def _api_request(
    endpoint: str,
    action: str,
    *,
    method: str = "GET",
    params: dict[str, str | int] | None = None,
    body: dict[str, Any] | None = None,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    base = os.environ.get("DNSHE_API_BASE_URL", DEFAULT_API_BASE).rstrip("?")
    query: dict[str, str | int] = {
        "m": "domain_hub",
        "endpoint": endpoint,
        "action": action,
    }
    if params:
        query.update(params)
    url = f"{base}?{urlencode(query)}"
    key = _private_secret(os.environ.get("DNSHE_API_KEY_FILE", DEFAULT_KEY_FILE))
    secret = _private_secret(
        os.environ.get("DNSHE_API_SECRET_FILE", DEFAULT_SECRET_FILE)
    )
    data = None
    headers = {
        "X-API-Key": key,
        "X-API-Secret": secret,
        "Accept": "application/json",
    }
    if body is not None:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with opener(request, timeout=20) as response:
            payload = json.loads(response.read())
    except Exception as exc:  # pragma: no cover - provider/network boundary
        raise HookError("DNSHE request failed") from exc
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise HookError("DNSHE request was rejected")
    return payload


def _subdomain_id(domain: str, *, opener: Callable[..., Any] = urlopen) -> int:
    configured = os.environ.get("DNSHE_SUBDOMAIN_ID", "").strip()
    if configured:
        try:
            return int(configured)
        except ValueError as exc:
            raise HookError("DNSHE_SUBDOMAIN_ID is invalid") from exc

    payload = _api_request(
        "subdomains",
        "list",
        params={"search": domain, "per_page": 20},
        opener=opener,
    )
    matches = [
        item
        for item in payload.get("subdomains", [])
        if isinstance(item, dict)
        and str(item.get("full_domain", "")).rstrip(".").lower() == domain
    ]
    if len(matches) != 1:
        raise HookError("DNSHE subdomain was not uniquely resolved")
    try:
        return int(matches[0]["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HookError("DNSHE subdomain id is invalid") from exc


def _challenge_name(domain: str) -> str:
    return f"_acme-challenge.{domain}"


def _relative_challenge_name() -> str:
    # DNSHE's API requires a relative name for records under a subdomain.
    return "_acme-challenge"


def _records(subdomain_id: int, *, opener: Callable[..., Any] = urlopen) -> list[dict[str, Any]]:
    payload = _api_request(
        "dns_records",
        "list",
        params={"subdomain_id": subdomain_id},
        opener=opener,
    )
    records = payload.get("records", [])
    return [item for item in records if isinstance(item, dict)]


def add_record(
    *, opener: Callable[..., Any] = urlopen, sleeper: Callable[[float], None] = time.sleep
) -> None:
    domain = _domain()
    validation = _validation()
    subdomain_id = _subdomain_id(domain, opener=opener)
    _api_request(
        "dns_records",
        "create",
        method="POST",
        body={
            "subdomain_id": subdomain_id,
            "type": "TXT",
            "name": _relative_challenge_name(),
            "content": validation,
            "ttl": int(os.environ.get("DNSHE_TXT_TTL", DEFAULT_TTL)),
        },
        opener=opener,
    )
    # DNSHE's provider is authoritative but recursive caches may lag.  Give
    # the TXT record time to reach the public nameservers before ACME polls.
    delay = int(
        os.environ.get(
            "DNSHE_PROPAGATION_SECONDS", DEFAULT_PROPAGATION_SECONDS
        )
    )
    if delay > 0:
        sleeper(delay)


def cleanup_record(*, opener: Callable[..., Any] = urlopen) -> None:
    domain = _domain()
    validation = _validation()
    subdomain_id = _subdomain_id(domain, opener=opener)
    challenge_names = {
        _challenge_name(domain).lower().rstrip("."),
        _relative_challenge_name(),
    }
    for record in _records(subdomain_id, opener=opener):
        name = str(record.get("name", "")).lower().rstrip(".")
        content = str(record.get("content", ""))
        if name not in challenge_names or content != validation:
            continue
        record_id = record.get("id") or record.get("record_id")
        if record_id is None:
            raise HookError("DNSHE TXT record id is missing")
        _api_request(
            "dns_records",
            "delete",
            method="POST",
            body={"id": record_id},
            opener=opener,
        )


def main(argv: list[str] | None = None) -> int:
    action = (argv or sys.argv[1:])[:1]
    try:
        if action == ["add"]:
            add_record()
        elif action == ["cleanup"]:
            cleanup_record()
        else:
            raise HookError("hook action is invalid")
    except (HookError, OSError, ValueError, json.JSONDecodeError):
        # Certbot only needs a non-zero status; never expose headers, secrets,
        # or provider response bodies in its logs.
        print("dnshe certbot hook failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
