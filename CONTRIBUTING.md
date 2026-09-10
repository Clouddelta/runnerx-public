# Contributing

Set up the Python development environment described in README. Keep pull requests focused and describe the behavior change and verification.

For parser changes, include synthetic inputs, expected outputs and failure cases. For database/API changes, include the migration and a MySQL integration test. Changes to tenant ownership must be tested both through HTTP and directly against database constraints.

Before submitting, run the unit tests and the complete MySQL suite. If only unit tests were run, state that clearly. Do not commit `.env`, API keys, local databases, real runner records or third-party training materials.

Development conventions:

- Use English throughout documentation, code comments, docstrings, CLI/API messages, tests, examples and synthetic sample data. Keep import headers and unit labels in English.
- Use explicit units in field names (`_sec`, `_km`, `_cm`, `_kg`).
- Distinguish a missing value from a numeric zero.
- Preserve source row locations for validation failures.
- Do not identify people by name alone.
- Keep public input schemas explicit; ownership comes from authentication.
- Maintain backwards-compatible migrations for shared deployments.
