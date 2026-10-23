"""Quality classification for complete assessment statistics."""

from __future__ import annotations

from .config_schemas import TaskConfig
from .schemas import QualityFlag, QualityInput

_FATAL_FLAGS: frozenset[QualityFlag] = frozenset(
    {"incomplete_duration", "frame_gap", "hidden", "interrupted"}
)


class QualityRejected(ValueError):
    """A run cannot be persisted as a completed result."""

    def __init__(self, reason_codes: tuple[QualityFlag, ...]) -> None:
        self.reason_codes = reason_codes
        super().__init__(f"assessment quality rejected: {','.join(reason_codes)}")


def quality_flags(config: TaskConfig, quality: QualityInput) -> tuple[QualityFlag, ...]:
    """Return non-fatal warnings, raising when a run was not continuously completed."""

    fatal: list[QualityFlag] = []
    if quality.duration_ms != config.duration_ms:
        fatal.append("incomplete_duration")
    if quality.max_frame_gap_ms > 500:
        fatal.append("frame_gap")
    if quality.hidden:
        fatal.append("hidden")
    if quality.interrupted:
        fatal.append("interrupted")
    if fatal:
        raise QualityRejected(tuple(fatal))

    warnings: list[QualityFlag] = []
    if quality.long_frame_gap_total_ms > 3000:
        warnings.append("long_frames")
    expected_input = "touch" if config.suite_id.startswith("mobile") else "mouse"
    if quality.input_kind == "mixed":
        warnings.append("mixed_input")
    elif quality.input_kind != expected_input:
        warnings.append("unconfirmed_input")
    return tuple(warnings)


def is_fatal_quality_flag(flag: QualityFlag) -> bool:
    """Let report persistence reject DTOs that bypassed normal scoring."""

    return flag in _FATAL_FLAGS
