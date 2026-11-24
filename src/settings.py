"""
Settings of the API service (database connection, output folder).

Same precedence as env.py: variables set in the process environment win over
the git-ignored .env file. The CLI and Streamlit don't import this module, so
they keep working without a database.

Nothing is read at import time: get_settings() fails with a clear validation
error only when the service actually needs a value that is missing.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env",
        env_file_encoding="utf-8-sig",  # Windows editors may save .env with a BOM
        extra="ignore",                 # .env also holds LLM_* for the CLI/UI
        hide_input_in_errors=True,      # never print a secret in a traceback
    )

    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5433
    postgres_user: str = "proposal"
    postgres_password: SecretStr = Field(min_length=1)
    postgres_db: str = "proposal"

    # Generated proposals of the API service (git-ignored)
    quotes_dir: Path = ROOT / "outputs" / "quotes"

    @property
    def database_url(self) -> URL:
        """URL object, not a string: special characters in the password are
        escaped correctly and str() masks it."""
        return URL.create(
            "postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
