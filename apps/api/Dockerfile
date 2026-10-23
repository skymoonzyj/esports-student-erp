ARG ERP_DEPENDENCY_IMAGE=python:3.12-slim

# NAS keeps the last working API image locally. Reusing its Python runtime
# avoids depending on a public package index for the project's pinned future
# dependency versions; a clean installation still falls back to pip below.
FROM ${ERP_DEPENDENCY_IMAGE} AS dependency_source

FROM python:3.12-slim AS base

COPY --from=dependency_source /usr/local /usr/local

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --system --gid 10001 erp \
    && useradd --system --uid 10001 --gid erp --home-dir /home/erp --create-home erp

COPY pyproject.toml ./
COPY migrations ./migrations
COPY alembic.ini ./

FROM base AS development

COPY app ./app

ARG ERP_USE_DEPENDENCY_IMAGE=0

RUN if [ "$ERP_USE_DEPENDENCY_IMAGE" = "1" ]; then \
      python -c "import fastapi, pip, pydantic, sqlalchemy; assert tuple(map(int, pip.__version__.split('.')[:2])) >= (26, 2)"; \
    else \
      python -m pip install --upgrade "pip>=26.2,<27" \
      && python -m pip install ".[dev]"; \
    fi

USER erp

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

FROM base AS production

ARG ERP_USE_DEPENDENCY_IMAGE=0

# Copy runtime packages explicitly so the E2E-only seed module never enters
# the production image layers.
COPY app/__init__.py app/main.py ./app/
COPY app/admissions ./app/admissions
COPY app/asset_management ./app/asset_management
COPY app/api ./app/api
COPY app/audit ./app/audit
COPY app/auth ./app/auth
COPY app/cli/__init__.py app/cli/create_admin.py app/cli/run_teaching_scheduler.py ./app/cli/
COPY app/core ./app/core
COPY app/esports_assessments ./app/esports_assessments
COPY app/evaluations ./app/evaluations
COPY app/food_preferences ./app/food_preferences
COPY app/housing ./app/housing
COPY app/identity ./app/identity
COPY app/mail ./app/mail
COPY app/operations ./app/operations
COPY app/personnel_intake ./app/personnel_intake
COPY app/portal ./app/portal
COPY app/procurement ./app/procurement
COPY app/rbac ./app/rbac
COPY app/referrals ./app/referrals
COPY app/registration ./app/registration
COPY app/revenue_share ./app/revenue_share
COPY app/security ./app/security
COPY app/talent_tests ./app/talent_tests
COPY app/team_management ./app/team_management
COPY app/teaching ./app/teaching
COPY app/travel_reports ./app/travel_reports

RUN if [ "$ERP_USE_DEPENDENCY_IMAGE" = "1" ]; then \
      python -c "import fastapi, pip, pydantic, sqlalchemy; assert tuple(map(int, pip.__version__.split('.')[:2])) >= (26, 2)"; \
    else \
      python -m pip install --upgrade "pip>=26.2,<27" \
      && python -m pip install "."; \
    fi

USER erp

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

FROM base AS e2e

COPY app ./app

ARG ERP_USE_DEPENDENCY_IMAGE=0

RUN if [ "$ERP_USE_DEPENDENCY_IMAGE" = "1" ]; then \
      python -c "import fastapi, pip, pydantic, sqlalchemy; assert tuple(map(int, pip.__version__.split('.')[:2])) >= (26, 2)"; \
    else \
      python -m pip install --upgrade "pip>=26.2,<27" \
      && python -m pip install "."; \
    fi

USER erp

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
