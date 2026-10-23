from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Never
from uuid import UUID

import anyio
from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse
from starlette.types import Message, Receive, Scope, Send

from app.auth.dependencies import require_standard_account
from app.auth.service import AuthContext
from app.core.database import get_db
from app.core.errors import ProblemError
from app.core.time import business_date_at
from app.food_preferences.access import FoodPreferenceAccess
from app.food_preferences.catalog_service import CuisineCatalogService
from app.food_preferences.dependencies import get_food_reporting_db
from app.food_preferences.errors import (
    FoodCuisineConflict,
    FoodCuisineInvalid,
    FoodCuisineProtected,
    FoodPreferenceError,
    FoodPreferenceNotFound,
    FoodPreferenceOnboardingIncomplete,
    FoodPreferencePermissionDenied,
    FoodPreferenceValidationFailed,
    FoodPreferenceVersionChanged,
)
from app.food_preferences.export_service import (
    FoodPreferenceExportFailed,
    FoodPreferenceExportService,
    PreparedFoodPreferenceExport,
    PrepareFoodPreferenceExportCommand,
)
from app.food_preferences.preference_service import (
    FoodPreferenceProjection,
    FoodPreferenceService,
    ReplaceFoodPreferenceCommand,
)
from app.food_preferences.reporting import FoodPreferenceReportingService
from app.food_preferences.schemas import (
    CuisineCatalogResponse,
    CuisineCreateRequest,
    CuisineOptionResponse,
    CuisinePatchRequest,
    DishSuggestionPageResponse,
    DishSuggestionResponse,
    FoodPopulation,
    FoodPreferenceCuisineResponse,
    FoodPreferenceExportRequest,
    FoodPreferencePersonPageResponse,
    FoodPreferencePersonResponse,
    FoodPreferenceProfileResponse,
    FoodPreferenceRankResponse,
    FoodPreferenceSelfResponse,
    FoodPreferenceStatisticsResponse,
    FoodPreferenceWriteRequest,
)

router = APIRouter(tags=["food-preferences"])

Db = Annotated[Session, Depends(get_db)]
StandardContext = Annotated[AuthContext, Depends(require_standard_account)]


def _now() -> datetime:
    return datetime.now(UTC)


def get_food_access(
    context: StandardContext,
    db: Db,
) -> FoodPreferenceAccess:
    return FoodPreferenceAccess(db, account_id=context.account.id, occurred_at=_now())


FoodAccess = Annotated[FoodPreferenceAccess, Depends(get_food_access)]


def get_food_catalog_service(db: Db, access: FoodAccess) -> CuisineCatalogService:
    return CuisineCatalogService(db, access=access)


def get_food_preference_service(db: Db) -> FoodPreferenceService:
    return FoodPreferenceService(db)


FoodCatalog = Annotated[CuisineCatalogService, Depends(get_food_catalog_service)]
FoodPreferences = Annotated[FoodPreferenceService, Depends(get_food_preference_service)]

FoodReportingDb = Annotated[
    Session,
    Depends(get_food_reporting_db, scope="request"),
]


def get_food_reporting_service(
    context: StandardContext,
    db: FoodReportingDb,
) -> FoodPreferenceReportingService:
    return FoodPreferenceReportingService(
        db,
        account_id=context.account.id,
        occurred_at=_now(),
    )


FoodReporting = Annotated[
    FoodPreferenceReportingService,
    Depends(get_food_reporting_service),
]


def get_food_export_service(
    context: StandardContext,
    db: FoodReportingDb,
) -> FoodPreferenceExportService:
    return FoodPreferenceExportService(
        db,
        account_id=context.account.id,
        occurred_at=_now(),
    )


FoodExports = Annotated[
    FoodPreferenceExportService,
    Depends(get_food_export_service),
]


def _request_id(request: Request) -> str:
    return str(request.state.request_id)


async def _export_content(content: bytes) -> AsyncIterator[bytes]:
    yield content


class CompletedFoodPreferenceExportResponse(StreamingResponse):
    """Commit the pending success audit only after the terminal ASGI frame succeeds."""

    def __init__(self, prepared: PreparedFoodPreferenceExport) -> None:
        self.prepared = prepared
        super().__init__(
            _export_content(prepared.content),
            media_type=prepared.media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{prepared.filename}"',
                "Cache-Control": "no-store, private",
                "X-Content-Type-Options": "nosniff",
            },
        )

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        terminal_sent = False

        async def tracked_send(message: Message) -> None:
            nonlocal terminal_sent
            await send(message)
            if (
                message["type"] == "http.response.body"
                and message.get("more_body", False) is False
            ):
                terminal_sent = True

        send_error: BaseException | None = None
        try:
            await super().__call__(scope, receive, tracked_send)
        except BaseException as exc:  # noqa: BLE001 - finalization must also handle cancellation
            send_error = exc

        finalizer = self.prepared.complete if terminal_sent else self.prepared.cancel
        finalizer_task = asyncio.create_task(run_in_threadpool(finalizer))
        cancellation_error: asyncio.CancelledError | None = None
        finalizer_error: BaseException | None = None
        response_task = asyncio.current_task()
        while not finalizer_task.done():
            try:
                with anyio.CancelScope(shield=True):
                    await asyncio.shield(finalizer_task)
            except asyncio.CancelledError as exc:
                cancellation_is_external = not finalizer_task.done() or (
                    response_task is not None and response_task.cancelling() > 0
                )
                if cancellation_is_external:
                    if cancellation_error is None:
                        cancellation_error = exc
                    continue
                finalizer_error = exc
                break
            except BaseException as exc:  # noqa: BLE001 - preserve the first send/finalizer error
                finalizer_error = exc
                break

        if finalizer_error is None:
            try:
                finalizer_task.result()
            except BaseException as exc:  # noqa: BLE001 - preserve the first finalizer error
                finalizer_error = exc

        try:
            await anyio.lowlevel.checkpoint_if_cancelled()
        except asyncio.CancelledError as exc:
            if cancellation_error is None:
                cancellation_error = exc

        if send_error is not None and cancellation_error is not None:
            send_error.__cause__ = cancellation_error
            send_error.__suppress_context__ = True

        if finalizer_error is not None:
            prior_error = send_error or cancellation_error
            if prior_error is not None:
                raise finalizer_error from prior_error
            raise finalizer_error
        if send_error is not None:
            raise send_error
        if cancellation_error is not None:
            raise cancellation_error


def _raise_food_error(exc: FoodPreferenceError) -> Never:
    if isinstance(exc, FoodPreferencePermissionDenied):
        raise ProblemError(
            status=403,
            code=exc.code,
            detail="当前账号没有该餐饮偏好操作权限",
        ) from exc
    if isinstance(exc, FoodPreferenceNotFound):
        raise ProblemError(
            status=404,
            code=exc.code,
            detail="未找到可访问的餐饮偏好资源",
        ) from exc
    if isinstance(exc, FoodPreferenceVersionChanged):
        raise ProblemError(
            status=409,
            code=exc.code,
            detail="餐饮偏好已被更新，请刷新后重试",
        ) from exc
    if isinstance(exc, (FoodCuisineConflict, FoodCuisineProtected)):
        raise ProblemError(
            status=409,
            code=exc.code,
            detail="菜系字典操作发生冲突",
        ) from exc
    if isinstance(
        exc,
        (
            FoodCuisineInvalid,
            FoodPreferenceValidationFailed,
            FoodPreferenceOnboardingIncomplete,
        ),
    ):
        raise ProblemError(
            status=422,
            code=exc.code,
            detail="餐饮偏好内容无效",
        ) from exc
    raise exc


def _profile_response(
    projection: FoodPreferenceProjection,
    *,
    can_update: bool,
    access_revision: str,
) -> FoodPreferenceProfileResponse:
    return FoodPreferenceProfileResponse(
        person_id=projection.person_id,
        version=projection.version,
        cuisines=tuple(
            FoodPreferenceCuisineResponse(
                cuisine_option_id=item.cuisine_option_id,
                name=item.name,
                custom_name=item.custom_name,
            )
            for item in projection.cuisines
        ),
        dishes=projection.dishes,
        updated_at=projection.updated_at,
        allowed_actions=("update",) if can_update else (),
        access_revision=access_revision,
    )


def _can_self_update(access: FoodPreferenceAccess) -> bool:
    try:
        access.require_self("food_preferences.update")
    except FoodPreferencePermissionDenied:
        return False
    return True


def _generic_source(
    context: AuthContext,
    access: FoodPreferenceAccess,
    person_id: UUID,
) -> str:
    if context.person.id == person_id:
        return "profile_self"
    tokens = access.rbac.person_resource_tokens(
        actor_person_id=context.person.id,
        target_person_id=person_id,
        reference_date=business_date_at(access.occurred_at),
    )
    if f"family:{person_id}" in tokens:
        return "profile_guardian"
    return "profile_admin"


@router.get(
    "/food-preferences/cuisines",
    response_model=CuisineCatalogResponse,
)
def list_cuisines(
    catalog: FoodCatalog,
    include_inactive: bool = False,
) -> CuisineCatalogResponse:
    try:
        projection = catalog.list(include_inactive=include_inactive)
        return CuisineCatalogResponse(
            items=tuple(CuisineOptionResponse.model_validate(item) for item in projection.items),
            allowed_actions=projection.allowed_actions,
            access_revision=projection.access_revision,
        )
    except FoodPreferenceError as exc:
        _raise_food_error(exc)


@router.post(
    "/food-preferences/cuisines",
    response_model=CuisineOptionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_cuisine(
    input: CuisineCreateRequest,
    request: Request,
    catalog: FoodCatalog,
) -> CuisineOptionResponse:
    try:
        return CuisineOptionResponse.model_validate(
            catalog.create(input, request_id=_request_id(request))
        )
    except FoodPreferenceError as exc:
        _raise_food_error(exc)


@router.patch(
    "/food-preferences/cuisines/{cuisine_id}",
    response_model=CuisineOptionResponse,
)
def patch_cuisine(
    cuisine_id: UUID,
    input: CuisinePatchRequest,
    request: Request,
    catalog: FoodCatalog,
) -> CuisineOptionResponse:
    try:
        return CuisineOptionResponse.model_validate(
            catalog.patch(cuisine_id, input, request_id=_request_id(request))
        )
    except FoodPreferenceError as exc:
        _raise_food_error(exc)


@router.get(
    "/food-preferences/dish-suggestions",
    response_model=DishSuggestionPageResponse,
)
def dish_suggestions(
    catalog: FoodCatalog,
    query: str = "",
    limit: Annotated[int, Query(ge=1, le=20)] = 10,
) -> DishSuggestionPageResponse:
    try:
        return DishSuggestionPageResponse(
            items=tuple(
                DishSuggestionResponse(name=name)
                for name in catalog.dish_suggestions(query, limit=limit)
            ),
            access_revision=catalog.access.access_revision,
        )
    except FoodPreferenceError as exc:
        _raise_food_error(exc)


@router.get(
    "/food-preferences/statistics",
    response_model=FoodPreferenceStatisticsResponse,
)
def food_preference_statistics(
    reporting: FoodReporting,
    population: FoodPopulation,
    include_inactive: bool = False,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> FoodPreferenceStatisticsResponse:
    try:
        projection = reporting.statistics(
            population=population,
            include_inactive=include_inactive,
            page=page,
            page_size=page_size,
        )
        return FoodPreferenceStatisticsResponse(
            population=projection.population,
            include_inactive=projection.include_inactive,
            total_people=projection.total_people,
            filled_people=projection.filled_people,
            unfilled_people=projection.unfilled_people,
            filled_ratio=projection.filled_ratio,
            cuisines=tuple(
                FoodPreferenceRankResponse.model_validate(row)
                for row in projection.cuisines
            ),
            dishes=tuple(
                FoodPreferenceRankResponse.model_validate(row)
                for row in projection.dishes
            ),
            people=FoodPreferencePersonPageResponse(
                items=tuple(
                    FoodPreferencePersonResponse.model_validate(row)
                    for row in projection.people
                ),
                page=projection.page,
                page_size=projection.page_size,
                total=projection.total_people,
            ),
            access_revision=projection.access_revision,
        )
    except FoodPreferenceError as exc:
        _raise_food_error(exc)


@router.post("/food-preferences/exports")
def export_food_preferences(
    input: FoodPreferenceExportRequest,
    request: Request,
    exports: FoodExports,
) -> CompletedFoodPreferenceExportResponse:
    try:
        prepared = exports.prepare(
            PrepareFoodPreferenceExportCommand(
                population=input.population,
                include_inactive=input.include_inactive,
                format=input.format,
                purpose=input.purpose,
                request_id=_request_id(request),
            )
        )
        return CompletedFoodPreferenceExportResponse(prepared)
    except FoodPreferenceError as exc:
        _raise_food_error(exc)
    except FoodPreferenceExportFailed as exc:
        raise ProblemError(
            status=500,
            code="food_preference_export_failed",
            detail="餐饮偏好导出失败",
        ) from exc


@router.get(
    "/me/food-preferences",
    response_model=FoodPreferenceSelfResponse,
)
def get_my_food_preferences(
    context: StandardContext,
    access: FoodAccess,
    preferences: FoodPreferences,
) -> FoodPreferenceSelfResponse:
    try:
        person = access.require_self("food_preferences.read")
        can_update: bool | None = None

        def reauthorize() -> None:
            nonlocal can_update
            access.require_self("food_preferences.read")
            can_update = _can_self_update(access)

        projection = preferences.get_self(
            account_id=context.account.id,
            reauthorize=reauthorize,
        )
        assert can_update is not None
        profile = _profile_response(
            projection.profile,
            can_update=can_update,
            access_revision=access.access_revision,
        )
        if projection.profile.person_id != person.id:
            raise FoodPreferenceNotFound("self profile not found")
        return FoodPreferenceSelfResponse(
            profile=profile,
            onboarding_required=can_update and projection.onboarding_required,
            onboarding_completed_at=projection.onboarding_completed_at,
        )
    except FoodPreferenceError as exc:
        _raise_food_error(exc)


@router.put(
    "/me/food-preferences",
    response_model=FoodPreferenceSelfResponse,
)
def replace_my_food_preferences(
    input: FoodPreferenceWriteRequest,
    request: Request,
    context: StandardContext,
    access: FoodAccess,
    preferences: FoodPreferences,
) -> FoodPreferenceSelfResponse:
    try:
        access.require_self("food_preferences.update")

        def reauthorize() -> None:
            access.require_self("food_preferences.update")

        projection = preferences.replace_self(
            account_id=context.account.id,
            input=input,
            request_id=_request_id(request),
            occurred_at=access.occurred_at,
            reauthorize=reauthorize,
        )
        return FoodPreferenceSelfResponse(
            profile=_profile_response(
                projection.profile,
                can_update=True,
                access_revision=access.access_revision,
            ),
            onboarding_required=projection.onboarding_required,
            onboarding_completed_at=projection.onboarding_completed_at,
        )
    except FoodPreferenceError as exc:
        _raise_food_error(exc)


@router.get(
    "/people/{person_id}/food-preferences",
    response_model=FoodPreferenceProfileResponse,
)
def get_person_food_preferences(
    person_id: UUID,
    access: FoodAccess,
    preferences: FoodPreferences,
) -> FoodPreferenceProfileResponse:
    try:
        target = access.require_target("food_preferences.read", person_id)

        def reauthorize() -> None:
            access.require_target("food_preferences.read", person_id)

        return _profile_response(
            preferences.get(target.id, reauthorize=reauthorize),
            can_update=access.can_target("food_preferences.update", target.id),
            access_revision=access.access_revision,
        )
    except FoodPreferenceError as exc:
        _raise_food_error(exc)


@router.put(
    "/people/{person_id}/food-preferences",
    response_model=FoodPreferenceProfileResponse,
)
def replace_person_food_preferences(
    person_id: UUID,
    input: FoodPreferenceWriteRequest,
    request: Request,
    context: StandardContext,
    access: FoodAccess,
    preferences: FoodPreferences,
) -> FoodPreferenceProfileResponse:
    try:
        target = access.require_target("food_preferences.update", person_id)

        def reauthorize_and_source() -> str:
            current_target = access.require_target(
                "food_preferences.update",
                person_id,
            )
            return _generic_source(context, access, current_target.id)

        projection = preferences.replace(
            target.id,
            ReplaceFoodPreferenceCommand(
                expected_version=input.expected_version,
                cuisines=input.cuisines,
                dishes=input.dishes,
                actor_user_id=context.account.id,
                source="profile_admin",
                request_id=_request_id(request),
            ),
            resolve_source=reauthorize_and_source,
        )
        return _profile_response(
            projection,
            can_update=True,
            access_revision=access.access_revision,
        )
    except FoodPreferenceError as exc:
        _raise_food_error(exc)
