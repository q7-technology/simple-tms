# Simple TMS step 1: skeleton — implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build step 1 of `docs/plans/2026-10-10-simple-tms-design.md`: the repo, Docker, sign-in for dispatchers and drivers, vehicles and drivers, and the Simple WMS subscriber that turns `delivery.packed` into a consignment with its packages and an append-only history. Usable through the API and `/docs`; the desktop and driver apps come with steps 2 and 3.

**Architecture:** One FastAPI app (`api/`, package `tms`) over PostgreSQL 16, laid out like Simple WMS's `api/wms`: `config.py` (pydantic-settings, `TMS_` prefix), `db.py`, `models/`, `services/`, `api/` (deps with `require(scope)`, errors, routes), `cli.py`, Alembic migrations. Simple WMS posts signed events to `POST /v1/wms/events`; the TMS checks the HMAC, ignores an event id it has seen, and on `delivery.packed` reads the delivery's ship-to from Simple WMS's API in the same request. Docker Compose runs `db`, `api` and `caddy`; the worker arrives with step 2.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, psycopg 3, httpx, pytest against a real PostgreSQL 16, Docker Compose, Caddy 2, GitHub Actions.

---

## Context

Everything below was read from Simple WMS (`/Users/leightonlauton/Q7 Technology/Software/q7/simple-wms/`, paths relative to it). Nothing in Simple WMS changes for this step.

### 1. The event envelope and its signature

- Envelope, built once per event in `api/wms/services/events.py:51-59`: `event_id` (uuid4, `:50`), `event_type`, `occurred_at` (UTC, `YYYY-MM-DDTHH:MM:SSZ`, `:14-15`), `warehouse`, `owner`, `external_ref`, `data`.
- The worker sends it as compact JSON: `json.dumps(row.payload, separators=(",", ":")).encode()` (`api/wms/worker.py:43`).
- Signature: `"sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()` (`api/wms/worker.py:31-32`): HMAC-SHA256 of the **exact body bytes**, **lower-case hex**, prefixed `sha256=`, keyed with the subscriber's own `secret`.
- Headers (`api/wms/worker.py:44-50`): `Content-Type: application/json`, `X-WMS-Signature`, `X-WMS-Event-Id` (the same uuid as the body's `event_id`), `X-WMS-Event-Type`, `User-Agent: simple-wms/0.1`. No `Authorization` header: the signature is the only proof. `docs/api.md:1021` documents the first two.
- Any 2xx is success; anything else, or a network error, is retried after 1 min, 5, 30 and 2 h, then marked failed (`api/wms/worker.py:28`, `:55-66`, `:91-99`). The worker waits 10 s for an answer (`api/wms/config.py:13`).
- Subscribers: `POST /v1/subscribers` with scope `integration:admin` (`api/wms/api/routes/integration.py:217-244`); body `name, url, secret? (16-128 chars, :131), event_types, warehouses, owner, transport, settings, active`; creates or updates by name; `event_types` takes `delivery.*` (`api/wms/services/events.py:18-22`). A delivered event cannot be retried from the WMS (`integration.py:321`).

So the TMS reads the raw body before parsing, compares `X-WMS-Signature` with `hmac.compare_digest`, and takes the event id from the signed body (the headers are not signed).

### 2. What `delivery.packed` carries

`api/wms/services/outbound.py:195-204`: `data = { delivery_ref, packages: [{ package_no, type, weight_kg, length_cm, width_cm, height_cm, sscc, container_id, contents: [{ delivery_line, sku, batch, qty, uom }] }] }`. Decimals are strings with no trailing zeros, or `null` (`api/wms/services/qty.py:4-8`). The envelope gives `warehouse`, `owner` and `external_ref`.

It does **not** carry the customer, the ship-to address or the required-by date. Those are on the delivery: `GET /v1/deliveries/{ref}?owner=…` (`api/wms/api/routes/outbound.py:274-277`), scope **`tasks:read`**, `owner` defaulting to `DEFAULT`, checked against the key's warehouses and owner (`:194-203`). It returns `DeliveryOut` (`:118-145`) with `ship_to` (`name, address, suburb, state, postcode, country, contact, phone, email`, `:28-38`), `required_by`, `warehouse`, `status`, lines and packages.

**Decision: fetch the delivery synchronously, while handling the event.** The WMS commits the packing and queues the event in one transaction (`events.py:46-48`), so the delivery is readable by the time the event arrives. If the fetch fails, the TMS answers `503` and keeps nothing (not even the event id), so the WMS's own backoff retries the event. That gives retries for free with no queue in the TMS, and a consignment never exists without its address. Deferring the fetch to a worker would need the queue table and a "consignment without an address" state a step early. The TMS waits at most 5 s (`TMS_WMS_TIMEOUT_SECONDS`), inside the WMS worker's 10 s. The call is in a small `tms/services/wms_client.py`, which step 2 grows with the ship call. Packages come from the signed event, the snapshot at packing time; the ship-to comes from the delivery.

### 3. Transfer events

There is no `transfer.packed`. Transfers can be packed (`api/wms/api/routes/transfers.py:298`) but that emits nothing; the events are `transfer.shipped` (`api/wms/services/transfers.py:205`), fired after the status is already `in_transit` (`:182`), and `transfer.received` (`:241`). By `transfer.shipped` the freight has left, too late to plan onto a run, and its packages carry only `package_no, weight_kg, sscc` (`:146-148`) with a warehouse code for a destination, not an address. **Transfers are deferred**: step 1 makes consignments from `delivery.packed` only. A transfer consignment needs either a new WMS event, which CLAUDE.md rules out ("Nothing in Simple WMS changes for it"), or a TMS-side booking. Either way it is a decision for the design, not for this step. `transfer.*` events that arrive are answered `200` and logged as ignored.

### 4. Decimals

The WMS stores weights and dimensions as `Numeric(18, 6)` (`api/wms/models/base.py:9-10`, `api/wms/models/documents.py:135-138`) and sends them as strings. The TMS does the same: `Numeric(18, 6)` columns, `Decimal(str(value))` on the way in, strings with no trailing zeros on the way out, as WMS's `Qty` does (`api/wms/api/schemas.py:12-17`). No floats anywhere.

### Patterns copied from Simple WMS

- Settings `api/wms/config.py:6-34`; engine `api/wms/db.py`; naming convention and `created_at_column` `api/wms/models/base.py:12-26`.
- PBKDF2 password hashing `api/wms/services/access.py:75-97`; HMAC session tokens with rotating refresh `api/wms/services/sessions.py:47-132`; `Principal`, `get_principal`, `require(scope)` `api/wms/api/deps.py:24-131`; error handlers `api/wms/api/errors.py`; desktop lockout after 5 wrong passwords for 15 minutes (`api/wms/api/routes/auth.py:117-141`, `api/wms/services/settings.py:26-27`); operator lock after 5 wrong PINs until unlocked (`api/wms/services/scanner_auth.py:66-75`, `settings.py:24`).
- Append-only trigger `block_change()` (`api/wms/alembic/versions/0001_skeleton.py:347-361`) and its tests (`api/tests/test_ledger.py:90-105`).
- Tests make and reset their own database: `api/tests/conftest.py:11-58`, with `TEST_DATABASE_URL` defaulting to the compose db on `127.0.0.1:5433`; migrations up/down/up and model drift `api/tests/test_migrations.py`.
- CLI `wms create-user` `api/wms/cli.py:27-38`; Dockerfile `api/Dockerfile`; compose `docker-compose.yml`; Caddy `deploy/Caddyfile`; CI `.github/workflows/ci.yml`.

### Ports on one machine

Simple WMS uses `127.0.0.1:5433` for its database and `127.0.0.1:8011` for its API (from its local `.env`), and its Caddy holds 80 and 443. Simple TMS takes **5434** for the database, **8021** for the API, and makes Caddy's host ports settings (`TMS_HTTP_PORT`, `TMS_HTTPS_PORT`).

### Decisions this plan makes that the design leaves open

- **Endpoint `POST /v1/wms/events`.** Named for the sender, so a later Platen callback can sit at `/v1/platen/...`. Signed, no bearer token, because the WMS worker sends none.
- **Unhandled event types** are answered `200 {"status": "ignored"}`, logged at INFO and recorded in `inbound_event`, so a repeat is a no-op too. Subscribing to `delivery.*` is fine.
- **Second `delivery.packed` for a delivery** that already has a consignment (a new event id) is ignored and logged, never a second consignment. `consignment` is unique on `(owner, wms_ref)`.
- **No worker container in step 1.** There is nothing to drain until step 2 brings `outbound_call` (the ship call and print jobs); a loop with nothing to do is just another thing to watch. Compose has a comment where it goes.
- **No `envelope.py` (inbound `message_id`) yet.** Nothing in step 1 is a delivery confirmation or a call to the WMS; it arrives with the driver app (step 3) and the ship call (step 2). Vehicle and driver edits are plain master data, as owners are in Simple WMS.
- **No API keys, no audit log, no user-management endpoints yet.** Desktop users are made with `tms create-user`; a partner key and the audit log come when something needs them.
- **Roles and scopes.** `admin` `*`; `manager` `fleet:read`, `fleet:write`, `consignments:read`; `dispatcher` `fleet:read`, `consignments:read`; a driver `fleet:read` (the design has them pick the truck after signing in).
- **Driver lock** is a boolean (`locked`) cleared by `POST /v1/drivers/{code}/unlock`; desktop users lock for 15 minutes, as in Simple WMS.
- **Rego** is stored upper case with spaces removed (`1ab 2cd` → `1AB2CD`) and is the key in vehicle URLs; driver `code` is the key in driver URLs.
- **The `/` route on Caddy** answers a short 404 until there is an app to serve.

---

## Before you start

```
cd "/Users/leightonlauton/Q7 Technology/Software/q7/simple-tms"
git switch -c step-1-skeleton
```

All `pytest` commands run from `api/` and need the compose `db` (Task 1 starts it). If the TMS db listens somewhere else, set `TEST_DATABASE_URL`.

---

### Task 1: Repo skeleton and a health check

**Files:**
- Create: `.gitignore`
- Create: `docker-compose.yml` (db only for now)
- Create: `.env.example` (database part for now)
- Create: `api/.python-version`, `api/.dockerignore`, `api/pyproject.toml`
- Create: `api/alembic.ini`, `api/alembic/env.py`, `api/alembic/script.py.mako`, `api/alembic/versions/` (empty directory)
- Create: `api/tms/__init__.py`, `api/tms/config.py`, `api/tms/db.py`
- Create: `api/tms/models/__init__.py`, `api/tms/models/base.py`
- Create: `api/tms/services/__init__.py`
- Create: `api/tms/api/__init__.py`, `api/tms/api/errors.py`, `api/tms/api/deps.py`, `api/tms/api/app.py`, `api/tms/api/routes/__init__.py`
- Create: `api/tests/conftest.py`, `api/tests/test_health.py`

**Step 1: Project files and the database**

`.gitignore`:
```
.env
__pycache__/
.venv/
*.pyc
.DS_Store
.pytest_cache/
*.egg-info/
```

`docker-compose.yml` (Task 11 adds `api` and `caddy`):
```yaml
services:
  db:
    image: postgres:16
    restart: unless-stopped
    environment:
      POSTGRES_DB: ${POSTGRES_DB:-tms}
      POSTGRES_USER: ${POSTGRES_USER:-tms}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in .env}
    volumes:
      - pgdata:/var/lib/postgresql/data
    # 5434 so it sits beside Simple WMS's database (5433) on one machine
    ports:
      - "127.0.0.1:${POSTGRES_PORT:-5434}:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-tms} -d ${POSTGRES_DB:-tms}"]
      interval: 5s
      timeout: 3s
      retries: 20

volumes:
  pgdata:
```

`.env.example` (Task 11 replaces it with the full file):
```
# Copy to .env and fill in. Nothing here is committed.

# Postgres (used by the db and api containers)
POSTGRES_DB=tms
POSTGRES_USER=tms
POSTGRES_PASSWORD=change-me
# Host port the database is published on (127.0.0.1 only). 5434 sits beside
# Simple WMS's 5433. The tests expect POSTGRES_PASSWORD=tms-dev on this port
# unless TEST_DATABASE_URL says otherwise.
POSTGRES_PORT=5434
```

`api/.python-version`:
```
3.12
```

`api/.dockerignore`:
```
.venv
__pycache__
*.pyc
tests
.pytest_cache
```

`api/pyproject.toml`:
```toml
[project]
name = "simple-tms-api"
version = "0.1.0"
description = "Simple TMS API"
requires-python = ">=3.12"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.30",
  "pydantic>=2.8",
  "pydantic-settings>=2.4",
  "sqlalchemy>=2.0.30",
  "psycopg[binary]>=3.2",
  "alembic>=1.13",
  "httpx>=0.27",
]

[project.optional-dependencies]
# httpx2: what newer Starlette test clients want, as in Simple WMS
dev = ["pytest>=8", "pytest-timeout>=2.3", "httpx2>=0.1"]

[project.scripts]
tms = "tms.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["tms"]

[tool.pytest.ini_options]
testpaths = ["tests"]
timeout = 60
filterwarnings = [
  "error",
  # third-party: Starlette's test client still uses an old anyio alias
  "ignore:The anyio.abc.BlockingPortal alias is deprecated:DeprecationWarning",
]
```

`api/alembic.ini`:
```ini
[alembic]
script_location = alembic
prepend_sys_path = .
path_separator = os
file_template = %%(rev)s_%%(slug)s
# Overridden by TMS_DATABASE_URL in env.py
sqlalchemy.url = postgresql+psycopg://tms:tms@127.0.0.1:5432/tms

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARNING
handlers = console
qualname =

[logger_sqlalchemy]
level = WARNING
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

`api/alembic/env.py` (Simple WMS's, plus the `configure_logger` switch so tests that run migrations do not silence the app's loggers):
```python
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from tms.models import Base

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

if os.environ.get("TMS_DATABASE_URL"):
    config.set_main_option("sqlalchemy.url", os.environ["TMS_DATABASE_URL"])

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    try:
        with connectable.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

`api/alembic/script.py.mako`: copy Simple WMS's unchanged:
```
cp "/Users/leightonlauton/Q7 Technology/Software/q7/simple-wms/api/alembic/script.py.mako" api/alembic/script.py.mako
mkdir -p api/alembic/versions
```

Start the database, and make the package so the venv can install it:
```
mkdir -p api/tms && touch api/tms/__init__.py
cp .env.example .env
sed -i '' 's/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=tms-dev/' .env
docker compose up -d db
cd api && uv venv -p 3.12 && uv pip install -e ".[dev]"
```

**Step 2: Write the failing test**

`api/tests/conftest.py`:
```python
import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

# The compose db listens on 127.0.0.1:5434 (Simple WMS has 5433), user tms.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://tms:tms-dev@127.0.0.1:5434/tms_test"
)
os.environ["TMS_DATABASE_URL"] = TEST_DATABASE_URL
os.environ["TMS_SECRET_KEY"] = "test-secret-key-that-is-long-enough-for-tests"
os.environ["TMS_WMS_WEBHOOK_SECRET"] = "test-webhook-secret-0123456789abcdef"
os.environ["TMS_WMS_URL"] = "http://127.0.0.1:9"  # nothing listens; the wms fixture replaces it
os.environ["TMS_WMS_API_KEY"] = "wms_test-key-for-the-tms"

WEBHOOK_SECRET = os.environ["TMS_WMS_WEBHOOK_SECRET"]


def _ensure_database():
    admin_url = TEST_DATABASE_URL.rsplit("/", 1)[0] + "/postgres"
    name = TEST_DATABASE_URL.rsplit("/", 1)[1]
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        exists = conn.execute(
            text("select 1 from pg_database where datname = :n"), {"n": name}
        ).scalar()
        if not exists:
            conn.execute(text(f'create database "{name}"'))
    engine.dispose()


@pytest.fixture(scope="session")
def engine():
    _ensure_database()
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as conn:
        conn.execute(text("drop schema public cascade"))
        conn.execute(text("create schema public"))
    from alembic import command
    from alembic.config import Config

    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine):
    from tms.models import Base

    with Session(engine) as session:
        yield session
        session.rollback()
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    if tables:
        with engine.begin() as conn:
            # TRUNCATE is not UPDATE or DELETE, so the append-only trigger lets it by
            conn.execute(text(f"truncate {tables} restart identity cascade"))


@pytest.fixture
def client(engine):
    from fastapi.testclient import TestClient

    from tms.api.app import app

    with TestClient(app) as c:
        yield c
```

`api/tests/test_health.py`:
```python
def test_health_answers_ok_once_the_database_is_there(client):
    r = client.get("/v1/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
```

**Step 3: Run it to see it fail**

Run: `cd api && .venv/bin/pytest tests/test_health.py -v`
Expected: ERROR, `ModuleNotFoundError: No module named 'tms.models'` (from `alembic/env.py`, run by the `engine` fixture).

**Step 4: Minimal implementation**

`api/tms/services/__init__.py`, `api/tms/api/__init__.py`, `api/tms/api/routes/__init__.py`: empty files (`api/tms/__init__.py` exists from Step 1).

`api/tms/config.py`:
```python
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TMS_", extra="ignore")

    database_url: str = "postgresql+psycopg://tms:tms@127.0.0.1:5432/tms"
    secret_key: str = "change-me"

    # Simple WMS. The webhook secret is the one its subscriber row signs with;
    # the URL and key are for reading a delivery's ship-to when it is packed.
    wms_webhook_secret: str = ""
    wms_url: str = ""
    wms_api_key: str = ""
    # Shorter than the WMS worker's own 10 s wait, so it hears our answer.
    wms_timeout_seconds: float = 5.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

`api/tms/db.py`:
```python
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tms.config import get_settings


@lru_cache
def get_engine():
    return create_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)
```

`api/tms/models/base.py`:
```python
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from sqlalchemy import DateTime, MetaData, Numeric, func
from sqlalchemy.orm import DeclarativeBase, mapped_column

# Weights, volumes and dimensions are decimals. Never floats.
Dec = Annotated[Decimal, mapped_column(Numeric(18, 6))]
DecOpt = Annotated[Decimal | None, mapped_column(Numeric(18, 6))]

naming = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=naming)


def created_at_column():
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
```

`api/tms/models/__init__.py` (Task 2 replaces it):
```python
from tms.models.base import Base

__all__ = ["Base"]
```

`api/tms/api/errors.py`:
```python
"""Every validation problem is a 422 with a list of field errors."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class FieldError(Exception):
    """A domain validation problem on one field, e.g. a rego already taken."""

    def __init__(self, field: str, message: str):
        self.field = field
        self.message = message
        super().__init__(f"{field}: {message}")


class NotFound(Exception):
    def __init__(self, message: str = "not found"):
        self.message = message
        super().__init__(message)


class Unauthorised(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class Forbidden(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def field_errors(errors: list[dict]) -> list[dict]:
    out = []
    for e in errors:
        loc = [str(x) for x in e.get("loc", []) if x not in ("body",)]
        out.append({"field": ".".join(loc) or "body", "message": e.get("msg", "invalid")})
    return out


def install(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"errors": field_errors(exc.errors())})

    @app.exception_handler(FieldError)
    async def _field(request: Request, exc: FieldError):
        return JSONResponse(
            status_code=422, content={"errors": [{"field": exc.field, "message": exc.message}]}
        )

    @app.exception_handler(NotFound)
    async def _not_found(request: Request, exc: NotFound):
        return JSONResponse(status_code=404, content={"detail": exc.message})

    @app.exception_handler(Unauthorised)
    async def _unauthorised(request: Request, exc: Unauthorised):
        return JSONResponse(status_code=401, content={"detail": exc.message},
                            headers={"WWW-Authenticate": "Bearer"})

    @app.exception_handler(Forbidden)
    async def _forbidden(request: Request, exc: Forbidden):
        return JSONResponse(status_code=403, content={"detail": exc.message})
```

`api/tms/api/deps.py` (Task 4 adds the principal):
```python
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from tms.db import get_sessionmaker


def get_db():
    with get_sessionmaker()() as session:
        yield session


DB = Annotated[Session, Depends(get_db)]
```

`api/tms/api/app.py`:
```python
import logging

from fastapi import APIRouter, FastAPI
from sqlalchemy import text

from tms.api import errors
from tms.api.deps import DB

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = FastAPI(
    title="Simple TMS",
    version="0.1.0",
    description="Open source, self-hosted transport management. The API is the only door.",
    docs_url="/docs",
    redoc_url=None,
)
errors.install(app)

v1 = APIRouter(prefix="/v1")


@v1.get("/health", tags=["system"])
def health(db: DB):
    db.execute(text("select 1"))
    return {"status": "ok"}


app.include_router(v1)
```

**Step 5: Run it to see it pass**

Run: `cd api && .venv/bin/pytest tests/test_health.py -v`
Expected: `1 passed`.

**Step 6: Commit**

```
git add .gitignore docker-compose.yml .env.example api/.python-version api/.dockerignore \
  api/pyproject.toml api/alembic.ini api/alembic/env.py api/alembic/script.py.mako \
  api/tms/__init__.py api/tms/config.py api/tms/db.py api/tms/models/__init__.py \
  api/tms/models/base.py api/tms/services/__init__.py api/tms/api/__init__.py \
  api/tms/api/errors.py api/tms/api/deps.py api/tms/api/app.py api/tms/api/routes/__init__.py \
  api/tests/conftest.py api/tests/test_health.py
git commit -m "Start the API: settings, database, health check and tests" \
  -m "Laid out like Simple WMS. The tests make and reset a tms_test database on the compose db, which listens on 5434 so it can sit beside Simple WMS."
```

---

### Task 2: Models, the first migration and the append-only history

**Files:**
- Create: `api/tms/models/access.py`, `api/tms/models/fleet.py`, `api/tms/models/consignment.py`, `api/tms/models/integration.py`
- Modify: `api/tms/models/__init__.py`
- Create: `api/alembic/versions/0001_skeleton.py`
- Create: `api/tests/test_migrations.py`, `api/tests/test_history.py`

**Step 1: Write the failing tests**

`api/tests/test_migrations.py`:
```python
"""A fresh install must work, and so must going back. Runs the migrations
up, down and up again on a database of its own, and checks the models agree."""
import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from conftest import TEST_DATABASE_URL

MIGRATION_URL = TEST_DATABASE_URL.rsplit("/", 1)[0] + "/tms_migration_check"
TABLES = {"user", "user_session", "driver", "vehicle", "consignment", "package",
          "consignment_event", "inbound_event", "alembic_version"}


@pytest.fixture
def blank_database(monkeypatch):
    admin = TEST_DATABASE_URL.rsplit("/", 1)[0] + "/postgres"
    name = MIGRATION_URL.rsplit("/", 1)[1]
    engine = create_engine(admin, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(text(f'drop database if exists "{name}" with (force)'))
        conn.execute(text(f'create database "{name}"'))
    engine.dispose()
    # alembic/env.py prefers this over the ini, so point it at the blank one
    monkeypatch.setenv("TMS_DATABASE_URL", MIGRATION_URL)
    yield MIGRATION_URL
    engine = create_engine(admin, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(text(f'drop database if exists "{name}" with (force)'))
    engine.dispose()


def alembic_config(url: str) -> Config:
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.attributes["configure_logger"] = False
    return cfg


def _tables(engine) -> set[str]:
    with engine.connect() as conn:
        return set(conn.execute(text(
            "select table_name from information_schema.tables where table_schema='public'"
        )).scalars())


def test_migrations_go_up_down_and_up_again(blank_database):
    cfg = alembic_config(blank_database)
    engine = create_engine(blank_database)

    command.upgrade(cfg, "head")
    assert _tables(engine) == TABLES
    with engine.connect() as conn:
        triggers = conn.execute(text(
            "select tgname from pg_trigger where not tgisinternal")).scalars().all()
    assert "consignment_event_append_only" in triggers

    command.downgrade(cfg, "base")
    assert _tables(engine) == {"alembic_version"}

    command.upgrade(cfg, "head")
    assert _tables(engine) == TABLES
    engine.dispose()


def test_the_models_match_the_migrations(blank_database):
    """No drift: what the models say is what a fresh database gets."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from tms.models import Base

    command.upgrade(alembic_config(blank_database), "head")
    engine = create_engine(blank_database)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diff == [], f"the models and the migrations disagree: {diff}"
```

`api/tests/test_history.py`:
```python
"""A consignment's history is append-only, and the database says so."""
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, update
from sqlalchemy.exc import DBAPIError

from tms.models import Consignment, ConsignmentEvent


@pytest.fixture
def consignment(db):
    c = Consignment(wms_ref="0080012345", warehouse="BAL-WH01", owner="DEFAULT",
                    customer="Ballarat Hardware", ship_to={"name": "Ballarat Hardware"},
                    status="packed")
    c.events.append(ConsignmentEvent(kind="packed", at=datetime.now(UTC), detail={}))
    db.add(c)
    db.commit()
    return c


def test_history_rows_cannot_be_updated(db, consignment):
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(update(ConsignmentEvent).values(kind="delivered"))
        db.commit()


def test_history_rows_cannot_be_deleted(db, consignment):
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(delete(ConsignmentEvent))
        db.commit()


def test_a_correction_is_a_new_row(db, consignment):
    db.add(ConsignmentEvent(consignment_id=consignment.id, kind="packed",
                            at=datetime.now(UTC), detail={"note": "recounted: 3 cartons, not 2"}))
    db.commit()
    db.refresh(consignment)
    assert [e.kind for e in consignment.events] == ["packed", "packed"]
```

**Step 2: Run them to see them fail**

Run: `cd api && .venv/bin/pytest tests/test_migrations.py tests/test_history.py -v`
Expected: `test_history.py` errors with `ImportError: cannot import name 'Consignment' from 'tms.models'`; `test_migrations_go_up_down_and_up_again` fails on `assert _tables(engine) == TABLES` (only `alembic_version` exists).

**Step 3: Minimal implementation**

`api/tms/models/access.py`:
```python
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from tms.models.base import Base, created_at_column


class User(Base):
    """Dispatchers, fleet managers and admins on the desktop. Deactivated,
    never deleted."""

    __tablename__ = "user"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str | None] = mapped_column(String(200))
    # dispatcher, manager, admin
    role: Mapped[str] = mapped_column(String(16), default="dispatcher")
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created_at_column()
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserSession(Base):
    """A desktop session: the refresh token (hashed) that mints short-lived
    access tokens."""

    __tablename__ = "user_session"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), index=True)
    refresh_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_column()
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Driver(Base):
    """Signs in to the driver app with a code and PIN. Five wrong PINs lock
    the driver until a manager unlocks them."""

    __tablename__ = "driver"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    pin_hash: Mapped[str] = mapped_column(String(200))
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created_at_column()
```

`api/tms/models/fleet.py`:
```python
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from tms.models.base import Base, Dec, created_at_column


class Vehicle(Base):
    """A truck, van or ute of our own. Deactivated, never deleted."""

    __tablename__ = "vehicle"

    id: Mapped[int] = mapped_column(primary_key=True)
    rego: Mapped[str] = mapped_column(String(12), unique=True)
    # van, rigid, ute
    type: Mapped[str] = mapped_column(String(8))
    max_kg: Mapped[Dec]
    max_m3: Mapped[Dec]
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created_at_column()
```

`api/tms/models/consignment.py`:
```python
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tms.models.base import Base, DecOpt, created_at_column


class Consignment(Base):
    """One delivery to one place, with its packages. Keeps the Simple WMS
    reference so the ship call in step 2 can name it."""

    __tablename__ = "consignment"
    __table_args__ = (UniqueConstraint("owner", "wms_ref"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # the delivery's external_ref in Simple WMS
    wms_ref: Mapped[str] = mapped_column(String(64))
    warehouse: Mapped[str] = mapped_column(String(32))
    owner: Mapped[str] = mapped_column(String(32))
    customer: Mapped[str] = mapped_column(String(200))
    ship_to: Mapped[dict] = mapped_column(JSONB, default=dict)
    required_by: Mapped[date | None] = mapped_column(Date)
    # packed for now; assigned, departed, delivered, failed arrive with runs
    status: Mapped[str] = mapped_column(String(16), default="packed", index=True)
    created_at: Mapped[datetime] = created_at_column()

    packages: Mapped[list[Package]] = relationship(
        back_populates="consignment", cascade="all, delete-orphan", order_by="Package.package_no")
    events: Mapped[list[ConsignmentEvent]] = relationship(
        back_populates="consignment", order_by="ConsignmentEvent.id")


class Package(Base):
    """A carton, pallet, tote or satchel, as Simple WMS packed it."""

    __tablename__ = "package"
    __table_args__ = (UniqueConstraint("consignment_id", "package_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    consignment_id: Mapped[int] = mapped_column(ForeignKey("consignment.id"), index=True)
    package_no: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(16), default="carton")
    weight_kg: Mapped[DecOpt]
    length_cm: Mapped[DecOpt]
    width_cm: Mapped[DecOpt]
    height_cm: Mapped[DecOpt]
    sscc: Mapped[str | None] = mapped_column(String(18))

    consignment: Mapped[Consignment] = relationship(back_populates="packages")


class ConsignmentEvent(Base):
    """Append-only status history. A trigger refuses UPDATE and DELETE; a
    mistake is corrected by a new row."""

    __tablename__ = "consignment_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    consignment_id: Mapped[int] = mapped_column(ForeignKey("consignment.id"), index=True)
    # packed, then assigned, departed, delivered, failed with later steps
    kind: Mapped[str] = mapped_column(String(16))
    at: Mapped[datetime] = created_at_column()
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)

    consignment: Mapped[Consignment] = relationship(back_populates="events")
```

`api/tms/models/integration.py`:
```python
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from tms.models.base import Base, created_at_column


class InboundEvent(Base):
    """Every Simple WMS event id we have acted on or chosen to ignore. A
    second delivery of the same id finds its row here and does nothing."""

    __tablename__ = "inbound_event"

    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64))
    external_ref: Mapped[str | None] = mapped_column(String(64))
    # created, ignored
    outcome: Mapped[str] = mapped_column(String(16))
    received_at: Mapped[datetime] = created_at_column()
```

`api/tms/models/__init__.py` (replace):
```python
from tms.models.base import Base
from tms.models.access import Driver, User, UserSession
from tms.models.fleet import Vehicle
from tms.models.consignment import Consignment, ConsignmentEvent, Package
from tms.models.integration import InboundEvent

__all__ = [
    "Base", "User", "UserSession", "Driver", "Vehicle", "Consignment", "Package",
    "ConsignmentEvent", "InboundEvent",
]
```

`api/alembic/versions/0001_skeleton.py` (written by hand so the trigger sits with the tables; `test_the_models_match_the_migrations` proves it matches the models):
```python
"""skeleton: users, drivers, vehicles, consignments, seen WMS events

Revision ID: 0001
Revises:
Create Date: 2026-10-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _created_at(name: str = "created_at") -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.text("now()"),
                     nullable=False)


def upgrade() -> None:
    op.create_table(
        "user",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("password_hash", sa.String(length=200), nullable=True),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        _created_at(),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user")),
        sa.UniqueConstraint("username", name=op.f("uq_user_username")),
    )
    op.create_table(
        "user_session",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("refresh_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], name=op.f("fk_user_session_user_id_user")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_session")),
        sa.UniqueConstraint("refresh_hash", name=op.f("uq_user_session_refresh_hash")),
    )
    op.create_index(op.f("ix_user_session_user_id"), "user_session", ["user_id"])
    op.create_table(
        "driver",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("pin_hash", sa.String(length=200), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False),
        sa.Column("locked", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_driver")),
        sa.UniqueConstraint("code", name=op.f("uq_driver_code")),
    )
    op.create_table(
        "vehicle",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rego", sa.String(length=12), nullable=False),
        sa.Column("type", sa.String(length=8), nullable=False),
        sa.Column("max_kg", sa.Numeric(18, 6), nullable=False),
        sa.Column("max_m3", sa.Numeric(18, 6), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vehicle")),
        sa.UniqueConstraint("rego", name=op.f("uq_vehicle_rego")),
    )
    op.create_table(
        "consignment",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("wms_ref", sa.String(length=64), nullable=False),
        sa.Column("warehouse", sa.String(length=32), nullable=False),
        sa.Column("owner", sa.String(length=32), nullable=False),
        sa.Column("customer", sa.String(length=200), nullable=False),
        sa.Column("ship_to", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("required_by", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_consignment")),
        sa.UniqueConstraint("owner", "wms_ref", name=op.f("uq_consignment_owner_wms_ref")),
    )
    op.create_index(op.f("ix_consignment_status"), "consignment", ["status"])
    op.create_table(
        "package",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("consignment_id", sa.Integer(), nullable=False),
        sa.Column("package_no", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("weight_kg", sa.Numeric(18, 6), nullable=True),
        sa.Column("length_cm", sa.Numeric(18, 6), nullable=True),
        sa.Column("width_cm", sa.Numeric(18, 6), nullable=True),
        sa.Column("height_cm", sa.Numeric(18, 6), nullable=True),
        sa.Column("sscc", sa.String(length=18), nullable=True),
        sa.ForeignKeyConstraint(["consignment_id"], ["consignment.id"],
                                name=op.f("fk_package_consignment_id_consignment")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_package")),
        sa.UniqueConstraint("consignment_id", "package_no",
                            name=op.f("uq_package_consignment_id_package_no")),
    )
    op.create_index(op.f("ix_package_consignment_id"), "package", ["consignment_id"])
    op.create_table(
        "consignment_event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("consignment_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        _created_at("at"),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["consignment_id"], ["consignment.id"],
                                name=op.f("fk_consignment_event_consignment_id_consignment")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_consignment_event")),
    )
    op.create_index(op.f("ix_consignment_event_consignment_id"), "consignment_event",
                    ["consignment_id"])
    op.create_table(
        "inbound_event",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("external_ref", sa.String(length=64), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        _created_at("received_at"),
        sa.PrimaryKeyConstraint("event_id", name=op.f("pk_inbound_event")),
    )

    # A consignment's history is append-only. A mistake is corrected by a new row.
    op.execute("""
        create or replace function block_change() returns trigger
        language plpgsql as $$
        begin
            raise exception '% is append-only: rows cannot be updated or deleted', tg_table_name
                using errcode = 'restrict_violation';
        end
        $$
    """)
    op.execute(
        "create trigger consignment_event_append_only before update or delete "
        "on consignment_event for each row execute function block_change()"
    )


def downgrade() -> None:
    op.execute("drop trigger if exists consignment_event_append_only on consignment_event")
    op.execute("drop function if exists block_change()")
    op.drop_table("inbound_event")
    op.drop_index(op.f("ix_consignment_event_consignment_id"), table_name="consignment_event")
    op.drop_table("consignment_event")
    op.drop_index(op.f("ix_package_consignment_id"), table_name="package")
    op.drop_table("package")
    op.drop_index(op.f("ix_consignment_status"), table_name="consignment")
    op.drop_table("consignment")
    op.drop_table("vehicle")
    op.drop_table("driver")
    op.drop_index(op.f("ix_user_session_user_id"), table_name="user_session")
    op.drop_table("user_session")
    op.drop_table("user")
```

**Step 4: Run them to see them pass**

Run: `cd api && .venv/bin/pytest tests/test_migrations.py tests/test_history.py tests/test_health.py -v`
Expected: `6 passed`. If the drift test lists differences, fix the migration to match the models, not the other way round.

**Step 5: Commit**

```
git add api/tms/models/access.py api/tms/models/fleet.py api/tms/models/consignment.py \
  api/tms/models/integration.py api/tms/models/__init__.py \
  api/alembic/versions/0001_skeleton.py api/tests/test_migrations.py api/tests/test_history.py
git commit -m "Add users, drivers, vehicles, consignments and seen WMS events" \
  -m "consignment_event is append-only: a trigger refuses UPDATE and DELETE, as Simple WMS does for its stock ledger. Weights and dimensions are Numeric, never float."
```

---

### Task 3: Passwords, PINs and session tokens

**Files:**
- Create: `api/tms/services/access.py`, `api/tms/services/sessions.py`
- Create: `api/tests/test_access.py`

**Step 1: Write the failing test**

`api/tests/test_access.py`:
```python
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from tms.services import access, sessions


def test_a_password_hash_checks_the_password_and_nothing_else():
    stored = access.hash_password("correct horse battery")
    assert stored.startswith("pbkdf2_sha256$")
    assert access.verify_password("correct horse battery", stored)
    assert not access.verify_password("Correct horse battery", stored)
    assert not access.verify_password("anything", None)
    assert not access.verify_password("anything", "not-a-hash")


def test_an_access_token_names_its_user_until_it_expires():
    user = SimpleNamespace(id=42)
    now = datetime.now(UTC)
    token = sessions.issue_access_token(user, now=now)
    assert token.startswith("tms_s.")
    assert sessions.read_access_token(token, now=now) == 42
    later = now + timedelta(seconds=sessions.ACCESS_TTL_SECONDS + 1)
    assert sessions.read_access_token(token, now=later) is None


def test_a_tampered_token_is_refused():
    token = sessions.issue_access_token(SimpleNamespace(id=42))
    payload, sig = token[len("tms_s."):].split(".", 1)
    assert sessions.read_access_token(f"tms_s.{payload}.{sig[:-2]}xx") is None
    assert sessions.read_access_token("tms_s.nonsense") is None


def test_a_driver_token_is_not_a_user_token():
    token = sessions.issue_driver_token(7)
    assert token.startswith("tms_d.")
    assert sessions.read_driver_token(token) == 7
    assert sessions.read_access_token(token) is None
```

**Step 2: Run it to see it fail**

Run: `cd api && .venv/bin/pytest tests/test_access.py -v`
Expected: ERROR, `ImportError: cannot import name 'access' from 'tms.services'`.

**Step 3: Minimal implementation**

`api/tms/services/access.py`:
```python
"""Passwords and PINs. Hashes only, never the secret."""
from __future__ import annotations

import hashlib
import hmac
import secrets

# PBKDF2 from the standard library, no extra dependency. Same scheme as Simple WMS.
_ITERATIONS = 600_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        algo, iterations, salt, digest = stored.split("$")
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    candidate = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), int(iterations)
    )
    return hmac.compare_digest(candidate.hex(), digest)
```

`api/tms/services/sessions.py`:
```python
"""Sessions. A desktop user gets a short-lived signed access token plus a
stored, rotating refresh token; a driver gets one signed token for the shift.
HMAC from the standard library, as in Simple WMS."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from tms.config import get_settings
from tms.models import User, UserSession

ACCESS_PREFIX = "tms_s."
ACCESS_TTL_SECONDS = 15 * 60
REFRESH_PREFIX = "tms_r."
REFRESH_TTL = timedelta(days=14)
DRIVER_PREFIX = "tms_d."
DRIVER_TTL_SECONDS = 12 * 3600

# What each desktop role may do.
ROLE_SCOPES: dict[str, list[str]] = {
    "admin": ["*"],
    "manager": ["fleet:read", "fleet:write", "consignments:read"],
    "dispatcher": ["fleet:read", "consignments:read"],
}
ROLES = tuple(ROLE_SCOPES)
# A driver picks a truck after signing in, so may see the fleet. More with step 3.
DRIVER_SCOPES = ["fleet:read"]


def scopes_for(role: str) -> list[str]:
    return list(ROLE_SCOPES.get(role, []))


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(payload: str) -> str:
    key = get_settings().secret_key.encode()
    return _b64(hmac.new(key, payload.encode(), hashlib.sha256).digest())


def _issue(prefix: str, claims: dict, ttl: int, now: datetime | None) -> str:
    now = now or datetime.now(UTC)
    payload = _b64(json.dumps(
        {**claims, "exp": int((now + timedelta(seconds=ttl)).timestamp()),
         "n": secrets.token_hex(4)},
        separators=(",", ":"),
    ).encode())
    return f"{prefix}{payload}.{_sign(payload)}"


def _read(prefix: str, token: str, now: datetime | None) -> dict | None:
    """The claims, or None if the token is not ours, tampered or expired."""
    if not token.startswith(prefix):
        return None
    try:
        payload, sig = token[len(prefix):].split(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(sig.encode(), _sign(payload).encode()):
        return None
    try:
        data = json.loads(_unb64(payload))
    except (ValueError, UnicodeDecodeError):
        return None
    now = now or datetime.now(UTC)
    if int(data.get("exp", 0)) <= int(now.timestamp()):
        return None
    return data


def issue_access_token(user: User, now: datetime | None = None,
                       ttl: int = ACCESS_TTL_SECONDS) -> str:
    return _issue(ACCESS_PREFIX, {"uid": user.id}, ttl, now)


def read_access_token(token: str, now: datetime | None = None) -> int | None:
    data = _read(ACCESS_PREFIX, token, now)
    return int(data["uid"]) if data else None


def issue_driver_token(driver_id: int, now: datetime | None = None,
                       ttl: int = DRIVER_TTL_SECONDS) -> str:
    return _issue(DRIVER_PREFIX, {"did": driver_id}, ttl, now)


def read_driver_token(token: str, now: datetime | None = None) -> int | None:
    data = _read(DRIVER_PREFIX, token, now)
    return int(data["did"]) if data else None


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def start_session(db: Session, user: User) -> tuple[UserSession, str]:
    raw = REFRESH_PREFIX + secrets.token_urlsafe(32)
    row = UserSession(user_id=user.id, refresh_hash=_hash(raw),
                      expires_at=datetime.now(UTC) + REFRESH_TTL)
    db.add(row)
    db.flush()
    return row, raw


def find_session(db: Session, raw_refresh: str) -> UserSession | None:
    row = db.execute(
        select(UserSession).where(UserSession.refresh_hash == _hash(raw_refresh))
    ).scalar_one_or_none()
    if row is None or row.revoked_at is not None or row.expires_at <= datetime.now(UTC):
        return None
    return row


def rotate_session(db: Session, row: UserSession) -> str:
    """Spend this refresh token and hand out a new one on the same session row."""
    raw = REFRESH_PREFIX + secrets.token_urlsafe(32)
    row.refresh_hash = _hash(raw)
    row.expires_at = datetime.now(UTC) + REFRESH_TTL
    row.last_used_at = datetime.now(UTC)
    db.flush()
    return raw


def revoke_session(db: Session, row: UserSession) -> None:
    row.revoked_at = datetime.now(UTC)
    db.flush()
```

**Step 4: Run it to see it pass**

Run: `cd api && .venv/bin/pytest tests/test_access.py -v`
Expected: `4 passed`.

**Step 5: Commit**

```
git add api/tms/services/access.py api/tms/services/sessions.py api/tests/test_access.py
git commit -m "Hash passwords and PINs, and sign session tokens" \
  -m "The same PBKDF2 and HMAC scheme as Simple WMS, with tms_ prefixes. Desktop tokens last 15 minutes with a 14-day refresh; driver tokens last a 12-hour shift."
```

---

### Task 4: Dispatcher sign-in and `require(scope)`

**Files:**
- Modify: `api/tms/api/deps.py` (replace)
- Create: `api/tms/api/routes/auth.py`
- Modify: `api/tms/api/app.py`
- Modify: `api/tests/conftest.py` (append)
- Create: `api/tests/test_auth.py`

**Step 1: Write the failing test**

Append to `api/tests/conftest.py`:
```python


# --- people -----------------------------------------------------------------

PASSWORD = "correct horse battery"


def make_user(db, username: str, role: str):
    from tms.models import User
    from tms.services.access import hash_password

    user = User(username=username, display_name=username.title(), role=role,
                password_hash=hash_password(PASSWORD), failed_attempts=0, active=True)
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def admin(db):
    return make_user(db, "leighton", "admin")


@pytest.fixture
def manager(db):
    return make_user(db, "fleet.mgr", "manager")


@pytest.fixture
def dispatcher(db):
    return make_user(db, "dee", "dispatcher")


def login(client, username: str, password: str = PASSWORD) -> dict:
    r = client.post("/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_headers(client, admin):
    return bearer(login(client, admin.username)["token"])


@pytest.fixture
def manager_headers(client, manager):
    return bearer(login(client, manager.username)["token"])


@pytest.fixture
def dispatcher_headers(client, dispatcher):
    return bearer(login(client, dispatcher.username)["token"])
```

`api/tests/test_auth.py`:
```python
from conftest import PASSWORD, bearer, login


def test_sign_in_gives_a_token_that_says_who_you_are(client, dispatcher):
    session = login(client, "dee")
    assert session["token"].startswith("tms_s.")
    assert session["refresh_token"].startswith("tms_r.")
    assert session["user"] == {"id": dispatcher.id, "username": "dee",
                               "display_name": "Dee", "role": "dispatcher"}

    r = client.get("/v1/auth/me", headers=bearer(session["token"]))
    assert r.status_code == 200
    assert r.json()["kind"] == "user"
    assert r.json()["scopes"] == ["fleet:read", "consignments:read"]


def test_a_wrong_password_says_how_many_tries_are_left(client, dispatcher):
    r = client.post("/v1/auth/login", json={"username": "dee", "password": "nope"})
    assert r.status_code == 401
    assert r.json() == {"detail": "wrong username or password", "code": "wrong_password",
                        "tries_left": 4}


def test_an_unknown_user_looks_like_a_wrong_password(client):
    r = client.post("/v1/auth/login", json={"username": "nobody", "password": "nope"})
    assert r.status_code == 401
    assert r.json()["code"] == "wrong_password"
    assert "tries_left" not in r.json()


def test_five_wrong_passwords_lock_the_account(client, dispatcher):
    for _ in range(4):
        client.post("/v1/auth/login", json={"username": "dee", "password": "nope"})
    r = client.post("/v1/auth/login", json={"username": "dee", "password": "nope"})
    assert r.status_code == 401
    assert r.json()["code"] == "locked"
    # even the right password waits out the lock
    r = client.post("/v1/auth/login", json={"username": "dee", "password": PASSWORD})
    assert r.status_code == 401
    assert r.json()["code"] == "locked"


def test_refresh_hands_out_a_new_pair_and_spends_the_old_one(client, dispatcher):
    session = login(client, "dee")
    r = client.post("/v1/auth/refresh", json={"refresh_token": session["refresh_token"]})
    assert r.status_code == 200
    assert r.json()["refresh_token"] != session["refresh_token"]
    again = client.post("/v1/auth/refresh", json={"refresh_token": session["refresh_token"]})
    assert again.status_code == 401


def test_logout_ends_the_session(client, dispatcher):
    session = login(client, "dee")
    assert client.post("/v1/auth/logout",
                       json={"refresh_token": session["refresh_token"]}).status_code == 204
    r = client.post("/v1/auth/refresh", json={"refresh_token": session["refresh_token"]})
    assert r.status_code == 401


def test_no_token_or_a_bad_one_is_401(client):
    assert client.get("/v1/auth/me").status_code == 401
    assert client.get("/v1/auth/me", headers=bearer("tms_s.forged.sig")).status_code == 401
    assert client.get("/v1/auth/me", headers=bearer("wms_somebody-elses-key")).status_code == 401


def test_a_deactivated_user_loses_their_session(client, db, dispatcher):
    token = login(client, "dee")["token"]
    dispatcher.active = False
    db.commit()
    assert client.get("/v1/auth/me", headers=bearer(token)).status_code == 401
```

**Step 2: Run it to see it fail**

Run: `cd api && .venv/bin/pytest tests/test_auth.py -v`
Expected: FAIL. The login helper's `assert r.status_code == 200` sees `404`, and the other tests get `404` where they expect `401`.

**Step 3: Minimal implementation**

`api/tms/api/deps.py` (replace; the driver branch is used from Task 7):
```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from tms.api.errors import Forbidden, Unauthorised
from tms.db import get_sessionmaker
from tms.models import Driver, User
from tms.services import sessions


def get_db():
    with get_sessionmaker()() as session:
        yield session


DB = Annotated[Session, Depends(get_db)]


@dataclass(slots=True)
class Principal:
    """Whoever is calling: a desktop user or a driver, each with a session."""

    kind: str  # user | driver
    id: int
    name: str
    display_name: str
    role: str
    scopes: list[str]
    user: User | None = None
    driver: Driver | None = None

    def has_scope(self, scope: str) -> bool:
        area = scope.split(":")[0]
        return "*" in self.scopes or scope in self.scopes or f"{area}:*" in self.scopes


def get_principal(db: DB, authorization: Annotated[str | None, Header()] = None) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise Unauthorised("missing bearer token")
    raw = authorization.split(" ", 1)[1].strip()

    if raw.startswith(sessions.ACCESS_PREFIX):
        user_id = sessions.read_access_token(raw)
        user = db.get(User, user_id) if user_id else None
        if user is None or not user.active:
            raise Unauthorised("session expired or invalid")
        return Principal(kind="user", id=user.id, name=user.username,
                         display_name=user.display_name, role=user.role,
                         scopes=sessions.scopes_for(user.role), user=user)

    if raw.startswith(sessions.DRIVER_PREFIX):
        driver_id = sessions.read_driver_token(raw)
        driver = db.get(Driver, driver_id) if driver_id else None
        if driver is None or not driver.active or driver.locked:
            raise Unauthorised("driver session expired or invalid")
        return Principal(kind="driver", id=driver.id, name=driver.code,
                         display_name=driver.name, role="driver",
                         scopes=list(sessions.DRIVER_SCOPES), driver=driver)

    raise Unauthorised("not a Simple TMS token")


Who = Annotated[Principal, Depends(get_principal)]


def require(scope: str):
    """Dependency: the caller must carry this scope."""

    def _check(who: Who) -> Principal:
        if not who.has_scope(scope):
            raise Forbidden(f"you do not have the {scope} scope")
        return who

    return Depends(_check)
```

`api/tms/api/routes/auth.py`:
```python
"""Sign in: desktop users with a username and password, drivers with a code and PIN."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from tms.api.deps import DB, Who
from tms.api.errors import Unauthorised
from tms.models import User
from tms.services import access, sessions

router = APIRouter(prefix="/auth", tags=["auth"])

PASSWORD_TRIES = 5
PASSWORD_LOCK_MINUTES = 15


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(max_length=64)
    password: str = Field(max_length=200)


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    role: str


class SessionOut(BaseModel):
    token: str
    expires_in: int
    refresh_token: str
    user: UserOut


class RefreshIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh_token: str = Field(max_length=200)


class MeOut(BaseModel):
    kind: str
    id: int
    name: str
    display_name: str
    role: str
    scopes: list[str]


def user_out(u: User) -> UserOut:
    return UserOut(id=u.id, username=u.username, display_name=u.display_name, role=u.role)


def _refused(detail: str, code: str, **extra) -> JSONResponse:
    return JSONResponse(status_code=401, content={"detail": detail, "code": code, **extra},
                        headers={"WWW-Authenticate": "Bearer"})


def _is_locked(user: User) -> bool:
    return bool(user.locked_until and user.locked_until > datetime.now(UTC))


@router.post("/login", response_model=SessionOut)
def login(body: LoginIn, db: DB):
    user = db.execute(select(User).where(User.username == body.username)).scalar_one_or_none()
    if user is None or not user.active:
        # an unknown name and a wrong password look alike
        return _refused("wrong username or password", "wrong_password")
    if _is_locked(user):
        return _refused("this account is locked; try again later or ask an admin", "locked")
    if not access.verify_password(body.password, user.password_hash):
        user.failed_attempts = (user.failed_attempts or 0) + 1
        left = PASSWORD_TRIES - user.failed_attempts
        if left <= 0:
            user.locked_until = datetime.now(UTC) + timedelta(minutes=PASSWORD_LOCK_MINUTES)
            user.failed_attempts = 0
            db.commit()
            return _refused(f"too many wrong passwords; locked for {PASSWORD_LOCK_MINUTES} minutes",
                            "locked")
        db.commit()
        return _refused("wrong username or password", "wrong_password", tries_left=left)

    row, refresh = sessions.start_session(db, user)
    user.failed_attempts = 0
    user.locked_until = None
    user.last_login_at = datetime.now(UTC)
    db.commit()
    return SessionOut(token=sessions.issue_access_token(user),
                      expires_in=sessions.ACCESS_TTL_SECONDS, refresh_token=refresh,
                      user=user_out(user))


@router.post("/refresh", response_model=SessionOut)
def refresh(body: RefreshIn, db: DB):
    row = sessions.find_session(db, body.refresh_token)
    user = db.get(User, row.user_id) if row else None
    if row is None or user is None or not user.active:
        raise Unauthorised("session expired or invalid")
    new_refresh = sessions.rotate_session(db, row)
    db.commit()
    return SessionOut(token=sessions.issue_access_token(user),
                      expires_in=sessions.ACCESS_TTL_SECONDS, refresh_token=new_refresh,
                      user=user_out(user))


@router.post("/logout", status_code=204)
def logout(body: RefreshIn, db: DB):
    row = sessions.find_session(db, body.refresh_token)
    if row is not None:
        sessions.revoke_session(db, row)
        db.commit()
    return Response(status_code=204)


@router.get("/me", response_model=MeOut)
def me(who: Who):
    return MeOut(kind=who.kind, id=who.id, name=who.name, display_name=who.display_name,
                 role=who.role, scopes=who.scopes)
```

In `api/tms/api/app.py`, add the import under `from tms.api.deps import DB`:
```python
from tms.api.routes import auth
```
and under `v1 = APIRouter(prefix="/v1")`:
```python
v1.include_router(auth.router)
```

**Step 4: Run it to see it pass**

Run: `cd api && .venv/bin/pytest tests/test_auth.py -v`
Expected: `8 passed`.

**Step 5: Commit**

```
git add api/tms/api/deps.py api/tms/api/routes/auth.py api/tms/api/app.py \
  api/tests/conftest.py api/tests/test_auth.py
git commit -m "Sign desktop users in, with refresh, logout and a lock after five wrong passwords" \
  -m "require(scope) works as in Simple WMS. Roles: admin, manager, dispatcher."
```

---

### Task 5: `tms create-user`

**Files:**
- Create: `api/tms/cli.py`
- Create: `api/tests/test_cli.py`

**Step 1: Write the failing test**

`api/tests/test_cli.py`:
```python
from sqlalchemy import select

from tms.cli import main
from tms.models import User
from tms.services.access import verify_password


def test_create_user_makes_someone_who_can_sign_in(client, db, capsys):
    assert main(["create-user", "--username", "kim", "--role", "manager",
                 "--password", "a long enough password"]) == 0
    assert "user kim" in capsys.readouterr().out
    user = db.execute(select(User).where(User.username == "kim")).scalar_one()
    assert user.role == "manager"
    assert verify_password("a long enough password", user.password_hash)
    r = client.post("/v1/auth/login", json={"username": "kim", "password": "a long enough password"})
    assert r.status_code == 200


def test_create_user_refuses_a_short_password(db, capsys):
    assert main(["create-user", "--username", "kim", "--password", "short"]) == 1
    assert "at least 12" in capsys.readouterr().err
    assert db.execute(select(User)).first() is None


def test_create_user_refuses_a_taken_username(db, admin, capsys):
    assert main(["create-user", "--username", admin.username,
                 "--password", "a long enough password"]) == 1
    assert "already exists" in capsys.readouterr().err
```

**Step 2: Run it to see it fail**

Run: `cd api && .venv/bin/pytest tests/test_cli.py -v`
Expected: ERROR, `ModuleNotFoundError: No module named 'tms.cli'`.

**Step 3: Minimal implementation**

`api/tms/cli.py`:
```python
"""Command line for what has to happen before anyone can sign in."""
from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import select

from tms.db import get_sessionmaker
from tms.models import User
from tms.services import access, sessions

MIN_PASSWORD = 12


def cmd_create_user(args) -> int:
    password = args.password or getpass.getpass("Password: ")
    if len(password) < MIN_PASSWORD:
        print(f"create-user: the password needs at least {MIN_PASSWORD} characters", file=sys.stderr)
        return 1
    with get_sessionmaker()() as db:
        if db.execute(select(User).where(User.username == args.username)).scalar_one_or_none():
            print(f"create-user: {args.username} already exists", file=sys.stderr)
            return 1
        user = User(username=args.username, display_name=args.display_name or args.username,
                    role=args.role, password_hash=access.hash_password(password),
                    failed_attempts=0, active=True)
        db.add(user)
        db.commit()
        print(f"user {user.username} (id {user.id}) created with role {user.role}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tms")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create-user", help="create a desktop user")
    p.add_argument("--username", required=True)
    p.add_argument("--display-name")
    p.add_argument("--role", default="admin", choices=list(sessions.ROLES))
    p.add_argument("--password", help="prompted for if omitted")
    p.set_defaults(func=cmd_create_user)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
```

**Step 4: Run it to see it pass**

Run: `cd api && .venv/bin/pytest tests/test_cli.py -v && .venv/bin/tms --help`
Expected: `3 passed`, then the usage line `usage: tms [-h] {create-user} ...`.

**Step 5: Commit**

```
git add api/tms/cli.py api/tests/test_cli.py
git commit -m "Add tms create-user for the first sign-in"
```

---

### Task 6: Vehicles

**Files:**
- Create: `api/tms/api/schemas.py`
- Create: `api/tms/api/routes/vehicles.py`
- Modify: `api/tms/api/app.py`
- Create: `api/tests/test_vehicles.py`

**Step 1: Write the failing test**

`api/tests/test_vehicles.py`:
```python
VAN = {"rego": "1ab 2cd", "type": "van", "max_kg": "1200", "max_m3": "8.5"}


def make_vehicle(client, headers, body=VAN):
    r = client.post("/v1/vehicles", headers=headers, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_a_manager_adds_a_vehicle_with_decimal_limits(client, manager_headers):
    out = make_vehicle(client, manager_headers)
    assert out["rego"] == "1AB2CD"
    assert out["type"] == "van"
    assert out["max_kg"] == "1200" and out["max_m3"] == "8.5"
    assert out["active"] is True


def test_the_same_plate_twice_is_refused_however_it_is_typed(client, manager_headers):
    make_vehicle(client, manager_headers)
    r = client.post("/v1/vehicles", headers=manager_headers, json={**VAN, "rego": "1AB2CD"})
    assert r.status_code == 422
    assert r.json()["errors"][0]["field"] == "rego"


def test_only_van_rigid_or_ute(client, manager_headers):
    r = client.post("/v1/vehicles", headers=manager_headers, json={**VAN, "type": "bicycle"})
    assert r.status_code == 422


def test_limits_must_be_positive(client, manager_headers):
    r = client.post("/v1/vehicles", headers=manager_headers, json={**VAN, "max_kg": "0"})
    assert r.status_code == 422


def test_a_dispatcher_sees_vehicles_but_cannot_add_one(client, manager_headers, dispatcher_headers):
    make_vehicle(client, manager_headers)
    r = client.get("/v1/vehicles", headers=dispatcher_headers)
    assert r.status_code == 200 and [v["rego"] for v in r.json()["items"]] == ["1AB2CD"]
    assert client.post("/v1/vehicles", headers=dispatcher_headers, json=VAN).status_code == 403


def test_update_changes_type_and_limits(client, manager_headers):
    make_vehicle(client, manager_headers)
    r = client.patch("/v1/vehicles/1ab2cd", headers=manager_headers,
                     json={"type": "rigid", "max_kg": "8000.5"})
    assert r.status_code == 200
    assert r.json()["type"] == "rigid" and r.json()["max_kg"] == "8000.5"
    assert r.json()["max_m3"] == "8.5"


def test_deactivated_not_deleted(client, manager_headers):
    make_vehicle(client, manager_headers)
    r = client.post("/v1/vehicles/1AB2CD/deactivate", headers=manager_headers)
    assert r.json()["active"] is False
    assert client.get("/v1/vehicles?active=true", headers=manager_headers).json()["total"] == 0
    assert client.get("/v1/vehicles/1AB2CD", headers=manager_headers).status_code == 200
    r = client.post("/v1/vehicles/1AB2CD/reactivate", headers=manager_headers)
    assert r.json()["active"] is True
    assert client.delete("/v1/vehicles/1AB2CD", headers=manager_headers).status_code == 405


def test_an_unknown_vehicle_is_404(client, manager_headers):
    assert client.get("/v1/vehicles/NOPE", headers=manager_headers).status_code == 404
```

**Step 2: Run it to see it fail**

Run: `cd api && .venv/bin/pytest tests/test_vehicles.py -v`
Expected: FAIL, `404` where `201`, `422` or `200` are expected.

**Step 3: Minimal implementation**

`api/tms/api/schemas.py`:
```python
from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, PlainSerializer


def fmt_dec(d: Decimal) -> str:
    """Decimal to a plain string with no trailing zeros: 1200.000000 -> "1200"."""
    return format(d.normalize(), "f") if d else "0"


# Weights and volumes go out as strings, like Simple WMS quantities.
Dec = Annotated[Decimal, PlainSerializer(fmt_dec, return_type=str, when_used="json")]


class Page[T](BaseModel):
    items: list[T]
    total: int
```

`api/tms/api/routes/vehicles.py`:
```python
"""Vehicles: created, changed, deactivated. Never deleted."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from tms.api.deps import DB, Principal, require
from tms.api.errors import FieldError, NotFound
from tms.api.schemas import Dec, Page
from tms.models import Vehicle

router = APIRouter(tags=["vehicles"])

VehicleType = Literal["van", "rigid", "ute"]


def normal_rego(value: str) -> str:
    """'abc 123' and 'ABC123' are the same plate."""
    return "".join(value.split()).upper()


class VehicleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rego: str = Field(min_length=1, max_length=12, pattern=r"^[A-Z0-9-]+$")
    type: VehicleType
    max_kg: Decimal = Field(gt=0, max_digits=12, decimal_places=3)
    max_m3: Decimal = Field(gt=0, max_digits=12, decimal_places=3)

    @field_validator("rego", mode="before")
    @classmethod
    def _rego(cls, v):
        return normal_rego(v) if isinstance(v, str) else v


class VehiclePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: VehicleType | None = None
    max_kg: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=3)
    max_m3: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=3)


class VehicleOut(BaseModel):
    id: int
    rego: str
    type: str
    max_kg: Dec
    max_m3: Dec
    active: bool
    created_at: datetime


def vehicle_out(v: Vehicle) -> VehicleOut:
    return VehicleOut(id=v.id, rego=v.rego, type=v.type, max_kg=v.max_kg, max_m3=v.max_m3,
                      active=v.active, created_at=v.created_at)


def _get(db, rego: str) -> Vehicle:
    v = db.execute(select(Vehicle).where(Vehicle.rego == normal_rego(rego))).scalar_one_or_none()
    if v is None:
        raise NotFound(f"no vehicle {rego}")
    return v


@router.post("/vehicles", status_code=201, response_model=VehicleOut)
def create_vehicle(body: VehicleIn, db: DB, who: Principal = require("fleet:write")):
    if db.execute(select(Vehicle).where(Vehicle.rego == body.rego)).scalar_one_or_none():
        raise FieldError("rego", f"vehicle {body.rego} already exists")
    v = Vehicle(rego=body.rego, type=body.type, max_kg=body.max_kg, max_m3=body.max_m3,
                active=True)
    db.add(v)
    db.commit()
    return JSONResponse(status_code=201, content=vehicle_out(v).model_dump(mode="json"))


@router.get("/vehicles", response_model=Page[VehicleOut])
def list_vehicles(db: DB, active: bool | None = None, who: Principal = require("fleet:read")):
    q = select(Vehicle)
    if active is not None:
        q = q.where(Vehicle.active.is_(active))
    rows = db.execute(q.order_by(Vehicle.rego)).scalars().all()
    return Page(items=[vehicle_out(v) for v in rows], total=len(rows))


@router.get("/vehicles/{rego}", response_model=VehicleOut)
def get_vehicle(rego: str, db: DB, who: Principal = require("fleet:read")):
    return vehicle_out(_get(db, rego))


@router.patch("/vehicles/{rego}", response_model=VehicleOut)
def update_vehicle(rego: str, body: VehiclePatch, db: DB, who: Principal = require("fleet:write")):
    v = _get(db, rego)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(v, field, value)
    db.commit()
    return vehicle_out(v)


@router.post("/vehicles/{rego}/deactivate", response_model=VehicleOut)
def deactivate_vehicle(rego: str, db: DB, who: Principal = require("fleet:write")):
    """Off the road for good, or sold. Its history stays."""
    v = _get(db, rego)
    v.active = False
    db.commit()
    return vehicle_out(v)


@router.post("/vehicles/{rego}/reactivate", response_model=VehicleOut)
def reactivate_vehicle(rego: str, db: DB, who: Principal = require("fleet:write")):
    v = _get(db, rego)
    v.active = True
    db.commit()
    return vehicle_out(v)
```

In `api/tms/api/app.py`, change the routes import to:
```python
from tms.api.routes import auth, vehicles
```
and add after `v1.include_router(auth.router)`:
```python
v1.include_router(vehicles.router)
```

**Step 4: Run it to see it pass**

Run: `cd api && .venv/bin/pytest tests/test_vehicles.py -v`
Expected: `8 passed`.

**Step 5: Commit**

```
git add api/tms/api/schemas.py api/tms/api/routes/vehicles.py api/tms/api/app.py \
  api/tests/test_vehicles.py
git commit -m "Add vehicles: van, rigid or ute, with weight and volume limits" \
  -m "Deactivated, never deleted. The rego is stored upper case without spaces and is the key in the URL."
```

---

### Task 7: Drivers and driver sign-in

**Files:**
- Create: `api/tms/api/routes/drivers.py`
- Modify: `api/tms/api/routes/auth.py`
- Modify: `api/tms/api/app.py`
- Create: `api/tests/test_drivers.py`

**Step 1: Write the failing test**

`api/tests/test_drivers.py`:
```python
from conftest import bearer

ALEX = {"code": "drv-01", "name": "Alex T.", "pin": "2468"}


def make_driver(client, headers, body=ALEX):
    r = client.post("/v1/drivers", headers=headers, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_a_manager_adds_a_driver_and_the_pin_is_never_shown(client, manager_headers):
    out = make_driver(client, manager_headers)
    assert out["code"] == "drv-01" and out["name"] == "Alex T."
    assert out["active"] is True and out["locked"] is False
    assert "pin" not in out and "pin_hash" not in out


def test_a_dispatcher_can_see_drivers_but_not_change_them(client, manager_headers, dispatcher_headers):
    make_driver(client, manager_headers)
    r = client.get("/v1/drivers", headers=dispatcher_headers)
    assert r.status_code == 200 and r.json()["total"] == 1
    r = client.post("/v1/drivers", headers=dispatcher_headers,
                    json={"code": "drv-02", "name": "Sam", "pin": "1357"})
    assert r.status_code == 403


def test_a_code_is_used_once(client, manager_headers):
    make_driver(client, manager_headers)
    r = client.post("/v1/drivers", headers=manager_headers, json=ALEX)
    assert r.status_code == 422
    assert r.json()["errors"][0]["field"] == "code"


def test_a_pin_is_four_to_eight_digits(client, manager_headers):
    r = client.post("/v1/drivers", headers=manager_headers, json={**ALEX, "pin": "12a4"})
    assert r.status_code == 422


def test_update_changes_the_name_and_pin(client, manager_headers):
    make_driver(client, manager_headers)
    r = client.patch("/v1/drivers/drv-01", headers=manager_headers,
                     json={"name": "Alex Tran", "pin": "8642"})
    assert r.status_code == 200 and r.json()["name"] == "Alex Tran"
    r = client.post("/v1/auth/driver-login", json={"code": "drv-01", "pin": "8642"})
    assert r.status_code == 200


def test_deactivated_not_deleted(client, manager_headers):
    make_driver(client, manager_headers)
    r = client.post("/v1/drivers/drv-01/deactivate", headers=manager_headers)
    assert r.status_code == 200 and r.json()["active"] is False
    assert client.get("/v1/drivers?active=false", headers=manager_headers).json()["total"] == 1
    assert client.get("/v1/drivers?active=true", headers=manager_headers).json()["total"] == 0
    r = client.post("/v1/auth/driver-login", json={"code": "drv-01", "pin": "2468"})
    assert r.status_code == 401
    r = client.post("/v1/drivers/drv-01/reactivate", headers=manager_headers)
    assert r.json()["active"] is True
    assert client.delete("/v1/drivers/drv-01", headers=manager_headers).status_code == 405


def test_driver_signs_in_with_code_and_pin(client, manager_headers):
    make_driver(client, manager_headers)
    r = client.post("/v1/auth/driver-login", json={"code": "drv-01", "pin": "2468"})
    assert r.status_code == 200
    token = r.json()["token"]
    assert token.startswith("tms_d.")
    me = client.get("/v1/auth/me", headers=bearer(token)).json()
    assert me["kind"] == "driver" and me["name"] == "drv-01" and me["scopes"] == ["fleet:read"]
    # a driver picks a truck, so may see the fleet, but not change it
    assert client.get("/v1/vehicles", headers=bearer(token)).status_code == 200
    r = client.post("/v1/drivers", headers=bearer(token),
                    json={"code": "drv-02", "name": "Sam", "pin": "1357"})
    assert r.status_code == 403


def test_five_wrong_pins_lock_the_driver_until_a_manager_unlocks(client, manager_headers):
    make_driver(client, manager_headers)
    for left in (4, 3, 2, 1):
        r = client.post("/v1/auth/driver-login", json={"code": "drv-01", "pin": "0000"})
        assert r.status_code == 401 and r.json()["tries_left"] == left
    r = client.post("/v1/auth/driver-login", json={"code": "drv-01", "pin": "0000"})
    assert r.status_code == 401 and r.json()["code"] == "locked"
    r = client.post("/v1/auth/driver-login", json={"code": "drv-01", "pin": "2468"})
    assert r.json()["code"] == "locked"

    r = client.post("/v1/drivers/drv-01/unlock", headers=manager_headers)
    assert r.json()["locked"] is False and r.json()["failed_attempts"] == 0
    r = client.post("/v1/auth/driver-login", json={"code": "drv-01", "pin": "2468"})
    assert r.status_code == 200


def test_a_right_pin_clears_the_count(client, manager_headers, db):
    make_driver(client, manager_headers)
    client.post("/v1/auth/driver-login", json={"code": "drv-01", "pin": "0000"})
    client.post("/v1/auth/driver-login", json={"code": "drv-01", "pin": "2468"})
    r = client.get("/v1/drivers/drv-01", headers=manager_headers)
    assert r.json()["failed_attempts"] == 0
```

**Step 2: Run it to see it fail**

Run: `cd api && .venv/bin/pytest tests/test_drivers.py -v`
Expected: FAIL, `404` from `POST /v1/drivers` in `make_driver`.

**Step 3: Minimal implementation**

`api/tms/api/routes/drivers.py`:
```python
"""Drivers: created, changed, deactivated. Never deleted."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from tms.api.deps import DB, Principal, require
from tms.api.errors import FieldError, NotFound
from tms.api.schemas import Page
from tms.models import Driver
from tms.services.access import hash_password

router = APIRouter(tags=["drivers"])

PIN = r"^[0-9]{4,8}$"


class DriverIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    code: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(min_length=1, max_length=120)
    pin: str = Field(pattern=PIN)


class DriverPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    pin: str | None = Field(default=None, pattern=PIN)


class DriverOut(BaseModel):
    id: int
    code: str
    name: str
    active: bool
    locked: bool
    failed_attempts: int
    created_at: datetime


def driver_out(d: Driver) -> DriverOut:
    return DriverOut(id=d.id, code=d.code, name=d.name, active=d.active, locked=d.locked,
                     failed_attempts=d.failed_attempts, created_at=d.created_at)


def _get(db, code: str) -> Driver:
    d = db.execute(select(Driver).where(Driver.code == code)).scalar_one_or_none()
    if d is None:
        raise NotFound(f"no driver {code}")
    return d


@router.post("/drivers", status_code=201, response_model=DriverOut)
def create_driver(body: DriverIn, db: DB, who: Principal = require("fleet:write")):
    if db.execute(select(Driver).where(Driver.code == body.code)).scalar_one_or_none():
        raise FieldError("code", f"driver {body.code} already exists")
    d = Driver(code=body.code, name=body.name, pin_hash=hash_password(body.pin),
               failed_attempts=0, locked=False, active=True)
    db.add(d)
    db.commit()
    return JSONResponse(status_code=201, content=driver_out(d).model_dump(mode="json"))


@router.get("/drivers", response_model=Page[DriverOut])
def list_drivers(db: DB, active: bool | None = None, who: Principal = require("fleet:read")):
    q = select(Driver)
    if active is not None:
        q = q.where(Driver.active.is_(active))
    rows = db.execute(q.order_by(Driver.code)).scalars().all()
    return Page(items=[driver_out(d) for d in rows], total=len(rows))


@router.get("/drivers/{code}", response_model=DriverOut)
def get_driver(code: str, db: DB, who: Principal = require("fleet:read")):
    return driver_out(_get(db, code))


@router.patch("/drivers/{code}", response_model=DriverOut)
def update_driver(code: str, body: DriverPatch, db: DB, who: Principal = require("fleet:write")):
    d = _get(db, code)
    if body.name is not None:
        d.name = body.name
    if body.pin is not None:
        d.pin_hash = hash_password(body.pin)
    db.commit()
    return driver_out(d)


@router.post("/drivers/{code}/deactivate", response_model=DriverOut)
def deactivate_driver(code: str, db: DB, who: Principal = require("fleet:write")):
    """Deactivated, never deleted: their runs and deliveries stay theirs."""
    d = _get(db, code)
    d.active = False
    db.commit()
    return driver_out(d)


@router.post("/drivers/{code}/reactivate", response_model=DriverOut)
def reactivate_driver(code: str, db: DB, who: Principal = require("fleet:write")):
    d = _get(db, code)
    d.active = True
    db.commit()
    return driver_out(d)


@router.post("/drivers/{code}/unlock", response_model=DriverOut)
def unlock_driver(code: str, db: DB, who: Principal = require("fleet:write")):
    d = _get(db, code)
    d.locked = False
    d.failed_attempts = 0
    db.commit()
    return driver_out(d)
```

In `api/tms/api/routes/auth.py`:

- change `from tms.models import User` to `from tms.models import Driver, User`;
- add `PIN_TRIES = 5` after `PASSWORD_LOCK_MINUTES = 15`;
- add these two models before `def user_out`:
```python
class DriverLoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    code: str = Field(max_length=32)
    pin: str = Field(max_length=8)


class DriverSessionOut(BaseModel):
    token: str
    expires_in: int
    driver: MeOut
```
- and append at the end of the file:
```python
@router.post("/driver-login", response_model=DriverSessionOut)
def driver_login(body: DriverLoginIn, db: DB):
    """Five wrong PINs lock the driver until a manager unlocks them."""
    driver = db.execute(select(Driver).where(Driver.code == body.code)).scalar_one_or_none()
    if driver is None or not driver.active:
        return _refused("no active driver with that code", "unknown_driver")
    if driver.locked:
        return _refused(f"{driver.code} is locked after too many wrong PINs; a manager can unlock it",
                        "locked")
    if not access.verify_password(body.pin, driver.pin_hash):
        driver.failed_attempts += 1
        if driver.failed_attempts >= PIN_TRIES:
            driver.locked = True
            db.commit()
            return _refused(f"wrong PIN {PIN_TRIES} times; {driver.code} is locked until a "
                            f"manager unlocks it", "locked")
        db.commit()
        return _refused("wrong PIN", "wrong_pin", tries_left=PIN_TRIES - driver.failed_attempts)
    driver.failed_attempts = 0
    db.commit()
    return DriverSessionOut(
        token=sessions.issue_driver_token(driver.id), expires_in=sessions.DRIVER_TTL_SECONDS,
        driver=MeOut(kind="driver", id=driver.id, name=driver.code, display_name=driver.name,
                     role="driver", scopes=list(sessions.DRIVER_SCOPES)),
    )
```

In `api/tms/api/app.py`, change the routes import to:
```python
from tms.api.routes import auth, drivers, vehicles
```
and make the router lines:
```python
v1.include_router(auth.router)
v1.include_router(drivers.router)
v1.include_router(vehicles.router)
```

**Step 4: Run it to see it pass**

Run: `cd api && .venv/bin/pytest tests/test_drivers.py tests/test_auth.py -v`
Expected: `17 passed`.

**Step 5: Commit**

```
git add api/tms/api/routes/drivers.py api/tms/api/routes/auth.py api/tms/api/app.py \
  api/tests/test_drivers.py
git commit -m "Add drivers, and driver sign-in with code and PIN" \
  -m "Five wrong PINs lock a driver until a manager unlocks them, as with Simple WMS operators. Deactivated, never deleted."
```

---

### Task 8: The signature check and the Simple WMS client

**Files:**
- Create: `api/tms/services/signature.py`, `api/tms/services/wms_client.py`
- Modify: `api/tests/conftest.py` (imports and a stand-in Simple WMS)
- Create: `api/tests/test_signature.py`, `api/tests/test_wms_client.py`

**Step 1: Write the failing tests**

In `api/tests/conftest.py`, make the imports at the top:
```python
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace
from urllib.parse import unquote, urlparse

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
```
and append:
```python


# --- a stand-in Simple WMS ----------------------------------------------------

class _FakeWms(BaseHTTPRequestHandler):
    deliveries: dict = {}
    statuses: list = []
    calls: list = []

    def do_GET(self):
        url = urlparse(self.path)
        self.calls.append({"path": url.path, "query": url.query,
                           "authorization": self.headers.get("Authorization")})
        ref = unquote(url.path.rsplit("/", 1)[-1])
        status = self.statuses.pop(0) if self.statuses else 200
        body = self.deliveries.get(ref)
        if status == 200 and body is None:
            status = 404
            body = {"detail": f"no delivery {ref}"}
        payload = json.dumps(body or {"detail": "unavailable"}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@pytest.fixture
def wms(monkeypatch):
    """Answers GET /v1/deliveries/{ref} from `deliveries`. Queue a status in
    `statuses` (e.g. 503) to make the next call fail."""
    from tms.config import get_settings

    handler = type("Handler", (_FakeWms,), {"deliveries": {}, "statuses": [], "calls": []})
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(get_settings(), "wms_url", f"http://127.0.0.1:{server.server_port}")
    yield SimpleNamespace(deliveries=handler.deliveries, statuses=handler.statuses,
                          calls=handler.calls)
    server.shutdown()
    server.server_close()
```

`api/tests/test_signature.py`:
```python
from tms.services.signature import sign, verify


def test_signature_is_sha256_hex_of_the_raw_body():
    body = b'{"event_id":"x"}'
    good = sign("s3cret", body)
    assert good.startswith("sha256=") and len(good) == len("sha256=") + 64
    assert verify("s3cret", body, good)


def test_anything_else_does_not_verify():
    body = b'{"event_id":"x"}'
    good = sign("s3cret", body)
    assert not verify("s3cret", body + b" ", good)
    assert not verify("other", body, good)
    assert not verify("s3cret", body, None)
    assert not verify("", body, good)
    assert not verify("s3cret", body, "sha256=é")
```

`api/tests/test_wms_client.py`:
```python
import pytest

from tms.services import wms_client


def test_get_delivery_asks_with_our_key_and_the_owner(wms):
    wms.deliveries["0080/12"] = {"external_ref": "0080/12", "ship_to": {"name": "Ballarat Hardware"}}
    out = wms_client.get_delivery("0080/12", "ACME")
    assert out["ship_to"]["name"] == "Ballarat Hardware"
    assert wms.calls == [{"path": "/v1/deliveries/0080%2F12", "query": "owner=ACME",
                          "authorization": "Bearer wms_test-key-for-the-tms"}]


def test_anything_but_200_is_unavailable(wms):
    with pytest.raises(wms_client.WmsUnavailable, match="HTTP 404"):
        wms_client.get_delivery("NOT-THERE", "DEFAULT")
    wms.deliveries["0080012345"] = {}
    wms.statuses.append(503)
    with pytest.raises(wms_client.WmsUnavailable, match="HTTP 503"):
        wms_client.get_delivery("0080012345", "DEFAULT")


def test_nobody_listening_is_unavailable(monkeypatch):
    from tms.config import get_settings

    monkeypatch.setattr(get_settings(), "wms_url", "http://127.0.0.1:9")
    with pytest.raises(wms_client.WmsUnavailable):
        wms_client.get_delivery("0080012345", "DEFAULT")


def test_not_configured_is_unavailable(monkeypatch):
    from tms.config import get_settings

    monkeypatch.setattr(get_settings(), "wms_url", "")
    with pytest.raises(wms_client.WmsUnavailable, match="not set"):
        wms_client.get_delivery("0080012345", "DEFAULT")
```

**Step 2: Run them to see them fail**

Run: `cd api && .venv/bin/pytest tests/test_signature.py tests/test_wms_client.py -v`
Expected: ERROR, `ModuleNotFoundError: No module named 'tms.services.signature'` and `ImportError: cannot import name 'wms_client'`.

**Step 3: Minimal implementation**

`api/tms/services/signature.py`:
```python
"""Simple WMS signs each event body: X-WMS-Signature: sha256=<hex HMAC-SHA256
of the raw body, keyed with the subscriber's secret>."""
from __future__ import annotations

import hashlib
import hmac


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify(secret: str, body: bytes, header: str | None) -> bool:
    if not secret or not header:
        return False
    # bytes, so a header with odd characters is a mismatch, not a TypeError
    return hmac.compare_digest(sign(secret, body).encode(), header.strip().encode())
```

`api/tms/services/wms_client.py`:
```python
"""The calls Simple TMS makes to Simple WMS. Step 1 only reads a delivery."""
from __future__ import annotations

from urllib.parse import quote

import httpx

from tms.config import get_settings


class WmsUnavailable(Exception):
    """Simple WMS could not be asked, or did not answer with the delivery."""


def get_delivery(ref: str, owner: str) -> dict:
    """GET /v1/deliveries/{ref}?owner=... with our WMS key (scope tasks:read)."""
    settings = get_settings()
    if not settings.wms_url or not settings.wms_api_key:
        raise WmsUnavailable("TMS_WMS_URL and TMS_WMS_API_KEY are not set")
    url = f"{settings.wms_url.rstrip('/')}/v1/deliveries/{quote(ref, safe='')}"
    try:
        resp = httpx.get(url, params={"owner": owner},
                         headers={"Authorization": f"Bearer {settings.wms_api_key}",
                                  "User-Agent": "simple-tms/0.1"},
                         timeout=settings.wms_timeout_seconds)
    except httpx.HTTPError as exc:
        raise WmsUnavailable(f"{type(exc).__name__}: {exc}"[:300]) from exc
    if resp.status_code != 200:
        raise WmsUnavailable(f"HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.json()
```

**Step 4: Run them to see them pass**

Run: `cd api && .venv/bin/pytest tests/test_signature.py tests/test_wms_client.py -v`
Expected: `6 passed`.

**Step 5: Commit**

```
git add api/tms/services/signature.py api/tms/services/wms_client.py api/tests/conftest.py \
  api/tests/test_signature.py api/tests/test_wms_client.py
git commit -m "Check Simple WMS signatures and read a delivery from its API" \
  -m "sha256=<hex HMAC of the raw body>, compared in constant time. The client asks GET /v1/deliveries/{ref} with a tasks:read key and gives up after 5 seconds."
```

---

### Task 9: `POST /v1/wms/events` makes consignments

**Files:**
- Create: `api/tms/services/consignments.py`
- Create: `api/tms/api/routes/wms_events.py`
- Modify: `api/tms/api/app.py`
- Create: `api/tests/wms_samples.py`, `api/tests/test_wms_events.py`

**Step 1: Write the failing test**

`api/tests/wms_samples.py`:
```python
"""What Simple WMS sends and answers, shaped as its code builds them
(simple-wms api/wms/services/events.py emit(), outbound.py finish_packing(),
api/routes/outbound.py DeliveryOut)."""
import json
import uuid

from tms.services.signature import sign


def packed_event(ref: str = "0080012345", event_id: str | None = None, owner: str = "DEFAULT") -> dict:
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "event_type": "delivery.packed",
        "occurred_at": "2026-10-10T03:12:00Z",
        "warehouse": "BAL-WH01",
        "owner": owner,
        "external_ref": ref,
        "data": {
            "delivery_ref": ref,
            "packages": [
                {"package_no": 1, "type": "carton", "weight_kg": "9.5", "length_cm": "60",
                 "width_cm": "40", "height_cm": "40", "sscc": "093123450000000012",
                 "container_id": None,
                 "contents": [{"delivery_line": 10, "sku": "GLV-L", "batch": None,
                               "qty": "20", "uom": "PR"}]},
                {"package_no": 2, "type": "satchel", "weight_kg": None, "length_cm": None,
                 "width_cm": None, "height_cm": None, "sscc": None, "container_id": None,
                 "contents": [{"delivery_line": 20, "sku": "TPE-48", "batch": None,
                               "qty": "6", "uom": "RL"}]},
            ],
        },
    }


def delivery(ref: str = "0080012345") -> dict:
    """The parts of GET /v1/deliveries/{ref} that Simple TMS reads."""
    return {
        "wms_id": "17", "external_ref": ref, "owner": "DEFAULT", "warehouse": "BAL-WH01",
        "status": "packed", "required_by": "2026-10-11",
        "ship_to": {"name": "Ballarat Hardware", "address": "12 Sturt St", "suburb": "Ballarat",
                    "state": "VIC", "postcode": "3350", "country": "AU", "phone": "03 5300 0000"},
        "lines": [], "packages": [],
    }


def body_of(event: dict) -> bytes:
    # compact, as the WMS worker sends it
    return json.dumps(event, separators=(",", ":")).encode()


def post_event(client, event: dict, secret: str, signature: str | None = "sign"):
    body = body_of(event)
    headers = {"Content-Type": "application/json", "X-WMS-Event-Id": event["event_id"],
               "X-WMS-Event-Type": event["event_type"]}
    if signature == "sign":
        headers["X-WMS-Signature"] = sign(secret, body)
    elif signature is not None:
        headers["X-WMS-Signature"] = signature
    return client.post("/v1/wms/events", content=body, headers=headers)
```

`api/tests/test_wms_events.py`:
```python
import json
import logging
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from conftest import WEBHOOK_SECRET
from tms.models import Consignment, InboundEvent
from tms.services.signature import sign
from wms_samples import body_of, delivery, packed_event, post_event


def count(db, model) -> int:
    return db.execute(select(func.count()).select_from(model)).scalar_one()


def test_delivery_packed_makes_a_consignment_with_its_packages(client, db, wms):
    wms.deliveries["0080012345"] = delivery()
    event = packed_event()
    r = post_event(client, event, WEBHOOK_SECRET)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "created"

    c = db.execute(select(Consignment)).scalar_one()
    assert r.json()["consignment_id"] == c.id
    assert (c.wms_ref, c.owner, c.warehouse, c.status) == ("0080012345", "DEFAULT", "BAL-WH01", "packed")
    assert c.customer == "Ballarat Hardware"
    assert c.ship_to["postcode"] == "3350"
    assert str(c.required_by) == "2026-10-11"
    assert [p.package_no for p in c.packages] == [1, 2]
    assert c.packages[0].weight_kg == Decimal("9.5")
    assert c.packages[0].sscc == "093123450000000012"
    assert c.packages[1].weight_kg is None
    assert [(e.kind, e.detail["wms_event_id"]) for e in c.events] == [("packed", event["event_id"])]

    # it asked the WMS for the delivery, with our key and the event's owner
    assert wms.calls == [{"path": "/v1/deliveries/0080012345", "query": "owner=DEFAULT",
                          "authorization": "Bearer wms_test-key-for-the-tms"}]


def test_a_repeated_event_id_does_nothing(client, db, wms):
    wms.deliveries["0080012345"] = delivery()
    event = packed_event()
    assert post_event(client, event, WEBHOOK_SECRET).json()["status"] == "created"
    r = post_event(client, event, WEBHOOK_SECRET)
    assert r.status_code == 200
    assert r.json()["status"] == "duplicate"
    assert count(db, Consignment) == 1
    assert len(wms.calls) == 1


def test_a_second_event_for_the_same_delivery_does_not_make_a_second_consignment(client, db, wms):
    wms.deliveries["0080012345"] = delivery()
    post_event(client, packed_event(), WEBHOOK_SECRET)
    r = post_event(client, packed_event(), WEBHOOK_SECRET)
    assert r.status_code == 200 and r.json()["status"] == "ignored"
    assert count(db, Consignment) == 1


@pytest.mark.parametrize("signature", [None, "sha256=" + "0" * 64, "not even close"])
def test_a_missing_or_wrong_signature_is_401_and_does_nothing(client, db, wms, signature):
    wms.deliveries["0080012345"] = delivery()
    r = post_event(client, packed_event(), WEBHOOK_SECRET, signature=signature)
    assert r.status_code == 401
    assert count(db, Consignment) == 0 and count(db, InboundEvent) == 0
    assert wms.calls == []


def test_signed_with_another_secret_is_401(client, db, wms):
    r = post_event(client, packed_event(), "somebody-elses-secret-0123456789")
    assert r.status_code == 401


def test_an_event_we_do_not_act_on_is_2xx_logged_and_remembered(client, db, wms, caplog):
    event = {**packed_event(), "event_type": "delivery.picked",
             "data": {"delivery_ref": "0080012345", "complete": True, "lines": []}}
    with caplog.at_level(logging.INFO, logger="tms.wms"):
        r = post_event(client, event, WEBHOOK_SECRET)
    assert r.status_code == 200 and r.json()["status"] == "ignored"
    assert "delivery.picked" in caplog.text
    assert count(db, Consignment) == 0
    assert db.get(InboundEvent, event["event_id"]).outcome == "ignored"
    assert wms.calls == []


def test_wms_unreachable_is_503_and_nothing_is_kept_so_the_retry_works(client, db, wms):
    wms.deliveries["0080012345"] = delivery()
    wms.statuses.append(503)
    event = packed_event()
    r = post_event(client, event, WEBHOOK_SECRET)
    assert r.status_code == 503
    assert r.json()["code"] == "wms_unavailable"
    assert count(db, Consignment) == 0 and count(db, InboundEvent) == 0

    # the WMS worker sends the same event again a minute later
    r = post_event(client, event, WEBHOOK_SECRET)
    assert r.status_code == 200 and r.json()["status"] == "created"


def test_a_delivery_the_wms_will_not_show_us_is_503(client, db, wms):
    r = post_event(client, packed_event("NOT-THERE"), WEBHOOK_SECRET)
    assert r.status_code == 503
    assert "404" in r.json()["detail"]


def test_no_secret_configured_is_503(client, db, wms, monkeypatch):
    from tms.config import get_settings

    monkeypatch.setattr(get_settings(), "wms_webhook_secret", "")
    r = post_event(client, packed_event(), WEBHOOK_SECRET)
    assert r.status_code == 503 and r.json()["code"] == "not_configured"


def test_a_signed_body_that_is_not_an_event_is_422(client, db, wms):
    body = b'{"hello":"world"}'
    r = client.post("/v1/wms/events", content=body,
                    headers={"X-WMS-Signature": sign(WEBHOOK_SECRET, body)})
    assert r.status_code == 422


def test_the_signature_is_over_the_exact_bytes(client, db, wms):
    """Re-serialising the JSON would change the bytes; we check what arrived."""
    wms.deliveries["0080012345"] = delivery()
    event = packed_event()
    pretty = json.dumps(event, indent=2).encode()
    r = client.post("/v1/wms/events", content=pretty,
                    headers={"X-WMS-Signature": sign(WEBHOOK_SECRET, body_of(event))})
    assert r.status_code == 401
```

**Step 2: Run it to see it fail**

Run: `cd api && .venv/bin/pytest tests/test_wms_events.py -v`
Expected: FAIL, every request answers `404`.

**Step 3: Minimal implementation**

`api/tms/services/consignments.py`:
```python
"""Consignments from Simple WMS deliveries."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from tms.models import Consignment, ConsignmentEvent, Package


def _dec(value) -> Decimal | None:
    # the WMS sends decimals as strings ("9.5"); never let a float in
    return None if value is None else Decimal(str(value))


def from_delivery_packed(db: Session, *, event_id: str, occurred_at: datetime, owner: str,
                         warehouse: str, ref: str, packages: list[dict],
                         delivery: dict) -> Consignment:
    """The packages come from the event, the ship-to from the delivery."""
    ship_to = delivery.get("ship_to") or {}
    required_by = delivery.get("required_by")
    consignment = Consignment(
        wms_ref=ref, warehouse=warehouse, owner=owner,
        customer=ship_to.get("name") or ref, ship_to=ship_to,
        required_by=date.fromisoformat(required_by) if required_by else None,
        status="packed",
    )
    for p in packages:
        consignment.packages.append(Package(
            package_no=int(p["package_no"]), type=p.get("type") or "carton",
            weight_kg=_dec(p.get("weight_kg")), length_cm=_dec(p.get("length_cm")),
            width_cm=_dec(p.get("width_cm")), height_cm=_dec(p.get("height_cm")),
            sscc=p.get("sscc"),
        ))
    consignment.events.append(ConsignmentEvent(
        kind="packed", at=occurred_at,
        detail={"wms_event_id": event_id, "packages": len(packages)},
    ))
    db.add(consignment)
    db.flush()
    return consignment
```

`api/tms/api/routes/wms_events.py`:
```python
"""Simple WMS events. Simple TMS is an ordinary subscriber: the body is
signed, an event id already seen is ignored, and anything we do not act on
still gets a 2xx so the WMS stops retrying it."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from tms.api.deps import DB
from tms.api.errors import Unauthorised, field_errors
from tms.config import get_settings
from tms.models import Consignment, InboundEvent
from tms.services import consignments, signature, wms_client

router = APIRouter(tags=["wms"])
log = logging.getLogger("tms.wms")

HANDLED = {"delivery.packed"}


class WmsEvent(BaseModel):
    """Simple WMS's outbound envelope. Unknown fields are ignored, so a newer
    WMS does not break us."""

    model_config = ConfigDict(extra="ignore")
    event_id: uuid.UUID
    event_type: str
    occurred_at: datetime
    warehouse: str | None = None
    owner: str = "DEFAULT"
    external_ref: str | None = None
    data: dict = Field(default_factory=dict)


class EventReply(BaseModel):
    status: str  # created, ignored, duplicate
    event_id: uuid.UUID
    consignment_id: int | None = None


async def raw_body(request: Request) -> bytes:
    """The bytes exactly as signed. Parsed only after the signature checks out."""
    return await request.body()


def _record(db, event: WmsEvent, outcome: str, consignment_id: int | None = None) -> EventReply:
    db.add(InboundEvent(event_id=event.event_id, event_type=event.event_type,
                        external_ref=event.external_ref, outcome=outcome))
    try:
        db.commit()
    except IntegrityError:
        # the same event arrived twice at once and the other one won
        db.rollback()
        return EventReply(status="duplicate", event_id=event.event_id)
    return EventReply(status=outcome, event_id=event.event_id, consignment_id=consignment_id)


@router.post("/wms/events", response_model=EventReply)
def receive_wms_event(
    db: DB, body: Annotated[bytes, Depends(raw_body)],
    x_wms_signature: Annotated[str | None, Header()] = None,
):
    secret = get_settings().wms_webhook_secret
    if not secret:
        # 503, not 401: the WMS keeps the event and retries once it is set
        return JSONResponse(status_code=503, content={
            "detail": "TMS_WMS_WEBHOOK_SECRET is not set", "code": "not_configured"})
    if not signature.verify(secret, body, x_wms_signature):
        raise Unauthorised("missing or wrong X-WMS-Signature")
    try:
        event = WmsEvent.model_validate_json(body)
    except ValidationError as exc:
        return JSONResponse(status_code=422, content={"errors": field_errors(exc.errors())})

    if db.get(InboundEvent, event.event_id) is not None:
        log.info("event %s (%s) already seen; ignored", event.event_id, event.event_type)
        return EventReply(status="duplicate", event_id=event.event_id)

    if event.event_type not in HANDLED:
        log.info("event %s (%s) for %s: nothing for Simple TMS to do",
                 event.event_id, event.event_type, event.external_ref)
        return _record(db, event, "ignored")

    ref = event.data.get("delivery_ref") or event.external_ref
    existing = db.execute(select(Consignment).where(
        Consignment.owner == event.owner, Consignment.wms_ref == ref)).scalar_one_or_none()
    if existing is not None:
        log.warning("event %s: delivery %s already has consignment %s; ignored",
                    event.event_id, ref, existing.id)
        return _record(db, event, "ignored", existing.id)

    try:
        delivery = wms_client.get_delivery(ref, event.owner)
    except wms_client.WmsUnavailable as exc:
        # nothing recorded, so the WMS's retry (1 min, 5, 30, 2 h) tries again
        log.warning("event %s: could not read delivery %s from Simple WMS: %s",
                    event.event_id, ref, exc)
        return JSONResponse(status_code=503, content={
            "detail": f"could not read delivery {ref} from Simple WMS: {exc}",
            "code": "wms_unavailable"})

    consignment = consignments.from_delivery_packed(
        db, event_id=str(event.event_id), occurred_at=event.occurred_at, owner=event.owner,
        warehouse=event.warehouse or delivery.get("warehouse") or "", ref=ref,
        packages=event.data.get("packages") or [], delivery=delivery)
    log.info("event %s: consignment %s for delivery %s", event.event_id, consignment.id, ref)
    return _record(db, event, "created", consignment.id)
```

In `api/tms/api/app.py`, change the routes import to:
```python
from tms.api.routes import auth, drivers, vehicles, wms_events
```
and add after `v1.include_router(vehicles.router)`:
```python
v1.include_router(wms_events.router)
```

**Step 4: Run it to see it pass**

Run: `cd api && .venv/bin/pytest tests/test_wms_events.py -v`
Expected: `13 passed`.

**Step 5: Commit**

```
git add api/tms/services/consignments.py api/tms/api/routes/wms_events.py api/tms/api/app.py \
  api/tests/wms_samples.py api/tests/test_wms_events.py
git commit -m "Make a consignment from each delivery.packed event from Simple WMS" \
  -m "Signed events only. An event id already seen does nothing. Events we do not act on get a 200 and a log line. If Simple WMS cannot be asked for the ship-to, the answer is 503 and nothing is kept, so its retry tries again."
```

---

### Task 10: Consignment list and history

**Files:**
- Create: `api/tms/api/routes/consignments.py`
- Modify: `api/tms/api/app.py`
- Create: `api/tests/test_consignments.py`

**Step 1: Write the failing test**

`api/tests/test_consignments.py`:
```python
from conftest import WEBHOOK_SECRET
from wms_samples import delivery, packed_event, post_event


def arrive(client, wms, ref: str, customer: str):
    d = delivery(ref)
    d["ship_to"] = {**d["ship_to"], "name": customer}
    wms.deliveries[ref] = d
    r = post_event(client, packed_event(ref), WEBHOOK_SECRET)
    assert r.status_code == 200, r.text
    return r.json()["consignment_id"]


def test_the_list_is_newest_first_with_packages(client, wms, dispatcher_headers):
    arrive(client, wms, "2044", "Ballarat Hardware")
    arrive(client, wms, "2045", "Sebastopol Farm Supplies")
    r = client.get("/v1/consignments", headers=dispatcher_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert [c["wms_ref"] for c in body["items"]] == ["2045", "2044"]
    first = body["items"][0]
    assert first["customer"] == "Sebastopol Farm Supplies"
    assert first["status"] == "packed"
    assert first["packages"][0] == {"package_no": 1, "type": "carton", "weight_kg": "9.5",
                                    "length_cm": "60", "width_cm": "40", "height_cm": "40",
                                    "sscc": "093123450000000012"}
    assert "history" not in first


def test_search_by_delivery_or_customer(client, wms, dispatcher_headers):
    arrive(client, wms, "2044", "Ballarat Hardware")
    arrive(client, wms, "2045", "Sebastopol Farm Supplies")
    by_ref = client.get("/v1/consignments?q=2044", headers=dispatcher_headers).json()
    assert [c["wms_ref"] for c in by_ref["items"]] == ["2044"]
    by_name = client.get("/v1/consignments?q=sebastopol", headers=dispatcher_headers).json()
    assert [c["wms_ref"] for c in by_name["items"]] == ["2045"]
    by_status = client.get("/v1/consignments?status=delivered", headers=dispatcher_headers).json()
    assert by_status["total"] == 0


def test_one_consignment_with_its_history(client, wms, dispatcher_headers):
    cid = arrive(client, wms, "2044", "Ballarat Hardware")
    r = client.get(f"/v1/consignments/{cid}", headers=dispatcher_headers)
    assert r.status_code == 200
    c = r.json()
    assert c["ship_to"]["suburb"] == "Ballarat"
    assert c["required_by"] == "2026-10-11"
    assert [h["kind"] for h in c["history"]] == ["packed"]
    assert c["history"][0]["at"].startswith("2026-10-10T03:12:00")
    assert c["history"][0]["detail"]["packages"] == 2


def test_an_unknown_consignment_is_404(client, dispatcher_headers):
    assert client.get("/v1/consignments/999", headers=dispatcher_headers).status_code == 404


def test_consignments_need_a_session(client):
    assert client.get("/v1/consignments").status_code == 401


def test_a_driver_cannot_list_consignments_yet(client, manager_headers):
    client.post("/v1/drivers", headers=manager_headers,
                json={"code": "drv-01", "name": "Alex T.", "pin": "2468"})
    token = client.post("/v1/auth/driver-login", json={"code": "drv-01", "pin": "2468"}).json()["token"]
    r = client.get("/v1/consignments", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
```

**Step 2: Run it to see it fail**

Run: `cd api && .venv/bin/pytest tests/test_consignments.py -v`
Expected: FAIL, `404` from `GET /v1/consignments`.

**Step 3: Minimal implementation**

`api/tms/api/routes/consignments.py`:
```python
"""Consignments, and each one's history."""
from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from tms.api.deps import DB, Principal, require
from tms.api.errors import NotFound
from tms.api.schemas import Dec, Page
from tms.models import Consignment

router = APIRouter(tags=["consignments"])


class PackageOut(BaseModel):
    package_no: int
    type: str
    weight_kg: Dec | None
    length_cm: Dec | None
    width_cm: Dec | None
    height_cm: Dec | None
    sscc: str | None


class HistoryOut(BaseModel):
    kind: str
    at: datetime
    detail: dict


class ConsignmentOut(BaseModel):
    id: int
    wms_ref: str
    warehouse: str
    owner: str
    customer: str
    ship_to: dict
    required_by: date | None
    status: str
    created_at: datetime
    packages: list[PackageOut]


class ConsignmentDetail(ConsignmentOut):
    history: list[HistoryOut]


def consignment_out(c: Consignment) -> dict:
    return dict(
        id=c.id, wms_ref=c.wms_ref, warehouse=c.warehouse, owner=c.owner, customer=c.customer,
        ship_to=c.ship_to or {}, required_by=c.required_by, status=c.status,
        created_at=c.created_at,
        packages=[PackageOut(package_no=p.package_no, type=p.type, weight_kg=p.weight_kg,
                             length_cm=p.length_cm, width_cm=p.width_cm, height_cm=p.height_cm,
                             sscc=p.sscc) for p in c.packages],
    )


@router.get("/consignments", response_model=Page[ConsignmentOut])
def list_consignments(
    db: DB, status: str | None = None, warehouse: str | None = None,
    q: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=200, le=2000), offset: int = 0,
    who: Principal = require("consignments:read"),
):
    """Newest first. `q` matches the WMS delivery or the customer."""
    query = select(Consignment).options(selectinload(Consignment.packages))
    if status:
        query = query.where(Consignment.status.in_(status.split(",")))
    if warehouse:
        query = query.where(Consignment.warehouse == warehouse)
    if q:
        like = f"%{q}%"
        query = query.where(or_(Consignment.wms_ref.ilike(like), Consignment.customer.ilike(like)))
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar_one()
    rows = db.execute(
        query.order_by(Consignment.id.desc()).limit(limit).offset(offset)
    ).scalars().all()
    return Page(items=[ConsignmentOut(**consignment_out(c)) for c in rows], total=total)


@router.get("/consignments/{id}", response_model=ConsignmentDetail)
def get_consignment(id: int, db: DB, who: Principal = require("consignments:read")):
    c = db.execute(
        select(Consignment)
        .options(selectinload(Consignment.packages), selectinload(Consignment.events))
        .where(Consignment.id == id)
    ).scalar_one_or_none()
    if c is None:
        raise NotFound(f"no consignment {id}")
    return ConsignmentDetail(
        **consignment_out(c),
        history=[HistoryOut(kind=e.kind, at=e.at, detail=e.detail or {}) for e in c.events],
    )
```

`api/tms/api/app.py` now reads, in full:
```python
import logging

from fastapi import APIRouter, FastAPI
from sqlalchemy import text

from tms.api import errors
from tms.api.deps import DB
from tms.api.routes import auth, consignments, drivers, vehicles, wms_events

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = FastAPI(
    title="Simple TMS",
    version="0.1.0",
    description="Open source, self-hosted transport management. The API is the only door.",
    docs_url="/docs",
    redoc_url=None,
)
errors.install(app)

v1 = APIRouter(prefix="/v1")
v1.include_router(auth.router)
v1.include_router(drivers.router)
v1.include_router(vehicles.router)
v1.include_router(wms_events.router)
v1.include_router(consignments.router)


@v1.get("/health", tags=["system"])
def health(db: DB):
    db.execute(text("select 1"))
    return {"status": "ok"}


app.include_router(v1)
```

**Step 4: Run it to see it pass**

Run: `cd api && .venv/bin/pytest tests/test_consignments.py -v`
Expected: `6 passed`.

**Step 5: Commit**

```
git add api/tms/api/routes/consignments.py api/tms/api/app.py api/tests/test_consignments.py
git commit -m "List consignments and show each one with its history" \
  -m "Search by WMS delivery or customer, filter by status and warehouse."
```

---

### Task 11: Docker image, Compose, Caddy and settings

**Files:**
- Create: `api/Dockerfile`
- Modify: `docker-compose.yml` (replace)
- Create: `deploy/Caddyfile`
- Modify: `.env.example` (replace)

**Step 1: The check that fails**

Run: `docker compose up -d --build db api`
Expected: `no such service: api`.

**Step 2: Implementation**

`api/Dockerfile`:
```dockerfile
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
COPY tms ./tms
COPY alembic.ini ./
COPY alembic ./alembic

RUN pip install --no-cache-dir . && rm -rf /root/.cache

EXPOSE 8000
CMD ["uvicorn", "tms.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
```

`docker-compose.yml`:
```yaml
services:
  db:
    image: postgres:16
    restart: unless-stopped
    environment:
      POSTGRES_DB: ${POSTGRES_DB:-tms}
      POSTGRES_USER: ${POSTGRES_USER:-tms}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in .env}
    volumes:
      - pgdata:/var/lib/postgresql/data
    # 5434 so it sits beside Simple WMS's database (5433) on one machine
    ports:
      - "127.0.0.1:${POSTGRES_PORT:-5434}:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-tms} -d ${POSTGRES_DB:-tms}"]
      interval: 5s
      timeout: 3s
      retries: 20

  api:
    build: ./api
    restart: unless-stopped
    command: sh -c "alembic upgrade head && uvicorn tms.api.app:app --host 0.0.0.0 --port 8000"
    environment:
      TMS_DATABASE_URL: postgresql+psycopg://${POSTGRES_USER:-tms}:${POSTGRES_PASSWORD}@db:5432/${POSTGRES_DB:-tms}
      TMS_SECRET_KEY: ${TMS_SECRET_KEY:?set TMS_SECRET_KEY in .env}
      TMS_WMS_WEBHOOK_SECRET: ${TMS_WMS_WEBHOOK_SECRET:-}
      TMS_WMS_URL: ${TMS_WMS_URL:-}
      TMS_WMS_API_KEY: ${TMS_WMS_API_KEY:-}
    depends_on:
      db:
        condition: service_healthy
    expose:
      - "8000"
    # Caddy is the only door in a real install. Published on the host as well
    # for local work, CI and a Simple WMS on the same machine.
    ports:
      - "127.0.0.1:${TMS_API_PORT:-8021}:8000"
    healthcheck:
      test: ["CMD-SHELL", "python -c \"import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/v1/health')\""]
      interval: 10s
      timeout: 5s
      retries: 10
      start_period: 20s

  # The worker arrives with step 2, with the queue it drains.

  caddy:
    image: caddy:2
    restart: unless-stopped
    environment:
      TMS_DOMAIN: ${TMS_DOMAIN:-localhost}
    ports:
      - "${TMS_HTTP_PORT:-80}:80"
      - "${TMS_HTTPS_PORT:-443}:443"
    volumes:
      - ./deploy/Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
    depends_on:
      - api

volumes:
  pgdata:
  caddy_data:
  caddy_config:
```

`deploy/Caddyfile`:
```
{$TMS_DOMAIN} {
	encode gzip

	# The API is the only door.
	handle /v1/* {
		reverse_proxy api:8000
	}
	handle /docs* {
		reverse_proxy api:8000
	}
	handle /openapi.json {
		reverse_proxy api:8000
	}

	# The desktop and driver apps arrive with build steps 2 and 3.
	handle {
		respond "Simple TMS. The API is at /v1, its docs at /docs." 404
	}
}
```

`.env.example`:
```
# Copy to .env and fill in. Nothing here is committed.

# Postgres (used by the db and api containers)
POSTGRES_DB=tms
POSTGRES_USER=tms
POSTGRES_PASSWORD=change-me
# Host port the database is published on (127.0.0.1 only). 5434 sits beside
# Simple WMS's 5433. The tests expect POSTGRES_PASSWORD=tms-dev on this port
# unless TEST_DATABASE_URL says otherwise.
POSTGRES_PORT=5434

# API
# Random string, at least 32 characters. Used to sign session tokens.
TMS_SECRET_KEY=change-me-to-a-long-random-string
# Host port the API is published on, for local work and health checks.
# Caddy is still the only door from outside.
TMS_API_PORT=8021

# Simple WMS (optional: Simple TMS works without it)
# The secret of the Simple TMS subscriber in Simple WMS. Events signed with
# anything else are refused.
TMS_WMS_WEBHOOK_SECRET=
# Where Simple WMS's API is, and a WMS API key with the tasks:read scope, to
# read a packed delivery's ship-to.
TMS_WMS_URL=
TMS_WMS_API_KEY=

# Caddy: the public host name. "localhost" gives a self-signed local cert.
TMS_DOMAIN=localhost
# Host ports Caddy listens on. Change them when Simple WMS's Caddy already
# has 80 and 443 on the same machine.
TMS_HTTP_PORT=80
TMS_HTTPS_PORT=443
```

Bring your `.env` up to date (keep `POSTGRES_PASSWORD=tms-dev`, which the tests expect):
```
cp .env.example .env
sed -i '' 's/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=tms-dev/' .env
sed -i '' "s/^TMS_SECRET_KEY=.*/TMS_SECRET_KEY=$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')/" .env
# Simple WMS's Caddy already has 80 and 443 on this machine
sed -i '' 's/^TMS_HTTP_PORT=.*/TMS_HTTP_PORT=8180/; s/^TMS_HTTPS_PORT=.*/TMS_HTTPS_PORT=8543/' .env
```

**Step 3: Run the check to see it pass**

```
docker compose up -d --build
for i in $(seq 1 30); do curl -fsS http://127.0.0.1:8021/v1/health && break; sleep 2; done; echo
curl -sk https://localhost:8543/v1/health; echo
curl -sk -o /dev/null -w '%{http_code}\n' https://localhost:8543/
```
Expected: `{"status":"ok"}` twice (straight to the API, then through Caddy), then `404` for `/`.

```
docker compose exec api tms create-user --username leighton --role admin --password 'a long enough password'
curl -s -X POST http://127.0.0.1:8021/v1/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"leighton","password":"a long enough password"}'
```
Expected: `user leighton (id 1) created with role admin`, then JSON starting `{"token":"tms_s.`.

**Step 4: Commit**

```
git add api/Dockerfile docker-compose.yml deploy/Caddyfile .env.example
git commit -m "Run the API, database and Caddy with Docker Compose" \
  -m "The API runs its migrations on start. Caddy's host ports are settings so it can run beside Simple WMS. The worker comes with step 2."
```

---

### Task 12: `docs/api.md`

**Files:**
- Create: `docs/api.md`

**Step 1: Write it**

`docs/api.md`:
````markdown
# Simple TMS — API

Base path `/v1`. JSON in, JSON out. Weights, volumes and dimensions are
decimals, sent and returned as strings (`"9.5"`), never floats. Interactive
docs are at `/docs`.

## Shared rules

- **Auth:** `Authorization: Bearer <token>`. A desktop user gets a token from
  `POST /v1/auth/login`, a driver from `POST /v1/auth/driver-login`. The
  desktop and the driver app use these endpoints and nothing else: the API is
  the only door. Simple WMS events are the exception: they are signed instead
  (see below).
- **Errors:** a missing, expired or unknown token is `401`. A token without
  the scope is `403`. Validation problems are `422` with
  `{ "errors": [{ "field", "message" }] }`. Something that is not there is
  `404` with `{ "detail" }`.
- **Never deleted:** drivers and vehicles are deactivated, not deleted. A
  consignment's history is append-only.

## Scopes

| Who | Scopes |
| --- | --- |
| `admin` | `*` |
| `manager` | `fleet:read`, `fleet:write`, `consignments:read` |
| `dispatcher` | `fleet:read`, `consignments:read` |
| a driver | `fleet:read` (to pick a truck) |

## Sign in

### POST /v1/auth/login
`{ "username", "password" }` →
```json
{
  "token": "tms_s.…", "expires_in": 900, "refresh_token": "tms_r.…",
  "user": { "id": 1, "username": "dee", "display_name": "Dee", "role": "dispatcher" }
}
```
The token lasts 15 minutes; the refresh token 14 days. A wrong password is
`401` with `{ "detail", "code": "wrong_password", "tries_left" }`. Five wrong
in a row lock the account for 15 minutes (`"code": "locked"`). An unknown
username looks like a wrong password.

### POST /v1/auth/refresh
`{ "refresh_token" }` → the same as login, with a new refresh token. The old
one is spent.

### POST /v1/auth/logout
`{ "refresh_token" }` → `204`. The session ends.

### GET /v1/auth/me
`{ "kind": "user" | "driver", "id", "name", "display_name", "role", "scopes" }`.

### POST /v1/auth/driver-login
`{ "code": "drv-01", "pin": "2468" }` →
`{ "token": "tms_d.…", "expires_in": 43200, "driver": { …as /me… } }`. The
token lasts a 12-hour shift. A wrong PIN is `401` with `"code": "wrong_pin"`
and `tries_left`. Five wrong PINs lock the driver (`"code": "locked"`) until a
manager unlocks them. An unknown or inactive code is `"code": "unknown_driver"`.

Desktop users are created on the command line in step 1:
`tms create-user --username you --role admin|manager|dispatcher`.

## Vehicles

`fleet:read` to list and read, `fleet:write` to change.

- `POST /v1/vehicles` — `{ "rego": "1AB 2CD", "type": "van" | "rigid" | "ute",
  "max_kg": "1200", "max_m3": "8.5" }` → `201`. The rego is stored upper case
  without spaces (`1AB2CD`); a rego already on file is `422`.
- `GET /v1/vehicles?active=true|false` — `{ "items", "total" }`, by rego.
- `GET /v1/vehicles/{rego}`
- `PATCH /v1/vehicles/{rego}` — any of `type`, `max_kg`, `max_m3`.
- `POST /v1/vehicles/{rego}/deactivate`, `/reactivate`

Each vehicle: `{ "id", "rego", "type", "max_kg", "max_m3", "active", "created_at" }`.

## Drivers

`fleet:read` to list and read, `fleet:write` to change.

- `POST /v1/drivers` — `{ "code": "drv-01", "name": "Alex T.", "pin": "2468" }`
  → `201`. Codes are letters, digits, `.`, `_` and `-`; a PIN is 4 to 8
  digits, stored hashed and never returned.
- `GET /v1/drivers?active=true|false`
- `GET /v1/drivers/{code}`
- `PATCH /v1/drivers/{code}` — `name` and/or a new `pin`.
- `POST /v1/drivers/{code}/deactivate`, `/reactivate`
- `POST /v1/drivers/{code}/unlock` — clears a PIN lock and the count.

Each driver: `{ "id", "code", "name", "active", "locked", "failed_attempts", "created_at" }`.

## Consignments

`consignments:read`.

### GET /v1/consignments
Newest first. Filters: `status` (comma separated), `warehouse`, `q` (part of
the WMS delivery ref or the customer name), `limit` (default 200, at most
2000), `offset`. → `{ "items", "total" }`, each:
```json
{
  "id": 1, "wms_ref": "0080012345", "warehouse": "BAL-WH01", "owner": "DEFAULT",
  "customer": "Ballarat Hardware",
  "ship_to": { "name": "Ballarat Hardware", "address": "12 Sturt St", "suburb": "Ballarat",
               "state": "VIC", "postcode": "3350", "country": "AU" },
  "required_by": "2026-10-11", "status": "packed", "created_at": "2026-10-10T03:12:04Z",
  "packages": [{ "package_no": 1, "type": "carton", "weight_kg": "9.5", "length_cm": "60",
                 "width_cm": "40", "height_cm": "40", "sscc": "093123450000000012" }]
}
```

### GET /v1/consignments/{id}
The same, plus its history, oldest first:
```json
"history": [{ "kind": "packed", "at": "2026-10-10T03:12:00Z",
              "detail": { "wms_event_id": "…", "packages": 1 } }]
```
Statuses in step 1: `packed`. Runs bring `assigned`, `departed`, `delivered`
and `failed`.

## From Simple WMS

### POST /v1/wms/events
Simple TMS is an ordinary Simple WMS subscriber. Register it in Simple WMS:
```
POST /v1/subscribers   (on Simple WMS, with an integration:admin key)
{ "name": "simple-tms", "url": "https://<tms>/v1/wms/events",
  "secret": "<the same value as TMS_WMS_WEBHOOK_SECRET>",
  "event_types": ["delivery.*"], "warehouses": ["*"], "owner": "*" }
```
and give Simple TMS a Simple WMS key with `tasks:read` in `TMS_WMS_API_KEY`
and the WMS address in `TMS_WMS_URL`.

The body is Simple WMS's event envelope, exactly as sent:
`{ "event_id", "event_type", "occurred_at", "warehouse", "owner", "external_ref", "data" }`.
`X-WMS-Signature: sha256=<hex HMAC-SHA256 of the raw body, keyed with the
secret>` must match, or the answer is `401` and nothing happens. No bearer
token: the signature is the authentication.

| Answer | When |
| --- | --- |
| `200 { "status": "created", "event_id", "consignment_id" }` | `delivery.packed`: a consignment with its packages and a `packed` history row |
| `200 { "status": "duplicate", "event_id" }` | this `event_id` was seen before; nothing happens |
| `200 { "status": "ignored", "event_id" }` | an event Simple TMS does not act on (logged), or a delivery that already has a consignment |
| `401` | missing or wrong signature |
| `422` | signed, but not an event envelope |
| `503 { "code": "wms_unavailable" }` | the delivery could not be read from Simple WMS; nothing is kept, so Simple WMS's retry (1 min, 5, 30, 2 h) tries again |
| `503 { "code": "not_configured" }` | `TMS_WMS_WEBHOOK_SECRET` is not set |

On `delivery.packed` the packages (number, type, weight, dimensions, SSCC)
come from the event; the customer, ship-to address and required-by date come
from `GET /v1/deliveries/{ref}?owner=…` on Simple WMS, because the event does
not carry them. Transfers are not consignments yet.

## System

- `GET /v1/health` → `{ "status": "ok" }` once the database answers.
````

**Step 2: Check it against the app**

```
cd api && .venv/bin/python -c "
from tms.api.app import app
for path, ops in app.openapi()['paths'].items():
    for m in ops: print(m.upper(), path)" | sort
```
Expected: 22 lines, from `GET /v1/auth/me` to `POST /v1/wms/events`, every one of them documented in `docs/api.md`, and nothing in `docs/api.md` missing from the list.

**Step 3: Commit**

```
git add docs/api.md
git commit -m "Document every endpoint in docs/api.md"
```

---

### Task 13: README and the repo layout in CLAUDE.md

**Files:**
- Create: `README.md`
- Modify: `CLAUDE.md` (the "Repo layout" block only)

**Step 1: Write them**

`README.md`:
````markdown
# Simple TMS

An open source, self-hosted transport management system for businesses that
deliver with their own trucks and also send freight with outside carriers.
It sits beside Simple WMS and Platen and works without either.

Status: **build step 1, the skeleton.** Sign-in for dispatchers and drivers,
vehicles and drivers, and consignments created from Simple WMS's
`delivery.packed` events. Runs, the planning screen and the driver app come
with the next steps in `docs/plans/2026-10-10-simple-tms-design.md`.

## What is in this repo

```
CLAUDE.md            rules for Claude Code when working here
docs/plans/          the design and the implementation plans
docs/api.md          every endpoint
design/              static HTML of each screen, and a gallery
api/                 FastAPI app, migrations, tests
deploy/Caddyfile     the only door in
docker-compose.yml   api, db, caddy
```

## Getting started

```
git clone https://github.com/q7-technology/simple-tms
cd simple-tms
cp .env.example .env            # set POSTGRES_PASSWORD and TMS_SECRET_KEY
docker compose up -d
docker compose exec api tms create-user --username you --role admin
```

The API is at `https://<TMS_DOMAIN>/v1` (default `https://localhost/`,
self-signed) and its interactive docs at `/docs`. Sign in with
`POST /v1/auth/login`. On a machine that already runs Simple WMS, its Caddy
has ports 80 and 443: set `TMS_HTTP_PORT` and `TMS_HTTPS_PORT` in `.env`
(say 8180 and 8543).

## Connecting Simple WMS

Simple TMS is an ordinary subscriber to Simple WMS's signed events. In `.env`
set `TMS_WMS_WEBHOOK_SECRET` (a long random string), `TMS_WMS_URL` (Simple
WMS's address) and `TMS_WMS_API_KEY` (a Simple WMS key with `tasks:read`),
then register a subscriber in Simple WMS with the same secret, pointing at
`/v1/wms/events`. Each packed delivery becomes a consignment. Details are in
`docs/api.md`.

## Run the API outside Docker

```
docker compose up -d db         # Postgres on 127.0.0.1:5434
cd api
uv venv -p 3.12 && uv pip install -e ".[dev]"
export TMS_DATABASE_URL=postgresql+psycopg://tms:<POSTGRES_PASSWORD>@127.0.0.1:5434/tms
.venv/bin/alembic upgrade head
.venv/bin/tms create-user --username you --role admin
.venv/bin/uvicorn tms.api.app:app --reload --port 8021
```

## Tests

pytest against a real PostgreSQL, so the append-only trigger is exercised for
real. The suite creates and resets a `tms_test` database on the compose `db`
service, expecting `POSTGRES_PASSWORD=tms-dev` locally; point
`TEST_DATABASE_URL` elsewhere to use another server.

```
docker compose up -d db
cd api && .venv/bin/pytest
```

## Stack

Python 3.12 + FastAPI · PostgreSQL 16 · Docker Compose · Caddy. The same
choices as Simple WMS, so one person can look after both.
````

In `CLAUDE.md`, replace the block under "## Repo layout" and the sentence after it with:
````
```
CLAUDE.md            this file
docs/plans/          the design and the implementation plans
docs/api.md          every endpoint: keep it in step with the routes
design/index.html    gallery of every screen; open a screen's HTML
design/screens/      static HTML of each screen: build to these
api/                 FastAPI app (package tms), migrations, tests
deploy/Caddyfile     the only door in
docker-compose.yml   api, db, caddy
```
The worker and the apps arrive with their build steps.
````

**Step 2: Commit**

```
git add README.md CLAUDE.md
git commit -m "Add the README and the API to the repo layout"
```

---

### Task 14: CI

**Files:**
- Create: `.github/workflows/ci.yml`

**Step 1: Write it**

`.github/workflows/ci.yml`:
```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

# One run per branch: a new push cancels the one before it.
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  api:
    name: API
    runs-on: ubuntu-latest
    services:
      db:
        image: postgres:16
        env:
          POSTGRES_DB: tms
          POSTGRES_USER: tms
          POSTGRES_PASSWORD: tms
        ports: ["5432:5432"]
        options: >-
          --health-cmd "pg_isready -U tms"
          --health-interval 5s
          --health-timeout 3s
          --health-retries 20
    env:
      # the suite makes and drops its own databases on this server
      TEST_DATABASE_URL: postgresql+psycopg://tms:tms@127.0.0.1:5432/tms_test
    defaults:
      run:
        working-directory: api
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true
      - name: Install
        run: uv venv -p 3.12 && uv pip install -e ".[dev]"
      - name: Tests
        run: .venv/bin/pytest -q

  compose:
    name: Docker Compose comes up
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Settings a real install needs
        run: |
          cp .env.example .env
          sed -i 's/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=ci-only-password/' .env
          sed -i 's/^TMS_SECRET_KEY=.*/TMS_SECRET_KEY=ci-only-secret-key-that-is-long-enough/' .env
      - name: Up
        run: docker compose up -d --build db api
      - name: The API answers and the migrations ran
        run: |
          for i in $(seq 1 30); do
            if curl -fsS http://127.0.0.1:8021/v1/health 2>/dev/null; then ok=1; break; fi
            sleep 2
          done
          test "${ok:-0}" = 1 || (docker compose logs api; exit 1)
          # the append-only trigger is there on a real install
          docker compose exec -T db psql -U tms -d tms -tAc \
            "select count(*) from pg_trigger where tgname = 'consignment_event_append_only'" | grep -qx 1
      - name: Logs on failure
        if: failure()
        run: docker compose logs
```

**Step 2: Check the YAML parses**

Run: `cd api && .venv/bin/python -c "import yaml" 2>/dev/null || uv pip install pyyaml; .venv/bin/python -c "import yaml; print(list(yaml.safe_load(open('../.github/workflows/ci.yml'))['jobs']))"`
Expected: `['api', 'compose']`. (PyYAML is only for this check; it is not added to `pyproject.toml`.)

**Step 3: Commit**

```
git add .github/workflows/ci.yml
git commit -m "Run the tests and bring up Compose in CI"
```

---

### Task 15: The whole suite and a clean Compose start

**Step 1: The whole suite**

Run: `cd api && .venv/bin/pytest -q`
Expected: `63 passed`, no warnings (warnings are errors in this suite).

**Step 2: Compose from nothing**

```
docker compose down -v
docker compose up -d --build
for i in $(seq 1 30); do curl -fsS http://127.0.0.1:8021/v1/health && break; sleep 2; done; echo
docker compose exec -T db psql -U tms -d tms -tAc \
  "select count(*) from pg_trigger where tgname = 'consignment_event_append_only'"
docker compose exec api tms create-user --username leighton --role admin --password 'a long enough password'
```
Expected: `{"status":"ok"}`, `1`, `user leighton (id 1) created with role admin`. Open `http://127.0.0.1:8021/docs` and check every endpoint in `docs/api.md` is listed.

`docker compose down -v` deletes the TMS database volume: on a machine with data worth keeping, use `docker compose down` and skip the user step.

Nothing to commit unless a step failed and something was fixed.

---

### Task 16: Manual check: Simple TMS as a subscriber of a local Simple WMS

A local Simple WMS with stock: its API on `127.0.0.1:8011` (its `WMS_API_PORT`) and the demo data (`docker compose exec api wms seed-demo` on an empty Simple WMS gives Ballarat with `GLV-L` gloves on the shelf). The TMS stack from Task 15 is up, with API on `127.0.0.1:8021`. Containers reach the host as `host.docker.internal` (Docker Desktop on macOS).

**Step 1: Two Simple WMS keys**

```
cd "/Users/leightonlauton/Q7 Technology/Software/q7/simple-wms"
docker compose exec api wms create-api-client --name simple-tms --scopes tasks:read --warehouses '*' --owner '*'
docker compose exec api wms create-api-client --name tms-check --scopes 'tasks:*,integration:*' --warehouses '*' --owner '*'
```
Each prints its key once. The first is for Simple TMS to read deliveries; the second is only for this check (registering the subscriber, making a delivery) and is revoked at the end.

**Step 2: Point Simple TMS at Simple WMS**

```
cd "/Users/leightonlauton/Q7 Technology/Software/q7/simple-tms"
SECRET=$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))'); echo "$SECRET"
sed -i '' "s|^TMS_WMS_WEBHOOK_SECRET=.*|TMS_WMS_WEBHOOK_SECRET=$SECRET|" .env
sed -i '' "s|^TMS_WMS_URL=.*|TMS_WMS_URL=http://host.docker.internal:8011|" .env
sed -i '' "s|^TMS_WMS_API_KEY=.*|TMS_WMS_API_KEY=<the simple-tms key>|" .env
docker compose up -d api
```

**Step 3: Register the subscriber in Simple WMS**

```
SETUP=<the tms-check key>
curl -s -X POST http://127.0.0.1:8011/v1/subscribers \
  -H "Authorization: Bearer $SETUP" -H 'Content-Type: application/json' \
  -d "{\"name\":\"simple-tms\",\"url\":\"http://host.docker.internal:8021/v1/wms/events\",\"secret\":\"$SECRET\",\"event_types\":[\"delivery.*\"],\"warehouses\":[\"*\"],\"owner\":\"*\"}"
```
Expected: `201` JSON with `"name":"simple-tms"` and `"status":"idle"`.

**Step 4: Make, pick and pack a delivery in Simple WMS**

```
WMS=http://127.0.0.1:8011 SETUP_KEY=$SETUP api/.venv/bin/python - <<'PY'
import os, uuid, httpx

WMS, REF = os.environ["WMS"], "TMS-CHECK-1"
H = {"Authorization": f"Bearer {os.environ['SETUP_KEY']}"}

def post(path, body):
    r = httpx.post(f"{WMS}/v1{path}", headers=H, timeout=30,
                   json={"message_id": str(uuid.uuid4()), **body})
    assert r.status_code < 300, f"POST {path}: {r.status_code} {r.text}"
    return r.json()

post("/deliveries", {
    "external_ref": REF, "warehouse": "BAL-WH01", "owner": "DEFAULT",
    "ship_to": {"name": "Ballarat Hardware", "address": "12 Sturt St", "suburb": "Ballarat",
                "state": "VIC", "postcode": "3350", "country": "AU"},
    "lines": [{"delivery_line": 10, "sku": "GLV-L", "qty": 2, "uom": "PR"}]})
task = httpx.get(f"{WMS}/v1/tasks", headers=H, params={
    "warehouse": "BAL-WH01", "source_ref": REF, "type": "pick"}).json()["items"][-1]
post(f"/tasks/{task['wms_id']}/start", {"operator": "op-017"})
for line in task["lines"]:
    post(f"/tasks/{task['wms_id']}/lines/{line['line_no']}/confirm",
         {"qty": line["expected_qty"], "uom": line["uom"], "operator": "op-017"})
post(f"/deliveries/{REF}/pack", {
    "warehouse": "BAL-WH01", "packed_by": "op-017", "complete": True,
    "packages": [{"package_no": 1, "type": "carton", "weight_kg": 1.2, "length_cm": 30,
                  "width_cm": 20, "height_cm": 10,
                  "lines": [{"delivery_line": 10, "sku": "GLV-L", "qty": 2, "uom": "PR"}]}]})
print("packed", REF)
PY
```
Expected: `packed TMS-CHECK-1`. Use a new `REF` to run it again.

**Step 5: See it arrive**

Within the WMS worker's poll (about 5 s):
```
curl -s "http://127.0.0.1:8011/v1/events?subscriber=simple-tms" -H "Authorization: Bearer $SETUP"
```
Expected: `delivery.allocated`, `delivery.picked` and `delivery.packed`, each `"status":"delivered"` after 1 attempt.

```
TOKEN=$(curl -s -X POST http://127.0.0.1:8021/v1/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"leighton","password":"a long enough password"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
curl -s "http://127.0.0.1:8021/v1/consignments?q=TMS-CHECK-1" -H "Authorization: Bearer $TOKEN"
curl -s "http://127.0.0.1:8021/v1/consignments/<id from the list>" -H "Authorization: Bearer $TOKEN"
docker compose exec -T db psql -U tms -d tms -c 'select event_type, external_ref, outcome from inbound_event order by received_at'
docker compose logs api | grep tms.wms
```
Expected: one consignment, `"wms_ref":"TMS-CHECK-1"`, `"customer":"Ballarat Hardware"`, the Sturt St ship-to, one package `"weight_kg":"1.2"` 30×20×10; its history one `packed` row carrying the WMS event id. `inbound_event` shows `delivery.allocated ignored`, `delivery.picked ignored`, `delivery.packed created`; the log has a "nothing for Simple TMS to do" line for each ignored one.

**Step 6: Simple WMS out of reach** (optional)

In simple-tms, set `TMS_WMS_URL=http://host.docker.internal:1` in `.env` and run `docker compose up -d api`. Pack another delivery (Step 4 with `REF = "TMS-CHECK-2"`). In Simple WMS, `GET /v1/events?subscriber=simple-tms` shows that `delivery.packed` as `pending`, `last_error` starting `HTTP 503`, `next_attempt_at` a minute on, and `GET /v1/consignments?q=TMS-CHECK-2` on the TMS is empty. Put `TMS_WMS_URL` back, `docker compose up -d api`, and after the retry `TMS-CHECK-2` is a consignment.

**Step 7: Tidy up**

Revoke the check key in Simple WMS (`GET /v1/api-clients` with it to find its id, then `POST /v1/api-clients/{id}/revoke`). Keep the `simple-tms` key and the subscriber if the two should stay connected.

Linux hosts: `host.docker.internal` does not reach ports published on `127.0.0.1`. Join the two stacks instead: `docker network connect simple-tms_default simple-wms-worker-1`, `docker network connect simple-wms_default simple-tms-api-1`, and use `http://simple-tms-api-1:8000/v1/wms/events` and `TMS_WMS_URL=http://simple-wms-api-1:8000`.

Nothing to commit.
