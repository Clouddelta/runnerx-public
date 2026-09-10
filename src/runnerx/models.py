"""MySQL schema. Composite foreign keys enforce tenant and camp ownership."""
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, ForeignKey, ForeignKeyConstraint,
    Index, Integer, JSON, Numeric, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_id() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Identity:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class Tenant(Identity, Base):
    __tablename__ = "tenants"
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)


class ApiKey(Identity, Base):
    __tablename__ = "api_keys"
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), default="default", nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Runner(Identity, Base):
    __tablename__ = "runners"
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False)
    external_id: Mapped[str] = mapped_column(String(80, collation="utf8mb4_bin"), nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    gender: Mapped[str] = mapped_column(String(1), default="U", nullable=False)
    birth_year: Mapped[int | None] = mapped_column(Integer)
    height_cm: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    __table_args__ = (
        UniqueConstraint("tenant_id", "external_id", name="uq_runner_external"),
        UniqueConstraint("tenant_id", "id", name="uq_runner_tenant_id"),
        CheckConstraint("gender IN ('M','F','O','U')", name="ck_runner_gender"),
        CheckConstraint("birth_year IS NULL OR birth_year BETWEEN 1800 AND 2200", name="ck_runner_year"),
        CheckConstraint("height_cm IS NULL OR height_cm > 0", name="ck_runner_height"),
        CheckConstraint("weight_kg IS NULL OR weight_kg > 0", name="ck_runner_weight"),
        Index("ix_runner_tenant_created", "tenant_id", "created_at", "id"),
    )


class Bootcamp(Identity, Base):
    __tablename__ = "bootcamps"
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    city: Mapped[str | None] = mapped_column(String(120))
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_bootcamp_code"),
        UniqueConstraint("tenant_id", "id", name="uq_bootcamp_tenant_id"),
        CheckConstraint("end_date >= start_date", name="ck_bootcamp_dates"),
        Index("ix_bootcamp_tenant_created", "tenant_id", "created_at", "id"),
    )


class BootcampGroup(Identity, Base):
    __tablename__ = "bootcamp_groups"
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    bootcamp_id: Mapped[str] = mapped_column(String(36), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    target_time_min_sec: Mapped[int | None] = mapped_column(Integer)
    target_time_max_sec: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "bootcamp_id"], ["bootcamps.tenant_id", "bootcamps.id"], ondelete="RESTRICT", name="fk_group_bootcamp"),
        UniqueConstraint("tenant_id", "bootcamp_id", "name", name="uq_group_name"),
        UniqueConstraint("tenant_id", "bootcamp_id", "id", name="uq_group_scope"),
        CheckConstraint("target_time_min_sec IS NULL OR target_time_min_sec > 0", name="ck_group_min"),
        CheckConstraint("target_time_max_sec IS NULL OR target_time_max_sec > 0", name="ck_group_max"),
        CheckConstraint("target_time_min_sec IS NULL OR target_time_max_sec IS NULL OR target_time_min_sec <= target_time_max_sec", name="ck_group_range"),
    )


class Registration(Identity, Base):
    __tablename__ = "registrations"
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    bootcamp_id: Mapped[str] = mapped_column(String(36), nullable=False)
    runner_id: Mapped[str] = mapped_column(String(36), nullable=False)
    group_id: Mapped[str | None] = mapped_column(String(36))
    test_10k_sec: Mapped[int | None] = mapped_column(Integer)
    fm_best_sec: Mapped[int | None] = mapped_column(Integer)
    prep_mileage_km: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    experience_text: Mapped[str | None] = mapped_column(Text)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "bootcamp_id"], ["bootcamps.tenant_id", "bootcamps.id"], ondelete="RESTRICT", name="fk_registration_bootcamp"),
        ForeignKeyConstraint(["tenant_id", "runner_id"], ["runners.tenant_id", "runners.id"], ondelete="RESTRICT", name="fk_registration_runner"),
        ForeignKeyConstraint(["tenant_id", "bootcamp_id", "group_id"], ["bootcamp_groups.tenant_id", "bootcamp_groups.bootcamp_id", "bootcamp_groups.id"], ondelete="RESTRICT", name="fk_registration_group"),
        UniqueConstraint("tenant_id", "bootcamp_id", "runner_id", name="uq_registration_runner"),
        CheckConstraint("test_10k_sec IS NULL OR test_10k_sec > 0", name="ck_registration_10k"),
        CheckConstraint("fm_best_sec IS NULL OR fm_best_sec > 0", name="ck_registration_fm"),
        CheckConstraint("prep_mileage_km IS NULL OR prep_mileage_km >= 0", name="ck_registration_mileage"),
        Index("ix_registration_runner", "tenant_id", "runner_id"),
    )


class TrainingSession(Identity, Base):
    __tablename__ = "training_sessions"
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    bootcamp_id: Mapped[str] = mapped_column(String(36), nullable=False)
    runner_id: Mapped[str] = mapped_column(String(36), nullable=False)
    session_date: Mapped[date] = mapped_column(Date, nullable=False)
    distance_km: Mapped[Decimal] = mapped_column(Numeric(8, 3), nullable=False)
    pace_sec_per_km: Mapped[int | None] = mapped_column(Integer)
    resting_hr: Mapped[int | None] = mapped_column(Integer)
    fatigue_level: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "bootcamp_id", "runner_id"], ["registrations.tenant_id", "registrations.bootcamp_id", "registrations.runner_id"], ondelete="RESTRICT", name="fk_session_registration"),
        UniqueConstraint("tenant_id", "bootcamp_id", "runner_id", "session_date", name="uq_session_day"),
        CheckConstraint("distance_km > 0", name="ck_session_distance"),
        CheckConstraint("pace_sec_per_km IS NULL OR pace_sec_per_km > 0", name="ck_session_pace"),
        CheckConstraint("resting_hr IS NULL OR resting_hr BETWEEN 1 AND 300", name="ck_session_hr"),
        CheckConstraint("fatigue_level IS NULL OR fatigue_level BETWEEN 1 AND 5", name="ck_session_fatigue"),
        Index("ix_session_camp_date", "tenant_id", "bootcamp_id", "session_date"),
        Index("ix_session_runner_date", "tenant_id", "runner_id", "session_date"),
    )


class ImportBatch(Identity, Base):
    __tablename__ = "import_batches"
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    bootcamp_id: Mapped[str] = mapped_column(String(36), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    accepted_count: Mapped[int] = mapped_column(Integer, nullable=False)
    rejected_count: Mapped[int] = mapped_column(Integer, nullable=False)
    inserted_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    errors: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "bootcamp_id"], ["bootcamps.tenant_id", "bootcamps.id"], ondelete="RESTRICT", name="fk_import_bootcamp"),
        CheckConstraint("kind IN ('registrations','sessions')", name="ck_import_kind"),
        CheckConstraint("status IN ('committed','rejected')", name="ck_import_status"),
        Index("ix_import_content", "tenant_id", "bootcamp_id", "kind", "sha256", "status"),
    )
