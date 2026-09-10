"""Import and foreign-key behavior against the disposable MySQL test database."""
from datetime import date
from uuid import uuid4

import pandas as pd
import pytest
from sqlalchemy import func, select

from runnerx.etl import import_file
from runnerx.models import Bootcamp, ImportBatch, Registration, Runner, Tenant, TrainingSession
from runnerx.sample import generate_sample


pytestmark = pytest.mark.integration


def _count(db, model, tenant_id):
    return db.scalar(select(func.count()).select_from(model).where(model.tenant_id == tenant_id))


def _import(db, tenant, bootcamp, path, kind="registrations", **kwargs):
    return import_file(db, tenant_id=tenant.id, bootcamp_id=bootcamp.id, path=path, kind=kind, **kwargs)


def test_registration_then_four_weeks_sessions_and_duplicate(db, tenant, bootcamp, tmp_path):
    sample = generate_sample(tmp_path, runners=3)
    first = _import(db, tenant, bootcamp, sample["registrations"])
    assert (first["status"], first["accepted_count"], first["inserted_count"]) == ("committed", 3, 3)
    assert _count(db, Runner, tenant.id) == 3
    assert _count(db, Registration, tenant.id) == 3
    original_ids = set(db.scalars(select(Runner.id).where(Runner.tenant_id == tenant.id)))
    duplicate = _import(db, tenant, bootcamp, sample["registrations"])
    assert duplicate["duplicate"] is True
    assert duplicate["batch_id"] == first["batch_id"]
    assert duplicate["accepted_count"] == duplicate["inserted_count"] == 0
    assert set(db.scalars(select(Runner.id).where(Runner.tenant_id == tenant.id))) == original_ids
    sessions = _import(db, tenant, bootcamp, sample["sessions"], kind="sessions")
    assert sessions["inserted_count"] == 36
    assert _count(db, TrainingSession, tenant.id) == 36
    assert _count(db, ImportBatch, tenant.id) == 2


def test_invalid_batch_is_atomic_and_keeps_existing_runner_unchanged(db, tenant, bootcamp, tmp_path):
    sample = generate_sample(tmp_path, runners=2)
    _import(db, tenant, bootcamp, sample["registrations"])
    existing = db.scalar(select(Runner).where(Runner.tenant_id == tenant.id, Runner.external_id == "SYN0001"))
    original_name = existing.full_name
    path = tmp_path / "mixed.csv"
    pd.DataFrame([
        {"external_id": "SYN0001", "full_name": "Changed Synthetic Name", "birth_year": 1990},
        {"external_id": "SYN-NEW", "full_name": "Sample Invalid", "birth_year": "not a year"},
    ]).to_csv(path, index=False)
    result = _import(db, tenant, bootcamp, path)
    assert result["status"] == "rejected"
    assert result["accepted_count"] == 0 and result["rejected_count"] == 2
    assert result["errors"][0]["row"] == 3 and result["errors"][0]["field"] == "birth_year"
    db.refresh(existing)
    assert existing.full_name == original_name
    assert _count(db, Runner, tenant.id) == _count(db, Registration, tenant.id) == 2
    assert db.get(ImportBatch, result["batch_id"]).status == "rejected"


def test_dry_run_writes_nothing_and_does_not_autoflush(db, tenant, bootcamp, tmp_path):
    sample = generate_sample(tmp_path, runners=2)
    pending = Runner(id=str(uuid4()), tenant_id=tenant.id, external_id="PENDING", full_name="Sample Pending", gender="U")
    db.add(pending)
    result = _import(db, tenant, bootcamp, sample["registrations"], dry_run=True)
    assert result["status"] == "validated" and result["batch_id"] is None
    assert pending in db.new
    with db.no_autoflush:
        assert _count(db, Runner, tenant.id) == 0
        assert _count(db, Registration, tenant.id) == 0
        assert _count(db, ImportBatch, tenant.id) == 0
    db.expunge(pending)
    rejected = _import(db, tenant, bootcamp, sample["invalid_registrations"], dry_run=True)
    assert rejected["status"] == "rejected" and rejected["batch_id"] is None
    assert _count(db, ImportBatch, tenant.id) == 0


def test_external_ids_preserve_distinct_people_and_support_updates(db, tenant, bootcamp, tmp_path):
    path = tmp_path / "same-name.csv"
    data = pd.DataFrame([
        {"external_id": "SYN-A", "full_name": "Sample Same Name", "test_10k_sec": "40:00"},
        {"external_id": "SYN-B", "full_name": "Sample Same Name", "test_10k_sec": "50:00"},
    ])
    data.to_csv(path, index=False)
    _import(db, tenant, bootcamp, path)
    original = {runner.external_id: runner.id for runner in db.scalars(select(Runner).where(Runner.tenant_id == tenant.id))}
    assert len(original) == 2
    data.loc[0, "full_name"] = "Sample Renamed"
    data.to_csv(path, index=False)
    result = _import(db, tenant, bootcamp, path)
    assert result["updated_count"] == 2 and result["inserted_count"] == 0
    assert {runner.external_id: runner.id for runner in db.scalars(select(Runner).where(Runner.tenant_id == tenant.id))} == original


def test_external_ids_are_case_sensitive(db, tenant, bootcamp, tmp_path):
    path = tmp_path / "case-sensitive-identifiers.csv"
    data = pd.DataFrame([
        {"external_id": "SYN-CASE", "full_name": "Sample Same Name"},
        {"external_id": "syn-case", "full_name": "Sample Same Name"},
    ])
    data.to_csv(path, index=False)
    first = _import(db, tenant, bootcamp, path)
    assert first["status"] == "committed" and first["inserted_count"] == 2
    original = {runner.external_id: runner.id for runner in db.scalars(select(Runner).where(Runner.tenant_id == tenant.id))}
    assert set(original) == {"SYN-CASE", "syn-case"}
    assert len(set(original.values())) == 2
    data.loc[1, "full_name"] = "Sample Lowercase Renamed"
    data.to_csv(path, index=False)
    updated = _import(db, tenant, bootcamp, path)
    assert updated["updated_count"] == 2 and updated["inserted_count"] == 0
    current = {runner.external_id: (runner.id, runner.full_name) for runner in db.scalars(select(Runner).where(Runner.tenant_id == tenant.id))}
    assert current["SYN-CASE"] == (original["SYN-CASE"], "Sample Same Name")
    assert current["syn-case"] == (original["syn-case"], "Sample Lowercase Renamed")


def test_session_validation_checks_registration_date_range_and_pace(db, tenant, bootcamp, tmp_path):
    sample = generate_sample(tmp_path, runners=1)
    _import(db, tenant, bootcamp, sample["registrations"])
    path = tmp_path / "bad-sessions.csv"
    pd.DataFrame([
        {"external_id": "SYN0001", "session_date": "2026-01-05", "distance_km": 10, "pace_sec_per_km": "5:30"},
        {"external_id": "SYN-MISSING", "session_date": "2026-01-06", "distance_km": 10, "pace_sec_per_km": "5:30"},
        {"external_id": "SYN0001", "session_date": "2026-02-02", "distance_km": 10, "pace_sec_per_km": "3:30:00"},
    ]).to_csv(path, index=False)
    result = _import(db, tenant, bootcamp, path, kind="sessions")
    assert result["rejected_count"] == 3
    assert {(error["row"], error["field"]) for error in result["errors"]} >= {(3, "external_id"), (4, "session_date"), (4, "pace_sec_per_km")}
    assert _count(db, TrainingSession, tenant.id) == 0


def test_import_scope_does_not_reuse_another_tenants_runner(db, tenant, bootcamp, tmp_path):
    other = Tenant(id=str(uuid4()), slug="sample-" + uuid4().hex[:12], name="Synthetic Other Tenant")
    db.add(other)
    db.flush()
    other_runner = Runner(id=str(uuid4()), tenant_id=other.id, external_id="SYN0001", full_name="Sample Other Tenant", gender="U")
    db.add(other_runner)
    db.flush()
    sample = generate_sample(tmp_path, runners=1)
    _import(db, tenant, bootcamp, sample["registrations"])
    local = db.scalar(select(Runner).where(Runner.tenant_id == tenant.id, Runner.external_id == "SYN0001"))
    assert local.id != other_runner.id
    assert other_runner.full_name == "Sample Other Tenant"
    with pytest.raises(ValueError, match="bootcamp not found"):
        import_file(db, tenant_id=other.id, bootcamp_id=bootcamp.id, path=sample["registrations"], kind="registrations")


def test_runner_must_be_registered_in_current_camp(db, tenant, bootcamp, tmp_path):
    sample = generate_sample(tmp_path, runners=1)
    _import(db, tenant, bootcamp, sample["registrations"])
    other_camp = Bootcamp(id=str(uuid4()), tenant_id=tenant.id, code="other-" + uuid4().hex[:8], name="Synthetic other camp", start_date=date(2026, 1, 5), end_date=date(2026, 2, 1))
    db.add(other_camp)
    db.flush()
    result = _import(db, tenant, other_camp, sample["sessions"], kind="sessions")
    assert result["status"] == "rejected"
    assert result["rejected_count"] == 12
    assert _count(db, TrainingSession, tenant.id) == 0


def test_duplicate_input_keys_are_rejected(db, tenant, bootcamp, tmp_path):
    path = tmp_path / "duplicates.csv"
    pd.DataFrame([
        {"external_id": "SYN0001", "full_name": "Sample One"},
        {"external_id": "SYN0001", "full_name": "Sample Two"},
    ]).to_csv(path, index=False)
    result = _import(db, tenant, bootcamp, path)
    assert result["status"] == "rejected"
    assert result["errors"] == [{"row": 3, "field": "external_id", "message": "duplicate import key in this file"}]
    assert _count(db, Runner, tenant.id) == 0


def test_invalid_precision_rejects_entire_batch_without_business_changes(db, tenant, bootcamp, tmp_path):
    sample = generate_sample(tmp_path, runners=1)
    _import(db, tenant, bootcamp, sample["registrations"])
    _import(db, tenant, bootcamp, sample["sessions"], kind="sessions")
    snapshot_query = select(TrainingSession.id, TrainingSession.session_date, TrainingSession.distance_km, TrainingSession.notes).where(TrainingSession.tenant_id == tenant.id).order_by(TrainingSession.id)
    before = db.execute(snapshot_query).all()
    path = tmp_path / "tiny-distance.csv"
    pd.DataFrame([
        {"external_id": "SYN0001", "session_date": "2026-01-05", "distance_km": "99.000", "pace_sec_per_km": 330, "notes": "Sample update must not be applied"},
        {"external_id": "SYN0001", "session_date": "2026-01-06", "distance_km": "0.0001", "pace_sec_per_km": 330},
    ]).to_csv(path, index=False)
    result = _import(db, tenant, bootcamp, path, kind="sessions")
    assert result["status"] == "rejected"
    assert result["accepted_count"] == result["inserted_count"] == result["updated_count"] == 0
    assert result["rejected_count"] == 2
    assert result["errors"][0]["row"] == 3 and result["errors"][0]["field"] == "distance_km"
    assert db.execute(snapshot_query).all() == before
    assert _count(db, TrainingSession, tenant.id) == 12
    assert _count(db, Runner, tenant.id) == _count(db, Registration, tenant.id) == 1


def test_missing_required_header_is_reported(db, tenant, bootcamp, tmp_path):
    path = tmp_path / "missing.csv"
    path.write_text("full_name\nSample No Identifier\n", encoding="utf-8")
    result = _import(db, tenant, bootcamp, path)
    assert result["status"] == "rejected"
    assert result["errors"][0] == {"row": 2, "field": "external_id", "message": "required value is missing"}
