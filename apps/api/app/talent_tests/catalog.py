from __future__ import annotations

from app.talent_tests.errors import UnknownCatalogVersion, UnknownTestId
from app.talent_tests.schemas import (
    DynamicVisionLevel,
    DynamicVisionRule,
    RuleSnapshot,
    TestId,
    TestRule,
)

LEGACY_TEST_IDS: tuple[TestId, ...] = (
    "reactiontime",
    "aim",
    "sequence",
    "number-memory",
    "verbal-memory",
    "chimp",
    "memory",
)
TEST_IDS: tuple[TestId, ...] = (*LEGACY_TEST_IDS, "dynamic-vision")
CURRENT_CATALOG_VERSION = "talent-training-v2"

_RULES_V1 = RuleSnapshot(
    catalog_version="talent-training-v1",
    scoring_version="talent-score-v1",
    report_version="talent-report-v1",
    test_ids=LEGACY_TEST_IDS,
    tests={
        "reactiontime": TestRule(
            engine_version="reactiontime-clone-v1",
            anchors=(500, 275, 225, 175, 150, 100),
            source_label="训练参考目标",
            unit="ms",
        ),
        "aim": TestRule(
            engine_version="aim-clone-v1",
            anchors=(1000, 550, 450, 350, 300, 250),
            source_label="训练参考目标",
            unit="ms",
        ),
        "sequence": TestRule(
            engine_version="sequence-clone-v1",
            anchors=(0, 8, 10, 18, 25, 35),
            source_label="训练参考目标",
            unit="level",
        ),
        "number-memory": TestRule(
            engine_version="number-memory-clone-v1",
            anchors=(0, 7, 9, 13, 16, 20),
            source_label="训练参考目标",
            unit="digits",
        ),
        "verbal-memory": TestRule(
            engine_version="verbal-memory-clone-v1",
            anchors=(0, 20, 50, 100, 200, 300),
            source_label="内部试行训练目标",
            unit="count",
        ),
        "chimp": TestRule(
            engine_version="chimp-clone-v1",
            anchors=(0, 9, 11, 15, 20, 25),
            source_label="训练参考目标",
            unit="count",
        ),
        "memory": TestRule(
            engine_version="memory-clone-v1",
            anchors=(0, 6, 10, 18, 25, 35),
            source_label="内部试行训练目标",
            unit="level",
        ),
    },
)

_V2_TESTS = {test_id: rule.model_copy(deep=True) for test_id, rule in _RULES_V1.tests.items()}
_V2_TESTS["dynamic-vision"] = DynamicVisionRule(
    kind="dynamic_vision",
    engine_version="dynamic-vision-v1",
    source_label="内部试行训练规则",
    unit="count",
    base_points_per_correct=10,
    levels=(
        DynamicVisionLevel(level=1, digit_count=3, nominal_display_ms=100, coefficient_tenths=12),
        DynamicVisionLevel(level=2, digit_count=4, nominal_display_ms=100, coefficient_tenths=14),
        DynamicVisionLevel(level=3, digit_count=5, nominal_display_ms=100, coefficient_tenths=16),
        DynamicVisionLevel(level=4, digit_count=5, nominal_display_ms=36, coefficient_tenths=18),
        DynamicVisionLevel(level=5, digit_count=6, nominal_display_ms=36, coefficient_tenths=20),
    ),
)
_RULES_V2 = RuleSnapshot(
    catalog_version=CURRENT_CATALOG_VERSION,
    scoring_version="talent-score-v2",
    report_version="talent-report-v2",
    test_ids=TEST_IDS,
    tests=_V2_TESTS,
)
_RULES_BY_VERSION = {
    _RULES_V1.catalog_version: _RULES_V1,
    _RULES_V2.catalog_version: _RULES_V2,
}


def get_rules(catalog_version: str) -> RuleSnapshot:
    try:
        rules = _RULES_BY_VERSION[catalog_version]
    except KeyError:
        raise UnknownCatalogVersion(catalog_version)
    return rules.model_copy(deep=True)


def get_test_rule(test_id: str, rules: RuleSnapshot) -> TestRule | DynamicVisionRule:
    get_rules(rules.catalog_version)
    if test_id not in rules.test_ids or test_id not in rules.tests:
        raise UnknownTestId(test_id)
    return rules.tests[test_id]


def get_dynamic_vision_level(rule: DynamicVisionRule, level: int) -> DynamicVisionLevel:
    if isinstance(level, bool):
        raise TypeError("dynamic-vision level must be an integer from 1 to 5")
    for candidate in rule.levels:
        if candidate.level == level:
            return candidate
    raise ValueError("dynamic-vision level must be an integer from 1 to 5")
