"""Authenticated, read-only assets for the isolated sound-localization trial."""

from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Response

from .access import AssessmentAccess
from .dependencies import Actor, Db
from .errors import AssessmentForbidden, AssessmentNotFound
from .http_support import EsportsAssessmentRoute

router = APIRouter(prefix="/sound-preview", route_class=EsportsAssessmentRoute)
_ASSET_ROOT = Path(__file__).resolve().with_name("sound_preview")
_MEDIA_TYPES = {
    "index.html": "text/html",
    "style.css": "text/css",
    "core.mjs": "application/javascript",
    "audio.mjs": "application/javascript",
    "app.mjs": "application/javascript",
}


@router.get("/{asset_name:path}", response_class=Response, include_in_schema=False)
def sound_preview_asset(asset_name: str, actor: Actor, db: Db) -> Response:
    if not AssessmentAccess(db, actor, datetime.now(UTC)).can_preview_suites():
        raise AssessmentForbidden()
    media_type = _MEDIA_TYPES.get(asset_name)
    if media_type is None:
        raise AssessmentNotFound()
    return Response(
        content=(_ASSET_ROOT / asset_name).read_bytes(),
        media_type=media_type,
        headers={
            "Cache-Control": "no-store, private",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )
