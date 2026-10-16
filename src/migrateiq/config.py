from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://migrateiq:migrateiq@localhost:5432/migrateiq"
    api_base_url: str = "http://localhost:8000"
    demo_crm_url: str = "http://localhost:8001"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"
    upload_dir: Path = Path("uploads")
    max_upload_mb: int = Field(default=50, ge=1, le=500)
    allow_private_networks: bool = False
    worker_poll_seconds: float = Field(default=2, ge=0.2, le=60)
    secret_encryption_key: str | None = None

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
