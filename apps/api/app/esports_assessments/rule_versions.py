"""Explicit immutable mappings between catalogs, raw contracts and result rules."""

from dataclasses import dataclass

from .config_schemas import KINDS, members_for_catalog


@dataclass(frozen=True, slots=True)
class RuleIdentity:
    raw_kind: str
    scoring_version: str
    report_version: str


def registered_rule(suite_id: str, test_id: str, catalog_version: str) -> RuleIdentity:
    """Return the one rule identity registered for an exact catalog member."""

    if test_id not in members_for_catalog(suite_id, catalog_version):
        raise ValueError("unknown suite or task")
    if catalog_version == "pc-moba-v4":
        return RuleIdentity("lane_last_hit", "pc-moba-v4", "pc-moba-v4")
    if catalog_version == "pc-moba-v5":
        return RuleIdentity("lane_last_hit_towers", "pc-moba-v5", "pc-moba-v5")
    if catalog_version in ("pc-fps-v4", "pc-fps-v5"):
        return RuleIdentity(KINDS[test_id], "pc-fps-v4", "pc-fps-v4")

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
