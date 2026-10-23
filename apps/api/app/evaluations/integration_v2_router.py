from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.evaluations.dependencies import require_evaluation_tool_v2_service

router = APIRouter(
    prefix="/integrations/evaluations/v2",
    tags=["evaluation-integration-v2"],
    dependencies=[Depends(require_evaluation_tool_v2_service)],
)


class EvaluationV2Capabilities(BaseModel):
    api_version: Literal["v2"] = "v2"
    lease_seconds: Literal[300] = 300
    max_attempts: Literal[3] = 3


@router.get("/capabilities", response_model=EvaluationV2Capabilities)
def capabilities() -> EvaluationV2Capabilities:
    return EvaluationV2Capabilities()
