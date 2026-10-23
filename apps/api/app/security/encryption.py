from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
from typing import Literal, TypedDict, cast

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_URLSAFE_BASE64 = re.compile(r"^[A-Za-z0-9_-]+={0,2}$")
_PAYLOAD_FIELDS = {
    "v",
    "wrapped_key_nonce",
    "wrapped_key",
    "data_nonce",
    "ciphertext",
}


class EncryptedFieldError(ValueError):
    """Raised when an encrypted database field cannot be authenticated."""


class EnvelopePayload(TypedDict):
    v: Literal[1]
    wrapped_key_nonce: str
    wrapped_key: str
    data_nonce: str
    ciphertext: str


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii")


def _decode(value: object) -> bytes:
    if not isinstance(value, str) or not _URLSAFE_BASE64.fullmatch(value):
        raise ValueError("invalid encoded value")
    return base64.b64decode(value, altchars=b"-_", validate=True)


def _aad(field_name: str, purpose: str) -> bytes:
    if not field_name:
        raise ValueError("field_name is required")
    return f"esports-erp:v1:{field_name}:{purpose}".encode()


class EnvelopeCipher:
    def __init__(self, master_key: bytes) -> None:
        if len(master_key) != 32:
            raise ValueError("master key must be 32 bytes")
        self._master_cipher = AESGCM(master_key)

    def encrypt(self, plaintext: str, *, field_name: str) -> str:
        data_key = AESGCM.generate_key(bit_length=256)
        wrapped_key_nonce = os.urandom(12)
        data_nonce = os.urandom(12)
        wrapped_key = self._master_cipher.encrypt(
            wrapped_key_nonce,
            data_key,
            _aad(field_name, "wrapped-key"),
        )
        ciphertext = AESGCM(data_key).encrypt(
            data_nonce,
            plaintext.encode("utf-8"),
            _aad(field_name, "data"),
        )
        payload: EnvelopePayload = {
            "v": 1,
            "wrapped_key_nonce": _encode(wrapped_key_nonce),
            "wrapped_key": _encode(wrapped_key),
            "data_nonce": _encode(data_nonce),
            "ciphertext": _encode(ciphertext),
        }
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)

    def decrypt(self, payload: str, *, field_name: str) -> str:
        try:
            raw = json.loads(payload)
            if not isinstance(raw, dict) or set(raw) != _PAYLOAD_FIELDS or raw.get("v") != 1:
                raise ValueError("invalid envelope")
            envelope = cast(EnvelopePayload, raw)
            wrapped_key_nonce = _decode(envelope["wrapped_key_nonce"])
            data_nonce = _decode(envelope["data_nonce"])
            if len(wrapped_key_nonce) != 12 or len(data_nonce) != 12:
                raise ValueError("invalid nonce")
            data_key = self._master_cipher.decrypt(
                wrapped_key_nonce,
                _decode(envelope["wrapped_key"]),
                _aad(field_name, "wrapped-key"),
            )
            if len(data_key) != 32:
                raise ValueError("invalid data key")
            plaintext = AESGCM(data_key).decrypt(
                data_nonce,
                _decode(envelope["ciphertext"]),
                _aad(field_name, "data"),
            )
            return plaintext.decode("utf-8")
        except (InvalidTag, ValueError, TypeError, KeyError, binascii.Error, UnicodeDecodeError):
            raise EncryptedFieldError("unable to decrypt encrypted field") from None


class LookupDigest:
    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("lookup key must be 32 bytes")
        self._key = key

    def digest(self, normalized: str) -> str:
        return hmac.new(self._key, normalized.encode("utf-8"), hashlib.sha256).hexdigest()

    def digest_domain(self, domain: str, normalized: str) -> str:
        if not domain or "\x00" in domain:
            raise ValueError("lookup digest domain must be non-empty and contain no NUL")
        message = domain.encode("utf-8") + b"\x00" + normalized.encode("utf-8")
        return hmac.new(self._key, message, hashlib.sha256).hexdigest()
