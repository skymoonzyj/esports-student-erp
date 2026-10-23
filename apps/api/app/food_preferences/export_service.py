from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.food_preferences.errors import FoodPreferenceError
from app.food_preferences.normalization import normalize_food_display
from app.food_preferences.renderer import render_csv_zip, render_xlsx
from app.food_preferences.reporting import FoodPreferenceReportingService
from app.food_preferences.schemas import FoodExportFormat, FoodPopulation

_MEDIA_TYPES: dict[FoodExportFormat, str] = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv_zip": "application/zip",
}
_EXTENSIONS: dict[FoodExportFormat, str] = {
    "xlsx": "xlsx",
    "csv_zip": "zip",
}


class FoodPreferenceExportFailed(RuntimeError):
    pass


def _rollback_without_masking(db: Session) -> None:
    try:
        db.rollback()
    except BaseException:  # noqa: BLE001, S110 - cleanup must not replace the primary error
        pass


@dataclass(frozen=True, slots=True)
class PrepareFoodPreferenceExportCommand:
    population: FoodPopulation
    include_inactive: bool
    format: FoodExportFormat
    purpose: str
    request_id: str

    def __post_init__(self) -> None:
        if self.population not in {"students", "all_people"}:
            raise ValueError("unsupported food preference export population")
        if not isinstance(self.include_inactive, bool):
            raise TypeError("include_inactive must be a boolean")
        if self.format not in {"xlsx", "csv_zip"}:
            raise ValueError("unsupported food preference export format")
        purpose = normalize_food_display(self.purpose, max_length=200)
        if len(purpose) < 4:
            raise ValueError("food preference export purpose is too short")
        if not isinstance(self.request_id, str) or not 1 <= len(self.request_id) <= 128:
            raise ValueError("food preference export request ID is invalid")
        object.__setattr__(self, "purpose", purpose)


@dataclass(frozen=True, slots=True)
class PreparedFoodPreferenceExport:
    content: bytes = field(repr=False)
    media_type: str
    filename: str
    file_sha256: str
    db: Session = field(repr=False, compare=False)

    def complete(self) -> None:
        """Commit after the terminal ASGI body send returns.

        This is the tested application boundary only: a successful send means
        the ASGI server accepted the final frame, not that the client received
        the file. Network receipt and this database commit cannot be
        mathematically atomic. Detected send or cancellation failures must call
        ``cancel()`` so the pending success audit is rolled back.
        """
        try:
            self.db.commit()
        except Exception:  # noqa: BLE001 - all ordinary commit failures cross a privacy boundary
            _rollback_without_masking(self.db)
            raise FoodPreferenceExportFailed("food preference export failed") from None
        except BaseException:
            _rollback_without_masking(self.db)
            raise

    def cancel(self) -> None:
        try:
            if self.db.in_transaction():
                self.db.rollback()
        except Exception:  # noqa: BLE001 - rollback details must not cross the export boundary
            raise FoodPreferenceExportFailed("food preference export failed") from None


class FoodPreferenceExportService:
    def __init__(
        self,
        db: Session,
        *,
        account_id: UUID,
        occurred_at: datetime,
    ) -> None:
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        self.db = db
        self.account_id = account_id
        self.occurred_at = occurred_at

    def prepare(
        self,
        command: PrepareFoodPreferenceExportCommand,
    ) -> PreparedFoodPreferenceExport:
        try:
            snapshot = FoodPreferenceReportingService(
                self.db,
                account_id=self.account_id,
                occurred_at=self.occurred_at,
            ).build_snapshot(
                population=command.population,
                include_inactive=command.include_inactive,
            )
            renderer = render_xlsx if command.format == "xlsx" else render_csv_zip
            content = renderer(snapshot)
            digest = sha256(content).hexdigest()
            population_token = command.population.replace("_", "-")
            timestamp = self.occurred_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
            filename = (
                f"food-preferences-{population_token}-{timestamp}."
                f"{_EXTENSIONS[command.format]}"
            )
            AuditService(self.db).append(
                actor_user_id=self.account_id,
                action="food_preferences.export",
                resource_type="food_preference_export",
                resource_id=None,
                purpose=command.purpose,
                outcome="allowed",
                request_id=command.request_id,
                metadata={
                    "population": snapshot.population,
                    "include_inactive": snapshot.include_inactive,
                    "format": command.format,
                    "person_count": snapshot.total_people,
                    "filled_count": snapshot.filled_people,
                    "file_sha256": digest,
                },
            )
            return PreparedFoodPreferenceExport(
                content=content,
                media_type=_MEDIA_TYPES[command.format],
                filename=filename,
                file_sha256=digest,
                db=self.db,
            )
        except FoodPreferenceError:
            _rollback_without_masking(self.db)
            raise
        except Exception:  # noqa: BLE001 - renderer/audit failures cross a privacy boundary
            _rollback_without_masking(self.db)
            raise FoodPreferenceExportFailed("food preference export failed") from None
        except BaseException:
            _rollback_without_masking(self.db)
            raise
