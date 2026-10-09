from functools import lru_cache
from pathlib import Path

from psycopg2.extensions import parse_dsn
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    app_env: str = "development"
    database_url: SecretStr | None = None
    db_schema: str | None = Field(default=None, pattern=r"^[a-z_][a-z0-9_]{0,62}$")
    database: str = "whatsapp_ai"
    host: str = "localhost"
    port: int = Field(default=5432, ge=1, le=65535)
    db_user: str = "postgres"
    password: SecretStr = SecretStr("")

    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4o-mini"
    openai_judge_model: str | None = None

    whatsapp_profile_dir: Path = Path("playwright/.auth/whatsapp_profile")
    whatsapp_headless: bool = True
    whatsapp_url: str = "https://web.whatsapp.com"
    whatsapp_load_timeout_s: int = 90
    max_messages_per_contact: int = Field(default=3, ge=1, le=50)
    max_chats_per_scrape: int = Field(default=200, ge=1)
    include_groups: bool = False

    agent_max_iterations: int = Field(default=6, ge=1, le=20)
    judge_threshold: float = Field(default=0.7, ge=0, le=1)
    judge_max_retries: int = Field(default=1, ge=0, le=3)

    @field_validator("db_schema", mode="before")
    @classmethod
    def empty_schema_is_unset(cls, value: str | None) -> str | None:
        return value or None

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple:
        def legacy_dotenv() -> dict:
            values = dotenv_settings()
            # Never interpret the OS USER environment variable as the database user.
            if "user" in values:
                values.setdefault("db_user", values.pop("user"))
            return values

        return init_settings, env_settings, legacy_dotenv, file_secret_settings

    @property
    def db_config(self) -> dict:
        if self.database_url and self.database_url.get_secret_value():
            url = self.database_url.get_secret_value()
            for driver in ("postgresql+asyncpg://", "postgresql+psycopg2://"):
                url = url.replace(driver, "postgresql://", 1)
            return parse_dsn(url)
        return {
            "dbname": self.database,
            "host": self.host,
            "port": self.port,
            "user": self.db_user,
            "password": self.password.get_secret_value(),
        }

    @property
    def profile_path(self) -> Path:
        p = self.whatsapp_profile_dir
        return p if p.is_absolute() else PROJECT_ROOT / p

    @property
    def judge_model(self) -> str:
        return self.openai_judge_model or self.openai_model


@lru_cache
def get_settings() -> Settings:
    return Settings()
