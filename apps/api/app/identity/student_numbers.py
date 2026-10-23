from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.time import business_date_at


def generate_student_number(db: Session, now: datetime) -> str:
    sequence = db.scalar(text("SELECT nextval('registration_student_number_seq')"))
    if not isinstance(sequence, int) or sequence < 1:
        raise ValueError("registration student number is unavailable")
    return f"REG-{business_date_at(now).year:04d}{sequence:08d}"
