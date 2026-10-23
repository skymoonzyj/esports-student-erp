from __future__ import annotations


class FoodPreferenceError(ValueError):
    code = "food_preference_error"


class FoodPreferencePermissionDenied(FoodPreferenceError):
    code = "food_preference_permission_denied"


class FoodPreferenceNotFound(FoodPreferenceError):
    code = "food_preference_not_found"


class FoodCuisineConflict(FoodPreferenceError):
    code = "food_cuisine_conflict"


class FoodCuisineProtected(FoodPreferenceError):
    code = "food_cuisine_protected"


class FoodCuisineInvalid(FoodPreferenceError):
    code = "food_cuisine_invalid"


class FoodPreferenceOnboardingIncomplete(FoodPreferenceError):
    code = "food_preference_onboarding_incomplete"


class FoodPreferenceValidationFailed(FoodPreferenceError):
    code = "food_preference_validation_failed"


class FoodPreferenceVersionChanged(FoodPreferenceError):
    code = "food_preference_version_changed"
