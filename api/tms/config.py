from functools import lru_cache

import re

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
    model_config = SettingsConfigDict(env_prefix="TMS_", extra="ignore")

    database_url: str = "postgresql+psycopg://tms:tms@127.0.0.1:5432/tms"
    # Signs session tokens. No usable default: start-up refuses a weak one.
    secret_key: str = "change-me"

    # Simple WMS. The webhook secret is the one its subscriber row signs with;
    # the URL and key are for reading a delivery's ship-to when it is packed.
    wms_webhook_secret: str = ""
    wms_url: str = ""
    wms_api_key: str = ""
    # Shorter than the WMS worker's own 10 s wait, so it hears our answer.
    wms_timeout_seconds: float = 5.0

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
    worker_poll_seconds: float = 5.0
    worker_http_timeout_seconds: float = 10.0

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
def get_settings() -> Settings:
    return Settings()
