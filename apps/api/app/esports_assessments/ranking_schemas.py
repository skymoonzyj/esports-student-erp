"""Private, subject-only real-time ranking response (never a leaderboard)."""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, StrictInt, model_validator

from .config_schemas import MEMBERS, FiniteNumber, StrictModel, members_for_catalog


class AnonymousTopScore(StrictModel):
    rank: Annotated[StrictInt, Field(ge=1)]
    score: Annotated[FiniteNumber, Field(ge=0, le=100)]
    gap: Annotated[FiniteNumber, Field(ge=-100, le=100)]


class RankingItem(StrictModel):
    test_id: str
    score: Annotated[StrictInt, Field(ge=0, le=100)]
    rank: Annotated[StrictInt, Field(ge=1)]
    top_scores: Annotated[tuple[AnonymousTopScore, ...], Field(max_length=3)] = ()


class AssessmentRankingView(StrictModel):
    report_id: UUID
    calculated_at: datetime
    status: Literal["ranked", "not_eligible"]
    reason: Literal["quality_warning", "incomplete_results", "incompatible_input"] | None = None
    best_report_id: UUID | None = None
    best_completed_at: datetime | None = None
    overall_score: Annotated[FiniteNumber, Field(ge=0, le=100)] | None = None
    overall_rank: Annotated[StrictInt, Field(ge=1)] | None = None
    participant_count: Annotated[StrictInt, Field(ge=0)] = 0
    items: tuple[RankingItem, ...] = ()
    top_scores: Annotated[tuple[AnonymousTopScore, ...], Field(max_length=3)] = ()

    @model_validator(mode="after")
    def check_state(self) -> Self:
        if self.calculated_at.utcoffset() is None or (
            self.best_completed_at is not None and self.best_completed_at.utcoffset() is None
        ):
            raise ValueError("ranking times must be timezone-aware")
        best = (self.best_report_id, self.best_completed_at, self.overall_score, self.overall_rank)
        if self.status == "ranked":
            if self.reason is not None or any(value is None for value in best):
                raise ValueError("ranked response requires complete best-round values")
            frozen_members = (
                *MEMBERS.values(),
                members_for_catalog("pc-fps", "pc-fps-v4"),
                members_for_catalog("pc-moba", "pc-moba-v4"),
            )
            if self.participant_count < 1 or tuple(item.test_id for item in self.items) not in (
                frozen_members
            ):
                raise ValueError("ranked response requires a complete round and participants")
            if len({item.test_id for item in self.items}) != len(self.items):
                raise ValueError("ranked items must be unique")
            if any(
                rank > self.participant_count
                for rank in (self.overall_rank or 0, *(item.rank for item in self.items))
            ):
                raise ValueError("rank cannot exceed participant count")
        elif (
            self.reason is None
            or any(value is not None for value in best)
            or self.items
            or self.participant_count
            or self.top_scores
        ):
            raise ValueError("ineligible response cannot contain rankings")
        return self
