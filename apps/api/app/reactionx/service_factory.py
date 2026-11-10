"""Server-only construction. Test environment alone never verifies hardware.

The explicit opt-in is exclusively for disposable synthetic acceptance. Do not
expose it through browser requests or change the frozen rule verification state.
"""

from collections.abc import Callable
from typing import get_args

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.reactionx.commands import ClientService
from app.reactionx.results import ResultService
from app.reactionx.runs import RunService
from app.reactionx.schemas import Mode


def validation_policy(settings: Settings) -> Callable[[Mode], bool]:
    def allowed(mode: Mode) -> bool:
        return mode == "manual" or (
            settings.app_env == "test"
            and settings.reactionx_synthetic_formal_enabled is True
            and mode in get_args(Mode)
        )

    return allowed


def run_service(db: Session, settings: Settings) -> RunService:
    return RunService(
        db, origin=settings.reactionx_public_origin, validation_policy=validation_policy(settings)
    )


def client_service(db: Session, settings: Settings) -> ClientService:
    return ClientService(
        db, origin=settings.reactionx_public_origin, validation_policy=validation_policy(settings)
    )


def result_service(db: Session, settings: Settings) -> ResultService:
    return ResultService(
        db, origin=settings.reactionx_public_origin, validation_policy=validation_policy(settings)
    )
