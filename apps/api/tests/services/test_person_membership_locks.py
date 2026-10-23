from __future__ import annotations

from app.identity.membership_locks import (
    person_membership_advisory_lock_key,
    person_membership_lock_keys,
)


def test_person_membership_lock_keys_are_sorted_deduplicated_and_domain_isolated() -> (
    None
):
    keys = person_membership_lock_keys(
        identity_digests=["identity-a", "identity-a"],
        phone_digests={"contact-a", "contact-b"},
        email_digests={"contact-a"},
    )

    assert keys == sorted(keys)
    assert len(keys) == len(set(keys))
    assert person_membership_advisory_lock_key("phone", "contact-a") in keys
    assert person_membership_advisory_lock_key("email", "contact-a") in keys
    assert (
        person_membership_advisory_lock_key("phone", "contact-a")
        != person_membership_advisory_lock_key("email", "contact-a")
    )
