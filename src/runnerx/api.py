"""Tenant-scoped REST API for the RunnerX training-camp data platform."""

from datetime import date
from typing import Any
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from runnerx import __version__
from runnerx.auth import get_tenant
from runnerx.config import get_settings
from runnerx.db import get_session
from runnerx.models import (
    Bootcamp, BootcampGroup, ImportBatch, Registration, Runner, Tenant, TrainingSession,
)
from runnerx.schemas import (
    BootcampCreate, BootcampOut, BootcampPatch, BootcampStats,
    GroupCreate, GroupOut, GroupPatch, ImportOut, Page,
    RegistrationCreate, RegistrationOut, RegistrationPatch,
    RunnerCreate, RunnerOut, RunnerPatch, SessionCreate, SessionOut, SessionPatch,
)


_settings = get_settings()
app = FastAPI(
    title="RunnerX API",
    version=__version__,
    description="Authenticated, tenant-scoped training-camp data and import audit records.",
    docs_url="/docs" if _settings.docs_enabled else None,
    redoc_url="/redoc" if _settings.docs_enabled else None,
    openapi_url="/openapi.json" if _settings.docs_enabled else None,
)


def _scope(model, tenant: Tenant):
    return select(model).where(model.tenant_id == str(tenant.id))


def _get(db: Session, tenant: Tenant, model, resource_id, *, for_update: bool = False):
    statement = _scope(model, tenant).where(model.id == str(resource_id))
    if for_update:
        # Lock and refresh the row after any concurrent writer finishes.
        statement = statement.with_for_update().execution_options(populate_existing=True)
    obj = db.scalar(statement)
    if obj is None:
        raise HTTPException(status_code=404, detail="Resource not found")
    return obj


def _exists(db: Session, tenant: Tenant, model, *conditions) -> bool:
    # This helper is only used when validating mutations. A current read keeps
    # dependency checks consistent with ETL changes committed while we waited.
    return db.scalar(_scope(model, tenant).where(*conditions).limit(1).with_for_update()) is not None


def _lock_mutation(db: Session, tenant: Tenant, model, obj, updates: dict | None = None):
    """Use ETL's camp-first lock order, then refresh the resource being changed."""
    camp_ids = set()
    if model in (BootcampGroup, Registration, TrainingSession):
        camp_ids.add(str(obj.bootcamp_id))
        if updates and updates.get("bootcamp_id"):
            camp_ids.add(str(updates["bootcamp_id"]))
        for camp_id in sorted(camp_ids):
            _get(db, tenant, Bootcamp, camp_id, for_update=True)
    current = _get(db, tenant, model, obj.id, for_update=True)
    if camp_ids and current.bootcamp_id not in camp_ids:
        raise HTTPException(409, "The resource moved to another bootcamp; reload it before retrying")
    return current


def _page(db: Session, statement, page: int, page_size: int, *ordering):
    total = db.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0
    items = db.scalars(statement.order_by(*ordering).offset((page - 1) * page_size).limit(page_size)).all()
    return {"total": total, "page": page, "page_size": page_size, "items": items}


def _values(payload: BaseModel, *, patch: bool = False) -> dict[str, Any]:
    return {
        key: str(value) if isinstance(value, UUID) else value
        for key, value in payload.model_dump(exclude_unset=patch).items()
    }


def _commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="The operation conflicts with an existing record or a related resource",
        ) from None


def _registration_has_sessions(db: Session, tenant: Tenant, obj: Registration) -> bool:
    return _exists(
        db, tenant, TrainingSession,
        TrainingSession.bootcamp_id == obj.bootcamp_id,
        TrainingSession.runner_id == obj.runner_id,
    )


def _validate_relations(db: Session, tenant: Tenant, model, values: dict, current=None) -> None:
    """Validate ownership and cross-record rules before any ORM mutation."""
    if model is Bootcamp:
        if values["end_date"] < values["start_date"]:
            raise HTTPException(422, "end_date must be on or after start_date")
        if current is not None and _exists(
            db, tenant, TrainingSession,
            TrainingSession.bootcamp_id == current.id,
            (TrainingSession.session_date < values["start_date"])
            | (TrainingSession.session_date > values["end_date"]),
        ):
            raise HTTPException(409, "Existing sessions fall outside the proposed bootcamp dates")
        return
    if model not in (BootcampGroup, Registration, TrainingSession):
        return

    bootcamp = _get(db, tenant, Bootcamp, values["bootcamp_id"], for_update=True)
    if model is BootcampGroup:
        lower, upper = values.get("target_time_min_sec"), values.get("target_time_max_sec")
        if lower is not None and upper is not None and lower > upper:
            raise HTTPException(422, "target_time_max_sec must be at least target_time_min_sec")
        if current is not None and current.bootcamp_id != bootcamp.id and _exists(
            db, tenant, Registration, Registration.group_id == current.id,
        ):
            raise HTTPException(409, "A group with registrations cannot move to another bootcamp")
        return

    _get(db, tenant, Runner, values["runner_id"], for_update=True)
    if model is Registration:
        if values.get("group_id") is not None:
            group = _get(db, tenant, BootcampGroup, values["group_id"], for_update=True)
            if group.bootcamp_id != bootcamp.id:
                raise HTTPException(409, "The selected group belongs to a different bootcamp")
        if current is not None and (
            current.bootcamp_id != values["bootcamp_id"] or current.runner_id != values["runner_id"]
        ) and _registration_has_sessions(db, tenant, current):
            raise HTTPException(409, "A registration with sessions cannot change its runner or bootcamp")
        return

    if not _exists(
        db, tenant, Registration,
        Registration.bootcamp_id == bootcamp.id,
        Registration.runner_id == values["runner_id"],
    ):
        raise HTTPException(409, "The runner must be registered for this bootcamp first")
    if not bootcamp.start_date <= values["session_date"] <= bootcamp.end_date:
        raise HTTPException(422, "session_date must fall within the bootcamp dates")


def _ensure_deletable(db: Session, tenant: Tenant, model, obj) -> None:
    dependencies = {
        Runner: [(Registration, Registration.runner_id), (TrainingSession, TrainingSession.runner_id)],
        Bootcamp: [
            (BootcampGroup, BootcampGroup.bootcamp_id), (Registration, Registration.bootcamp_id),
            (TrainingSession, TrainingSession.bootcamp_id), (ImportBatch, ImportBatch.bootcamp_id),
        ],
        BootcampGroup: [(Registration, Registration.group_id)],
    }
    if any(_exists(db, tenant, child, foreign_key == obj.id) for child, foreign_key in dependencies.get(model, [])):
        raise HTTPException(409, "Delete the dependent records before deleting this resource")
    if model is Registration and _registration_has_sessions(db, tenant, obj):
        raise HTTPException(409, "Delete the training sessions before deleting this registration")


def _register_crud(resource: str, model, create_schema, patch_schema, out_schema, required_fields: set[str]):
    """One mutation implementation keeps tenancy and transaction handling consistent."""
    path = f"/api/v1/{resource}"

    def detail(resource_id: UUID, db: Session = Depends(get_session), tenant: Tenant = Depends(get_tenant)):
        return _get(db, tenant, model, resource_id)

    def create(payload, db: Session = Depends(get_session), tenant: Tenant = Depends(get_tenant)):
        values = _values(payload)
        _validate_relations(db, tenant, model, values)
        obj = model(id=str(uuid4()), tenant_id=str(tenant.id), **values)
        db.add(obj)
        _commit(db)
        db.refresh(obj)
        return obj

    def patch(resource_id: UUID, payload, db: Session = Depends(get_session), tenant: Tenant = Depends(get_tenant)):
        obj = _get(db, tenant, model, resource_id)
        updates = _values(payload, patch=True)
        if not updates:
            raise HTTPException(422, "Provide at least one field to update")
        if any(key in required_fields and value is None for key, value in updates.items()):
            raise HTTPException(422, "Required fields cannot be set to null")
        obj = _lock_mutation(db, tenant, model, obj, updates)
        merged = {key: getattr(obj, key) for key in create_schema.model_fields}
        merged.update(updates)
        _validate_relations(db, tenant, model, merged, current=obj)
        for key, value in updates.items():
            setattr(obj, key, value)
        _commit(db)
        db.refresh(obj)
        return obj

    def delete(resource_id: UUID, db: Session = Depends(get_session), tenant: Tenant = Depends(get_tenant)):
        obj = _get(db, tenant, model, resource_id)
        obj = _lock_mutation(db, tenant, model, obj)
        _ensure_deletable(db, tenant, model, obj)
        db.delete(obj)
        _commit(db)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    create.__annotations__["payload"] = create_schema
    patch.__annotations__["payload"] = patch_schema
    app.add_api_route(path, create, methods=["POST"], response_model=out_schema,
                      status_code=201, tags=[resource], name=f"create_{resource}")
    app.add_api_route(path + "/{resource_id}", detail, methods=["GET"], response_model=out_schema,
                      tags=[resource], name=f"get_{resource}")
    app.add_api_route(path + "/{resource_id}", patch, methods=["PATCH"], response_model=out_schema,
                      tags=[resource], name=f"patch_{resource}")
    app.add_api_route(path + "/{resource_id}", delete, methods=["DELETE"], status_code=204,
                      tags=[resource], name=f"delete_{resource}")


@app.get("/health", tags=["health"])
def health():
    return {"status": "ok"}


@app.get("/ready", tags=["health"])
def ready(db: Session = Depends(get_session)):
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, "Database unavailable") from None
    return {"status": "ok"}


@app.get("/api/v1/runners", response_model=Page[RunnerOut], tags=["runners"])
def list_runners(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    q: str | None = Query(None, max_length=200), external_id: str | None = Query(None, max_length=120),
    db: Session = Depends(get_session), tenant: Tenant = Depends(get_tenant),
):
    stmt = _scope(Runner, tenant)
    if q:
        stmt = stmt.where(Runner.full_name.contains(q, autoescape=True))
    if external_id is not None:
        stmt = stmt.where(Runner.external_id == external_id)
    return _page(db, stmt, page, page_size, Runner.created_at.desc(), Runner.id)


@app.get("/api/v1/bootcamps", response_model=Page[BootcampOut], tags=["bootcamps"])
def list_bootcamps(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    q: str | None = Query(None, max_length=200), city: str | None = Query(None, max_length=120),
    db: Session = Depends(get_session), tenant: Tenant = Depends(get_tenant),
):
    stmt = _scope(Bootcamp, tenant)
    if q:
        stmt = stmt.where(Bootcamp.name.contains(q, autoescape=True))
    if city is not None:
        stmt = stmt.where(Bootcamp.city == city)
    return _page(db, stmt, page, page_size, Bootcamp.created_at.desc(), Bootcamp.id)


@app.get("/api/v1/groups", response_model=Page[GroupOut], tags=["groups"])
def list_groups(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    bootcamp_id: UUID | None = None,
    db: Session = Depends(get_session), tenant: Tenant = Depends(get_tenant),
):
    stmt = _scope(BootcampGroup, tenant)
    if bootcamp_id is not None:
        stmt = stmt.where(BootcampGroup.bootcamp_id == str(bootcamp_id))
    return _page(db, stmt, page, page_size, BootcampGroup.name, BootcampGroup.id)


@app.get("/api/v1/registrations", response_model=Page[RegistrationOut], tags=["registrations"])
def list_registrations(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    bootcamp_id: UUID | None = None, runner_id: UUID | None = None, group_id: UUID | None = None,
    db: Session = Depends(get_session), tenant: Tenant = Depends(get_tenant),
):
    stmt = _scope(Registration, tenant)
    for column, value in ((Registration.bootcamp_id, bootcamp_id), (Registration.runner_id, runner_id),
                          (Registration.group_id, group_id)):
        if value is not None:
            stmt = stmt.where(column == str(value))
    return _page(db, stmt, page, page_size, Registration.id)


@app.get("/api/v1/sessions", response_model=Page[SessionOut], tags=["sessions"])
def list_sessions(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    bootcamp_id: UUID | None = None, runner_id: UUID | None = None,
    date_from: date | None = None, date_to: date | None = None,
    db: Session = Depends(get_session), tenant: Tenant = Depends(get_tenant),
):
    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(422, "date_to must be on or after date_from")
    stmt = _scope(TrainingSession, tenant)
    if bootcamp_id is not None:
        stmt = stmt.where(TrainingSession.bootcamp_id == str(bootcamp_id))
    if runner_id is not None:
        stmt = stmt.where(TrainingSession.runner_id == str(runner_id))
    if date_from is not None:
        stmt = stmt.where(TrainingSession.session_date >= date_from)
    if date_to is not None:
        stmt = stmt.where(TrainingSession.session_date <= date_to)
    return _page(db, stmt, page, page_size, TrainingSession.session_date.desc(), TrainingSession.id)


@app.get("/api/v1/bootcamps/{bootcamp_id}/stats", response_model=BootcampStats, tags=["bootcamps"])
def bootcamp_stats(bootcamp_id: UUID, db: Session = Depends(get_session), tenant: Tenant = Depends(get_tenant)):
    camp = _get(db, tenant, Bootcamp, bootcamp_id)
    runner_count = db.scalar(select(func.count()).select_from(Registration).where(
        Registration.tenant_id == str(tenant.id), Registration.bootcamp_id == camp.id,
    )) or 0
    session_count, total_distance, active_count = db.execute(select(
        func.count(TrainingSession.id), func.coalesce(func.sum(TrainingSession.distance_km), 0),
        func.count(func.distinct(TrainingSession.runner_id)),
    ).where(TrainingSession.tenant_id == str(tenant.id), TrainingSession.bootcamp_id == camp.id)).one()
    return {"runner_count": runner_count, "session_count": session_count,
            "total_distance_km": float(total_distance), "active_runner_count": active_count}


@app.get("/api/v1/imports", response_model=Page[ImportOut], tags=["imports"])
def list_imports(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    bootcamp_id: UUID | None = None, kind: str | None = Query(None, max_length=40),
    import_status: str | None = Query(None, alias="status", max_length=40),
    db: Session = Depends(get_session), tenant: Tenant = Depends(get_tenant),
):
    stmt = _scope(ImportBatch, tenant)
    if bootcamp_id is not None:
        stmt = stmt.where(ImportBatch.bootcamp_id == str(bootcamp_id))
    if kind is not None:
        stmt = stmt.where(ImportBatch.kind == kind)
    if import_status is not None:
        stmt = stmt.where(ImportBatch.status == import_status)
    return _page(db, stmt, page, page_size, ImportBatch.created_at.desc(), ImportBatch.id)


@app.get("/api/v1/imports/{import_id}", response_model=ImportOut, tags=["imports"])
def get_import(import_id: UUID, db: Session = Depends(get_session), tenant: Tenant = Depends(get_tenant)):
    return _get(db, tenant, ImportBatch, import_id)


_register_crud("runners", Runner, RunnerCreate, RunnerPatch, RunnerOut, {"external_id", "full_name", "gender"})
_register_crud("bootcamps", Bootcamp, BootcampCreate, BootcampPatch, BootcampOut, {"code", "name", "start_date", "end_date"})
_register_crud("groups", BootcampGroup, GroupCreate, GroupPatch, GroupOut, {"bootcamp_id", "name"})
_register_crud("registrations", Registration, RegistrationCreate, RegistrationPatch, RegistrationOut,
               {"bootcamp_id", "runner_id", "raw_data"})
_register_crud("sessions", TrainingSession, SessionCreate, SessionPatch, SessionOut,
               {"bootcamp_id", "runner_id", "session_date", "distance_km"})
