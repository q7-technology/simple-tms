import re
import time
from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_SECRET_KEY = 32
# OpenStreetMap's own tile servers are for its website, not for apps
# (operations.osmfoundation.org/policies/tiles): the design rules them out.
OSM_TILES = re.compile(r"^https://([a-c]\.)?tile\.openstreetmap\.org/", re.IGNORECASE)
# https://host or https://host:port and nothing else: it goes into the
# desktop's Content-Security-Policy as it is
ORIGIN = re.compile(r"^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?$")


class Settings(BaseSettings):
    # A setting left empty (Compose hands the api and the worker every one,
    # as "" when .env leaves it out) is the default here, not "".
    model_config = SettingsConfigDict(env_prefix="TMS_", extra="ignore", env_ignore_empty=True)

    database_url: str = "postgresql+psycopg://tms:tms@127.0.0.1:5432/tms"
    # Signs session tokens. No usable default: start-up refuses a weak one.
    secret_key: str = "change-me"

    # Simple WMS. The webhook secret is the one its subscriber row signs with;
    # the URL and key are for reading a delivery's ship-to when it is packed.
    wms_webhook_secret: str = ""
    wms_url: str = ""
    wms_api_key: str = ""
    # Shorter than the WMS worker's own 10 s wait, so it hears our answer.
    wms_timeout_seconds: float = Field(default=5.0, gt=0, le=60)

    # Platen, or any print service that takes the same job: its intake
    # address (https://<platen>/intake/jobs), a Platen key, and the printer
    # run sheets go to (a Platen printer's id or name).
    platen_url: str = ""
    platen_key: str = ""
    run_sheet_printer: str = "dispatch"
    # The Platen printer carrier manifests go to; empty, the run sheets' one.
    manifest_printer: str = ""

    # Proof of delivery: where signatures and photos are kept on disk (a
    # volume in Compose). Only the API reads and writes them.
    pod_dir: str = "/data/pod"

    # Live location. A truck's positions, and the position kept on a proof of
    # delivery, are deleted by the worker this many days after they were
    # taken: they are personal information, not business history.
    position_retention_days: int = Field(default=90, ge=1)
    # Map tiles for the desktop's "Where is it": a raster tile address with
    # {z}, {x} and {y}, from a tile provider (with its key) or a tile server
    # of your own (an https address, or a path on this server such as
    # /tiles/{z}/{x}/{y}.png), and the attribution it asks for. Left empty,
    # the desktop lists each truck's position without a map.
    map_tile_url: str = ""
    # HTML, as Leaflet shows it: a provider's credit often has a link in it
    # (<a href="https://www.maptiler.com/copyright/">© MapTiler</a>)
    map_attribution: str = ""
    # Where the tiles come from, https://host[:port], let into the desktop's
    # Content-Security-Policy by Caddy: the tile URL's own origin, or empty
    # for tiles on this server. Checked here so a mistake stops the API, not
    # just the map.
    map_tile_origin: str = ""
    # ETAs: the minutes a stop is taken to need until the driver has done one.
    eta_minutes_per_stop: int = Field(default=20, ge=1, le=600)

    # The worker that sends the ship calls and print jobs.
    worker_poll_seconds: float = Field(default=5.0, ge=1, le=300)
    worker_http_timeout_seconds: float = Field(default=10.0, gt=0, le=120)

    @property
    def manifest_printer_or_default(self) -> str:
        return self.manifest_printer.strip() or self.run_sheet_printer

    @field_validator("secret_key")
    @classmethod
    def _strong_secret_key(cls, value: str) -> str:
        if len(value) < MIN_SECRET_KEY or value.startswith("change-me"):
            raise ValueError(
                f"TMS_SECRET_KEY must be at least {MIN_SECRET_KEY} random characters, not the "
                "example; make one with: openssl rand -hex 32")
        return value

    @field_validator("wms_url", "platen_url")
    @classmethod
    def _an_address(cls, value: str, info) -> str:
        value = value.strip()
        if value and not re.match(r"^https?://[^\s/?#]+", value):
            raise ValueError(f"TMS_{info.field_name.upper()} must be an http:// or https:// "
                             "address, or empty")
        return value

    @field_validator("map_tile_url")
    @classmethod
    def _usable_tiles(cls, value: str) -> str:
        value = value.strip()
        if not value:
            return value
        if not all(part in value for part in ("{z}", "{x}", "{y}")):
            raise ValueError("TMS_MAP_TILE_URL needs {z}, {x} and {y} in it, e.g. "
                             "https://tiles.example.com/{z}/{x}/{y}.png")
        if not (value.startswith("https://") or value.startswith("/")):
            raise ValueError("TMS_MAP_TILE_URL must be an https:// address, or a path on this "
                             "server such as /tiles/{z}/{x}/{y}.png")
        if "{s}" in value:
            raise ValueError("TMS_MAP_TILE_URL can't use {s} subdomains: name one host, so "
                             "TMS_MAP_TILE_ORIGIN can let it into the desktop's CSP")
        if OSM_TILES.match(value):
            raise ValueError("TMS_MAP_TILE_URL can't be OpenStreetMap's own tile servers: their "
                             "policy rules out apps. Use a tile provider or a server of your own")
        return value

    @model_validator(mode="after")
    def _origin_of_the_tiles(self) -> "Settings":
        origin = self.map_tile_origin = self.map_tile_origin.strip()
        if origin and not ORIGIN.match(origin):
            raise ValueError("TMS_MAP_TILE_ORIGIN must be https://host or https://host:port, "
                             "with no path, spaces, ; or quotes")
        url = self.map_tile_url
        parts = urlsplit(url) if url.startswith("https://") else None
        wanted = f"https://{parts.netloc}" if parts else ""
        if origin.lower() != wanted.lower():
            raise ValueError(
                f"TMS_MAP_TILE_ORIGIN must be {wanted}, where TMS_MAP_TILE_URL's tiles come from"
                if wanted else
                "TMS_MAP_TILE_ORIGIN must be empty unless TMS_MAP_TILE_URL is an https:// address")
        return self


@lru_cache
def file_settings() -> Settings:
    """What .env (the environment) says, read once at start. The database's
    address and the session key come from here and nowhere else."""
    return Settings()


# How often the API and the worker look again for settings an admin has
# changed on screen: each process takes a change within this many seconds,
# without a restart.
REFRESH_SECONDS = 5.0
_now = time.monotonic
_effective: Settings | None = None
_read_at = 0.0


def get_settings() -> Settings:
    """The settings in force: .env's, with any an admin has given on screen
    in their place (services/runtime_settings.py), read again at most every
    REFRESH_SECONDS. With none given on screen it is file_settings() itself.
    If the database can't be read, the last settings read stay in force."""
    global _effective, _read_at
    now = _now()
    if _effective is None or now - _read_at >= REFRESH_SECONDS:
        # here, not at the top: the database is reached through file_settings()
        from tms.services import runtime_settings

        _effective = runtime_settings.load(file_settings()) or _effective or file_settings()
        _read_at = now
    return _effective


def forget_settings() -> None:
    """Read them again on the next get_settings(): after a change on screen,
    so the process that made it has it at once."""
    global _effective
    _effective = None
