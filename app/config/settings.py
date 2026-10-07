from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    app_env: str = "development"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/whatsapp_ai"

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

    api_key: SecretStr | None = None

    agent_max_iterations: int = Field(default=6, ge=1, le=20)
    judge_threshold: float = Field(default=0.7, ge=0, le=1)
    judge_max_retries: int = Field(default=1, ge=0, le=3)

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
