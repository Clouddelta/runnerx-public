"""The CV's data lifecycle: real Excel -> ETL -> MySQL -> HTTP API."""
import json

import pytest
from sqlalchemy import func, select

from runnerx.cli import main
from runnerx.etl import import_file
from runnerx.models import ImportBatch, Runner, TrainingSession
from runnerx.sample import generate_sample

pytestmark = pytest.mark.integration


def test_excel_to_api_with_atomic_rejection_and_replay(db, tenant, bootcamp, client, headers, tmp_path):
    files = generate_sample(tmp_path, runners=5)
    for kind in ("registrations", "sessions"):
        result = import_file(db, tenant_id=tenant.id, bootcamp_id=bootcamp.id, path=files[kind], kind=kind)
        assert result["status"] == "committed"
        db.commit()
    assert client.get("/api/v1/runners", headers=headers).json()["total"] == 5
    stats = client.get(f"/api/v1/bootcamps/{bootcamp.id}/stats", headers=headers).json()
    assert stats["runner_count"] == stats["active_runner_count"] == 5
    assert stats["session_count"] == 60
    assert stats["total_distance_km"] > 0
    failed = import_file(db, tenant_id=tenant.id, bootcamp_id=bootcamp.id,
                         path=files["invalid_registrations"], kind="registrations")
    db.commit()
    assert failed["status"] == "rejected"
    report = client.get(f"/api/v1/imports/{failed['batch_id']}", headers=headers)
    assert report.status_code == 200 and len(report.json()["errors"]) >= 2
    replay = import_file(db, tenant_id=tenant.id, bootcamp_id=bootcamp.id,
                         path=files["registrations"], kind="registrations")
    db.commit()
    assert replay["duplicate"]
    assert db.scalar(select(func.count()).select_from(Runner)) == 5
    assert db.scalar(select(func.count()).select_from(TrainingSession)) == 60


def test_cli_rejection_sets_nonzero_exit_and_dry_run_is_read_only(db, tenant, bootcamp, tmp_path, monkeypatch, capsys):
    import runnerx.cli as cli
    monkeypatch.setattr(cli, "get_engine", lambda: db.get_bind())
    files = generate_sample(tmp_path, runners=2)
    report = tmp_path / "report.json"
    args = ["import", "--tenant", tenant.slug, "--bootcamp", bootcamp.code, "--kind", "registrations",
            "--file", str(files["invalid_registrations"]), "--report", str(report)]
    assert main(args + ["--dry-run"]) == 2
    assert json.loads(report.read_text())["status"] == "rejected"
    assert db.scalar(select(func.count()).select_from(ImportBatch)) == 0
    assert main(args) == 2
    db.rollback()  # End the previous read snapshot before observing another session's commit.
    assert db.scalar(select(func.count()).select_from(ImportBatch)) == 1
    assert db.scalar(select(func.count()).select_from(Runner)) == 0
