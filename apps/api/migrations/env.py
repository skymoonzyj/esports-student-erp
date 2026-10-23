from __future__ import annotations

import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.admissions import models as admissions_models  # noqa: F401
from app.asset_management import models as asset_management_models  # noqa: F401
from app.audit import models as audit_models  # noqa: F401
from app.auth import models as auth_models  # noqa: F401
from app.core.models import Base
from app.esports_assessments import models as esports_assessment_models  # noqa: F401
from app.evaluations import models as evaluations_models  # noqa: F401
from app.food_preferences import models as food_preference_models  # noqa: F401
from app.housing import (
    history_void_models,  # noqa: F401
    reporting_models,  # noqa: F401
)
from app.housing import models as housing_models  # noqa: F401
from app.identity import models as identity_models  # noqa: F401
from app.mail import models as mail_models  # noqa: F401
from app.operations import (
    cloud_auth_models,  # noqa: F401
    cloud_models,  # noqa: F401
)
from app.personnel_intake import activation_models  # noqa: F401
from app.personnel_intake import models as personnel_intake_models  # noqa: F401
from app.personnel_intake.claims import models as profile_claim_models  # noqa: F401
from app.procurement import models as procurement_models  # noqa: F401
from app.rbac import models as rbac_models  # noqa: F401
from app.registration import models as registration_models  # noqa: F401
from app.revenue_share import models as revenue_share_models  # noqa: F401
from app.talent_tests import models as talent_test_models  # noqa: F401
from app.teaching import models as teaching_models  # noqa: F401
from app.team_management import models as team_management_models  # noqa: F401
from app.travel_reports import models as travel_report_models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

database_url = config.attributes.get("database_url") or os.environ.get("DATABASE_URL")
if not database_url:
    database_url_file = os.environ.get("DATABASE_URL_FILE")
    if database_url_file:
        try:
            database_url = Path(database_url_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeError("unable to read database URL secret") from exc
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
