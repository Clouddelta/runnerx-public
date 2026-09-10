"""Windows development MySQL instance, isolated from installed MySQL services.

Only the private instance's generated administrator credential can stop it.
Runtime credentials and database files stay in .local/, outside version control.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time

import pymysql

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local" / "native"
STATE = LOCAL / "state.json"


def connection(state, initial=False):
    return pymysql.connect(host="127.0.0.1", port=state["port"], user="root",
                           password="" if initial else state["root_password"],
                           autocommit=True, connect_timeout=2)


def prepare(bin_dir, port):
    executable = (Path(bin_dir) / "mysqld.exe").resolve()
    if not executable.is_file():
        raise ValueError("mysqld.exe not found; pass -MySqlBin to run-local.ps1")
    LOCAL.mkdir(parents=True, exist_ok=True)
    existing = STATE.exists()
    state = json.loads(STATE.read_text()) if existing else {
        "port": port, "root_password": secrets.token_urlsafe(32),
        "db_password": secrets.token_urlsafe(32), "api_key": secrets.token_urlsafe(32),
    }
    if state["port"] != port:
        raise ValueError("Existing private database uses another port; use its original port")
    with socket.socket() as probe:
        occupied = probe.connect_ex(("127.0.0.1", port)) == 0
    if occupied:
        if not existing:
            raise ValueError("Requested database port is in use; choose a different -DatabasePort")
        with connection(state) as cn:
            with cn.cursor() as cursor:
                cursor.execute("SELECT @@datadir")
                actual = Path(cursor.fetchone()[0]).resolve()
                if actual != (LOCAL / "data").resolve():
                    raise ValueError("Port belongs to another database; refusing to manage it")
        print("Private MySQL instance is already running")
        return
    data = LOCAL / "data"
    base_args = [str(executable), "--no-defaults", f"--basedir={executable.parent.parent}", f"--datadir={data}"]
    initialized = (data / "mysql").is_dir()
    if not initialized:
        if existing:
            raise ValueError("State exists but the data directory is incomplete; inspect .local/native manually")
        # Save private state first so a failed launch never loses recovery context.
        STATE.write_text(json.dumps(state), encoding="utf-8")
        subprocess.run(base_args + ["--initialize-insecure", "--console"], check=True)
    elif not existing:
        raise ValueError("A data directory exists without private credentials; refusing to initialize over it")
    with (LOCAL / "mysql.log").open("ab") as log:
        process = subprocess.Popen(base_args + [f"--port={port}", "--bind-address=127.0.0.1", "--mysqlx=0", "--console"],
                                   stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
    for _ in range(60):
        if process.poll() is not None:
            raise ValueError("Private MySQL failed to start; inspect .local/native/mysql.log")
        try:
            cn = connection(state, initial=not initialized)
            break
        except pymysql.MySQLError:
            time.sleep(0.5)
    else:
        raise ValueError("Private MySQL did not become ready; inspect .local/native/mysql.log")
    with cn:
        if not initialized:
            with cn.cursor() as cursor:
                for name in ("runnerx", "runnerx_test"):
                    cursor.execute(f"CREATE DATABASE {name} CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
                cursor.execute("CREATE USER 'runnerx'@'localhost' IDENTIFIED BY %s", (state["db_password"],))
                for name in ("runnerx", "runnerx_test"):
                    cursor.execute(f"GRANT ALL PRIVILEGES ON {name}.* TO 'runnerx'@'localhost'")
                cursor.execute("ALTER USER 'root'@'localhost' IDENTIFIED BY %s", (state["root_password"],))
    print(f"Private MySQL ready on 127.0.0.1:{port}")


def stop():
    state = json.loads(STATE.read_text())
    with connection(state) as cn:
        with cn.cursor() as cursor:
            cursor.execute("SELECT @@datadir")
            if Path(cursor.fetchone()[0]).resolve() != (LOCAL / "data").resolve():
                raise ValueError("Refusing to stop an unrecognized database")
            cursor.execute("SHUTDOWN")
    print("Private MySQL stopped; data retained")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "stop"])
    parser.add_argument("--bin-dir", default=r"C:\Program Files\MySQL\MySQL Server 8.0\bin")
    parser.add_argument("--port", type=int, default=3318)
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("This helper is for native Windows MySQL; use Docker Compose on other systems")
    try:
        prepare(args.bin_dir, args.port) if args.action == "prepare" else stop()
    except (ValueError, OSError, pymysql.MySQLError) as exc:
        raise SystemExit(f"Local database operation failed ({type(exc).__name__}). {exc if isinstance(exc, ValueError) else 'Inspect local configuration and logs.'}")
