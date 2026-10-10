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


def all_at_once(n: int, call):
    """Run call() n times on n threads released together; return the results."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    start = threading.Barrier(n)

    def one():
        start.wait()
        return call()

    with ThreadPoolExecutor(max_workers=n) as pool:
        return [f.result() for f in [pool.submit(one) for _ in range(n)]]


def fresh_client():
    """A test client of its own, for one thread."""
    from fastapi.testclient import TestClient

    from tms.api.app import app

    return TestClient(app)
