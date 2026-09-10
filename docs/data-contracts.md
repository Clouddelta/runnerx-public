# Data contracts

Imports accept UTF-8 CSV (optional BOM) and XLSX files up to 32 MiB. XLSX reads the first worksheet with headers in row 1. Blank rows are ignored; empty cells and `nan`, `null`, `none`, `n/a` are treated as missing.

Headers accept canonical English names and [aliases](../src/runnerx/etl.py), ignoring case, whitespace, underscores, hyphens, parentheses and slashes. Duplicate column mappings are rejected. Registration `raw_data` retains source columns, including extra fields.

## Registration rows

| Field | Required | Format / limits |
| --- | --- | --- |
| `external_id` | Yes | Case-sensitive string, 1-80 characters; unique per tenant |
| `full_name` | Yes | 1-120 characters |
| `gender` | No | M/F/O/U or male/female/other/unknown, case-insensitive; new runners default to U |
| `birth_year` | No | Integer, 1900 through the current year |
| `height_cm` | No | > 0 to 300; up to 2 decimal places |
| `weight_kg` | No | > 0 to 1000; up to 2 decimal places |
| `group_id` | No | Existing group UUID in the target tenant and bootcamp |
| `test_10k_sec`, `fm_best_sec` | No | Whole seconds, 1-172800; duration formats below |
| `prep_mileage_km` | No | 0-100000; up to 2 decimal places |
| `experience_text` | No | Up to 10000 characters |

Use stable source IDs; keep Excel ID cells as text to preserve leading zeros. Duplicate `external_id` values within a file are rejected.

Durations accept numeric seconds, `MM:SS`, `HH:MM:SS`, Excel time cells, or English units such as `3h5m9s`. Units accept `h`/`hr`/`hour`, `m`/`min`/`minute`, `s`/`sec`/`second` and plurals, case-insensitively. Fractional seconds and invalid clock components are rejected.

## Training rows

| Field | Required | Format / limits |
| --- | --- | --- |
| `external_id` | Yes | Runner already registered in the target bootcamp |
| `session_date` | Yes | Explicit year/month/day (`YYYY-MM-DD`, `/` also accepted) or Excel date; within bootcamp dates |
| `distance_km` | Yes | > 0 to 1000; up to 3 decimal places |
| `pace_sec_per_km` | Yes | Whole seconds/km, 1-7200; pace formats below |
| `resting_hr` | No | Integer, 1-300 |
| `fatigue_level` | No | Integer, 1-5 |
| `notes` | No | Up to 10000 characters |

Pace accepts numeric seconds/km, `MM:SS`, Excel time cells, or English minutes/seconds such as `5m30s/km` or `330 seconds/km`. The `/km` suffix is optional; hours and `HH:MM:SS` are rejected. Repeated `(external_id, session_date)` keys within a file are rejected.

## Batch behavior

- Import registrations before training sessions. Decimal values exceeding field precision are rejected rather than rounded.
- Row or header errors reject all business changes and create a rejection audit. Unsupported formats and oversized files fail before database access.
- Error reports contain the 1-based source row, field and message. `rejected_count` counts unapplied rows, not individual errors.
- Replay detection uses tenant, bootcamp, import kind and exact file-byte SHA-256. Identical committed files are skipped, including after API edits.
- Changed files update matching business keys. Missing optional values preserve existing attributes; API PATCH with explicit null clears nullable fields.
- The administrative CLI supports `--dry-run` without writes. Import files are not accepted through the HTTP API.
