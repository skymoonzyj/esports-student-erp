from uuid import uuid4

from app.auth.dependencies import require_standard_account
from tests.api import test_esports_access as access_fixtures
from tests.api.test_esports_access import grant
from tests.services.test_esports_rankings import round_row

esports_people = access_fixtures.esports_people


def test_esports_ranking_endpoint_requires_authentication_and_is_private(client):
    response = client.get(f"/api/v1/esports-assessments/reports/{uuid4()}/ranking")
    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store, private"


def test_esports_ranking_http_contract_and_private_deny(db_client, db, esports_people):
    _, actors, _ = esports_people
    _, report = round_row(db, actors["student"])
    _, other = round_row(db, actors["other"], (90,) * 4)
    db.commit()
    db_client.app.dependency_overrides[require_standard_account] = lambda: actors["student"]
    response = db_client.get(f"/api/v1/esports-assessments/reports/{report.id}/ranking")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    payload = response.json()
    assert set(payload) == {
        "report_id",
        "calculated_at",
        "status",
        "reason",
        "best_report_id",
        "best_completed_at",
        "overall_score",
        "overall_rank",
        "participant_count",
        "items",
        "top_scores",
    }
    assert payload["top_scores"] == []
    assert all(item["top_scores"] == [] for item in payload["items"])
    assert payload["overall_rank"] == 2 and payload["participant_count"] == 2
    assert payload["best_report_id"] == str(report.id)
    assert str(other.id) not in str(payload)
    for target in (other.id, uuid4()):
        denied = db_client.get(f"/api/v1/esports-assessments/reports/{target}/ranking")
        assert denied.status_code == 404
        assert denied.headers["cache-control"] == "no-store, private"
    grant(db, actors["student"], effect="deny", scope="self")
    assert (
        db_client.get(f"/api/v1/esports-assessments/reports/{report.id}/ranking").status_code == 404
    )
