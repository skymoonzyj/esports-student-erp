from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

from app.operations.metrics import RequestMetricsStore
from app.operations.monitor import OperationsBackgroundMonitor, service_conditions
from app.operations.runtime import RuntimeSampler


def test_background_runs_without_dashboard_and_flushes_on_shutdown(test_settings, monkeypatch) -> None:
    monitor = OperationsBackgroundMonitor(test_settings, RequestMetricsStore(), RuntimeSampler())
    flush = Mock()
    monkeypatch.setattr(monitor, "flush", flush)

    async def exercise() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        tick = Mock(side_effect=lambda: loop.call_soon_threadsafe(stop.set))
        monkeypatch.setattr(monitor, "tick", tick)
        await monitor.run(stop)
        tick.assert_called_once()

    asyncio.run(exercise())
    flush.assert_called_once()


def test_probe_and_flush_failures_are_isolated_and_logs_omit_secrets(
    test_settings, monkeypatch, caplog,
) -> None:
    monitor = OperationsBackgroundMonitor(test_settings, RequestMetricsStore(), RuntimeSampler())
    flush = Mock(side_effect=[RuntimeError("password=private-value"), None])
    probe = Mock(side_effect=RuntimeError("private-host/path"))
    monkeypatch.setattr(monitor, "flush", flush)
    monkeypatch.setattr(monitor, "monitor", probe)
    monitor.tick()
    monitor.tick()
    assert flush.call_count == 2
    assert probe.call_count == 1
    assert "private-value" not in caplog.text
    assert "private-host" not in caplog.text
    assert "operations_metrics_flush_failed" in caplog.text
    assert "operations_monitor_tick_failed" in caplog.text
    monitor.engine.dispose()


def test_dropped_error_warning_resolves_after_pressure_window(monkeypatch) -> None:
    from app.operations import monitor as module

    store = RequestMetricsStore(max_pending_errors=1, clock=lambda: 1_000)
    for _ in range(2):
        store.record(method="GET", route="/safe", status_code=500,
                     duration_ms=1, occurred_at=1_000)
    monkeypatch.setattr(module, "persistent_snapshot", lambda *a, **k: SimpleNamespace(
        server_error_count=0, top_routes=[],
    ))
    monkeypatch.setattr(module, "OperationsMonitorService", lambda *a, **k: SimpleNamespace(
        _background_metrics=lambda now: SimpleNamespace(
            scheduler_status="healthy", failed_tasks=0,
        ),
    ))
    sampler = SimpleNamespace(sample=lambda: SimpleNamespace(cpu_percent=0, memory_percent=0))
    initial = service_conditions(None, store, sampler,
                                 now=datetime.fromtimestamp(1_001, tz=UTC),
                                 scheduler_interval_seconds=30)
    batch = store.pending_batch()
    assert batch is not None
    store.acknowledge_batch(batch.id)
    recovered = service_conditions(None, store, sampler,
                                   now=datetime.fromtimestamp(1_001, tz=UTC) + timedelta(minutes=6),
                                   scheduler_interval_seconds=30)
    assert [row.key for row in initial] == ["request_error_detail_buffer_pressure"]
    assert recovered == []
    assert store.dropped_error_details == 1
