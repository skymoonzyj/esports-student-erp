"""Only fixed builtin import protocols. No application parameters or service URL input."""

from datetime import datetime
from typing import Literal

from .baidu import BaiduProvider
from .drive115 import Drive115Provider
from .provider_types import AccountIdentity, ImportedTokenGrant, ProviderError

AuthProvider = Literal["baidu", "115"]


def _provider(provider: AuthProvider, client=None):
    if provider == "baidu":
        return BaiduProvider(client)
    if provider == "115":
        return Drive115Provider(client)
    raise ProviderError("provider_response_invalid")


def get_import_identity(
    provider: AuthProvider, access_token: str, *, client=None
) -> AccountIdentity:
    return _provider(provider, client).get_import_identity(access_token)


def refresh_import(
    provider: AuthProvider, refresh_token: str, *, now: datetime, client=None
) -> ImportedTokenGrant:
    return _provider(provider, client).refresh_import(refresh_token, now=now)
