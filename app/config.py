from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    bot_token: SecretStr

    admin_ids: str = ""
    database_url: str = f"sqlite+aiosqlite:///{(BASE_DIR / 'data' / 'bot.db').as_posix()}"
    log_level: str = "INFO"

    ignore_admin_messages: bool = True
    warn_on_delete: bool = False
    warn_ttl_seconds: int = Field(default=5, ge=1, le=120)

    openrouter_api_key: SecretStr | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "nvidia/nemotron-3-ultra-550b-a55b:free"
    openrouter_site_url: str = ""
    openrouter_app_name: str = ""
    ai_max_history: int = Field(default=20, ge=2, le=100)

    ai_moderation_model: str = ""
    ai_moderation_timeout: int = Field(default=20, ge=5, le=120)
    ai_moderation_error_cooldown: int = Field(default=300, ge=0, le=3600)

    @field_validator("log_level")
    @classmethod
    def _normalize_log_level(cls, value: str) -> str:
        return value.strip().upper()

    @property
    def admin_id_list(self) -> list[int]:
        result: list[int] = []
        for chunk in self.admin_ids.replace(";", ",").split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            try:
                result.append(int(chunk))
            except ValueError:
                continue
        return result

    @property
    def admin_id_set(self) -> frozenset[int]:
        return frozenset(self.admin_id_list)

    @property
    def database_url_resolved(self) -> str:
        """Return an absolute SQLite URL and make sure its directory exists."""
        url = self.database_url
        prefix = "sqlite+aiosqlite:///"
        if not url.startswith(prefix):
            return url

        raw_path = url[len(prefix) :]
        if not raw_path or raw_path == ":memory:":
            return url

        path = Path(raw_path)
        if not path.is_absolute():
            path = BASE_DIR / path
        path.parent.mkdir(parents=True, exist_ok=True)
        return f"{prefix}{path.as_posix()}"

    @property
    def openrouter_key(self) -> str | None:
        if self.openrouter_api_key is None:
            return None
        value = self.openrouter_api_key.get_secret_value().strip()
        return value or None


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
