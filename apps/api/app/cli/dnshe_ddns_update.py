from __future__ import annotations

import argparse
import json
import stat
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

DEFAULT_ENDPOINT = (
    "https://api005.dnshe.com/"
    "index.php?m=domain_hub&endpoint=ddns&action=update"
)


class DdnsUpdateError(RuntimeError):
    pass


def update_dnshe(
    token_file: Path,
    *,
    endpoint: str = DEFAULT_ENDPOINT,
    timeout: float = 20.0,
    opener: Callable[..., Any] = urlopen,
) -> str:
    if stat.S_IMODE(token_file.stat().st_mode) & 0o077:
        raise DdnsUpdateError("DNSHE DDNS token file must be private")
    token = token_file.read_text(encoding="utf-8").strip()
    request = Request(
        endpoint,
        data=b"",
        headers={"X-DDNS-Token": token},
        method="POST",
    )
    with opener(request, timeout=timeout) as response:
        payload = json.loads(response.read())
    if payload.get("success") is not True:
        raise DdnsUpdateError("DNSHE DDNS update failed")
    message = str(payload.get("message", "")).casefold()
    return "unchanged" if "not changed" in message else "updated"


def main(
    argv: list[str] | None = None,
    *,
    opener: Callable[..., Any] = urlopen,
) -> int:
    parser = argparse.ArgumentParser(description="Update a DNSHE DDNS record")
    parser.add_argument("--token-file", required=True, type=Path)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--timeout", default=20.0, type=float)
    args = parser.parse_args(argv)
    try:
        result = update_dnshe(
            args.token_file,
            endpoint=args.endpoint,
            timeout=args.timeout,
            opener=opener,
        )
    except (DdnsUpdateError, OSError, UnicodeError, json.JSONDecodeError):
        print("dnshe-ddns update failed", file=sys.stderr)
        return 1
    print(f"dnshe-ddns {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
