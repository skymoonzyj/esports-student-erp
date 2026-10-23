from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class OrganizationManagerCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: UUID
    full_name: str
    nickname: str | None


class OrganizationManagerResponse(OrganizationManagerCandidate):
    organization_id: UUID
    granted_at: datetime


class OrganizationManagerCandidatePage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[OrganizationManagerCandidate]
    total: int
    page: int
    page_size: int
