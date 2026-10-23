import pytest

from app.identity.models import Person
from tests.api.test_people import sign_in
from tests.services.test_personnel_intake import actor
from tests.services.test_personnel_queue_filters import queue_row, scoped_admin
from tests.services.test_team_access import teams

URL = "/api/v1/personnel-intake/requests"


def test_api_mine_review_and_submitted_by_shape(db, db_client, platform_users):
    own = queue_row(db, platform_users, submitter="admin")
    other = queue_row(db, platform_users)
    sign_in(db_client, platform_users, "admin")
    mine = db_client.get(URL, params={"view": "mine"})
    assert mine.status_code == 200
    assert [row["id"] for row in mine.json()["items"]] == [str(own.id)]
    review = db_client.get(URL, params={"view": "review", "submitter_query": " 教练 "})
    assert review.status_code == 200
    assert (review.json()["total"], [row["id"] for row in review.json()["items"]]) == (
        1,
        [str(other.id)],
    )
    item = review.json()["items"][0]
    assert item["submitted_by"] == {
        "id": str(platform_users["accounts"]["coach"].id),
        "label": "平台教练",
    }
    assert "scoped_payload" not in item
    assert review.headers["Cache-Control"] == "no-store, private"


def test_api_filters_count_across_pages(db, db_client, platform_users):
    wanted = [queue_row(db, platform_users, status="returned") for _ in range(3)]
    queue_row(db, platform_users)
    queue_row(db, platform_users, submitter="admin", status="returned")
    sign_in(db_client, platform_users, "admin")
    rows = []
    for page in (1, 2):
        response = db_client.get(
            URL,
            params={
                "view": "review",
                "status": "returned",
                "kind": "student",
                "submitter_query": "教练",
                "page": page,
                "page_size": 2,
            },
        )
        assert response.status_code == 200
        assert response.json()["total"] == 3
        rows.extend(row["id"] for row in response.json()["items"])
    assert rows == [str(row.id) for row in sorted(wanted, key=lambda row: row.id)]


def test_api_name_limit_applies_after_trimming(db, db_client, platform_users):
    db.get(Person, platform_users["people"]["admin"].id).full_name = "名" * 80
    wanted = queue_row(db, platform_users, submitter="admin")
    sign_in(db_client, platform_users, "admin")
    response = db_client.get(
        URL, params={"view": "review", "submitter_query": "  " + "名" * 80 + "  "}
    )
    assert response.status_code == 200
    assert (response.json()["total"], [row["id"] for row in response.json()["items"]]) == (
        1,
        [str(wanted.id)],
    )


@pytest.mark.parametrize(
    "params",
    [
        {"view": "secret@example.com"},
        {"status": "secret@example.com"},
        {"kind": "secret@example.com"},
        {"page": 0},
        {"page_size": 101},
        {"submitter_query": "secret@example.com"},
        {"view": "mine", "submitter_query": "secret@example.com"},
        {"view": "review", "submitter_query": "secret@example.com" * 8},
    ],
)
def test_api_invalid_queries_are_private_and_not_cached(db_client, platform_users, params):
    sign_in(db_client, platform_users, "admin")
    response = db_client.get(URL, params=params)
    assert response.status_code == 422
    assert "secret@example.com" not in response.text
    assert response.headers["Cache-Control"] == "no-store, private"


def test_api_unauthorized_review_is_generic_and_not_cached(db_client, platform_users):
    sign_in(db_client, platform_users, "coach")
    response = db_client.get(URL, params={"view": "review", "submitter_query": "学员家长"})
    assert response.status_code == 403
    assert "学员家长" not in response.text
    assert response.headers["Cache-Control"] == "no-store, private"


def test_api_scoped_review_only_ignores_hidden_submitter_names(db, db_client, platform_users):
    first, second = teams(db, actor(platform_users, "admin").account.id)
    visible = queue_row(db, platform_users, submitter="admin", kind="player", team=first.id)
    queue_row(db, platform_users, submitter="guardian", kind="player", team=second.id)
    scoped_admin(db, platform_users, first.id)
    sign_in(db_client, platform_users, "coach")
    response = db_client.get(URL, params={"view": "review"})
    assert response.status_code == 200
    assert [row["id"] for row in response.json()["items"]] == [str(visible.id)]
    hidden = db_client.get(URL, params={"view": "review", "submitter_query": "学员家长"})
    assert hidden.status_code == 200
    assert hidden.json()["items"] == [] and hidden.json()["total"] == 0
