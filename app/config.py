from functools import lru_cache

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from process environment variables only."""

    model_config = SettingsConfigDict(
        env_prefix="",
        case_sensitive=False,
        extra="ignore",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    manus_api_key: SecretStr | None = Field(default=None, validation_alias="MANUS_API_KEY")
    manus_task_id: str | None = Field(default=None, validation_alias="MANUS_TASK_ID")
    manus_project_id: str | None = Field(default=None, validation_alias="MANUS_PROJECT_ID")
    manus_api_base_url: AnyHttpUrl = Field(
        default="https://api.manus.ai",
        validation_alias="MANUS_API_BASE_URL",
    )
    manus_api_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        le=300,
        validation_alias="MANUS_API_TIMEOUT_SECONDS",
    )
    manus_response_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        le=300,
        validation_alias="MANUS_RESPONSE_TIMEOUT_SECONDS",
    )
    manus_poll_interval_seconds: float = Field(
        default=0.75,
        gt=0,
        le=5,
        validation_alias="MANUS_POLL_INTERVAL_SECONDS",
    )
    cors_origins_raw: str = Field(
        default="http://localhost:3000,http://localhost:5173",
        validation_alias="CORS_ORIGINS",
    )

    @field_validator("manus_api_key", mode="before")
    @classmethod
    def normalize_api_key(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, SecretStr):
            return value
        if not isinstance(value, str):
            raise TypeError("MANUS_API_KEY must be a string")
        normalized = value.strip()
        return normalized or None

    @field_validator("manus_task_id", "manus_project_id", mode="before")
    @classmethod
    def normalize_optional_identifier(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("Manus identifiers must be strings")
        normalized = value.strip()
        return normalized or None

    @field_validator("cors_origins_raw")
    @classmethod
    def validate_cors_origins_raw(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("CORS_ORIGINS must contain at least one origin")
        origins = [item.strip() for item in value.split(",") if item.strip()]
        if not origins:
            raise ValueError("CORS_ORIGINS must contain at least one origin")
        if "*" in origins:
            raise ValueError("CORS_ORIGINS must not use '*' with credentialed CORS")
        return ",".join(origins)

    @property
    def cors_origins(self) -> list[str]:
        return [item.strip() for item in self.cors_origins_raw.split(",") if item.strip()]

    @property
    def manus_api_configured(self) -> bool:
        return self.manus_api_key is not None

    @property
    def manus_api_url(self) -> str:
        return str(self.manus_api_base_url).rstrip("/")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
