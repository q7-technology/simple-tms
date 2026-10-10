import logging

from fastapi import APIRouter, FastAPI
from sqlalchemy import text

from tms.api import errors
from tms.api.deps import DB
from tms.api.routes import auth, drivers, vehicles, wms_events

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


@v1.get("/health", tags=["system"])
def health(db: DB):
    db.execute(text("select 1"))
    return {"status": "ok"}


app.include_router(v1)
