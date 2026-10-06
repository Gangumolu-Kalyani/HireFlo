"""Application settings loaded from environment variables / .env."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

    openrouter_api_key: SecretStr | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    llm_model: str = "anthropic/claude-sonnet-5.5"

    # Optional pricing in USD per 1 million tokens.
    # Token usage is still tracked when pricing is not configured.
    llm_input_cost_per_million: float | None = Field(
        default=None,
        ge=0,
    )
    llm_output_cost_per_million: float | None = Field(
        default=None,
        ge=0,
    )

    # Persistent PostgreSQL storage used for production audit records.
    # Kept optional so local development and tests do not require a database.
    database_url: SecretStr | None = None

    # Hard cap on agent loop iterations to prevent runaway loops.
    agent_max_iterations: int = Field(default=10, ge=1, le=50)

    @field_validator(
        "llm_input_cost_per_million",
        "llm_output_cost_per_million",
        mode="before",
    )
    @classmethod
    def empty_optional_float_as_none(cls, value: object) -> object:
        """Treat blank optional pricing environment variables as unset."""
        if isinstance(value, str) and not value.strip():
            return None
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
