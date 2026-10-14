import contextlib
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace
from urllib.parse import unquote, urlparse

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
            seed_checklists(conn)


# The checklist migration 0005 gives a fresh install, put back after each
# test as it was: van items 1-6, rigid 7-12, ute 13-18. (label, critical)
DEFAULT_CHECKLIST = [("Tyres", False), ("Lights", True), ("Brakes", True), ("Mirrors", False),
                     ("Load restraints", False), ("Damage", False)]


def seed_checklists(conn):
    conn.execute(text("delete from checklist_item"))
    conn.execute(text("alter sequence checklist_item_id_seq restart"))
    conn.execute(
        text("insert into checklist_item (vehicle_type, position, label, critical) "
             "values (:t, :p, :l, :c)"),
        [{"t": t, "p": i, "l": label, "c": critical} for t in ("van", "rigid", "ute")
         for i, (label, critical) in enumerate(DEFAULT_CHECKLIST, start=1)])


@pytest.fixture
def client(engine):
    from fastapi.testclient import TestClient

    from tms.api.app import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def pod_dir(tmp_path, monkeypatch):
    """Signatures and photos go to a folder of the test's own, never /data/pod."""
    from tms.config import get_settings

    folder = tmp_path / "pod"
    monkeypatch.setattr(get_settings(), "pod_dir", str(folder))
    return folder


@pytest.fixture
def today_is_day(monkeypatch):
    """The server's today is the samples' DAY, whatever the clock says, for
    tests where a run's day must not be in the past."""
    from runs_samples import DAY
    from tms.services import fleet

    monkeypatch.setattr(fleet, "today", lambda: DAY)
    return DAY


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


def in_background(call):
    """Start call() on a thread; .result() waits for it and returns what it did."""
    from concurrent.futures import ThreadPoolExecutor

    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(call)
    pool.shutdown(wait=False)
    return future


def blocked_on_a_lock(engine, future, timeout: float = 5) -> bool:
    """Wait until another connection is waiting on a lock (True), or until the
    background call has finished without waiting (False)."""
    import time

    deadline = time.monotonic() + timeout
    with engine.connect() as conn:
        while time.monotonic() < deadline and not future.done():
            waiting = conn.execute(text(
                "select count(*) from pg_stat_activity "
                "where wait_event_type = 'Lock' and datname = current_database()")).scalar()
            conn.rollback()
            if waiting:
                return True
            time.sleep(0.02)
    return False


@contextlib.contextmanager
def queries():
    """Counts the SQL statements sent while inside: [n]."""
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    n = [0]

    def count(*args):
        n[0] += 1

    event.listen(Engine, "before_cursor_execute", count)
    try:
        yield n
    finally:
        event.remove(Engine, "before_cursor_execute", count)


@contextlib.contextmanager
def beaten_to_it(engine, rival_for):
    """Just before the API writes a new row, another connection commits the row
    rival_for(new_object) returns: two requests racing for the same key."""
    from sqlalchemy import event

    done = []

    def before_flush(session, flush_context, instances):
        if done:
            return
        for obj in list(session.new):
            rival = rival_for(obj)
            if rival is not None:
                done.append(rival)
                with Session(engine) as other:
                    other.add(rival)
                    other.commit()
                return

    event.listen(Session, "before_flush", before_flush)
    try:
        yield
    finally:
        event.remove(Session, "before_flush", before_flush)
    assert done, "nothing raced"


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
        if isinstance(body, bytes):  # not JSON at all, e.g. a proxy's error page
            payload = body
        else:
            payload = json.dumps(body if body is not None else {"detail": "unavailable"}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@pytest.fixture
def wms(monkeypatch):
    """Answers GET /v1/deliveries/{ref} from `deliveries` (JSON, or bytes sent
    as they are). Queue a status in `statuses` (e.g. 503) to make the next call fail."""
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
