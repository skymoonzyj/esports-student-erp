from __future__ import annotations

import asyncio
from unittest.mock import Mock

from app.operations.metrics import RequestMetricsStore
from app.operations.monitor import OperationsBackgroundMonitor
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
