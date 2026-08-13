from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 39057
    app_base_path: str = ""
    public_base_url: str = ""
    admin_api_token: str = ""
    public_link_secret: str = ""
    public_link_lifetime_days: int = Field(default=30, ge=1, le=365)

    dingtalk_app_id: str = ""
    dingtalk_agent_id: str = ""
    dingtalk_app_key: str = ""
    dingtalk_app_secret: str = ""
    dingtalk_aitable_operator_id: str = ""
    dingtalk_callback_token: str = ""

    aitable_base_id: str = ""
    bi_center_base_url: str = "http://127.0.0.1:39054"
    bi_center_api_token: str = ""

    ai_base_url: str = ""
    ai_api_key: str = ""
    ai_model: str = ""
    ai_provider: str = ""
    ai_include_person_names: bool = False
    ai_max_items: int = Field(default=120, ge=10, le=500)
    ai_max_text_chars: int = Field(default=600, ge=100, le=4000)

    database_path: str = "runtime/weekly_report_assistant.db"
    artifact_dir: str = "runtime/reports"
    scheduler_enabled: bool = True
    scheduler_poll_seconds: int = Field(default=30, ge=5, le=3600)
    http_timeout_seconds: int = Field(default=30, ge=3, le=180)

    @property
    def database_file(self) -> Path:
        return Path(self.database_path).expanduser().resolve()

    @property
    def artifact_path(self) -> Path:
        return Path(self.artifact_dir).expanduser().resolve()

    @property
    def normalized_base_path(self) -> str:
        value = self.app_base_path.strip()
        if not value or value == "/":
            return ""
        return f"/{value.strip('/')}"

    @property
    def production_like(self) -> bool:
        return self.app_env.strip().lower() in {"production", "prod", "staging"}

    @property
    def dingtalk_configured(self) -> bool:
        return bool(self.dingtalk_app_key.strip() and self.dingtalk_app_secret.strip())

    @property
    def aitable_configured(self) -> bool:
        return bool(
            self.dingtalk_configured
            and self.aitable_base_id.strip()
            and self.dingtalk_aitable_operator_id.strip()
        )

    @property
    def bi_center_configured(self) -> bool:
        return bool(self.bi_center_base_url.strip() and self.bi_center_api_token.strip())

    @property
    def ai_configured(self) -> bool:
        return bool(self.ai_base_url.strip() and self.ai_api_key.strip() and self.ai_model.strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
