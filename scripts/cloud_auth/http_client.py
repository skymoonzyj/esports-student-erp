"""TLS-only stdlib HTTP, pinned public DNS, no redirects/proxies, 64 KiB maximum.

All requested endpoints are fixed official OAuth or token-bound identity paths.
Query/form secrets are never retained in errors, logs or reprs.
"""

import http.client
import ipaddress
import json
import math
import re
import socket
import ssl
import struct
import zlib
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode, urlsplit

from .provider_types import ProviderError

MAX_RESPONSE = 64 * 1024
# 22-char PNG data URI prefix + Base64 must fit the peer's 48,000-char field.
# This leaves more than 14 KiB for device materials and the private RPC envelope.
MAX_QR_PNG = ((48000 - len("data:image/png;base64,")) // 4) * 3
API_ENDPOINTS = {
    ("GET", "openapi.baidu.com", "/oauth/2.0/device/code"),
    ("GET", "openapi.baidu.com", "/oauth/2.0/token"),
    ("POST", "passportapi.115.com", "/open/authDeviceCode"),
    ("POST", "passportapi.115.com", "/open/deviceCodeToToken"),
    ("GET", "qrcodeapi.115.com", "/get/status/"),
    ("GET", "pan.baidu.com", "/rest/2.0/xpan/nas"),
    ("GET", "proapi.115.com", "/open/user/info"),
    ("POST", "passportapi.115.com", "/open/refreshToken"),
    ("GET", "api.oplist.org", "/baiduyun/renewapi"),
}


def _url(url: str, code: str = "provider_url_invalid"):
    if (
        not isinstance(url, str)
        or not url
        or len(url) > 2048
        or "\\" in url
        or any(ord(c) < 33 or ord(c) > 126 for c in url)
    ):
        raise ProviderError(code)
    try:
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or parts.username
            or parts.password
            or parts.port
            or parts.fragment
            or "%" in parts.path
            or parts.netloc != parts.hostname
        ):
            raise ValueError
    except ValueError:
        raise ProviderError(code) from None
    return parts


def validate_baidu_qr(url: str) -> str:
    parts = _url(url, "provider_qr_invalid")
    if (
        parts.hostname != "openapi.baidu.com"
        or parts.query
        or not re.fullmatch(r"/device/qrcode/[A-Za-z0-9_-]{1,512}/[A-Za-z0-9_-]{1,128}", parts.path)
    ):
        raise ProviderError("provider_qr_invalid")
    return url


def validate_baidu_open_url(url: str) -> str:
    parts = _url(url, "provider_qr_invalid")
    if (
        parts.hostname != "openapi.baidu.com"
        or parts.query
        or parts.path not in {"/device", "/oauth/2.0/device"}
    ):
        raise ProviderError("provider_qr_invalid")
    return url


def validate_115_qr(url: str) -> str:
    parts = _url(url, "provider_qr_invalid")
    if (
        parts.hostname != "115.com"
        or parts.query
        or not re.fullmatch(r"/scan/dg-[A-Za-z0-9_-]{1,512}", parts.path)
    ):
        raise ProviderError("provider_qr_invalid")
    return url


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        # Resolve once and connect to a checked IP while verifying TLS for the official host.
        addresses = socket.getaddrinfo(self.host, 443, type=socket.SOCK_STREAM)
        if not addresses or any(
            not ipaddress.ip_address(item[4][0]).is_global for item in addresses
        ):
            raise ProviderError("provider_url_invalid")
        last_error = None
        for family, kind, protocol, _, address in addresses:
            raw = socket.socket(family, kind, protocol)
            raw.settimeout(5)
            try:
                raw.connect(address)
                self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
                return
            except OSError as error:
                raw.close()
                last_error = error
        raise OSError("provider_connection_failed") from last_error


def _connection(host: str):
    return _PinnedHTTPSConnection(host, timeout=5, context=ssl.create_default_context())


def _retry_after(value: str | None) -> int:
    if not value:
        return 15
    if value.isdecimal():
        digits = value.lstrip("0") or "0"
        # Any longer delay exceeds both the bridge bound and grant lifetime;
        # retain that fact without parsing an unbounded integer or retrying early.
        return max(2, int(digits)) if len(digits) <= 8 else 601
    try:
        seconds = math.ceil((parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
        return max(2, seconds)
    except (ValueError, TypeError, OverflowError):
        return 15


def validate_png(body: bytes) -> bytes:
    if not isinstance(body, bytes) or len(body) > MAX_QR_PNG or body[:8] != b"\x89PNG\r\n\x1a\n":
        raise ProviderError("provider_qr_invalid")
    offset, saw_data, saw_header = 8, False, False
    while offset + 12 <= len(body):
        size = struct.unpack(">I", body[offset : offset + 4])[0]
        kind = body[offset + 4 : offset + 8]
        end = offset + 12 + size
        if end > len(body) or kind not in {
            b"IHDR",
            b"PLTE",
            b"IDAT",
            b"IEND",
            b"tRNS",
            b"gAMA",
            b"pHYs",
            b"sRGB",
            b"cHRM",
            b"bKGD",
            b"sBIT",
        }:
            raise ProviderError("provider_qr_invalid")
        # Metadata/text chunks are not needed to show QR codes and are rejected.
        if zlib.crc32(body[offset + 4 : end - 4]) != struct.unpack(">I", body[end - 4 : end])[0]:
            raise ProviderError("provider_qr_invalid")
        if kind == b"IHDR":
            if saw_header or offset != 8 or size != 13:
                raise ProviderError("provider_qr_invalid")
            width, height = struct.unpack(">II", body[offset + 8 : offset + 16])
            if not 1 <= width <= 1024 or not 1 <= height <= 1024:
                raise ProviderError("provider_qr_invalid")
            saw_header = True
        elif kind == b"IDAT":
            saw_data = True
        elif kind == b"IEND":
            if size != 0 or not saw_header or not saw_data or end != len(body):
                raise ProviderError("provider_qr_invalid")
            return body
        offset = end
    raise ProviderError("provider_qr_invalid")


class SafeHttpClient:
    def _request(self, method, url, params, headers, read_timeout, *, png=False):
        parts = _url(url)
        if png:
            validate_baidu_qr(url)
        elif (method, parts.hostname, parts.path) not in API_ENDPOINTS or parts.query:
            raise ProviderError("provider_url_invalid")
        if read_timeout not in {15, 35} or (
            read_timeout == 35
            and (parts.hostname, parts.path) != ("qrcodeapi.115.com", "/get/status/")
        ):
            raise ProviderError("provider_url_invalid")
        headers = headers or {}
        if any(key != "Authorization" for key in headers) or (
            headers and (parts.hostname, parts.path) != ("proapi.115.com", "/open/user/info")
        ):
            raise ProviderError("provider_url_invalid")
        if any(not isinstance(v, str) or "\r" in v or "\n" in v for v in headers.values()):
            raise ProviderError("provider_response_invalid")
        body = urlencode(params or {}).encode("ascii")
        if len(body) > MAX_RESPONSE:
            raise ProviderError("provider_response_too_large")
        path = parts.path
        if method == "GET" and body:
            path += "?" + body.decode("ascii")
        request_headers = {"Accept": "image/png" if png else "application/json", **headers}
        if method == "POST":
            request_headers["Content-Type"] = "application/x-www-form-urlencoded"
        connection = None
        result_error = None
        result = None
        try:
            connection = _connection(parts.hostname)
            connection.connect()
            connection.sock.settimeout(read_timeout)
            connection.request(
                method, path, body=body if method == "POST" else None, headers=request_headers
            )
            response = connection.getresponse()
            response_headers = {key.lower(): value for key, value in response.getheaders()}
            if 300 <= response.status < 400:
                raise ProviderError("provider_redirect_rejected")
            if response.status == 429:
                raise ProviderError(
                    "provider_throttled",
                    retry_after_seconds=_retry_after(response_headers.get("retry-after")),
                )
            if response.status >= 500:
                raise ProviderError("provider_network_error", retry_after_seconds=15)
            if response.status not in {200, 400, 401, 403} or (png and response.status != 200):
                raise ProviderError("provider_response_invalid")
            result = response.read(MAX_RESPONSE + 1)
            if len(result) > MAX_RESPONSE:
                raise ProviderError("provider_response_too_large")
            if png:
                if response_headers.get("content-type", "").split(";")[0] != "image/png":
                    raise ProviderError("provider_qr_invalid")
                result = validate_png(result)
            else:
                result = json.loads(result)
                if not isinstance(result, dict):
                    raise ProviderError("provider_response_invalid")
        except ProviderError as error:
            result_error = (error.code, error.retry_after_seconds)
        except (ValueError, UnicodeError, RecursionError):
            result_error = ("provider_response_invalid", None)
        except (OSError, http.client.HTTPException):
            result_error = ("provider_network_error", 15)
        finally:
            if connection:
                connection.close()
        # Raise outside the exception handler: raw transport errors are not chained/contextualized.
        if result_error:
            raise ProviderError(result_error[0], retry_after_seconds=result_error[1])
        return result

    def request_json(self, method, url, *, params=None, headers=None, read_timeout=15):
        return self._request(method, url, params, headers, read_timeout)

    def request_png(self, url):
        return self._request("GET", url, None, None, 15, png=True)
