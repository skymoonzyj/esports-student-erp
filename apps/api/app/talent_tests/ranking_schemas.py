from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from app.talent_tests.schemas import StrictModel

RankingReason = Literal["quality_warning", "incomplete_results", "incompatible_input"]


class RankingItem(StrictModel):
    test_id: str
    score: Annotated[int, Field(ge=0, le=100)]
    rank: Annotated[int, Field(ge=1)]


class RankingView(StrictModel):
    report_id: UUID
    calculated_at: AwareDatetime
    status: Literal["ranked", "not_eligible"]
    reason: RankingReason | None = None
    best_report_id: UUID | None = None
    best_completed_at: AwareDatetime | None = None
    overall_score: Annotated[float, Field(ge=0, le=100)] | None = None
    overall_rank: Annotated[int, Field(ge=1)] | None = None
    participant_count: Annotated[int, Field(ge=0)] = 0
    items: list[RankingItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_ranking_state(self) -> "RankingView":
        best = (self.best_report_id, self.best_completed_at, self.overall_score, self.overall_rank)
        if self.status == "not_eligible":
            if (
                self.reason is None
                or any(value is not None for value in best)
                or self.items
                or self.participant_count
            ):
                raise ValueError("ineligible rankings must not contain ranking values")
        elif (
            self.reason is not None
            or any(value is None for value in best)
            or not self.items
            or not self.participant_count
            or self.overall_rank is None
            or self.overall_rank > self.participant_count
            or any(item.rank > self.participant_count for item in self.items)
            or len({item.test_id for item in self.items}) != len(self.items)
        ):
            raise ValueError("ranked responses require a complete best round and bounded ranks")
        return self
