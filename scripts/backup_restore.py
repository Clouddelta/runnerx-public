"""Back up Docker MySQL and verify a restore in a NEW *_restore_test database.

Run from any directory with Python 3.11+. Backups contain application data and
API-key hashes: keep them private. A checksum detects damage, not authenticity;
restore only archives produced by this tool that you trust. No live database is
ever overwritten, and restore imports use a temporary account restricted to the
new test database. The test database remains available for inspection.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone


PROJECT = Path(__file__).resolve().parents[1]
IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")
MYSQL_NAME = re.compile(r"[A-Za-z0-9_]{1,64}\Z")
DUMP_OPTIONS = [
    "--single-transaction", "--quick", "--skip-lock-tables", "--no-tablespaces",
    "--set-gtid-purged=OFF", "--skip-comments", "--skip-dump-date",
    "--skip-add-locks", "--order-by-primary", "--hex-blob",
]
MANIFEST_KEYS = {
    "format_version", "created_at", "source_database", "mysql_version",
    "character_set", "collation", "dump_file", "dump_options", "bytes", "sha256",
}


class BackupError(RuntimeError):
    """An actionable error that is safe to print without credentials."""


def database_name(value: str, *, restore: bool = False) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise BackupError("Database names must start with a letter and contain at most 64 letters, digits or underscores.")
    if restore and not value.endswith("_restore_test"):
        raise BackupError("Restore database must end with _restore_test.")
    return value


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


class DockerMySQL:
    def __init__(self, project: Path = PROJECT):
        self.project = project.resolve()
        if not (self.project / "compose.yaml").is_file():
            raise BackupError("Project compose.yaml was not found.")

    def command(self, script: str, *args: str) -> list[str]:
        return [
            "docker", "compose", "--project-directory", str(self.project),
            "--file", str(self.project / "compose.yaml"), "exec", "-T", "db",
            "sh", "-c", script, "runnerx-backup", *args,
        ]

    def run(self, script: str, *args: str, stage: str, stdin=None, stdout=None):
        try:
            result = subprocess.run(
                self.command(script, *args), cwd=self.project, stdin=stdin,
                stdout=stdout if stdout is not None else subprocess.PIPE,
                stderr=subprocess.PIPE, check=False,
            )
        except OSError as exc:
            raise BackupError(f"{stage} could not start; check Docker Desktop and the docker command.") from exc
        if result.returncode:
            # Do not echo raw SQL, command output, environment values or passwords.
            raise BackupError(f"{stage} failed (exit {result.returncode}); check that the project's db service is healthy.")
        return result.stdout

    def query(self, sql: str, *, root: bool = False) -> str:
        password = "MYSQL_ROOT_PASSWORD" if root else "MYSQL_PASSWORD"
        user = "root" if root else '"$MYSQL_USER"'
        script = f'export MYSQL_PWD="${password}"; exec mysql --protocol=TCP --host=127.0.0.1 --user={user} --batch --skip-column-names --database="$MYSQL_DATABASE"'
        # SQL is sent over stdin, so temporary account passwords never enter argv.
        with tempfile.TemporaryFile() as query_file:
            query_file.write(sql.encode("utf-8"))
            query_file.seek(0)
            return self.run(script, stage="Database query", stdin=query_file).decode("utf-8")

    def source_metadata(self) -> dict:
        row = self.query(
            "SELECT DATABASE(), @@version, DEFAULT_CHARACTER_SET_NAME, DEFAULT_COLLATION_NAME "
            "FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=DATABASE();"
        ).strip().split("\t")
        if len(row) != 4:
            raise BackupError("Could not read source database metadata.")
        database_name(row[0])
        if not all(MYSQL_NAME.fullmatch(value) for value in row[2:]):
            raise BackupError("Unsupported database character set or collation.")
        return dict(zip(("source_database", "mysql_version", "character_set", "collation"), row))

    def dump(self, output, database: str | None = None):
        root = database is not None
        if root:
            database_name(database, restore=True)
        password = "MYSQL_ROOT_PASSWORD" if root else "MYSQL_PASSWORD"
        user = "root" if root else '"$MYSQL_USER"'
        target = '"$1"' if root else '"$MYSQL_DATABASE"'
        script = f'export MYSQL_PWD="${password}"; exec mysqldump --protocol=TCP --host=127.0.0.1 --user={user} {" ".join(DUMP_OPTIONS)} {target}'
        self.run(script, *((database,) if root else ()), stage="Database dump", stdout=output)

    def import_dump(self, path: Path, database: str, user: str, password: str):
        script = (
            'IFS= read -r MYSQL_PWD; export MYSQL_PWD; '
            'exec mysql --protocol=TCP --host=127.0.0.1 --user="$1" '
            '--database="$2" --binary-mode'
        )
        # A temporary stream avoids loading large dumps into memory. Password is
        # consumed by sh; only the following dump is passed to the mysql client.
        with tempfile.TemporaryFile() as stream, path.open("rb") as dump_file:
            stream.write(password.encode("ascii") + b"\n")
            shutil.copyfileobj(dump_file, stream)
            stream.seek(0)
            self.run(script, user, database, stage="Database restore", stdin=stream)

    def table_counts(self, database: str) -> dict[str, int]:
        database_name(database, restore=True)
        names = self.query(
            f"SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA='{database}' "
            "AND TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME;", root=True,
        ).splitlines()
        if not names:
            raise BackupError("Restored database contains no tables.")
        if not all(IDENTIFIER.fullmatch(name) for name in names):
            raise BackupError("Unsupported table name in restored database.")
        counts = self.query(" UNION ALL ".join(
            f"SELECT '{name}', COUNT(*) FROM `{database}`.`{name}`" for name in names
        ) + ";", root=True)
        try:
            result = {name: int(count) for name, count in (line.split("\t") for line in counts.splitlines())}
        except (ValueError, TypeError) as exc:
            raise BackupError("Could not read restored table counts.") from exc
        if set(result) != set(names):
            raise BackupError("Restored table counts are incomplete.")
        return result


def backup(client: DockerMySQL, destination: Path) -> dict:
    if destination.exists() or destination.is_symlink():
        raise BackupError("Backup destination already exists; choose a new directory.")
    metadata = client.source_metadata()
    destination.mkdir(parents=True, mode=0o700)
    dump_path = destination / "database.sql"
    try:
        with dump_path.open("xb") as output:
            os.chmod(dump_path, 0o600)
            client.dump(output)
        size = dump_path.stat().st_size
        if not size:
            raise BackupError("Database dump was empty; no valid backup was created.")
        manifest = {
            "format_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            **metadata, "dump_file": "database.sql", "dump_options": DUMP_OPTIONS,
            "bytes": size, "sha256": digest(dump_path),
        }
        with (destination / "manifest.json").open("x", encoding="utf-8") as output:
            json.dump(manifest, output, indent=2)
            output.write("\n")
    except Exception:
        # Only remove the partial file created by this call; never a directory.
        dump_path.unlink(missing_ok=True)
        raise
    return {"status": "backed_up", "directory": str(destination.resolve()), **manifest}


def validate_archive(directory: Path) -> tuple[dict, Path]:
    manifest_path = directory / "manifest.json"
    if not directory.is_dir() or directory.is_symlink() or manifest_path.is_symlink():
        raise BackupError("Backup must be a regular directory with a regular manifest.")
    try:
        if manifest_path.stat().st_size > 16384:
            raise BackupError("Backup manifest is too large.")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BackupError("Backup manifest is missing or invalid JSON.") from exc
    if not isinstance(manifest, dict) or set(manifest) != MANIFEST_KEYS:
        raise BackupError("Backup manifest fields are invalid.")
    if type(manifest["format_version"]) is not int or manifest["format_version"] != 1:
        raise BackupError("Unsupported backup format version.")
    if manifest["dump_file"] != "database.sql" or manifest["dump_options"] != DUMP_OPTIONS:
        raise BackupError("Backup dump filename or options are invalid.")
    database_name(manifest["source_database"])
    if not all(isinstance(manifest[key], str) and MYSQL_NAME.fullmatch(manifest[key]) for key in ("character_set", "collation")):
        raise BackupError("Backup character set or collation is invalid.")
    if not isinstance(manifest["mysql_version"], str) or not manifest["mysql_version"]:
        raise BackupError("Backup MySQL version is invalid.")
    try:
        created_at = datetime.fromisoformat(manifest["created_at"])
        if created_at.tzinfo is None:
            raise ValueError("missing time zone")
    except (TypeError, ValueError) as exc:
        raise BackupError("Backup creation time is invalid.") from exc
    if type(manifest["bytes"]) is not int or manifest["bytes"] <= 0:
        raise BackupError("Backup size is invalid.")
    if not isinstance(manifest["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", manifest["sha256"]):
        raise BackupError("Backup checksum is invalid.")
    dump_path = directory / "database.sql"
    if not dump_path.is_file() or dump_path.is_symlink():
        raise BackupError("Backup SQL file is missing or is a symbolic link.")
    if dump_path.stat().st_size != manifest["bytes"] or digest(dump_path) != manifest["sha256"]:
        raise BackupError("Backup SQL checksum/size mismatch; restore was not started.")
    return manifest, dump_path


def restore(client: DockerMySQL, directory: Path, target: str) -> dict:
    database_name(target, restore=True)
    manifest, dump_path = validate_archive(directory)
    live = client.source_metadata()
    if target.lower() in {manifest["source_database"].lower(), live["source_database"].lower()}:
        raise BackupError("Restore target must differ from both the backup source and live database.")
    if manifest["mysql_version"] != live["mysql_version"]:
        raise BackupError("Restore verification requires the same MySQL version as the backup.")
    # No IF NOT EXISTS: an existing database is never cleared or reused.
    client.query(
        f"CREATE DATABASE `{target}` CHARACTER SET {manifest['character_set']} COLLATE {manifest['collation']};",
        root=True,
    )
    user = "rx_restore_" + secrets.token_hex(8)
    password = secrets.token_urlsafe(32)
    account_created = False
    try:
        client.query(f"CREATE USER '{user}'@'127.0.0.1' IDENTIFIED BY '{password}';", root=True)
        account_created = True
        # In database-level GRANT statements, underscores are wildcard tokens
        # unless escaped, even inside backticks. Grant this exact test DB only.
        grant_target = target.replace("_", "\\_")
        client.query(f"GRANT ALL PRIVILEGES ON `{grant_target}`.* TO '{user}'@'127.0.0.1';", root=True)
        client.import_dump(dump_path, target, user, password)
        # Matching a canonical re-dump checks schema, data and row identifiers,
        # beyond merely comparing counts. Temporary verification data is private.
        with tempfile.TemporaryFile() as restored_dump:
            client.dump(restored_dump, target)
            restored_dump.seek(0)
            restored_sha256 = hashlib.file_digest(restored_dump, "sha256").hexdigest()
        if restored_sha256 != manifest["sha256"]:
            raise BackupError(f"Restore verification failed; inspect the retained test database {target}.")
        counts = client.table_counts(target)
    finally:
        if account_created:
            client.query(f"DROP USER '{user}'@'127.0.0.1';", root=True)
    return {
        "status": "restore_verified", "source_database": manifest["source_database"],
        "restore_database": target, "sha256": restored_sha256, "tables": counts,
        "note": "Test database retained for inspection; the live database was not overwritten.",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("backup", help="Create a new private backup directory")
    create.add_argument("--output", type=Path, default=None)
    recover = commands.add_parser("restore", help="Verify a trusted backup in a NEW test database")
    recover.add_argument("--backup", type=Path, required=True)
    recover.add_argument("--database", required=True, help="New name ending with _restore_test")
    args = parser.parse_args(argv)
    try:
        client = DockerMySQL()
        if args.command == "backup":
            destination = args.output or PROJECT / ".local" / "backups" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            result = backup(client, destination)
        else:
            result = restore(client, args.backup, args.database)
    except (BackupError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
