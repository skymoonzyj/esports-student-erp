from datetime import date, datetime
from zoneinfo import ZoneInfo

BUSINESS_TIME_ZONE = ZoneInfo("Asia/Shanghai")


def business_date_at(value: datetime) -> date:
    if value.tzinfo is None:
        raise ValueError("business datetime must be timezone-aware")
    return value.astimezone(BUSINESS_TIME_ZONE).date()


def business_today() -> date:
    return business_date_at(datetime.now(BUSINESS_TIME_ZONE))
