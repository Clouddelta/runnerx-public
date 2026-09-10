# Synthetic RunnerX sample data

All names, identifiers, measurements, and training records in this directory are generated. They do not describe real people and were not copied from any legacy runner spreadsheet.

Generation: runners=30, seed=42. The same options produce byte-identical files.

Suggested bootcamp: code `demo-2026`, start date `2026-01-05`, end date `2026-02-01`.

- `registrations.xlsx`: 30 synthetic runners, Chinese column aliases, explicit birth years.
- `sessions.csv`: 360 synthetic sessions, three per runner per week for four weeks.
- `invalid_registrations.xlsx`: two deliberately invalid rows (missing external ID; malformed 10K clock). The entire batch should be rejected, with no business rows changed.

Import registrations before sessions. Runner identity is `external_id` within a tenant; names are not keys. Repeated committed imports of the same bytes are skipped.

Numeric race times are seconds. Race times also accept MM:SS, HH:MM:SS, or Chinese time units. Pace is seconds per kilometre or MM:SS; a race-duration HH:MM:SS value is rejected as pace.
