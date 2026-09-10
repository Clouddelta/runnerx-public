"""Explicit environment configuration; importing the app never opens a database."""
from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    app_env: str = "local"
    db_host: str = "127.0.0.1"
    db_port: int = 3308
    db_user: str = "runnerx"
    db_password: str = ""
    db_name: str = "runnerx"
    db_unix_socket: str = ""
    database_url: str = ""
    docs_enabled: bool = True


def get_settings() -> Settings:
    # Only the working directory's file is read; do not discover parent secrets.
    load_dotenv(Path.cwd() / ".env", override=False)
    environment = os.getenv("APP_ENV", "local")
    return Settings(
        app_env=environment,
        db_host=os.getenv("DB_HOST", "127.0.0.1"),
        db_port=int(os.getenv("DB_PORT", "3308")),
        db_user=os.getenv("DB_USER", "runnerx"),
        db_password=os.getenv("DB_PASSWORD", ""),
        db_name=os.getenv("DB_NAME", "runnerx"),
        db_unix_socket=os.getenv("DB_UNIX_SOCKET", ""),
        database_url=os.getenv("RUNNERX_DATABASE_URL", ""),
        docs_enabled=os.getenv("API_DOCS_ENABLED", "false" if environment == "production" else "true").lower() == "true",
    )
