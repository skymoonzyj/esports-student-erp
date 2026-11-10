"""Boundary tests: reject privacy leaks, fabricated ranks, and non-JSON numbers."""

import copy
import json
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from app.assessment_rankings.schemas import ITEM_RANKING_REASON_LABELS, ItemRankingView


def ranked_payload() -> dict[str, Any]:
    return {
        "status": "ranked",
        "reason": None,
        "group_label": "网页反应速度 · v1",
        "calculated_at": "2026-10-31T09:00:00+08:00",
        "participant_count": 4,
        "metrics": [
            {"key": "score", "label": "评分", "unit": "分", "better": "higher", "decimals": 1}
        ],
        "current": {"rank": 4, "values": {"score": 70.5}},
        "best": {"rank": 2, "values": {"score": 80}},
        "top_three": [
            {
                "rank": 1,
                "values": {"score": 90},
                "is_self": False,
                "tie_count": 1,
                "gaps": [{"key": "score", "amount": 19.5, "state": "behind"}],
            },
            {
                "rank": 2,
                "values": {"score": 80},
                "is_self": True,
                "tie_count": 2,
                "gaps": [{"key": "score", "amount": 9.5, "state": "behind"}],
            },
            {
                "rank": 2,
                "values": {"score": 80},
                "is_self": False,
                "tie_count": 2,
                "gaps": [{"key": "score", "amount": 9.5, "state": "behind"}],
            },
        ],
    }


def ineligible_payload(reason: str = "quality_warning") -> dict[str, Any]:
    return {
        **ranked_payload(),
        "status": "not_eligible",
        "reason": reason,
        "participant_count": 0,
        "current": None,
        "best": None,
        "top_three": [],
    }


def test_ranked_contract_preserves_anonymous_competition_ranks() -> None:
    view = ItemRankingView.model_validate(ranked_payload())
    assert view.participant_count == 4
    assert [row.rank for row in view.top_three] == [1, 2, 2]
    assert set(view.model_dump()) == {
        "status",
        "reason",
        "group_label",
        "calculated_at",
        "participant_count",
        "metrics",
        "current",
        "best",
        "top_three",
    }
    assert set(view.top_three[0].model_dump()) == {"rank", "values", "is_self", "tie_count", "gaps"}


def test_decimal_values_and_gaps_serialize_as_json_numbers() -> None:
    payload = ranked_payload()
    payload["current"]["values"]["score"] = Decimal("70.5")
    payload["top_three"][0]["gaps"][0]["amount"] = Decimal("19.5")
    decoded = json.loads(ItemRankingView.model_validate(payload).model_dump_json())
    assert decoded["current"]["values"]["score"] == 70.5
    assert decoded["top_three"][0]["gaps"][0]["amount"] == 19.5
    assert isinstance(decoded["current"]["values"]["score"], float)


@pytest.mark.parametrize(
    "number",
    [
        float("nan"),
        float("inf"),
        -float("inf"),
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("1e9999"),
        True,
        "70.5",
        None,
    ],
)
@pytest.mark.parametrize("location", ["current", "best", "top", "gap"])
def test_rejects_non_finite_or_coerced_numbers(number: Any, location: str) -> None:
    payload = ranked_payload()
    if location in {"current", "best"}:
        payload[location]["values"]["score"] = number
    elif location == "top":
        payload["top_three"][0]["values"]["score"] = number
    else:
        payload["top_three"][0]["gaps"][0]["amount"] = number
    with pytest.raises(ValidationError):
        ItemRankingView.model_validate(payload)


@pytest.mark.parametrize("location", ["root", "metric", "current", "best", "top", "gap"])
@pytest.mark.parametrize(
    "field", ["person_id", "account_id", "report_id", "device_address", "sort_key"]
)
def test_rejects_unapproved_fields_at_every_object_boundary(location: str, field: str) -> None:
    payload = ranked_payload()
    targets = {
        "root": payload,
        "metric": payload["metrics"][0],
        "current": payload["current"],
        "best": payload["best"],
        "top": payload["top_three"][0],
        "gap": payload["top_three"][0]["gaps"][0],
    }
    targets[location][field] = "private"
    with pytest.raises(ValidationError):
        ItemRankingView.model_validate(payload)


@pytest.mark.parametrize(
    "override",
    [
        {"participant_count": 0},
        {"participant_count": -1},
        {"participant_count": True},
        {"participant_count": "4"},
        {"reason": "quality_warning"},
        {"current": None},
        {"best": None},
        {"top_three": []},
        {"metrics": []},
        {"group_label": " "},
        {"calculated_at": "2026-10-31T09:00:00"},
    ],
)
def test_ranked_requires_complete_state(override: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ItemRankingView.model_validate({**ranked_payload(), **override})


def test_ranked_requires_explicit_participant_count() -> None:
    payload = ranked_payload()
    del payload["participant_count"]
    with pytest.raises(ValidationError):
        ItemRankingView.model_validate(payload)


@pytest.mark.parametrize("location", ["current", "best", "top"])
@pytest.mark.parametrize("rank", [0, -1, 5, True, "1", 1.5])
def test_ranks_are_strict_positive_integers_bounded_by_population(location: str, rank: Any) -> None:
    payload = ranked_payload()
    target = payload["top_three"][0] if location == "top" else payload[location]
    target["rank"] = rank
    with pytest.raises(ValidationError):
        ItemRankingView.model_validate(payload)


@pytest.mark.parametrize("keys", [{}, {"score": 70, "person_id": 123}, {"other": 70}])
@pytest.mark.parametrize("location", ["current", "best", "top"])
def test_values_match_exactly_declared_metrics(keys: dict[str, Any], location: str) -> None:
    payload = ranked_payload()
    target = payload["top_three"][0] if location == "top" else payload[location]
    target["values"] = keys
    with pytest.raises(ValidationError):
        ItemRankingView.model_validate(payload)


@pytest.mark.parametrize("key", ["", "score-total", "person.id", "__proto__", "Score", " score"])
def test_rejects_unsafe_metric_keys(key: str) -> None:
    payload = ranked_payload()
    payload["metrics"][0]["key"] = key
    with pytest.raises(ValidationError):
        ItemRankingView.model_validate(payload)


@pytest.mark.parametrize(
    "change",
    [
        "duplicate_metric",
        "missing_gap",
        "extra_gap",
        "unknown_gap",
        "negative_gap",
        "equal_positive",
        "behind_zero",
        "ahead_zero",
        "four_rows",
        "duplicate_self",
        "unordered",
        "bad_competition",
        "too_many_ties",
        "inconsistent_ties",
    ],
)
def test_rejects_inconsistent_metrics_gaps_and_top_rows(change: str) -> None:
    payload = ranked_payload()
    row = payload["top_three"][0]
    gap = row["gaps"][0]
    if change == "duplicate_metric":
        payload["metrics"] *= 2
    elif change == "missing_gap":
        row["gaps"] = []
    elif change == "extra_gap":
        row["gaps"] *= 2
    elif change == "unknown_gap":
        gap["key"] = "latency"
    elif change == "negative_gap":
        gap["amount"] = -1
    elif change == "equal_positive":
        gap["state"] = "equal"
    elif change in {"behind_zero", "ahead_zero"}:
        gap.update(amount=0, state=change.removesuffix("_zero"))
    elif change == "four_rows":
        payload["top_three"].append(copy.deepcopy(row))
    elif change == "duplicate_self":
        row["is_self"] = True
    elif change == "unordered":
        payload["top_three"].reverse()
    elif change == "bad_competition":
        payload["top_three"][1]["rank"] = 3
    elif change == "too_many_ties":
        row["tie_count"] = 5
    else:
        payload["top_three"][2]["tie_count"] = 1
    with pytest.raises(ValidationError):
        ItemRankingView.model_validate(payload)


def test_ties_can_extend_beyond_three_visible_rows() -> None:
    payload = ranked_payload()
    for row in payload["top_three"]:
        row.update(rank=1, tie_count=4, values={"score": 80})
    payload["best"]["rank"] = 1
    assert [row.tie_count for row in ItemRankingView.model_validate(payload).top_three] == [4, 4, 4]


def test_accepts_competition_ranks_one_one_three_and_equal_or_ahead_gaps() -> None:
    payload = ranked_payload()
    for row, rank, count in zip(payload["top_three"], [1, 1, 3], [2, 2, 1], strict=True):
        row.update(rank=rank, tie_count=count)
    payload["top_three"][0]["gaps"][0].update(amount=0, state="equal")
    payload["top_three"][1]["gaps"][0].update(amount=1.5, state="ahead")
    assert [row.rank for row in ItemRankingView.model_validate(payload).top_three] == [1, 1, 3]


@pytest.mark.parametrize(
    "reason",
    [
        "quality_warning",
        "incomplete_result",
        "incompatible_input",
        "missing_comparison_metadata",
        "subject_inactive",
        "practice_only",
        "mode_unverified",
    ],
)
def test_each_ineligible_reason_has_an_explanatory_chinese_label(reason: str) -> None:
    view = ItemRankingView.model_validate(ineligible_payload(reason))
    assert view.reason == reason
    assert any(
        "\u4e00" <= character <= "\u9fff" for character in ITEM_RANKING_REASON_LABELS[reason]
    )


@pytest.mark.parametrize(
    "override",
    [
        {"reason": None},
        {"reason": "incomplete_results"},
        {"reason": "unknown"},
        {"participant_count": 1},
        {"current": {"rank": 1, "values": {"score": 80}}},
        {"best": {"rank": 1, "values": {"score": 80}}},
        {"top_three": ranked_payload()["top_three"]},
    ],
)
def test_ineligible_never_contains_ranking_values(override: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ItemRankingView.model_validate({**ineligible_payload(), **override})


def test_generic_lower_is_better_metric_is_not_limited_to_talent_score_range() -> None:
    payload = ranked_payload()
    payload["metrics"] = [
        {"key": "latency_ms", "label": "反应时间", "unit": "毫秒", "better": "lower", "decimals": 2}
    ]
    for standing in [payload["current"], payload["best"], *payload["top_three"]]:
        standing["values"] = {"latency_ms": 234.56}
    for row in payload["top_three"]:
        row["gaps"] = [{"key": "latency_ms", "amount": 0, "state": "equal"}]
    assert ItemRankingView.model_validate(payload).metrics[0].better == "lower"


@pytest.mark.parametrize(
    "field,value",
    [
        ("key", "score\n"),
        ("label", " "),
        ("unit", 123),
        ("better", "descending"),
        ("decimals", -1),
        ("decimals", True),
        ("decimals", 1.5),
    ],
)
def test_metric_metadata_is_strict(field: str, value: Any) -> None:
    payload = ranked_payload()
    payload["metrics"][0][field] = value
    with pytest.raises(ValidationError):
        ItemRankingView.model_validate(payload)


@pytest.mark.parametrize(
    "field,value", [("is_self", 1), ("is_self", "false"), ("tie_count", 0), ("tie_count", True)]
)
def test_top_row_flags_and_counts_are_not_coerced(field: str, value: Any) -> None:
    payload = ranked_payload()
    payload["top_three"][0][field] = value
    with pytest.raises(ValidationError):
        ItemRankingView.model_validate(payload)


def test_rejects_decimal_gap_that_would_underflow_into_a_false_equal_gap() -> None:
    payload = ranked_payload()
    payload["top_three"][0]["gaps"][0]["amount"] = Decimal("1e-9999")
    with pytest.raises(ValidationError):
        ItemRankingView.model_validate(payload)


@pytest.mark.parametrize("timestamp", [1793418000, 1793418000.0, "1793418000"])
def test_calculation_time_is_not_coerced_from_untyped_epoch_values(timestamp: Any) -> None:
    with pytest.raises(ValidationError):
        ItemRankingView.model_validate({**ranked_payload(), "calculated_at": timestamp})
