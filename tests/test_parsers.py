from datetime import date, time
from hashlib import sha256

import pandas as pd
import pytest
from openpyxl import load_workbook

from runnerx.etl import _read_rows, clean_cell, parse_date, parse_duration, parse_pace
from runnerx.sample import generate_sample


@pytest.mark.parametrize("value, expected", [
    (2400, 2400), (2400.0, 2400), ("40:30", 2430), ("3:05:09", 11109),
    ("3小时5分9秒", 11109), ("40分30秒", 2430), ("180分钟", 10800),
    ("3600秒", 3600), ("40：30", 2430), (time(3, 5, 9), 11109),
])
def test_duration_formats(value, expected):
    assert parse_duration(value) == expected


@pytest.mark.parametrize("value", [None, float("nan"), "", "00:99:00", "2:60", "fast", 0, -10, 2400.5, "200000", "1小时80分"])
def test_duration_rejects_ambiguous_or_invalid_values(value):
    with pytest.raises(ValueError):
        parse_duration(value)


@pytest.mark.parametrize("value, expected", [(330, 330), ("5:30", 330), ("5:30/km", 330), ("5分30秒/公里", 330), ("330秒/公里", 330), (time(0, 5, 30), 330)])
def test_pace_formats(value, expected):
    assert parse_pace(value) == expected


@pytest.mark.parametrize("value", ["3:30:00", "00:05:30", "1小时", time(1, 0), 0, -1, 7201, "5:99", "1.5"])
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
    rows, errors = _read_rows("跑者编号,姓名,出生年份\n0001,Sample Runner,1990\n".encode("utf-8-sig"), ".csv")
    assert errors == []
    assert rows[0]["data"] == {"external_id": "0001", "full_name": "Sample Runner", "birth_year": "1990"}
    for header in ("external_id,跑者编号", "external_id,external_id"):
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
