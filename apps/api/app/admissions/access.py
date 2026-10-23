from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AdmissionsVisibility:
    can_read: bool
    can_manage: bool
    can_read_amounts: bool
