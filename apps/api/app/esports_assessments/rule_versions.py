"""Explicit immutable mappings between catalogs, raw contracts and result rules."""

from dataclasses import dataclass

from .config_schemas import KINDS, MEMBERS


@dataclass(frozen=True, slots=True)
class RuleIdentity:
    raw_kind: str
    scoring_version: str
    report_version: str


def registered_rule(suite_id: str, test_id: str, catalog_version: str) -> RuleIdentity:
    """Return the one rule identity registered for an exact catalog member."""

    if suite_id not in MEMBERS or test_id not in MEMBERS[suite_id]:
        raise ValueError("unknown suite or task")
    if catalog_version not in {
        f"{suite_id}-v1",
        f"{suite_id}-v2",
        f"{suite_id}-v3",
    }:
        raise ValueError("unknown catalog version")

    raw_kind = KINDS[test_id]
    scoring_version = report_version = f"{suite_id}-v1"
    if catalog_version == f"{suite_id}-v3":
        if test_id == "move-fire":
            raw_kind = "move_fire_v3"
        elif test_id == "dodge":
            raw_kind = "dodge_choice"
        if test_id in {"switching", "move-fire", "dodge"}:
            scoring_version = report_version = f"{suite_id}-v2"
    return RuleIdentity(raw_kind, scoring_version, report_version)
