"""Integration tests only touch an explicitly supplied database ending in _test."""
from datetime import date
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from runnerx.auth import hash_api_key
from runnerx.db import get_session
from runnerx.models import ApiKey, Base, Bootcamp, Tenant


@pytest.fixture(scope="session")
def engine():
    value = os.getenv("RUNNERX_TEST_DATABASE_URL")
    if not value:
        pytest.skip("Set RUNNERX_TEST_DATABASE_URL to a disposable MySQL database ending in _test")
    url = make_url(value)
    if url.drivername != "mysql+pymysql" or not url.database or not url.database.endswith("_test"):
        pytest.fail("Refusing test cleanup: explicit mysql+pymysql URL with a database ending in _test is required")
    previous = os.environ.get("RUNNERX_DATABASE_URL")
    os.environ["RUNNERX_DATABASE_URL"] = value
    try:
        command.upgrade(Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head")
    finally:
        if previous is None:
            os.environ.pop("RUNNERX_DATABASE_URL", None)
        else:
            os.environ["RUNNERX_DATABASE_URL"] = previous
    result = create_engine(url, pool_pre_ping=True, hide_parameters=True, isolation_level="READ COMMITTED")
    yield result
    result.dispose()


@pytest.fixture
def db(engine):
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(delete(table))
    with Session(engine, expire_on_commit=False) as session:
        yield session
        session.rollback()


def _tenant(db, slug, token):
    obj = Tenant(slug=slug, name=f"Synthetic {slug}")
    db.add(obj)
    db.flush()
    db.add(ApiKey(tenant_id=obj.id, name="test", key_hash=hash_api_key(token)))
    db.commit()
    return obj


@pytest.fixture
def tenant(db):
    return _tenant(db, "test-one", "synthetic-test-tenant-one-api-key-2026")


@pytest.fixture
def other_tenant(db):
    return _tenant(db, "test-two", "synthetic-test-tenant-two-api-key-2026")


@pytest.fixture
def headers(tenant):
    return {"Authorization": "Bearer synthetic-test-tenant-one-api-key-2026"}


@pytest.fixture
def other_headers(other_tenant):
    return {"Authorization": "Bearer synthetic-test-tenant-two-api-key-2026"}


@pytest.fixture
def bootcamp(db, tenant):
    obj = Bootcamp(tenant_id=tenant.id, code="demo-2026", name="Synthetic Demo Camp", city="Demo City",
                   start_date=date(2026, 1, 5), end_date=date(2026, 2, 1))
    db.add(obj)
    db.commit()
    return obj


@pytest.fixture
def client(db, engine):
    from runnerx.api import app

    def session_override():
        with Session(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    with TestClient(app) as result:
        yield result
    app.dependency_overrides.clear()
