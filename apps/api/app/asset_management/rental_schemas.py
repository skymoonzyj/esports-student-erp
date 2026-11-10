from datetime import date, datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.asset_management.rental_dates import RentalPeriod

OwnershipType = Literal["owned", "rented"]
OwnershipFilter = Literal["owned", "rented", "unconfirmed"]


class RentalFacts(BaseModel):
    ownership_type: OwnershipType | None = None
    acquisition_method: Literal["purchase", "other"] | None = None
    acquisition_channel: Literal["online", "offline"] | None = None
    acquisition_note: str | None = Field(default=None, max_length=500)
    rental_started_on: date | None = None
    rental_expires_on: date | None = None

    @field_validator("rental_started_on", "rental_expires_on", mode="before")
    @classmethod
    def date_only(cls, value: object) -> object:
        if isinstance(value, str):
            if len(value) != 10 or value[4:5] != "-" or value[7:8] != "-":
                raise ValueError("lease date must be YYYY-MM-DD")
        elif value is not None and (not isinstance(value, date) or isinstance(value, datetime)):
            raise ValueError("lease date must be a date")
        return value


def validate_rental_facts(facts: RentalFacts) -> None:
    acquisition = (facts.acquisition_method, facts.acquisition_channel, facts.acquisition_note)
    lease = (facts.rental_started_on, facts.rental_expires_on)
    if facts.ownership_type == "rented":
        if any(value is not None for value in acquisition) or any(value is None for value in lease):
            raise ValueError("rented assets require lease dates and no acquisition fields")
        assert facts.rental_started_on is not None and facts.rental_expires_on is not None
        if facts.rental_started_on > facts.rental_expires_on:
            raise ValueError("rental start must not follow expiry")
    elif facts.ownership_type == "owned":
        if any(value is not None for value in lease) or facts.acquisition_method is None:
            raise ValueError("owned assets require acquisition method and no rental dates")
        if facts.acquisition_method == "purchase" and facts.acquisition_channel is None:
            raise ValueError("purchased assets require purchase channel")
        if facts.acquisition_method == "other" and (
            not facts.acquisition_note
            or not facts.acquisition_note.strip()
            or facts.acquisition_channel
        ):
            raise ValueError("other acquisition requires note and no purchase channel")
    elif any(value is not None for value in acquisition + lease):
        raise ValueError("unconfirmed assets cannot include ownership details")


class RentalProjection(BaseModel):
    ownership_type: OwnershipType | None = None
    rental_started_on: date | None = None
    rental_expires_on: date | None = None
    rental_returned_on: date | None = None


class RentalRenewItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: UUID
    expected_rental_revision: int = Field(ge=0)


class RentalRenewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_key: UUID
    period: RentalPeriod
    assets: list[RentalRenewItem] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_assets(self) -> Self:
        if len({item.asset_id for item in self.assets}) != len(self.assets):
            raise ValueError("rental batch must contain unique assets")
        return self


class RentalRenewResultItem(BaseModel):
    asset_id: UUID
    previous_expires_on: date
    rental_expires_on: date
    rental_revision: int


class RentalRenewResponse(BaseModel):
    operation_key: UUID
    period: RentalPeriod
    replayed: bool
    items: list[RentalRenewResultItem]


class RentalReturnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    returned_on: date
    expected_rental_revision: int = Field(ge=0)

    @field_validator("returned_on", mode="before")
    @classmethod
    def date_only(cls, value: object) -> object:
        return RentalFacts.date_only(value)


class RentalRenewalResponse(BaseModel):
    id: UUID
    period: RentalPeriod
    previous_expires_on: date
    rental_expires_on: date
    created_at: datetime
    actor_name: str | None


class RentalRenewalPage(BaseModel):
    items: list[RentalRenewalResponse]
    total: int
    page: int
    page_size: int
