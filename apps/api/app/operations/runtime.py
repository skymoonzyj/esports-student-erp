from __future__ import annotations

import os
import resource
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

_MIB = 1024 * 1024


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    cpu_percent: float
    memory_used_mb: float
    memory_limit_mb: float | None
    memory_percent: float | None
    process_memory_mb: float
    uptime_seconds: int


def _read_number(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding="ascii").strip()
    except OSError:
        return None
    if raw == "max":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _process_rss_bytes() -> int:
    try:
        for line in Path("/proc/self/status").read_text(encoding="ascii").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak_rss if sys.platform == "darwin" else peak_rss * 1024)


def _host_memory_bytes() -> int | None:
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError):
        return None
    if not isinstance(pages, int) or not isinstance(page_size, int):
        return None
    return pages * page_size


def _container_memory() -> tuple[int | None, int | None]:
    current = _read_number(Path("/sys/fs/cgroup/memory.current"))
    limit = _read_number(Path("/sys/fs/cgroup/memory.max"))
    if current is not None:
        return current, limit
    return (
        _read_number(Path("/sys/fs/cgroup/memory/memory.usage_in_bytes")),
        _read_number(Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")),
    )


class RuntimeSampler:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started_monotonic = time.monotonic()
        self._previous_wall = self._started_monotonic
        self._previous_cpu = time.process_time()

    def sample(self) -> RuntimeSnapshot:
        with self._lock:
            wall = time.monotonic()
            cpu = time.process_time()
            wall_delta = max(wall - self._previous_wall, 0.001)
            cpu_delta = max(cpu - self._previous_cpu, 0.0)
            self._previous_wall = wall
            self._previous_cpu = cpu
        cpu_percent = min(100.0, cpu_delta / wall_delta * 100)

        process_rss = _process_rss_bytes()
        container_used, container_limit = _container_memory()
        memory_used = container_used if container_used is not None else process_rss
        memory_limit = container_limit or _host_memory_bytes()
        memory_percent = (
            memory_used / memory_limit * 100
            if memory_limit is not None and memory_limit > 0
            else None
        )
        return RuntimeSnapshot(
            cpu_percent=cpu_percent,
            memory_used_mb=memory_used / _MIB,
            memory_limit_mb=(memory_limit / _MIB if memory_limit is not None else None),
            memory_percent=memory_percent,
            process_memory_mb=process_rss / _MIB,
            uptime_seconds=max(0, int(wall - self._started_monotonic)),
        )
