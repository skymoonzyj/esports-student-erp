from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import Request

from app.core.errors import ProblemError


def _trusted_origin(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("invalid trusted origin")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("invalid trusted origin")
    host = parsed.hostname
    if ":" in host:
        host = f"[{host}]"
    port = parsed.port
    if port is not None and port != (443 if parsed.scheme == "https" else 80):
        host = f"{host}:{port}"
    return f"{parsed.scheme}://{host}"


def require_activation_request(request: Request) -> None:
    """Reject browser cross-origin writes before evaluating any dependencies.

    Native URLSession does not send Origin; it is accepted only with JSON and
    without hostile or malformed browser Fetch Metadata. Host/forwarded headers
    never define trust for these anonymous cookie-bearing endpoints.
    """
    content_types = request.headers.getlist("content-type")
    if (
        len(content_types) != 1
        or content_types[0].split(";", 1)[0].strip().lower() != "application/json"
    ):
        raise ProblemError(status=415, code="activation_json_required", detail="请使用 JSON 请求")

    def reject() -> None:
        raise ProblemError(
            status=403, code="activation_origin_invalid", detail="安全校验失败，请刷新后重试"
        )

    origins = request.headers.getlist("origin")
    if len(origins) > 1:
        reject()
    if origins:
        origin = origins[0]
        try:
            trusted = _trusted_origin(request.app.state.settings.erp_web_base_url)
            if any(ord(char) <= 32 or ord(char) >= 127 for char in origin):
                reject()
            if origin != _trusted_origin(origin) or origin != trusted:
                reject()
        except ValueError:
            reject()

    allowed = {
        "sec-fetch-site": {"same-origin", "same-site", "none"},
        "sec-fetch-mode": {"cors", "same-origin"},
        "sec-fetch-dest": {"empty"},
        "sec-fetch-user": set(),
    }
    for name, values in allowed.items():
        headers = request.headers.getlist(name)
        if len(headers) > 1 or (headers and headers[0] not in values):
            reject()
