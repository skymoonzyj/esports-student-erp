import pytest

from app.audit.service import AuditMetadataRejected, AuditService


def test_rental_audit_preserves_controlled_lease_facts():
    metadata = {
        "rental_period": "month",
        "previous_expires_on": "2027-01-31",
        "rental_expires_on": "2027-02-28",
        "rental_revision": 1,
    }
    AuditService.validate_metadata(metadata)
    assert AuditService.public_metadata(metadata) == metadata


@pytest.mark.parametrize(
    "metadata",
    [
        {"rental_period": "year"},
        {"rental_revision": -1},
        {"rental_revision": True},
        {"rental_expires_on": "2027-02-30"},
        {"previous_expires_on": "13900000000"},
        {"rental_returned_on": "2027-01-01T12:00:00"},
    ],
)
def test_rental_audit_rejects_untyped_or_private_data(metadata):
    with pytest.raises(AuditMetadataRejected):
        AuditService.validate_metadata(metadata)


def test_correction_audit_preserves_typed_old_and_new_facts_without_free_text():
    previous = {
        "ownership_type": "rented",
        "acquisition_method": None,
        "acquisition_channel": None,
        "rental_started_on": "2026-01-01",
        "rental_expires_on": "2027-01-31",
        "rental_returned_on": None,
        "rental_revision": 2,
    }
    metadata = {
        "previous_rental_facts": previous,
        "rental_facts": {**previous, "rental_expires_on": "2027-01-30", "rental_revision": 3},
        "acquisition_note_changed": False,
    }
    AuditService.validate_metadata(metadata)
    assert AuditService.public_metadata(metadata) == metadata


@pytest.mark.parametrize(
    "field,value",
    [
        ("rental_revision", True),
        ("rental_revision", -1),
        ("ownership_type", "private@example.com"),
        ("acquisition_channel", "someone"),
        ("rental_started_on", "2026-02-30"),
        ("acquisition_note", "do not expose free text"),
    ],
)
def test_correction_audit_rejects_untyped_facts(field, value):
    facts = {
        "ownership_type": "rented",
        "acquisition_method": None,
        "acquisition_channel": None,
        "rental_started_on": "2026-01-01",
        "rental_expires_on": "2027-01-31",
        "rental_returned_on": None,
        "rental_revision": 2,
        field: value,
    }
    with pytest.raises(AuditMetadataRejected):
        AuditService.validate_metadata({"rental_facts": facts})
