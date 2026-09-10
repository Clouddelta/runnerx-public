from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import URL, Engine
from sqlalchemy.orm import Session

from runnerx.config import Settings, get_settings


def database_url(settings: Settings | None = None) -> str | URL:
    settings = settings or get_settings()
    if settings.database_url:
        if not settings.database_url.startswith("mysql+pymysql://"):
            raise ValueError("RUNNERX_DATABASE_URL must use mysql+pymysql")
        return settings.database_url
    if not settings.db_password:
        raise ValueError("Set DB_PASSWORD or RUNNERX_DATABASE_URL before connecting")
    query = {"charset": "utf8mb4"}
    if settings.db_unix_socket:
        query["unix_socket"] = settings.db_unix_socket
    return URL.create(
        "mysql+pymysql", username=settings.db_user, password=settings.db_password,
        host=settings.db_host, port=settings.db_port, database=settings.db_name, query=query,
    )


@lru_cache(maxsize=4)
def _engine(url: str | URL) -> Engine:
    return create_engine(
        url, pool_pre_ping=True, pool_recycle=1800, pool_size=5, max_overflow=5,
        connect_args={"connect_timeout": 10}, hide_parameters=True,
        isolation_level="READ COMMITTED",
    )


def get_engine() -> Engine:
    return _engine(database_url())


def get_session():
    with Session(get_engine(), expire_on_commit=False) as session:
        try:
            yield session
        except Exception:
            session.rollback()
            raise
