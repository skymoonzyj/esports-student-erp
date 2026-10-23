from datetime import UTC, datetime, timedelta

from app.operations.cloud_status import CloudCopyEvidence, cloud_conditions, with_cloud_copy
from app.operations.schemas import BackupSecondaryStatus, OperationsBackupStatus

NOW = datetime(2026, 10, 2, tzinfo=UTC)


def evidence(provider="baidu", **overrides):
    values = {
        "provider": provider,
        "enabled": True,
        "last_success_at": None,
        "last_error_code": None,
    }
    return CloudCopyEvidence(**(values | overrides))


def test_connection_without_verified_archive_does_not_close_independence_warning():
    status = with_cloud_copy(
        OperationsBackupStatus(status="ok", reason="备份完成"), [evidence()], now=NOW
    )
    assert status.secondary_copy.configured is True
    assert status.secondary_copy.status == "failed"
    assert status.secondary_copy.last_success_at is None


def test_fresh_verified_cloud_copy_satisfies_independence_without_mutating_local_status():
    original = OperationsBackupStatus(
        status="ok", reason="备份完成", verification_status="unverified"
    )
    result = with_cloud_copy(original, [evidence(last_success_at=NOW)], now=NOW)
    assert result.secondary_copy.status == "ok"
    assert result.secondary_copy.last_success_at == NOW
    assert result.verification_status == "unverified"
    assert original.secondary_copy.configured is False


def test_other_failed_provider_remains_visible_when_one_cloud_copy_is_healthy():
    targets = [evidence(last_success_at=NOW), evidence("115", last_error_code="cloud_auth_failed")]
    assert (
        with_cloud_copy(
            OperationsBackupStatus(status="ok", reason="备份完成"), targets, now=NOW
        ).secondary_copy.status
        == "ok"
    )
    alerts = cloud_conditions(targets, worker_last_seen_at=NOW, now=NOW)
    assert [alert.key for alert in alerts] == ["cloud_backup_115_failed"]
    assert "115" in alerts[0].title


def test_disabled_and_future_or_expired_evidence_never_count_as_current_copy():
    for target in [
        evidence(enabled=False, last_success_at=NOW),
        evidence(last_success_at=NOW + timedelta(minutes=1)),
        evidence(last_success_at=NOW - timedelta(hours=37)),
        evidence(last_success_at=NOW, last_error_code="cloud_verification_failed"),
    ]:
        result = with_cloud_copy(
            OperationsBackupStatus(status="ok", reason="备份完成"), [target], now=NOW
        )
        assert result.secondary_copy.status != "ok"


def test_legacy_verified_secondary_is_preserved_when_cloud_is_not_ready():
    original = OperationsBackupStatus(
        status="ok",
        reason="备份完成",
        secondary_copy=BackupSecondaryStatus(configured=True, status="ok", last_success_at=NOW),
    )
    assert (
        with_cloud_copy(original, [evidence()], now=NOW).secondary_copy == original.secondary_copy
    )


def test_cloud_worker_alert_requires_enabled_target_and_has_no_raw_error_details():
    assert cloud_conditions([evidence(enabled=False)], worker_last_seen_at=None, now=NOW) == []
    alerts = cloud_conditions(
        [evidence(last_error_code="secret token in exception")],
        worker_last_seen_at=NOW - timedelta(minutes=11),
        now=NOW,
    )
    assert {alert.key for alert in alerts} == {
        "cloud_backup_baidu_failed",
        "cloud_backup_worker_stale",
    }
    assert all("secret" not in alert.detail for alert in alerts)


def test_invalid_managed_authorization_never_counts_historical_success_and_alerts_disabled_target():
    target = evidence(
        enabled=False, last_success_at=NOW, authorization_status="reauthorization_required"
    )
    status = with_cloud_copy(
        OperationsBackupStatus(status="ok", reason="备份完成"), [target], now=NOW
    )
    assert status.secondary_copy.status != "ok"
    alerts = cloud_conditions([target], worker_last_seen_at=NOW, now=NOW)
    assert [alert.key for alert in alerts] == ["cloud_backup_baidu_authorization_invalid"]
