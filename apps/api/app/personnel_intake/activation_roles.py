"""Least-privilege account roles derived only from authenticated intake kind."""

from app.personnel_intake.schemas import PersonKind


def activation_role_keys(kind: PersonKind) -> frozenset[str]:
    match kind:
        case "student":
            return frozenset({"student"})
        case "player":
            return frozenset({"team_member"})
        case "both":
            return frozenset({"student", "team_member"})
        case "staff":
            return frozenset({"registered_user"})
        case _:
            raise ValueError("unsupported activation kind")
