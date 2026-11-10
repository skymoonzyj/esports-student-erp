"""Comparison keys for one saved item, independent of the rest of its round."""

import json
from dataclasses import dataclass

from pydantic import TypeAdapter, ValidationError

from app.talent_tests.catalog import get_rules
from app.talent_tests.errors import UnknownCatalogVersion
from app.talent_tests.models import TalentTestResult, TalentTestSession
from app.talent_tests.schemas import DynamicVisionRule, DynamicVisionRuleV2, TestRule

_RULE: TypeAdapter[TestRule | DynamicVisionRule | DynamicVisionRuleV2] = TypeAdapter(
    TestRule | DynamicVisionRule | DynamicVisionRuleV2
)
_INPUTS = {"mouse", "touch", "pen", "mixed"}


@dataclass(frozen=True)
class ItemGroupKey:
    test_id: str
    catalog_version: str
    scoring_version: str
    rule_json: str
    engine_version: str
    locale: str
    input_kind: str


def missing_item_metadata(session: TalentTestSession, result: TalentTestResult) -> bool:
    """Do not invent missing legacy metadata from today's catalog or device."""
    snapshot = session.rules_snapshot
    tests = snapshot.get("tests") if isinstance(snapshot, dict) else None
    rule = tests.get(result.test_id) if isinstance(tests, dict) else None
    normalized = result.normalized_result
    device = result.device_snapshot
    return (
        not isinstance(rule, dict)
        or any(key not in rule for key in ("engine_version", "unit", "source_label"))
        or not isinstance(snapshot, dict)
        or any(key not in snapshot for key in ("catalog_version", "scoring_version"))
        or not isinstance(normalized, dict)
        or "scoring_version" not in normalized
        or not isinstance(device, dict)
        or "input_kind" not in device
    )


def build_item_group(session: TalentTestSession, result: TalentTestResult) -> ItemGroupKey | None:
    if missing_item_metadata(session, result):
        return None
    snapshot = session.rules_snapshot
    try:
        known = get_rules(session.catalog_version)
        rule = _RULE.validate_python(snapshot["tests"][result.test_id])
    except (ValidationError, UnknownCatalogVersion):
        return None
    kind = result.device_snapshot["input_kind"]
    if (
        result.test_id not in known.tests
        or snapshot["catalog_version"] != session.catalog_version
        or snapshot["scoring_version"] != session.scoring_version
        or session.scoring_version != known.scoring_version
        or result.normalized_result["scoring_version"] != session.scoring_version
        or rule.engine_version != result.engine_version
        or not isinstance(kind, str)
        or kind not in _INPUTS
        or result.locale != "zh-CN"
    ):
        return None
    return ItemGroupKey(
        test_id=result.test_id,
        catalog_version=session.catalog_version,
        scoring_version=session.scoring_version,
        rule_json=json.dumps(snapshot["tests"][result.test_id], sort_keys=True),
        engine_version=result.engine_version,
        locale=result.locale,
        input_kind=kind,
    )
