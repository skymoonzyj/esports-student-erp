from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.esports_assessments.models import AssessmentAttempt
from tests.api import test_esports_routes as routes
from tests.api.test_esports_public_opening import counts, login
from tests.esports_helpers import attempt_request, quality, session_request
from tests.unit.test_esports_moba_v5 import raw_v5

no_rate_limit = routes.no_rate_limit
PREFIX = "/api/v1/esports-assessments"


@pytest.mark.parametrize("mode", ["melee", "ranged"])
def test_v5_http_round_persists_seventy_five_denominator_and_hero_stats(
    db_client, db, platform_users, mode
):
    headers = login(db_client, platform_users)
    response = db_client.post(
        f"{PREFIX}/sessions",
        headers=headers,
        json=session_request("pc-moba")
        .model_copy(update={"attack_mode": mode})
        .model_dump(mode="json"),
    )
    assert response.status_code == 201
    assert response.json()["suite_config"]["catalog_version"] == "pc-moba-v5"
    session_id = response.json()["id"]
    started = db_client.post(
        f"{PREFIX}/sessions/{session_id}/attempts",
        headers=headers,
        json=attempt_request("pc-moba", "last-hit").model_dump(mode="json"),
    )
    assert started.status_code == 201
    run = started.json()
    payload = {
        "operation_key": "00000000-0000-4000-8000-000000000055",
        "raw": raw_v5(hero_death_times=[10000], hero_damage_taken=1000),
        "quality": quality("pc-moba", duration_ms=300000).model_dump(mode="json"),
    }
    assert (
        db_client.post(
            f"{PREFIX}/attempts/{run['id']}/result", headers=headers, json=payload
        ).status_code
        == 409
    )
    row = db.get(AssessmentAttempt, UUID(run["id"]))
    row.created_at = datetime.now(UTC) - timedelta(seconds=304)
    db.commit()
    saved = db_client.post(f"{PREFIX}/attempts/{run['id']}/result", headers=headers, json=payload)
    assert saved.status_code == 201
    assert saved.json()["scored"]["metrics"]["unresolved"] == 75
    assert saved.json()["scored"]["metrics"]["hero_deaths"] == 1
    assert (
        db_client.post(
            f"{PREFIX}/attempts/{run['id']}/result", headers=headers, json=payload
        ).json()
        == saved.json()
    )


def test_missing_v5_mode_does_not_create_data(db_client, db, platform_users):
    headers = login(db_client, platform_users)
    before = counts(db)
    response = db_client.post(
        f"{PREFIX}/sessions",
        headers=headers,
        json=session_request("pc-moba").model_dump(mode="json"),
    )
    assert response.status_code == 409
    assert response.json()["code"] == "esports_attack_mode_required"
    assert counts(db) == before
