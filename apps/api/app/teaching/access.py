from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class TeachingVisibility:
    global_access: bool
    class_ids: frozenset[UUID] = frozenset()
    excluded_class_ids: frozenset[UUID] = frozenset()

    @classmethod
    def global_scope(cls) -> TeachingVisibility:
        return cls(global_access=True)
