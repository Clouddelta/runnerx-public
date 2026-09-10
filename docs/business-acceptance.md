# Live business acceptance

Executed on 2026-09-10 against the running Docker Compose API at `http://127.0.0.1:8002`, backed by MySQL 8.4.11. These checks use real HTTP requests and administrative CLI imports inside the API container. They are separate from the 93-test pytest suite.

Two dedicated synthetic QA tenants were created for run `qa-business-20260910-161712-f9c484`. Each has its own camp with code `qa-2026`, covering 2026-01-05 through 2026-02-01. Tenant A imported three generated registrations and 36 training sessions. Tenant B initially had only its own empty camp, then imported the same registration file.

## Observed results

All 11 acceptance checks passed. They cover these three business scenarios:

| Scenario | Operation | Observed result |
| --- | --- | --- |
| Modify an existing runner through Excel | Keep `SYN0001`; set name to `QA Updated Runner`, weight to `66.25`, and 10K time to `45:30` | API returned the new values, including 2730 seconds; runner and registration UUIDs were unchanged |
| Validate the modified workbook | Import with `--dry-run` | `validated`; no business or import-audit changes |
| Import the modified workbook | Import all three registration rows | `committed`, 0 inserted and 3 existing rows processed; session data and the assigned group were preserved |
| Replay the modified workbook | Reimport the exact bytes | `duplicate`, original batch ID retained; no data or audit changes |
| Reject a mixed valid/invalid workbook | Include a valid name change, a valid new runner `QA-NEW`, and invalid `00:99:00` in Excel cell G3 | CLI exited 2; all 4 data rows rejected; neither the name change nor the new runner was saved |
| Preview the invalid workbook | Import with `--dry-run` | CLI exited 2 without creating an audit entry or changing business data |
| Inspect the rejection audit | Query the returned import batch ID | One rejected audit recorded the row 3 `test_10k_sec` error; accepted, inserted and updated counts were zero |
| Protect audit records | Attempt POST, PATCH and DELETE through the API | HTTP 405 |
| Keep tenant data isolated | Tenant B attempts GET, PATCH and DELETE on A's runners, camps, groups, registrations and sessions | HTTP 404 for every foreign object; A's data unchanged |
| Scope statistics, audit details and filters | B requests A's camp statistics, import details or lists filtered by A's IDs | Details returned 404; filtered lists returned zero records |
| Allow the same external ID in different tenants | Import the same original Excel bytes into B | 3 new B registrations; distinct UUIDs; B retained the original runner name while A retained its edited name |
| Reject foreign relations and ownership fields | Link B records to A's runner/camp, or submit `tenant_id`, `id` or unknown input fields | Foreign links returned 404; forbidden fields returned 422; both tenants' existing data unchanged |
| Preserve the existing demo | Compare all demo resource records before and after the checks | Unchanged: 30 runners, 30 registrations and 360 sessions |

`updated_count` counts existing registration rows processed by the import, not the number of cells that changed. The modified workbook contained all three registration rows, so the observed count was 3 even though only the first runner's values were edited. A rejected batch reports all rows as rejected; its error list identifies the malformed row.

## Final QA data

| Tenant | Runners | Registrations | Sessions | Groups | Camps | Import audits |
| --- | --- | --- | --- | --- | --- | --- |
| A | 3 | 3 | 36 | 1 | 1 | 4 |
| B | 3 | 3 | 0 | 0 | 1 | 1 |

The QA tenants were retained for local inspection. Their credentials, the generated/edited workbooks and the detailed JSON report are stored only under the ignored `.local/qa-business-20260910-161712-f9c484/` directory. The original files in `data/sample` were not edited. The local helper `.local/business_acceptance.py` performed the checks; it is an execution artifact, not part of the distributed test suite.

## Repeating the scenarios

Use two new tenant slugs for each run. In the running API container, provision each tenant with `python -m runnerx.cli create-tenant`, save its returned key locally, and create its camp through `POST /api/v1/bootcamps`. Generate fixtures with `python -m runnerx.cli generate-sample --output /tmp/<qa-run> --runners 3 --seed 42`, then use the normal `import` CLI with each tenant slug and camp code.

For the update scenario, preserve cell A2 and edit B2, F2 and G2. For atomic rejection, add both a valid new row and an invalid G3 value to a copy of the updated workbook. Compare API record contents and UUIDs as well as totals. For tenant isolation, repeat requests using the other tenant's key and resource UUIDs. Use the CLI's exit code and audit response to distinguish a validation rejection from a runtime failure.
