import pytest

from app.audit.service import AuditMetadataRejected, AuditService


@pytest.mark.parametrize("code", ["housing_access_changed", "invalid_session", "force_password_change_required"])
def test_refreshed_property_identity_failures_are_safe_audit_codes(code):
    metadata = {"error_code": code}
    AuditService.validate_metadata(metadata)
    assert AuditService.public_metadata(metadata) == metadata


@pytest.mark.parametrize("metadata", [
    {"housing_change_decision": "approve"},
    {"housing_change_decision": "reject"},
    {"housing_export_kind": "occupants"},
    {"housing_export_kind": "problems"},
])
def test_property_audit_preserves_typed_decision_and_export_kind(metadata):
    AuditService.validate_metadata(metadata)
    assert AuditService.public_metadata(metadata) == metadata


@pytest.mark.parametrize("frequency", ["unspecified", "daily", "weekly", "on_demand"])
def test_cleaning_frequency_audit_retains_controlled_before_after(frequency):
    metadata = {"previous_cleaning_frequency": "unspecified", "cleaning_frequency": frequency}
    AuditService.validate_metadata(metadata)
    assert AuditService.public_metadata(metadata) == metadata


@pytest.mark.parametrize("before", [None, "2026-11-01"])
def test_room_card_date_audit_retains_nullable_iso_dates(before):
    metadata = {
        "previous_card_issued_on": before,
        "card_issued_on": "2026-11-05",
        "previous_card_expires_on": before,
        "card_expires_on": "2027-01-31",
    }
    AuditService.validate_metadata(metadata)
    assert AuditService.public_metadata(metadata) == metadata


@pytest.mark.parametrize("metadata", [
    {"cleaning_frequency": "sometimes"},
    {"previous_cleaning_frequency": False},
    {"card_issued_on": "2026-02-30"},
    {"card_expires_on": "2026-11-05T12:00:00"},
    {"card_expires_on": "20261105"},
    {"card_issued_on": "owner@example.test"},
    {"card_expires_on": 20261105},
    {"housing_change_decision": "unknown"},
    {"housing_export_kind": "identity"},
    {"raw_notes": "hidden-private-note"},
])
def test_housing_audit_rejects_uncontrolled_values(metadata):
    with pytest.raises(AuditMetadataRejected):
        AuditService.validate_metadata(metadata)
    assert AuditService.public_metadata(metadata) == {}
