"""Offline tests for archive integrity, restore boundaries and Docker failures."""

import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


spec = importlib.util.spec_from_file_location(
    "runnerx_backup_restore", Path(__file__).resolve().parents[1] / "scripts" / "backup_restore.py"
)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


SQL = b"CREATE TABLE `example` (`id` int PRIMARY KEY);\nINSERT INTO `example` VALUES (1);\n"


class FakeMySQL:
    def __init__(self, *, failure=None, source="runnerx", restored_sql=SQL):
        self.failure = failure
        self.source = source
        self.restored_sql = restored_sql
        self.queries = []
        self.imports = []

    def source_metadata(self):
        return {
            "source_database": self.source, "mysql_version": "8.4.8",
            "character_set": "utf8mb4", "collation": "utf8mb4_0900_ai_ci",
        }

    def dump(self, output, database=None):
        output.write(self.restored_sql if database else SQL)
        if self.failure == "dump":
            raise tool.BackupError("dump failed")

    def query(self, sql, *, root=False):
        assert root
        self.queries.append(sql)
        if self.failure == "existing" and sql.startswith("CREATE DATABASE"):
            raise tool.BackupError("database already exists")
        if self.failure == "grant" and sql.startswith("GRANT"):
            raise tool.BackupError("grant failed")
        return ""

    def import_dump(self, path, database, user, password):
        self.imports.append((path, database, user, password))
        if self.failure == "import":
            raise tool.BackupError("import failed")

    def table_counts(self, database):
        return {"example": 1}


@pytest.fixture
def archive(tmp_path):
    target = tmp_path / "backup"
    tool.backup(FakeMySQL(), target)
    return target


def change_manifest(directory, **changes):
    path = directory / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest.update(changes)
    path.write_text(json.dumps(manifest), encoding="utf-8")


def test_backup_manifest_matches_actual_sql(archive):
    manifest, dump_path = tool.validate_archive(archive)
    assert manifest["sha256"] == tool.digest(dump_path)
    assert manifest["bytes"] == len(SQL)
    assert manifest["source_database"] == "runnerx"
    assert dump_path.read_bytes() == SQL


def test_backup_refuses_existing_output_and_keeps_files(archive):
    before = (archive / "database.sql").read_bytes()
    with pytest.raises(tool.BackupError, match="already exists"):
        tool.backup(FakeMySQL(), archive)
    assert (archive / "database.sql").read_bytes() == before


def test_failed_dump_does_not_leave_a_valid_archive(tmp_path):
    destination = tmp_path / "failed"
    with pytest.raises(tool.BackupError, match="dump failed"):
        tool.backup(FakeMySQL(failure="dump"), destination)
    assert not (destination / "database.sql").exists()
    assert not (destination / "manifest.json").exists()


@pytest.mark.parametrize("name", ["runnerx", "../x_restore_test", "x;DROP DATABASE runnerx_restore_test", "1x_restore_test", "x" * 65, "x-restore_test", ""])
def test_invalid_target_is_rejected_before_database_access(archive, name):
    client = FakeMySQL()
    with pytest.raises(tool.BackupError):
        tool.restore(client, archive, name)
    assert client.queries == []


@pytest.mark.parametrize("changes", [
    {"dump_file": "../private.sql"}, {"dump_file": "C:/private.sql"},
    {"bytes": -1}, {"bytes": True}, {"sha256": "no-checksum"},
    {"format_version": True}, {"format_version": 2}, {"dump_options": []},
    {"source_database": "x`; DROP DATABASE runnerx; --"},
    {"character_set": "utf8mb4;DROP DATABASE runnerx"},
    {"collation": None}, {"created_at": "not-a-date"},
    {"created_at": "2026-01-01T00:00:00"}, {"mysql_version": None},
])
def test_invalid_manifest_is_rejected_before_database_access(archive, changes):
    change_manifest(archive, **changes)
    client = FakeMySQL()
    with pytest.raises(tool.BackupError):
        tool.restore(client, archive, "runnerx_restore_test")
    assert client.queries == []


def test_tampered_sql_is_rejected_even_when_length_is_unchanged(archive):
    path = archive / "database.sql"
    path.write_bytes(SQL.replace(b"VALUES (1)", b"VALUES (2)"))
    client = FakeMySQL()
    with pytest.raises(tool.BackupError, match="checksum/size mismatch"):
        tool.restore(client, archive, "runnerx_restore_test")
    assert client.queries == []


def test_missing_or_non_object_manifest_is_rejected(archive):
    path = archive / "manifest.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(tool.BackupError, match="fields"):
        tool.validate_archive(archive)
    path.unlink()
    with pytest.raises(tool.BackupError, match="missing"):
        tool.validate_archive(archive)


def test_symlink_sql_is_rejected(archive, tmp_path):
    path = archive / "database.sql"
    path.unlink()
    original = tmp_path / "elsewhere.sql"
    original.write_bytes(SQL)
    try:
        path.symlink_to(original)
    except OSError:
        pytest.skip("Host does not allow creating symbolic links")
    with pytest.raises(tool.BackupError, match="symbolic link"):
        tool.validate_archive(archive)


def test_existing_target_is_not_dropped_or_imported(archive):
    client = FakeMySQL(failure="existing")
    with pytest.raises(tool.BackupError, match="already exists"):
        tool.restore(client, archive, "runnerx_restore_test")
    assert len(client.queries) == 1
    assert "IF NOT EXISTS" not in client.queries[0]
    assert not client.imports


def test_live_source_cannot_be_restore_target(archive):
    client = FakeMySQL(source="runnerx_restore_test")
    with pytest.raises(tool.BackupError, match="differ"):
        tool.restore(client, archive, "runnerx_restore_test")
    assert client.queries == []


def test_backup_source_cannot_be_restore_target(archive):
    change_manifest(archive, source_database="runnerx_restore_test")
    client = FakeMySQL()
    with pytest.raises(tool.BackupError, match="differ"):
        tool.restore(client, archive, "runnerx_restore_test")
    assert client.queries == []


def test_version_mismatch_is_rejected_before_creating_database(archive):
    change_manifest(archive, mysql_version="8.0.44")
    client = FakeMySQL()
    with pytest.raises(tool.BackupError, match="same MySQL version"):
        tool.restore(client, archive, "runnerx_restore_test")
    assert client.queries == []


def test_verified_restore_uses_isolated_account_then_removes_it(archive):
    client = FakeMySQL()
    report = tool.restore(client, archive, "runnerx_restore_test")
    assert report["status"] == "restore_verified"
    assert report["tables"] == {"example": 1}
    assert report["sha256"] == tool.digest(archive / "database.sql")
    _, database, user, password = client.imports[0]
    assert database == "runnerx_restore_test"
    assert user.startswith("rx_restore_") and len(user) <= 32
    assert len(password) >= 32
    grant = next(query for query in client.queries if query.startswith("GRANT"))
    assert "`runnerx\\_restore\\_test`.*" in grant
    assert client.queries[-1].startswith("DROP USER")
    assert not any("DROP DATABASE" in query for query in client.queries)


@pytest.mark.parametrize("failure", ["grant", "import"])
def test_restore_error_removes_temporary_account_but_preserves_database(archive, failure):
    client = FakeMySQL(failure=failure)
    with pytest.raises(tool.BackupError):
        tool.restore(client, archive, "runnerx_restore_test")
    assert client.queries[-1].startswith("DROP USER")
    assert not any("DROP DATABASE" in query for query in client.queries)


def test_content_mismatch_fails_verification_even_with_same_counts(archive):
    client = FakeMySQL(restored_sql=SQL.replace(b"VALUES (1)", b"VALUES (2)"))
    with pytest.raises(tool.BackupError, match="verification failed"):
        tool.restore(client, archive, "runnerx_restore_test")
    assert client.queries[-1].startswith("DROP USER")


def docker_client(tmp_path):
    (tmp_path / "compose.yaml").write_text("services: {}", encoding="utf-8")
    return tool.DockerMySQL(tmp_path)


def test_command_failure_does_not_disclose_stderr(monkeypatch, tmp_path):
    client = docker_client(tmp_path)
    monkeypatch.setattr(tool.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=b"", stderr=b"secret-password"))
    with pytest.raises(tool.BackupError, match="Database dump failed") as error:
        client.dump(io.BytesIO())
    assert "secret-password" not in str(error.value)


def test_missing_docker_is_reported_cleanly(monkeypatch, tmp_path):
    client = docker_client(tmp_path)
    def missing(*args, **kwargs):
        raise FileNotFoundError("docker")
    monkeypatch.setattr(tool.subprocess, "run", missing)
    with pytest.raises(tool.BackupError, match="could not start"):
        client.dump(io.BytesIO())


def test_password_never_appears_in_host_command_arguments(monkeypatch, tmp_path):
    client = docker_client(tmp_path)
    captured = []
    def command(args, **kwargs):
        captured.append((args, kwargs["stdin"].read()))
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
    monkeypatch.setattr(tool.subprocess, "run", command)
    client.query("CREATE USER 'test' IDENTIFIED BY 'secret-test-password';", root=True)
    dump_path = tmp_path / "database.sql"
    dump_path.write_bytes(SQL)
    client.import_dump(dump_path, "runnerx_restore_test", "restore_user", "secret-test-password")
    for args, payload in captured:
        assert "secret-test-password" not in " ".join(args)
        assert "secret-test-password" in payload.decode()
        assert args[0:2] == ["docker", "compose"]
        assert args[args.index("exec") + 1:args.index("exec") + 3] == ["-T", "db"]
    assert captured[1][1] == b"secret-test-password\n" + SQL
