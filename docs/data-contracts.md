# Data contracts

Input files are UTF-8 CSV (a BOM is accepted) or XLSX, at most 32 MiB. XLSX uses the first worksheet and row 1 as its header. Blank rows are ignored. English canonical names and documented Chinese aliases are accepted; ambiguous duplicate mappings are rejected. Extra columns are retained in registration `raw_data` but do not automatically become modeled attributes.

## Registration rows

Required: `external_id`, `full_name`. Example Chinese headers are `跑者编号`, `姓名`, `性别`, `出生年份`, `身高(cm)`, `体重(kg)`, `10K成绩`, `全马最佳成绩`, `备赛跑量(km)`, and `经历与计划`. The included sample uses the actual accepted aliases; `src/runnerx/etl.py` is the full mapping source.

Optional: `gender`, `birth_year`, `height_cm`, `weight_kg`, `group_id`, `test_10k_sec`, `fm_best_sec`, `prep_mileage_km`, `experience_text`.

- External IDs are strings, case-sensitive, at most 80 characters. Keep Excel ID cells formatted as text when leading zeros matter. IDs must come from a stable source, not an inferred name match.
- Names contain 1..120 characters. Gender is M/F/O/U (Chinese equivalents are accepted by ETL); missing gender defaults to U on a new runner.
- Birth year is explicit, from 1900 through the current year. Age is not silently converted using a fixed year.
- Numeric race times are seconds. Strings may use MM:SS, HH:MM:SS or Chinese hours/minutes/seconds. Invalid clock components such as `00:99:00` are rejected.
- Height, weight and mileage permit at most two decimal places; values are not silently rounded. Group references must belong to the same tenant and camp.

## Training rows

Required by ETL: `external_id`, `session_date`, `distance_km`, `pace_sec_per_km`. Optional: `resting_hr`, `fatigue_level`, `notes`.

- Import registrations first. The runner must already be registered for the target camp.
- Dates must be explicit ISO dates (`YYYY-MM-DD`) or Excel date cells and fall within the camp dates.
- Distance is a positive number of kilometres with at most three decimal places.
- Pace is seconds per kilometre or MM:SS. An HH:MM:SS race duration is not accepted as pace.
- Fatigue is an integer 1..5. Heart rate is an optional integer. These are recorded observations, not diagnoses.
- Repeated `(external_id, session_date)` keys within a file are rejected.

## Corrections, failures and replay

A file is validated as one batch. Any row/header error rejects every business change in that batch. The error report gives a 1-based source row, field and message. `rejected_count` is the number of rows not applied, not the number of individual error messages.

Committed files are identified by tenant, camp, import kind and exact file-byte SHA-256. Replaying identical bytes skips the import, even if business rows have subsequently been edited. To apply a deliberate correction, submit a changed source file or use the API. A corrected file updates matching business keys; absent optional values leave existing attributes intact.

Import is a local administrative CLI operation, not a public file-upload endpoint. Use `--dry-run` before applying an unfamiliar source format.
