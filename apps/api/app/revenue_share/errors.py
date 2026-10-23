class RevenueShareError(ValueError):
    code = "revenue_share_error"


class ReferralCodeInvalid(RevenueShareError):
    code = "referral_code_invalid"


class RevenueShareNotFound(RevenueShareError):
    code = "revenue_share_not_found"


class RevenueShareConflict(RevenueShareError):
    code = "revenue_share_conflict"


class RevenueShareCursorInvalid(RevenueShareError):
    code = "revenue_share_cursor_invalid"


class RevenueShareReconcileBusy(RevenueShareError):
    code = "revenue_share_reconcile_busy"


class RevenueShareSettlementExceedsBalance(RevenueShareError):
    code = "revenue_share_settlement_exceeds_balance"


class RevenueShareSettlementOperationConflict(RevenueShareError):
    code = "revenue_share_settlement_operation_conflict"


class RevenueShareSettlementReferenceConflict(RevenueShareError):
    code = "revenue_share_settlement_reference_conflict"
