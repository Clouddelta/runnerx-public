# Sample data

Synthetic fixtures; they do not describe real people.

Generated with `runners=30`, `seed=42`. Identical options produce byte-identical files.

| File | Content |
| --- | --- |
| `registrations.xlsx` | 30 runners with canonical English headers |
| `sessions.csv` | 360 sessions over four weeks |
| `invalid_registrations.xlsx` | Missing external ID and invalid 10K time; the batch is rejected |

Camp: `demo-2026`, `2026-01-05` through `2026-02-01`. Import registrations before sessions.
