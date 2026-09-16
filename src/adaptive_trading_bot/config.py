"""Typed application configuration loaded from environment variables."""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    """Supported application deployment environments."""

    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """Application settings with safe defaults and ``ATB_`` environment overrides."""

    model_config = SettingsConfigDict(
        env_prefix="ATB_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Environment = Environment.DEVELOPMENT
    app_name: str = "adaptive-trading-bot"
    log_level: str = "INFO"
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)
    database_url: PostgresDsn = PostgresDsn(
        "postgresql+asyncpg://adaptive_bot:local-dev-only@localhost:5432/adaptive_trading_bot"
    )
    historical_market_data_root: Path = Path("data/market_data")
    qualified_research_corpus_root: Path = Path("data/research_corpus")
    ccxt_request_timeout_ms: int = Field(default=10_000, ge=1, le=60_000)
    ccxt_ohlcv_page_limit: int = Field(default=500, ge=1, le=1_000)
    live_trading_enabled: bool = False


@lru_cache
def get_settings() -> Settings:
    """Return a process-wide cached settings instance."""

    return Settings()
