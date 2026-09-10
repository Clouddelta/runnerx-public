"""Validated public API contracts. Tenant ownership is never client writable."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator


Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
ExternalId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
Code = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
Gender = Literal["M", "F", "O", "U"]
Height = Annotated[Decimal, Field(gt=0, le=300, decimal_places=2, allow_inf_nan=False)]
Weight = Annotated[Decimal, Field(gt=0, le=1000, decimal_places=2, allow_inf_nan=False)]
Distance = Annotated[Decimal, Field(gt=0, le=1000, decimal_places=3, allow_inf_nan=False)]
NonnegativeNumber = Annotated[Decimal, Field(ge=0, le=100000, decimal_places=2, allow_inf_nan=False)]
PositiveSeconds = Annotated[int, Field(gt=0, le=172800)]
Pace = Annotated[int, Field(gt=0, le=7200)]
BirthYear = Annotated[int, Field(ge=1900, le=date.today().year)]
Notes = Annotated[str, StringConstraints(max_length=10000)]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class OutputModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RunnerCreate(InputModel):
    external_id: ExternalId
    full_name: Name
    gender: Gender = "U"
    birth_year: BirthYear | None = None
    height_cm: Height | None = None
    weight_kg: Weight | None = None


class RunnerPatch(InputModel):
    external_id: ExternalId | None = None
    full_name: Name | None = None
    gender: Gender | None = None
    birth_year: BirthYear | None = None
    height_cm: Height | None = None
    weight_kg: Weight | None = None


class RunnerOut(OutputModel):
    id: UUID
    external_id: str
    full_name: str
    gender: str
    birth_year: int | None
    height_cm: float | None
    weight_kg: float | None
    created_at: datetime


class BootcampCreate(InputModel):
    code: Code
    name: Name
    city: Annotated[str, StringConstraints(max_length=120)] | None = None
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def validate_dates(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class BootcampPatch(InputModel):
    code: Code | None = None
    name: Name | None = None
    city: Annotated[str, StringConstraints(max_length=120)] | None = None
    start_date: date | None = None
    end_date: date | None = None


class BootcampOut(OutputModel):
    id: UUID
    code: str
    name: str
    city: str | None
    start_date: date
    end_date: date
    created_at: datetime


class GroupCreate(InputModel):
    bootcamp_id: UUID
    name: Name
    target_time_min_sec: PositiveSeconds | None = None
    target_time_max_sec: PositiveSeconds | None = None

    @model_validator(mode="after")
    def validate_target_range(self):
        if (self.target_time_min_sec is not None
                and self.target_time_max_sec is not None
                and self.target_time_max_sec < self.target_time_min_sec):
            raise ValueError("target_time_max_sec must be at least target_time_min_sec")
        return self


class GroupPatch(InputModel):
    bootcamp_id: UUID | None = None
    name: Name | None = None
    target_time_min_sec: PositiveSeconds | None = None
    target_time_max_sec: PositiveSeconds | None = None


class GroupOut(OutputModel):
    id: UUID
    bootcamp_id: UUID
    name: str
    target_time_min_sec: int | None
    target_time_max_sec: int | None


class RegistrationCreate(InputModel):
    bootcamp_id: UUID
    runner_id: UUID
    group_id: UUID | None = None
    test_10k_sec: PositiveSeconds | None = None
    fm_best_sec: PositiveSeconds | None = None
    prep_mileage_km: NonnegativeNumber | None = None
    experience_text: Notes | None = None
    raw_data: dict[str, JsonValue] = Field(default_factory=dict)


class RegistrationPatch(InputModel):
    bootcamp_id: UUID | None = None
    runner_id: UUID | None = None
    group_id: UUID | None = None
    test_10k_sec: PositiveSeconds | None = None
    fm_best_sec: PositiveSeconds | None = None
    prep_mileage_km: NonnegativeNumber | None = None
    experience_text: Notes | None = None
    raw_data: dict[str, JsonValue] | None = None


class RegistrationOut(OutputModel):
    id: UUID
    bootcamp_id: UUID
    runner_id: UUID
    group_id: UUID | None
    test_10k_sec: int | None
    fm_best_sec: int | None
    prep_mileage_km: float | None
    experience_text: str | None
    raw_data: dict[str, Any]


class SessionCreate(InputModel):
    bootcamp_id: UUID
    runner_id: UUID
    session_date: date
    distance_km: Distance
    pace_sec_per_km: Pace | None = None
    resting_hr: Annotated[int, Field(gt=0, le=300)] | None = None
    fatigue_level: Annotated[int, Field(ge=1, le=5)] | None = None
    notes: Notes | None = None


class SessionPatch(InputModel):
    bootcamp_id: UUID | None = None
    runner_id: UUID | None = None
    session_date: date | None = None
    distance_km: Distance | None = None
    pace_sec_per_km: Pace | None = None
    resting_hr: Annotated[int, Field(gt=0, le=300)] | None = None
    fatigue_level: Annotated[int, Field(ge=1, le=5)] | None = None
    notes: Notes | None = None


class SessionOut(OutputModel):
    id: UUID
    bootcamp_id: UUID
    runner_id: UUID
    session_date: date
    distance_km: float
    pace_sec_per_km: int | None
    resting_hr: int | None
    fatigue_level: int | None
    notes: str | None


class ImportOut(OutputModel):
    id: UUID
    bootcamp_id: UUID | None
    kind: str
    file_name: str
    sha256: str
    status: str
    row_count: int
    accepted_count: int
    rejected_count: int
    inserted_count: int
    updated_count: int
    errors: Any
    created_at: datetime


class BootcampStats(OutputModel):
    runner_count: int
    session_count: int
    total_distance_km: float
    active_runner_count: int = Field(description="Registered runners with at least one recorded session.")


Item = TypeVar("Item")


class Page(BaseModel, Generic[Item]):
    total: int
    page: int
    page_size: int
    items: list[Item]
