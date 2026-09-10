"""API integration tests against the disposable MySQL fixtures in conftest."""

from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
import runpy
from threading import Event

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import event

from runnerx.models import ImportBatch


pytestmark = pytest.mark.integration
PREFIX = "/api/v1"


def create(client, headers, resource, payload):
    response = client.post(f"{PREFIX}/{resource}", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def seed(client, headers, label="demo"):
    runner = create(client, headers, "runners", {"external_id": f"{label}-01", "full_name": "Synthetic Runner"})
    camp = create(client, headers, "bootcamps", {
        "code": label, "name": "Synthetic Camp", "start_date": "2026-01-01", "end_date": "2026-02-28",
    })
    group = create(client, headers, "groups", {"bootcamp_id": camp["id"], "name": "A"})
    registration = create(client, headers, "registrations", {
        "bootcamp_id": camp["id"], "runner_id": runner["id"], "group_id": group["id"],
    })
    session = create(client, headers, "sessions", {
        "bootcamp_id": camp["id"], "runner_id": runner["id"], "session_date": "2026-01-12", "distance_km": 10.5,
    })
    return {"runners": runner, "bootcamps": camp, "groups": group,
            "registrations": registration, "sessions": session}


def test_health_readiness_and_authentication(client, headers):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").json() == {"status": "ok"}
    assert client.get(f"{PREFIX}/runners").status_code == 401
    assert client.get(f"{PREFIX}/runners", headers={"Authorization": "Bearer invalid-test-key"}).status_code == 401
    assert client.get(f"{PREFIX}/runners", headers=headers).status_code == 200


def test_runner_uniqueness_is_tenant_scoped_and_conflict_recovers(client, headers, other_headers):
    payload = {"external_id": "same-id", "full_name": "Synthetic Runner"}
    first = create(client, headers, "runners", payload)
    assert client.post(f"{PREFIX}/runners", headers=headers, json=payload).status_code == 409
    second = create(client, other_headers, "runners", payload)
    assert second["id"] != first["id"]
    create(client, headers, "runners", {**payload, "external_id": "next-id"})


@pytest.mark.parametrize("resource", ["runners", "bootcamps", "groups", "registrations", "sessions"])
def test_tenant_cannot_read_update_or_delete_other_resources(client, headers, other_headers, resource):
    records = seed(client, headers)
    resource_id = records[resource]["id"]
    path = f"{PREFIX}/{resource}/{resource_id}"
    response = client.get(f"{PREFIX}/{resource}", headers=other_headers)
    assert response.status_code == 200
    assert response.json()["total"] == 0
    assert response.json()["items"] == []
    assert client.get(path, headers=other_headers).status_code == 404
    assert client.patch(path, headers=other_headers, json={}).status_code == 404
    assert client.delete(path, headers=other_headers).status_code == 404
    assert client.get(path, headers=headers).status_code == 200


def test_complete_lifecycle_checks_dependents_and_stats(client, headers):
    records = seed(client, headers)
    camp_id = records["bootcamps"]["id"]
    stats = client.get(f"{PREFIX}/bootcamps/{camp_id}/stats", headers=headers)
    assert stats.status_code == 200
    assert stats.json() == {"runner_count": 1, "session_count": 1, "total_distance_km": 10.5, "active_runner_count": 1}
    for resource in ("runners", "bootcamps", "groups", "registrations"):
        assert client.delete(f"{PREFIX}/{resource}/{records[resource]['id']}", headers=headers).status_code == 409
    session_path = f"{PREFIX}/sessions/{records['sessions']['id']}"
    response = client.patch(session_path, headers=headers, json={"distance_km": 12.25, "notes": "Easy effort"})
    assert response.status_code == 200
    assert response.json()["distance_km"] == 12.25
    assert client.get(f"{PREFIX}/bootcamps/{camp_id}/stats", headers=headers).json()["total_distance_km"] == 12.25
    for resource in ("sessions", "registrations", "groups", "runners", "bootcamps"):
        path = f"{PREFIX}/{resource}/{records[resource]['id']}"
        assert client.delete(path, headers=headers).status_code == 204
        assert client.get(path, headers=headers).status_code == 404


def test_payload_rejects_ownership_unknown_fields_and_invalid_uuid(client, headers, tenant):
    payload = {"external_id": "synthetic", "full_name": "Synthetic Runner"}
    for extra in ({"tenant_id": str(tenant.id)}, {"id": str(uuid4())}, {"unexpected": True}):
        assert client.post(f"{PREFIX}/runners", headers=headers, json={**payload, **extra}).status_code == 422
    assert client.get(f"{PREFIX}/runners/not-a-uuid", headers=headers).status_code == 422
    assert client.get(f"{PREFIX}/runners/{uuid4()}", headers=headers).status_code == 404


def test_patch_distinguishes_missing_nullable_and_required_fields(client, headers):
    runner = create(client, headers, "runners", {
        "external_id": "patch-01", "full_name": "Synthetic Runner", "weight_kg": 65.5, "birth_year": 1991,
    })
    path = f"{PREFIX}/runners/{runner['id']}"
    response = client.patch(path, headers=headers, json={"weight_kg": None})
    assert response.status_code == 200
    assert response.json()["weight_kg"] is None
    assert response.json()["birth_year"] == 1991
    for payload in ({}, {"full_name": None}, {"external_id": None}, {"gender": None}, {"tenant_id": str(uuid4())}):
        assert client.patch(path, headers=headers, json=payload).status_code == 422


def test_pagination_filters_are_bounded_stable_and_literal(client, headers):
    for i, name in enumerate(("Synthetic A", "Synthetic B", "100% Synthetic", "Synthetic C")):
        create(client, headers, "runners", {"external_id": f"filter-{i}", "full_name": name})
    first = client.get(f"{PREFIX}/runners?page_size=2", headers=headers).json()
    again = client.get(f"{PREFIX}/runners?page_size=2", headers=headers).json()
    second = client.get(f"{PREFIX}/runners?page_size=2&page=2", headers=headers).json()
    assert first == again
    assert first["total"] == second["total"] == 4
    assert {item["id"] for item in first["items"]}.isdisjoint(item["id"] for item in second["items"])
    assert client.get(f"{PREFIX}/runners", headers=headers, params={"q": "%"}).json()["total"] == 1
    assert client.get(f"{PREFIX}/runners", headers=headers, params={"q": "' OR 1=1 --"}).json()["total"] == 0
    assert client.get(f"{PREFIX}/runners?external_id=filter-0", headers=headers).json()["total"] == 1
    for query in ("page=0", "page_size=0", "page_size=101"):
        assert client.get(f"{PREFIX}/runners?{query}", headers=headers).status_code == 422


def test_foreign_tenant_associations_are_invisible(client, headers, other_headers):
    own = seed(client, headers, "own")
    foreign = seed(client, other_headers, "foreign")
    invalid_payloads = [
        ("groups", {"bootcamp_id": foreign["bootcamps"]["id"], "name": "Foreign"}),
        ("registrations", {"bootcamp_id": own["bootcamps"]["id"], "runner_id": foreign["runners"]["id"]}),
        ("registrations", {"bootcamp_id": own["bootcamps"]["id"], "runner_id": own["runners"]["id"], "group_id": foreign["groups"]["id"]}),
        ("sessions", {"bootcamp_id": foreign["bootcamps"]["id"], "runner_id": own["runners"]["id"], "session_date": "2026-01-15", "distance_km": 5}),
    ]
    for resource, payload in invalid_payloads:
        assert client.post(f"{PREFIX}/{resource}", headers=headers, json=payload).status_code == 404
    assert client.get(f"{PREFIX}/bootcamps/{foreign['bootcamps']['id']}/stats", headers=headers).status_code == 404


def test_groups_cannot_cross_bootcamps_or_move_with_registrations(client, headers):
    first = seed(client, headers, "first")
    second = seed(client, headers, "second")
    response = client.patch(f"{PREFIX}/registrations/{first['registrations']['id']}", headers=headers,
                            json={"group_id": second["groups"]["id"]})
    assert response.status_code == 409
    response = client.patch(f"{PREFIX}/groups/{first['groups']['id']}", headers=headers,
                            json={"bootcamp_id": second["bootcamps"]["id"]})
    assert response.status_code == 409
    response = client.patch(f"{PREFIX}/registrations/{first['registrations']['id']}", headers=headers,
                            json={"runner_id": second["runners"]["id"]})
    assert response.status_code == 409


def test_sessions_require_registration_and_camp_date_bounds(client, headers):
    records = seed(client, headers)
    unregistered = create(client, headers, "runners", {"external_id": "outsider", "full_name": "Synthetic Outsider"})
    payload = {"bootcamp_id": records["bootcamps"]["id"], "runner_id": unregistered["id"],
               "session_date": "2026-01-15", "distance_km": 5}
    assert client.post(f"{PREFIX}/sessions", headers=headers, json=payload).status_code == 409
    payload["runner_id"] = records["runners"]["id"]
    for day in ("2025-12-31", "2026-03-01"):
        assert client.post(f"{PREFIX}/sessions", headers=headers, json={**payload, "session_date": day}).status_code == 422
    for day in ("2026-01-01", "2026-02-28"):
        create(client, headers, "sessions", {**payload, "session_date": day})
    response = client.patch(f"{PREFIX}/bootcamps/{records['bootcamps']['id']}", headers=headers,
                            json={"start_date": "2026-01-02"})
    assert response.status_code == 409
    filtered = client.get(f"{PREFIX}/sessions", headers=headers,
                          params={"bootcamp_id": records["bootcamps"]["id"], "date_from": "2026-02-01"}).json()
    assert filtered["total"] == 1
    assert client.get(f"{PREFIX}/sessions?date_from=2026-02-01&date_to=2026-01-01", headers=headers).status_code == 422


def test_patch_validates_merged_date_and_target_ranges(client, headers):
    records = seed(client, headers)
    camp_path = f"{PREFIX}/bootcamps/{records['bootcamps']['id']}"
    assert client.patch(camp_path, headers=headers, json={"end_date": "2025-12-31"}).status_code == 422
    assert client.patch(camp_path, headers=headers, json={"start_date": None}).status_code == 422
    group_path = f"{PREFIX}/groups/{records['groups']['id']}"
    assert client.patch(group_path, headers=headers, json={"target_time_min_sec": 10800}).status_code == 200
    assert client.patch(group_path, headers=headers, json={"target_time_max_sec": 10000}).status_code == 422
    assert client.patch(group_path, headers=headers, json={"target_time_min_sec": None}).status_code == 200


@pytest.mark.parametrize("payload", [
    {"full_name": "x" * 121}, {"external_id": "x" * 81}, {"height_cm": 10000},
    {"weight_kg": -1}, {"birth_year": 1700}, {"gender": "unknown"},
])
def test_runner_validation_prevents_database_type_errors(client, headers, payload):
    response = client.post(f"{PREFIX}/runners", headers=headers,
                           json={"external_id": "invalid", "full_name": "Synthetic Runner", **payload})
    assert response.status_code == 422


def test_import_audit_is_tenant_scoped_and_read_only(client, db, tenant, headers, other_headers):
    camp = create(client, headers, "bootcamps", {
        "code": "audit", "name": "Audit Camp", "start_date": "2026-01-01", "end_date": "2026-02-28",
    })
    audit = ImportBatch(
        tenant_id=str(tenant.id), bootcamp_id=camp["id"], kind="sessions", file_name="synthetic.csv",
        sha256="a" * 64, status="committed", row_count=3, accepted_count=2, rejected_count=1,
        inserted_count=2, updated_count=0, errors=[{"row": 3, "code": "invalid_distance"}],
    )
    db.add(audit)
    db.commit()
    audit_id = str(audit.id)
    response = client.get(f"{PREFIX}/imports?kind=sessions&status=committed", headers=headers)
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["inserted_count"] == 2
    path = f"{PREFIX}/imports/{audit_id}"
    assert client.get(path, headers=headers).json()["rejected_count"] == 1
    assert client.get(path, headers=other_headers).status_code == 404
    assert client.get(f"{PREFIX}/imports", headers=other_headers).json()["items"] == []
    assert client.post(f"{PREFIX}/imports", headers=headers, json={}).status_code == 405
    assert client.patch(path, headers=headers, json={"status": "rejected"}).status_code == 405
    assert client.delete(path, headers=headers).status_code == 405


@pytest.mark.parametrize("enabled", [False, True])
def test_documentation_setting_is_respected_without_database(monkeypatch, enabled):
    import runnerx.api as api_module
    import runnerx.config as config_module

    monkeypatch.setattr(config_module, "get_settings", lambda: config_module.Settings(docs_enabled=enabled))
    isolated_app = runpy.run_path(api_module.__file__)["app"]
    with TestClient(isolated_app) as isolated_client:
        assert isolated_client.get("/health").status_code == 200
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert isolated_client.get(path).status_code == (200 if enabled else 404)


@pytest.mark.parametrize("model_name,values", [
    ("RunnerCreate", {"external_id": "SYN-PRECISION", "full_name": "Synthetic Runner", "height_cm": "170.001"}),
    ("RunnerCreate", {"external_id": "SYN-PRECISION", "full_name": "Synthetic Runner", "weight_kg": "65.555"}),
    ("RegistrationCreate", {"bootcamp_id": str(uuid4()), "runner_id": str(uuid4()), "prep_mileage_km": "80.001"}),
    ("SessionCreate", {"bootcamp_id": str(uuid4()), "runner_id": str(uuid4()), "session_date": "2026-01-01", "distance_km": "0.0001"}),
    ("RegistrationCreate", {"bootcamp_id": str(uuid4()), "runner_id": str(uuid4()), "raw_data": {"measurement": float("nan")}}),
])
def test_schema_precision_and_json_values_prevent_database_errors(model_name, values):
    from runnerx import schemas

    with pytest.raises(ValidationError):
        getattr(schemas, model_name)(**values)


def test_camp_date_patch_waits_for_import_and_validates_committed_sessions(
    client, db, engine, tenant, headers, bootcamp, tmp_path,
):
    """An import in flight must not slip outside a concurrently shortened camp."""
    from runnerx.etl import import_file

    registrations = tmp_path / "synthetic-registrations.csv"
    registrations.write_text("external_id,full_name\nSYN-CONCURRENT,Synthetic Runner\n", encoding="utf-8")
    result = import_file(db, tenant_id=tenant.id, bootcamp_id=bootcamp.id,
                         path=registrations, kind="registrations")
    assert result["status"] == "committed"
    db.commit()
    sessions = tmp_path / "synthetic-sessions.csv"
    sessions.write_text("external_id,session_date,distance_km,pace_sec_per_km\nSYN-CONCURRENT,2026-01-20,10,330\n", encoding="utf-8")
    result = import_file(db, tenant_id=tenant.id, bootcamp_id=bootcamp.id,
                         path=sessions, kind="sessions")
    assert result["status"] == "committed"
    # The importer deliberately has not committed and still owns the camp lock.
    request_attempted_mutation = Event()

    def observe_camp_mutation(connection, cursor, statement, parameters, context, executemany):
        normalized = statement.upper().strip()
        # The fixed path waits at SELECT FOR UPDATE, before checking sessions.
        # The former path checked too early, then waited at UPDATE instead.
        if (("FROM BOOTCAMPS" in normalized and "FOR UPDATE" in normalized)
                or normalized.startswith("UPDATE BOOTCAMPS SET")):
            request_attempted_mutation.set()

    event.listen(engine, "before_cursor_execute", observe_camp_mutation)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                client.patch, f"{PREFIX}/bootcamps/{bootcamp.id}", headers=headers,
                json={"end_date": "2026-01-15"},
            )
            try:
                assert request_attempted_mutation.wait(10), "API did not attempt its camp mutation"
                db.commit()
                response = future.result(timeout=15)
                assert response.status_code == 409, response.text
            finally:
                # Release locks before joining the worker if an assertion fails.
                db.rollback()
    finally:
        event.remove(engine, "before_cursor_execute", observe_camp_mutation)
    assert client.get(f"{PREFIX}/bootcamps/{bootcamp.id}", headers=headers).json()["end_date"] == "2026-02-01"
    assert client.get(f"{PREFIX}/sessions", headers=headers).json()["total"] == 1
