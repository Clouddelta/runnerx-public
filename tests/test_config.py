from fastapi.testclient import TestClient

from runnerx.config import Settings
from runnerx.db import database_url


def test_password_is_encoded_and_socket_is_supported():
    url = database_url(Settings(db_password="synthetic@password/%", db_unix_socket="/cloudsql/project:region:instance"))
    assert url.password == "synthetic@password/%"
    assert url.query["unix_socket"] == "/cloudsql/project:region:instance"
    assert "synthetic@password" not in str(url)


def test_liveness_does_not_connect_to_mysql():
    from runnerx.api import app
    assert TestClient(app).get("/health").status_code == 200
