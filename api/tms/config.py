from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_SECRET_KEY = 32


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

    # Proof of delivery: where signatures and photos are kept on disk (a
    # volume in Compose). Only the API reads and writes them.
    pod_dir: str = "/data/pod"

    # The worker that sends the ship calls and print jobs.
    worker_poll_seconds: float = 5.0
    worker_http_timeout_seconds: float = 10.0

    @field_validator("secret_key")
    @classmethod
    def _strong_secret_key(cls, value: str) -> str:
        if len(value) < MIN_SECRET_KEY or value.startswith("change-me"):
            raise ValueError(
                f"TMS_SECRET_KEY must be at least {MIN_SECRET_KEY} random characters, not the "
                "example; make one with: openssl rand -hex 32")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
