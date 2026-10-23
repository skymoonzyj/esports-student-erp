from __future__ import annotations

import base64
import binascii
import re
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError

from app.revenue_share.errors import RevenueShareCursorInvalid


class LedgerCursor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["ledger"] = "ledger"
    occurred_at: AwareDatetime
    entry_id: UUID


class ReconcileCursor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["reconcile"] = "reconcile"
    cutoff: AwareDatetime
    paid_at: AwareDatetime
    payment_id: UUID


def encode_cursor(value: LedgerCursor | ReconcileCursor) -> str:
    return base64.urlsafe_b64encode(value.model_dump_json().encode()).decode().rstrip("=")


def decode_cursor[Cursor: (LedgerCursor, ReconcileCursor)](
    value: str, model: type[Cursor]
) -> Cursor:
    # These cursors only control traversal; authorization always filters the query
    # by the authenticated person. Never accept arbitrary SQL/order expressions.
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,768}", value):
        raise RevenueShareCursorInvalid("invalid pagination cursor")
    try:
        payload = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        return model.model_validate_json(payload)
    except (binascii.Error, ValueError, ValidationError):
        raise RevenueShareCursorInvalid("invalid pagination cursor") from None
