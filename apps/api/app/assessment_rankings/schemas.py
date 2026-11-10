"""Public ranking DTOs; never expose candidate identities or sorting keys.

Metric catalogs and exact ranking comparisons belong to the supplying service.
These schemas validate presentation data, not rounded-score ranking semantics.
"""

import math
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    StrictBool,
    StringConstraints,
    model_validator,
)

ItemRankingReason = Literal[
    "quality_warning",
    "incomplete_result",
    "incompatible_input",
    "missing_comparison_metadata",
    "subject_inactive",
    "practice_only",
    "mode_unverified",
]

ITEM_RANKING_REASON_LABELS: dict[str, str] = {
    "quality_warning": "本项成绩存在质量提示，暂不参与排名。",
    "incomplete_result": "本项尚未有效完成，暂不参与排名。",
    "incompatible_input": "输入方式或测试条件不兼容，无法进行排名比较。",
    "missing_comparison_metadata": "缺少必要的比较条件，暂不参与排名。",
    "subject_inactive": "人员已删除或账号均已停用，暂不参与排名。",
    "practice_only": "本次为练习成绩，不参与正式排名。",
    "mode_unverified": "此模式尚未完成排名验证，暂不参与排名。",
}


def _finite_number(value: object) -> int | float | Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        # Pydantic validators intentionally use ValueError, not an escaping TypeError.
        raise ValueError("ranking values must be JSON numbers")  # noqa: TRY004
    try:
        serialized = float(value)
        finite = math.isfinite(serialized)
    except (OverflowError, ValueError):
        finite = False
    if not finite:
        raise ValueError("ranking values must be finite JSON numbers")
    if isinstance(value, Decimal) and value != 0 and serialized == 0:
        raise ValueError("ranking values must not underflow during JSON serialization")
    return value


def _json_number(value: float | Decimal) -> float:
    # Pydantic normally serializes Decimal as a string; the wire contract is numeric.
    return float(value) if isinstance(value, Decimal) else value


def _iso_datetime(value: object) -> datetime | str:
    if isinstance(value, datetime) or (isinstance(value, str) and "T" in value):
        return value
    raise ValueError("calculated_at requires an aware datetime or ISO datetime string")


JsonNumber = Annotated[
    int | float | Decimal,
    BeforeValidator(_finite_number),
    PlainSerializer(_json_number, return_type=int | float, when_used="json"),
]
PositiveInteger = Annotated[int, Field(strict=True, ge=1, le=9_007_199_254_740_991)]
NonnegativeInteger = Annotated[int, Field(strict=True, ge=0, le=9_007_199_254_740_991)]
MetricKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]
NonblankText = Annotated[str, StringConstraints(pattern=r"\S")]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ItemRankingMetric(_StrictModel):
    key: MetricKey
    label: NonblankText
    unit: str
    better: Literal["higher", "lower"]
    decimals: NonnegativeInteger


class ItemRankingStanding(_StrictModel):
    rank: PositiveInteger
    values: dict[str, JsonNumber]


class ItemRankingGap(_StrictModel):
    key: MetricKey
    amount: JsonNumber
    state: Literal["ahead", "behind", "equal"]

    @model_validator(mode="after")
    def validate_amount(self) -> "ItemRankingGap":
        if self.amount < 0 or (self.state == "equal") != (self.amount == 0):
            raise ValueError("equal gaps require zero; ahead/behind gaps require positive amounts")
        return self


class ItemRankingTopRow(ItemRankingStanding):
    is_self: StrictBool
    tie_count: PositiveInteger
    gaps: list[ItemRankingGap]


class ItemRankingView(_StrictModel):
    status: Literal["ranked", "not_eligible"]
    reason: ItemRankingReason | None
    group_label: NonblankText
    calculated_at: Annotated[AwareDatetime, BeforeValidator(_iso_datetime)]
    participant_count: NonnegativeInteger
    metrics: Annotated[list[ItemRankingMetric], Field(min_length=1)]
    current: ItemRankingStanding | None
    best: ItemRankingStanding | None
    top_three: Annotated[list[ItemRankingTopRow], Field(max_length=3)]

    @model_validator(mode="after")
    def validate_ranking_state(self) -> "ItemRankingView":
        keys = {metric.key for metric in self.metrics}
        if len(keys) != len(self.metrics):
            raise ValueError("metric keys must be unique")
        if self.status == "not_eligible":
            if (
                self.reason is None
                or self.participant_count != 0
                or self.current is not None
                or self.best is not None
                or self.top_three
            ):
                raise ValueError("ineligible responses must contain a reason and no ranking values")
            return self
        if (
            self.reason is not None
            or self.participant_count == 0
            or self.current is None
            or self.best is None
            or not self.top_three
            or len(self.top_three) > self.participant_count
        ):
            raise ValueError("ranked responses require a complete ranking state")
        for standing in [self.current, self.best, *self.top_three]:
            if standing.rank > self.participant_count or set(standing.values) != keys:
                raise ValueError("standing rank or metric keys do not match the declared ranking")
        if sum(row.is_self for row in self.top_three) > 1:
            raise ValueError("only one anonymous row may represent self")
        previous: ItemRankingTopRow | None = None
        cohort_size = 0
        for row in self.top_three:
            if row.rank + row.tie_count - 1 > self.participant_count:
                raise ValueError("tie cohort exceeds participant count")
            if previous is None:
                if row.rank != 1:
                    raise ValueError("top rows must begin at rank one")
                cohort_size = 1
            elif row.rank == previous.rank:
                cohort_size += 1
                if row.tie_count != previous.tie_count or cohort_size > row.tie_count:
                    raise ValueError("tied rows must share the full cohort size")
            else:
                if (
                    cohort_size != previous.tie_count
                    or row.rank != previous.rank + previous.tie_count
                ):
                    raise ValueError("top rows must use monotone competition ranks")
                cohort_size = 1
            gap_keys = [gap.key for gap in row.gaps]
            if len(gap_keys) != len(keys) or set(gap_keys) != keys:
                raise ValueError("gaps must match each declared metric exactly once")
            previous = row
        return self
