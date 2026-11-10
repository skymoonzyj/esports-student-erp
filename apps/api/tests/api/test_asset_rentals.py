from uuid import uuid4

from tests.api.test_assets import sign_in


def test_safe_specific_rental_failures(db_client, platform_users):
    csrf = sign_in(db_client, platform_users, "admin")
    future = create_rental(db_client, csrf, rental_started_on="9999-01-01", rental_expires_on="9999-12-31")
    response = renew(db_client, csrf, [future])
    assert response.json()["code"] == "asset_rental_not_started"
    assert response.json()["detail"] == "租赁尚未开始，不能续期"
    overflow = create_rental(db_client, csrf, rental_expires_on="9999-12-31")
    response = renew(db_client, csrf, [overflow])
    assert response.json()["code"] == "asset_rental_date_overflow"
    assert "9999" in response.json()["detail"]
    asset = create_rental(db_client, csrf)
    assert renew(db_client, csrf, [asset]).status_code == 200
    response = renew(db_client, csrf, [asset])
    assert response.json()["code"] == "asset_rental_revision_conflict"
    assert "刷新" in response.json()["detail"]
    response = db_client.patch(f"/api/v1/assets/{asset['id']}", headers={"X-CSRF-Token": csrf}, json={"rental_expires_on": "2027-03-01", "expected_rental_revision": 1})
    assert response.json()["code"] == "asset_rental_history_protected"
    assert "续期" in response.json()["detail"]


def create_rental(client, csrf, **overrides):
    response = client.post(
        "/api/v1/assets",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "租用设备",
            "ownership_type": "rented",
            "rental_started_on": "2026-01-01",
            "rental_expires_on": "2027-01-31",
            **overrides,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def renew(client, csrf, assets, key=None, period="month"):
    return client.post(
        "/api/v1/assets/rentals/renew",
        headers={"X-CSRF-Token": csrf},
        json={
            "operation_key": key or str(uuid4()),
            "period": period,
            "assets": [
                {"asset_id": item["id"], "expected_rental_revision": item["rental_revision"]}
                for item in assets
            ],
        },
    )


def test_rental_create_renew_replay_conflict_history_and_filters(db_client, platform_users):
    csrf = sign_in(db_client, platform_users, "admin")
    asset = create_rental(db_client, csrf)
    assert asset["rental_revision"] == 0
    key = str(uuid4())
    renewed = renew(db_client, csrf, [asset], key)
    assert renewed.status_code == 200, renewed.text
    assert renewed.json()["items"][0]["rental_expires_on"] == "2027-02-28"
    assert renewed.json()["items"][0]["previous_expires_on"] == "2027-01-31"
    replayed = renew(db_client, csrf, [asset], key)
    assert replayed.status_code == 200
    assert replayed.json() == {**renewed.json(), "replayed": True}
    assert renew(db_client, csrf, [asset], key, "week").status_code == 409
    assert renew(db_client, csrf, [asset]).status_code == 409
    history = db_client.get(f"/api/v1/assets/{asset['id']}/rental-renewals").json()
    assert history["total"] == 1
    assert history["items"][0]["previous_expires_on"] == "2027-01-31"
    assert db_client.get("/api/v1/assets?ownership_type=rented").json()["total"] == 1
    assert db_client.get("/api/v1/assets?ownership_type=unconfirmed").json()["total"] == 0


def test_batch_failure_rolls_back_and_overflow_rejected(db_client, platform_users):
    csrf = sign_in(db_client, platform_users, "admin")
    good = create_rental(db_client, csrf)
    bad = create_rental(db_client, csrf, rental_expires_on="9999-12-31")
    response = renew(db_client, csrf, [good, bad])
    assert response.status_code == 422
    assert db_client.get(f"/api/v1/assets/{good['id']}").json()["rental_expires_on"] == "2027-01-31"
    assert db_client.get(f"/api/v1/assets/{good['id']}/rental-renewals").json()["total"] == 0


def test_return_requires_custody_return_and_blocks_assign_and_renew(db_client, platform_users):
    csrf = sign_in(db_client, platform_users, "admin")
    asset = create_rental(db_client, csrf)
    headers = {"X-CSRF-Token": csrf}
    assign = {
        "person_id": str(platform_users["people"]["self_student"].id),
        "assigned_at": "2026-01-01T00:00:00+08:00",
    }
    assert (
        db_client.post(
            f"/api/v1/assets/{asset['id']}/assign", headers=headers, json=assign
        ).status_code
        == 200
    )
    payload = {"returned_on": "2026-01-02", "expected_rental_revision": 0}
    assert (
        db_client.post(
            f"/api/v1/assets/{asset['id']}/rental-return", headers=headers, json=payload
        ).status_code
        == 409
    )
    assert (
        db_client.post(
            f"/api/v1/assets/{asset['id']}/return",
            headers=headers,
            json={"returned_at": "2026-01-02T00:00:00+08:00"},
        ).status_code
        == 200
    )
    returned = db_client.post(
        f"/api/v1/assets/{asset['id']}/rental-return", headers=headers, json=payload
    )
    assert returned.status_code == 200, returned.text
    assert returned.json()["rental_revision"] == 1
    assert returned.json()["rental_expires_on"] == "2027-01-31"
    assert (
        db_client.post(
            f"/api/v1/assets/{asset['id']}/rental-return", headers=headers, json=payload
        ).status_code
        == 409
    )
    assert (
        db_client.post(
            f"/api/v1/assets/{asset['id']}/assign", headers=headers, json=assign
        ).status_code
        == 422
    )
    assert renew(db_client, csrf, [returned.json()]).status_code == 422


def test_date_correction_requires_version_and_cannot_bypass_renewal(db_client, platform_users):
    csrf = sign_in(db_client, platform_users, "admin")
    asset = create_rental(db_client, csrf)
    url = f"/api/v1/assets/{asset['id']}"
    headers = {"X-CSRF-Token": csrf}
    assert (
        db_client.patch(url, headers=headers, json={"rental_expires_on": "2027-01-30"}).status_code
        == 409
    )
    corrected = db_client.patch(
        url,
        headers=headers,
        json={"rental_expires_on": "2027-01-30", "expected_rental_revision": 0},
    )
    assert corrected.status_code == 200
    assert corrected.json()["rental_revision"] == 1
    assert (
        db_client.patch(
            url,
            headers=headers,
            json={"rental_started_on": "2028-01-01", "expected_rental_revision": 1},
        ).status_code
        == 422
    )
    assert renew(db_client, csrf, [corrected.json()]).status_code == 200
    assert (
        db_client.patch(
            url,
            headers=headers,
            json={"rental_expires_on": "2028-01-01", "expected_rental_revision": 2},
        ).status_code
        == 409
    )
    assert (
        db_client.patch(
            url,
            headers=headers,
            json={
                "ownership_type": None,
                "rental_started_on": None,
                "rental_expires_on": None,
                "expected_rental_revision": 2,
            },
        ).status_code
        == 409
    )


def test_ordinary_admin_cannot_renew_or_read_history(db_client, db, platform_users):
    from app.rbac.models import UserRole
    from app.rbac.seed import ROLES

    csrf = sign_in(db_client, platform_users, "admin")
    asset = create_rental(db_client, csrf)
    account = platform_users["accounts"]["customer_service"]
    db.add(UserRole(user_id=account.id, role_id=ROLES["admin"].id))
    db.commit()
    csrf = sign_in(db_client, platform_users, "customer_service")
    assert renew(db_client, csrf, [asset]).status_code == 403
    assert db_client.get(f"/api/v1/assets/{asset['id']}/rental-renewals").status_code == 403


def test_procurement_assets_snapshot_ownership_without_using_warranty_channel(
    db_client, platform_users
):
    from tests.api.test_procurement import _create_center

    csrf = sign_in(db_client, platform_users, "admin")
    center = _create_center(db_client, csrf, "租赁采购测试中心")
    response = db_client.post(
        "/api/v1/procurement",
        headers={"X-CSRF-Token": csrf},
        json={
            "operation_key": str(uuid4()),
            "purchase_date": "2026-08-28",
            "item_name": "自有采购设备",
            "quantity": "1",
            "unit_price_cents": 100,
            "settlement_center_id": center["id"],
            "purchase_channel": "online",
            "add_to_assets": True,
        },
    )
    assert response.status_code == 201, response.text
    items = db_client.get("/api/v1/assets?ownership_type=owned").json()["items"]
    assert len(items) == 1
    assert items[0]["acquisition_method"] == "purchase"
    assert items[0]["acquisition_channel"] == "online"
    assert items[0]["warranty_channel"] is None
    exported = db_client.post(
        "/api/v1/assets/exports",
        headers={"X-CSRF-Token": csrf},
        json={
            "format": "csv",
            "purpose": "测试归属导出",
            "ownership_type": "owned",
        },
    )
    assert exported.status_code == 200
    assert "自有采购设备" in exported.content.decode("utf-8-sig")
    excluded = db_client.post(
        "/api/v1/assets/exports",
        headers={"X-CSRF-Token": csrf},
        json={
            "format": "csv",
            "purpose": "测试归属筛选",
            "ownership_type": "rented",
        },
    )
    assert "自有采购设备" not in excluded.content.decode("utf-8-sig")
