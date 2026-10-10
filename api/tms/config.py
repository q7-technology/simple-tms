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
