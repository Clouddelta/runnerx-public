from datetime import date, time
from hashlib import sha256

import pandas as pd
import pytest
from openpyxl import load_workbook

from runnerx.etl import _read_rows, clean_cell, parse_date, parse_duration, parse_pace
from runnerx.sample import generate_sample


@pytest.mark.parametrize("value, expected", [
    (2400, 2400), (2400.0, 2400), ("40:30", 2430), ("3:05:09", 11109),
    ("3h5m9s", 11109), ("3h 5m 9s", 11109), ("40 min 30 sec", 2430), ("180 minutes", 10800),
    ("3600 seconds", 3600), ("3 hours 5 minutes 9 seconds", 11109),
    ("1 hour 1 minute 1 second", 3661), ("2 hrs 4 mins 8 secs", 7448),
    ("1 HR 2 MIN 3 SEC", 3723), (time(3, 5, 9), 11109),
])
def test_duration_formats(value, expected):
    assert parse_duration(value) == expected


@pytest.mark.parametrize("value", [
    None, float("nan"), "", "00:99:00", "2:60", "fast", 0, -10, 2400.5,
    "200000", "1h80m", "5m60s", "1.5 hours", "3m2h", "1hourglass", "hours",
])
def test_duration_rejects_ambiguous_or_invalid_values(value):
    with pytest.raises(ValueError):
        parse_duration(value)


@pytest.mark.parametrize("value, expected", [
    (330, 330), ("5:30", 330), ("5:30/km", 330), ("5m30s/km", 330),
    ("330 sec/km", 330), ("5 minutes 30 seconds / km", 330),
    ("330 SECONDS / KM", 330), (time(0, 5, 30), 330),
])
def test_pace_formats(value, expected):
    assert parse_pace(value) == expected


@pytest.mark.parametrize("value", [
    "3:30:00", "00:05:30", "1h", "1 hour", "0h5m", "1 HR", time(1, 0),
    0, -1, 7201, "5:99", "1.5", "5m60s/km", "5:30/mile",
])
def test_pace_is_not_a_race_duration(value):
    with pytest.raises(ValueError):
        parse_pace(value)


def test_dates_require_year_and_real_calendar_date():
    assert parse_date("2026-01-05") == date(2026, 1, 5)
    assert parse_date("2026/1/5") == date(2026, 1, 5)
    for value in ("0105", "01-05", "2026-02-30", 46000):
        with pytest.raises(ValueError):
            parse_date(value)


def test_missing_cells_are_cleaned_without_dropping_zero_or_ids():
    for value in (None, float("nan"), pd.NA, " ", "NaN"):
        assert clean_cell(value) is None
    assert clean_cell(0) == 0
    assert clean_cell(" 0001 ") == "0001"


def test_csv_aliases_preserve_leading_zero_ids_and_report_header_collisions():
    rows, errors = _read_rows("Runner Code,Name,Birth Year\n0001,Sample Runner,1990\n".encode("utf-8-sig"), ".csv")
    assert errors == []
    assert rows[0]["data"] == {"external_id": "0001", "full_name": "Sample Runner", "birth_year": "1990"}
    for header in ("external_id,runner_external_id", "external_id,external_id"):
        _, errors = _read_rows((header + "\nSYN1,SYN2\n").encode(), ".csv")
        assert errors[0]["field"] == "headers"


def test_sample_is_deterministic_and_explicitly_synthetic(tmp_path):
    first = generate_sample(tmp_path / "first", runners=3, seed=42)
    second = generate_sample(tmp_path / "second", runners=3, seed=42)
    assert set(first) == {"registrations", "sessions", "invalid_registrations", "readme"}
    for kind in first:
        assert sha256(first[kind].read_bytes()).digest() == sha256(second[kind].read_bytes()).digest()
    workbook = load_workbook(first["registrations"], read_only=True, data_only=True)
    rows = list(workbook.active.values)
    workbook.close()
    assert len(rows) == 4
    assert rows[0] == (
        "external_id", "full_name", "gender", "birth_year", "height_cm", "weight_kg",
        "test_10k_sec", "fm_best_sec", "prep_mileage_km", "experience_text",
    )
    assert all(value.isascii() for row in rows for value in row if isinstance(value, str))
    assert {row[2] for row in rows[1:]} == {"M", "F"}
    assert rows[1][0:2] == ("SYN0001", "Sample Runner0001")
    sessions = pd.read_csv(first["sessions"])
    assert len(sessions) == 36
    assert sessions.session_date.min() == "2026-01-05"
    assert sessions.session_date.max() <= "2026-02-01"
    assert sessions.groupby("external_id").size().tolist() == [12, 12, 12]
    assert "do not describe real people" in first["readme"].read_text(encoding="utf-8")


@pytest.mark.parametrize("runners", [0, -1, 10001, True, 1.5])
def test_sample_rejects_invalid_size(tmp_path, runners):
    with pytest.raises(ValueError):
        generate_sample(tmp_path, runners=runners)
