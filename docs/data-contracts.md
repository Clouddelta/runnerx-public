# Data contracts

Input files are UTF-8 CSV (a BOM is accepted) or XLSX, at most 32 MiB. XLSX uses the first worksheet and row 1 as its header. Blank rows are ignored. English canonical names and documented English aliases are accepted; ambiguous duplicate mappings are rejected. Extra columns are retained in registration `raw_data` but do not automatically become modeled attributes.

## Registration rows

Required: `external_id`, `full_name`. The bundled workbook uses canonical English headers, including `gender`, `birth_year`, `height_cm`, `weight_kg`, `test_10k_sec`, `fm_best_sec`, `prep_mileage_km` and `experience_text`. Aliases include `runner_code` for `external_id` and `name` for `full_name`. Header matching ignores case, spaces, underscores, hyphens, parentheses and slashes. See `src/runnerx/etl.py` for the full English alias mapping.

Optional: `gender`, `birth_year`, `height_cm`, `weight_kg`, `group_id`, `test_10k_sec`, `fm_best_sec`, `prep_mileage_km`, `experience_text`.

- External IDs are strings, case-sensitive, at most 80 characters. Keep Excel ID cells formatted as text when leading zeros matter. IDs must come from a stable source, not an inferred name match.
- Names contain 1..120 characters. Gender accepts M/F/O/U or `male`/`female`/`other`/`unknown`, case-insensitively; missing gender defaults to U on a new runner.
- Birth year is explicit, from 1900 through the current year. Age is not silently converted using a fixed year.
- Numeric race times are seconds. Strings may use MM:SS, HH:MM:SS or English time units, such as `3h5m9s` or `3 hours 5 minutes 9 seconds`. Unit names accept `h`/`hr`/`hour`, `m`/`min`/`minute` and `s`/`sec`/`second`, including plural forms, case-insensitively. Excel time cells are also accepted. Invalid clock components such as `00:99:00` are rejected.
- Height, weight and mileage permit at most two decimal places; values are not silently rounded. Group references must belong to the same tenant and camp.

## Training rows

Required by ETL: `external_id`, `session_date`, `distance_km`, `pace_sec_per_km`. Optional: `resting_hr`, `fatigue_level`, `notes`.

- Import registrations first. The runner must already be registered for the target camp.
- Dates must be explicit ISO dates (`YYYY-MM-DD`) or Excel date cells and fall within the camp dates.
- Distance is a positive number of kilometres with at most three decimal places.
- Pace accepts seconds per kilometre, MM:SS or English minutes/seconds, such as `5m30s/km` or `330 seconds/km`. The `/km` suffix is optional. Hour units and HH:MM:SS race durations are not accepted as pace.
- Fatigue is an integer 1..5. Heart rate is an optional integer. These are recorded observations, not diagnoses.
- Repeated `(external_id, session_date)` keys within a file are rejected.

## Corrections, failures and replay

A file is validated as one batch. Any row/header error rejects every business change in that batch. The error report gives a 1-based source row, field and message. `rejected_count` is the number of rows not applied, not the number of individual error messages.

Committed files are identified by tenant, camp, import kind and exact file-byte SHA-256. Replaying identical bytes skips the import, even if business rows have subsequently been edited. To apply a deliberate correction, submit a changed source file or use the API. A corrected file updates matching business keys; absent optional values leave existing attributes intact.

Import is a local administrative CLI operation, not a public file-upload endpoint. Use `--dry-run` before applying an unfamiliar source format.

## Version compatibility

Starting with v0.1.1, import headers and unit labels use English. Convert localized headers and unit strings from v0.1.0 to the canonical field names and formats above before importing. This parser change does not require a database migration or modify previously stored data.
