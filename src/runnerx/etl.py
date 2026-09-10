"""Validated, tenant-scoped CSV/XLSX imports. The caller owns the transaction.

An accepted row counts one registration or training session, independently of
whether its runner also had to be created. Blank optional cells do not erase
previously stored values. Names are display data, never identity keys.
"""
from __future__ import annotations

import hashlib
import io
import math
import re
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from runnerx.models import (
    Bootcamp, BootcampGroup, ImportBatch, Registration, Runner, TrainingSession,
)


ALIASES = {
    "external_id": ("external_id", "runner_external_id", "runner_code"),
    "full_name": ("full_name", "name"),
    "gender": ("gender", "sex"),
    "birth_year": ("birth_year",),
    "height_cm": ("height_cm",),
    "weight_kg": ("weight_kg",),
    "group_id": ("group_id",),
    "test_10k_sec": ("test_10k_sec", "test_10k"),
    "fm_best_sec": ("fm_best_sec", "fm_best"),
    "prep_mileage_km": ("prep_mileage_km", "prep_cycle_mileage_km"),
    "experience_text": ("experience_text",),
    "session_date": ("session_date", "date"),
    "distance_km": ("distance_km", "distance"),
    "pace_sec_per_km": ("pace_sec_per_km", "pace"),
    "resting_hr": ("resting_hr",),
    "fatigue_level": ("fatigue_level",),
    "notes": ("notes",),
}


def _header(value: object) -> str:
    return re.sub(r"[\s_\-()/]+", "", str(value)).casefold()


_ALIAS_MAP = {_header(alias): field for field, aliases in ALIASES.items() for alias in aliases}
MAX_IMPORT_BYTES = 32 * 1024 * 1024


def clean_cell(value: object) -> object:
    """Convert spreadsheet missing values to None without changing identifiers."""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return None if not value or value.casefold() in {"nan", "null", "none", "n/a"} else value
    try:
        if bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _number(value: object, *, integer: bool = False) -> int | float:
    if isinstance(value, bool):
        raise ValueError("must be a number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("must be a number") from None
    if not result.is_finite():
        raise ValueError("must be finite")
    if integer:
        if result != result.to_integral_value():
            raise ValueError("must be a whole number")
        return int(result)
    return float(result)


def parse_duration(value: object) -> int:
    """Parse whole seconds, MM:SS, HH:MM:SS, or English time units.

    Units accept h/hr/hour, m/min/minute, and s/sec/second, including plurals
    and compact combinations such as 3h5m9s. Bare numeric values always mean
    seconds, never Excel day fractions or minutes. Fractional seconds and
    malformed clock components are rejected.
    """
    value = clean_cell(value)
    if value is None or isinstance(value, (bool, date)):
        raise ValueError("provide seconds, MM:SS, HH:MM:SS, or English hours/minutes/seconds")
    if isinstance(value, time):
        if value.microsecond:
            raise ValueError("fractional seconds are not supported")
        total = value.hour * 3600 + value.minute * 60 + value.second
    else:
        text = str(value).strip()
        if re.fullmatch(r"\d+(?:\.0+)?", text):
            total = int(Decimal(text))
        elif ":" in text:
            pieces = text.split(":")
            if len(pieces) not in (2, 3) or not all(re.fullmatch(r"\d+", part) for part in pieces):
                raise ValueError("use MM:SS or HH:MM:SS with whole seconds")
            numbers = list(map(int, pieces))
            if numbers[-1] >= 60 or (len(numbers) == 3 and numbers[-2] >= 60):
                raise ValueError("clock minutes/seconds must be below 60")
            total = numbers[0] * 60 + numbers[1] if len(numbers) == 2 else numbers[0] * 3600 + numbers[1] * 60 + numbers[2]
        else:
            match = re.fullmatch(
                r"(?:(\d+)\s*(?:hours?|hrs?|h))?\s*"
                r"(?:(\d+)\s*(?:minutes?|mins?|m))?\s*"
                r"(?:(\d+)\s*(?:seconds?|secs?|s))?",
                text,
                flags=re.IGNORECASE,
            )
            if not match or not any(part is not None for part in match.groups()):
                raise ValueError("unrecognized time; use seconds, MM:SS, HH:MM:SS, or English time units")
            hours, minutes, seconds = (int(part or 0) for part in match.groups())
            if (seconds >= 60 and any(part is not None for part in match.groups()[:2])) or (match.group(1) is not None and minutes >= 60):
                raise ValueError("clock minutes/seconds must be below 60")
            total = hours * 3600 + minutes * 60 + seconds
    if not 0 < total <= 172800:
        raise ValueError("time must be greater than 0 and at most 172800 seconds")
    return total


def parse_pace(value: object) -> int:
    """Parse seconds/km, MM:SS/km, or English minutes/seconds per km."""
    value = clean_cell(value)
    if isinstance(value, time):
        if value.hour:
            raise ValueError("pace must use seconds/km or MM:SS, not hours")
        result = parse_duration(value)
    else:
        text = str(value).strip()
        text = re.sub(r"\s*/\s*km\s*$", "", text, flags=re.IGNORECASE)
        if text.count(":") > 1 or re.search(r"\d+\s*(?:hours?|hrs?|h)", text, flags=re.IGNORECASE):
            raise ValueError("pace must use seconds/km or MM:SS, not a race duration")
        # A bare seconds unit is useful for CSV exports (e.g. 330 sec/km).
        result = parse_duration(text)
    if not 0 < result <= 7200:
        raise ValueError("pace must be greater than 0 and at most 7200 seconds/km")
    return int(result)


def parse_date(value: object) -> date:
    """Require an explicit year; do not infer a year from a training filename."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not re.fullmatch(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", text):
        raise ValueError("use an explicit YYYY-MM-DD date")
    try:
        return date(*(int(part) for part in re.split(r"[-/]", text)))
    except ValueError:
        raise ValueError("invalid calendar date") from None


def _json_cell(value: object) -> object:
    value = clean_cell(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "item"):
        return _json_cell(value.item())
    return str(value)


def _read_rows(content: bytes, suffix: str) -> tuple[list[dict], list[dict]]:
    errors: list[dict] = []
    if suffix.lower() == ".xlsx":
        frame = pd.read_excel(io.BytesIO(content), engine="openpyxl", dtype=object, keep_default_na=False)
    elif suffix.lower() == ".csv":
        frame = pd.read_csv(io.BytesIO(content), encoding="utf-8-sig", dtype=object, keep_default_na=False, skip_blank_lines=False)
    else:
        raise ValueError("only .xlsx and UTF-8 .csv files are supported")
    fields = [_ALIAS_MAP.get(_header(column), _ALIAS_MAP.get(_header(re.sub(r"\.\d+$", "", str(column))), str(column).strip())) for column in frame.columns]
    if len(fields) != len(set(fields)):
        errors.append({"row": 1, "field": "headers", "message": "multiple columns map to the same field"})
    rows = []
    for index, cells in enumerate(frame.itertuples(index=False, name=None), start=2):
        values = [clean_cell(value) for value in cells]
        if all(value is None for value in values):
            continue
        rows.append({"row": index, "data": dict(zip(fields, values)), "raw": {str(key): _json_cell(value) for key, value in zip(frame.columns, values)}})
    return rows, errors


def _validate_rows(db: Session, tenant_id: str, camp: Bootcamp, rows: list[dict], kind: str) -> tuple[list[dict], dict, list[dict]]:
    parsed, errors, seen = [], [], set()
    for source in rows:
        data, number = source["data"], source["row"]
        result = {"_row": number, "raw_data": source["raw"]}

        def cell(field, parser, *, required=False):
            value = data.get(field)
            if value is None:
                if required:
                    errors.append({"row": number, "field": field, "message": "required value is missing"})
                result[field] = None
                return
            try:
                result[field] = parser(value)
            except (ValueError, TypeError, OverflowError) as exc:
                result[field] = None
                errors.append({"row": number, "field": field, "message": str(exc)})

        def text(value, limit):
            value = str(value).strip()
            if not value or len(value) > limit:
                raise ValueError(f"must contain 1 to {limit} characters")
            return value

        def bounded(value, minimum, maximum, *, integer=False, exclusive=False, scale=None):
            result = _number(value, integer=integer)
            if result > maximum or result < minimum or (exclusive and result == minimum):
                raise ValueError(f"must be {'greater than' if exclusive else 'at least'} {minimum} and at most {maximum}")
            if scale is not None:
                decimal = Decimal(str(value))
                if decimal != decimal.quantize(Decimal(1).scaleb(-scale)):
                    raise ValueError(f"must have at most {scale} decimal places")
            return result

        cell("external_id", lambda value: text(value, 80), required=True)
        if kind == "registrations":
            cell("full_name", lambda value: text(value, 120), required=True)
            genders = {"m": "M", "male": "M", "f": "F", "female": "F", "o": "O", "other": "O", "u": "U", "unknown": "U"}

            def gender(value):
                normalized = genders.get(str(value).casefold())
                if normalized is None:
                    raise ValueError("use M/F/O/U or male/female/other/unknown")
                return normalized

            cell("gender", gender)
            cell("birth_year", lambda value: bounded(value, 1900, date.today().year, integer=True))
            cell("height_cm", lambda value: bounded(value, 0, 300, exclusive=True, scale=2))
            cell("weight_kg", lambda value: bounded(value, 0, 1000, exclusive=True, scale=2))
            cell("group_id", lambda value: text(value, 36))
            cell("test_10k_sec", parse_duration)
            cell("fm_best_sec", parse_duration)
            cell("prep_mileage_km", lambda value: bounded(value, 0, 100000, scale=2))
            cell("experience_text", lambda value: text(value, 10000))
            key = result["external_id"]
        else:
            cell("session_date", parse_date, required=True)
            cell("distance_km", lambda value: bounded(value, 0, 1000, exclusive=True, scale=3), required=True)
            cell("pace_sec_per_km", parse_pace, required=True)
            cell("resting_hr", lambda value: bounded(value, 1, 300, integer=True))
            cell("fatigue_level", lambda value: bounded(value, 1, 5, integer=True))
            cell("notes", lambda value: text(value, 10000))
            session_date = result["session_date"]
            if session_date and not camp.start_date <= session_date <= camp.end_date:
                errors.append({"row": number, "field": "session_date", "message": "date is outside this bootcamp's start_date/end_date"})
            key = (result["external_id"], session_date)
        if result["external_id"] is not None:
            if key in seen:
                errors.append({"row": number, "field": "external_id", "message": "duplicate import key in this file"})
            seen.add(key)
        parsed.append(result)

    external_ids = {row["external_id"] for row in parsed if row["external_id"] is not None}
    runners = {runner.external_id: runner for runner in db.scalars(select(Runner).where(Runner.tenant_id == tenant_id, Runner.external_id.in_(external_ids)))} if external_ids else {}
    if kind == "sessions":
        registered = set(db.scalars(select(Registration.runner_id).where(Registration.tenant_id == tenant_id, Registration.bootcamp_id == camp.id)))
        for row in parsed:
            external_id = row["external_id"]
            if external_id is not None and (external_id not in runners or runners[external_id].id not in registered):
                errors.append({"row": row["_row"], "field": "external_id", "message": "runner is not registered in this tenant's bootcamp"})
    else:
        group_ids = {row["group_id"] for row in parsed if row.get("group_id")}
        groups = set(db.scalars(select(BootcampGroup.id).where(BootcampGroup.tenant_id == tenant_id, BootcampGroup.bootcamp_id == camp.id, BootcampGroup.id.in_(group_ids)))) if group_ids else set()
        for row in parsed:
            if row.get("group_id") and row["group_id"] not in groups:
                errors.append({"row": row["_row"], "field": "group_id", "message": "group does not belong to this tenant's bootcamp"})
    return parsed, runners, errors


def _assign(target, values, fields):
    for field in fields:
        if values.get(field) is not None:
            setattr(target, field, values[field])


def import_file(db: Session, *, tenant_id: str, bootcamp_id: str, path: Path, kind: str, dry_run: bool = False) -> dict:
    """Validate the entire file before touching business rows; never commit.

    Rejected non-dry runs create only an ImportBatch audit. Unsupported suffixes
    and files above 32 MiB fail preflight before any database access. Database
    failures propagate, and the caller must roll back. A dry run does not flush,
    even when the caller has unrelated pending ORM objects.
    """
    if kind not in {"registrations", "sessions"}:
        raise ValueError("kind must be registrations or sessions")
    path = Path(path)
    if path.suffix.lower() not in {".csv", ".xlsx"}:
        raise ValueError("only .xlsx and UTF-8 .csv files are supported")
    if path.stat().st_size > MAX_IMPORT_BYTES:
        raise ValueError("input file exceeds the 32 MiB size limit")
    with path.open("rb") as stream:
        content = stream.read(MAX_IMPORT_BYTES + 1)
    if len(content) > MAX_IMPORT_BYTES:
        raise ValueError("input file exceeds the 32 MiB size limit")
    digest = hashlib.sha256(content).hexdigest()
    with db.no_autoflush:
        camp_query = select(Bootcamp).where(Bootcamp.id == bootcamp_id, Bootcamp.tenant_id == tenant_id)
        # Serialize concurrent imports of the same camp on MySQL. The lock
        # remains owned by the caller's transaction and is never used by dry runs.
        camp = db.scalar(camp_query if dry_run else camp_query.with_for_update())
        if camp is None:
            raise ValueError("bootcamp not found for this tenant")
        existing = db.scalar(select(ImportBatch).where(ImportBatch.tenant_id == tenant_id, ImportBatch.bootcamp_id == bootcamp_id, ImportBatch.kind == kind, ImportBatch.sha256 == digest, ImportBatch.status == "committed"))
        if existing is not None:
            return {"batch_id": existing.id, "status": "duplicate", "row_count": existing.row_count, "accepted_count": 0, "rejected_count": 0, "errors": [], "inserted_count": 0, "updated_count": 0, "duplicate": True}
        try:
            rows, errors = _read_rows(content, path.suffix)
        except Exception as exc:
            rows, errors = [], [{"row": 1, "field": "file", "message": f"cannot read input: {exc}"}]
        if not rows and not errors:
            errors.append({"row": 1, "field": "file", "message": "file has no data rows"})
        parsed, runners, row_errors = _validate_rows(db, tenant_id, camp, rows, kind)
        errors.extend(row_errors)
        result = {"batch_id": None, "status": "rejected" if errors else "validated" if dry_run else "committed", "row_count": len(rows), "accepted_count": 0 if errors else len(rows), "rejected_count": len(rows) if errors else 0, "errors": errors, "inserted_count": 0, "updated_count": 0, "duplicate": False}
        if dry_run:
            return result
        if not errors:
            if kind == "registrations":
                registrations = {item.runner_id: item for item in db.scalars(select(Registration).where(Registration.tenant_id == tenant_id, Registration.bootcamp_id == bootcamp_id))}
                for row in parsed:
                    runner = runners.get(row["external_id"])
                    if runner is None:
                        runner = Runner(id=str(uuid4()), tenant_id=tenant_id, external_id=row["external_id"], full_name=row["full_name"], gender=row.get("gender") or "U")
                        db.add(runner)
                        runners[row["external_id"]] = runner
                    _assign(runner, row, ("full_name", "gender", "birth_year", "height_cm", "weight_kg"))
                # Flush runners before inserting dependent registrations.
                db.flush()
                for row in parsed:
                    runner = runners[row["external_id"]]
                    registration = registrations.get(runner.id)
                    if registration is None:
                        registration = Registration(id=str(uuid4()), tenant_id=tenant_id, bootcamp_id=bootcamp_id, runner_id=runner.id)
                        db.add(registration)
                        result["inserted_count"] += 1
                    else:
                        result["updated_count"] += 1
                    _assign(registration, row, ("group_id", "test_10k_sec", "fm_best_sec", "prep_mileage_km", "experience_text", "raw_data"))
            else:
                sessions = {(item.runner_id, item.session_date): item for item in db.scalars(select(TrainingSession).where(TrainingSession.tenant_id == tenant_id, TrainingSession.bootcamp_id == bootcamp_id))}
                for row in parsed:
                    runner = runners[row["external_id"]]
                    session = sessions.get((runner.id, row["session_date"]))
                    if session is None:
                        session = TrainingSession(id=str(uuid4()), tenant_id=tenant_id, bootcamp_id=bootcamp_id, runner_id=runner.id, session_date=row["session_date"])
                        db.add(session)
                        result["inserted_count"] += 1
                    else:
                        result["updated_count"] += 1
                    _assign(session, row, ("distance_km", "pace_sec_per_km", "resting_hr", "fatigue_level", "notes"))
        batch = ImportBatch(id=str(uuid4()), tenant_id=tenant_id, bootcamp_id=bootcamp_id, kind=kind, file_name=path.name, sha256=digest, status=result["status"], row_count=result["row_count"], accepted_count=result["accepted_count"], rejected_count=result["rejected_count"], errors=result["errors"], inserted_count=result["inserted_count"], updated_count=result["updated_count"])
        db.add(batch)
        db.flush()
        result["batch_id"] = batch.id
        return result
