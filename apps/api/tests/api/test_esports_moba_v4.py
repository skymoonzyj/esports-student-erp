from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.esports_assessments.models import AssessmentAttempt
from tests.api import test_esports_routes as routes
from tests.api.test_esports_public_opening import counts, login
from tests.esports_helpers import attempt_request, result_request, session_request

no_rate_limit = routes.no_rate_limit
PREFIX = "/api/v1/esports-assessments"


@pytest.fixture(autouse=True)
def frozen_v4_catalog(monkeypatch):
    from tests.esports_helpers import pin_moba_v4

    pin_moba_v4(monkeypatch)


@pytest.mark.parametrize("mode", ["melee", "ranged"])
def test_public_lane_character_is_frozen_and_single_http_save_creates_report(
    db_client, db, platform_users, mode
):
    headers = login(db_client, platform_users)
    request = session_request("pc-moba").model_copy(update={"attack_mode": mode})
    created = db_client.post(
        f"{PREFIX}/sessions", json=request.model_dump(mode="json"), headers=headers
    )
    assert created.status_code == 201
    assert created.json()["members"] == ["last-hit"]
    session_id = created.json()["id"]
    started = db_client.post(
        f"{PREFIX}/sessions/{session_id}/attempts",
        json=attempt_request("pc-moba", "last-hit").model_dump(mode="json"),
        headers=headers,
    )
    assert started.status_code == 201
    run = started.json()
    assert run["config"]["params"]["attack_mode"] == mode
    assert run["config"]["duration_ms"] == 300000
    payload = result_request(
        "pc-moba", "last-hit", seed=run["seed"], catalog_version="pc-moba-v4"
    ).model_dump(mode="json")
    early = db_client.post(f"{PREFIX}/attempts/{run['id']}/result", json=payload, headers=headers)
    assert early.status_code == 409
    assert early.json()["code"] == "esports_result_too_early"
    row = db.get(AssessmentAttempt, UUID(run["id"]))
    row.created_at = datetime.now(UTC) - timedelta(seconds=304)
    db.commit()
    saved = db_client.post(f"{PREFIX}/attempts/{run['id']}/result", json=payload, headers=headers)
    assert saved.status_code == 201
    assert saved.json()["session"]["status"] == "completed"
    assert saved.json()["scored"]["score"] == 100
    assert saved.json()["session"]["report_id"]


def test_missing_lane_mode_is_rejected_without_creating_data(db_client, db, platform_users):
    headers = login(db_client, platform_users)
    before = counts(db)
    response = db_client.post(
        f"{PREFIX}/sessions",
        json=session_request("pc-moba").model_dump(mode="json"),
        headers=headers,
    )
    assert response.status_code == 409
    assert response.json()["code"] == "esports_attack_mode_required"
    assert "请选择近战或远程人物" in response.text
    assert counts(db) == before
