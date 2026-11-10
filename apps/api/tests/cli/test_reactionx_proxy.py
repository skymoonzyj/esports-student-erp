"""Evaluate the selected production location rather than merely grep a filename."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def location(path):
    source = (ROOT / "infra/nginx/nginx.conf").read_text()
    blocks = re.findall(r'location\s+(~\s+"[^"\n]+"|[^\n{]+)\s*\{([^{}]*)\}', source)
    exact = [(key, body) for key, body in blocks if key.strip() == "= " + path]
    if exact:
        return exact[0][1]
    for key, body in blocks:
        if key.strip().startswith("~ ") and re.search(key.strip()[2:].strip('"'), path):
            return body
    prefix = [
        (key.strip(), body)
        for key, body in blocks
        if key.strip().startswith("/") and path.startswith(key.strip())
    ]
    return max(prefix, key=lambda item: len(item[0]))[1]


def test_reactionx_limits_and_trusted_proxy_headers_are_selected():
    result = "/api/v1/reactionx-client/runs/00000000-0000-4000-8000-000000000001/result"
    for path, limit in [
        (result, "8m"),
        (result + "/extra", "256k"),
        ("/api/v1/reactionx/runs/prepare", "256k"),
        ("/api/v1/reactionx-client/heartbeat", "256k"),
    ]:
        block = location(path)
        assert f"client_max_body_size {limit};" in block
        for directive in (
            "proxy_pass http://api:8000;",
            "proxy_http_version 1.1;",
            "proxy_set_header Host $host;",
            "proxy_set_header X-Real-IP $remote_addr;",
            "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
            "proxy_set_header X-Forwarded-Proto $client_forwarded_proto;",
            "proxy_hide_header X-Powered-By;",
        ):
            assert directive in block
    assert "client_max_body_size" not in location("/api/v1/other/upload")
    assert "client_max_body_size 25m;" in (ROOT / "infra/nginx/nginx.conf").read_text()
