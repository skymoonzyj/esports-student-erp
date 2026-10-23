from __future__ import annotations

from sqlalchemy.orm.util import AliasedClass
from sqlalchemy.sql.elements import ColumnElement

from app.housing.models import HousingStay


def effective_stay_clause(
    stay: type[HousingStay] | AliasedClass[HousingStay] = HousingStay,
) -> ColumnElement[bool]:
    """Exclude void facts without changing the caller's status or visibility scope."""
    return stay.voided_at.is_(None)
