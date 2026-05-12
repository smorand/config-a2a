"""Application settings loaded from environment variables."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the A2A agent service."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "config-a2a"
    host: str = "0.0.0.0"  # nosec B104
    port: int = 8000
    log_level: str = "INFO"
    config_path: Path = Path("examples/agent.yaml")


def get_settings() -> Settings:
    """Return a Settings instance; FastAPI dependency-injection friendly."""
    return Settings()
