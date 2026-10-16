# MigrateIQ AI Live Integration and Acceptance Report

**Execution date:** 2026-10-16 (Asia/Calcutta)

**Environment:** Windows 11, Docker Desktop 29.0.1, PostgreSQL 16, Python 3.12
**Scope:** Live Docker, database migrations, end-to-end ETL, recovery, reconciliation,
Streamlit actions, Gemini, automated tests, and report exports.

## Overall result

The core demonstration workflows passed with real PostgreSQL writes and real HTTP requests.
All five Compose services are healthy. The complete automated suite passes (`19 passed`),
Ruff passes, and Python bytecode compilation passes. The 10,000-input benchmark completed in
10.480 seconds and an identical rerun created zero additional destination rows.

## Live service and database verification

| Component | Result | Evidence |
|---|---:|---|
| PostgreSQL | PASS | `postgres:16-alpine`, healthy; host port `55432` |
| FastAPI backend | PASS | Healthy on `http://localhost:8000`; runs `alembic upgrade head` before startup |
| Migration worker | PASS | Healthy database probe; processed live jobs |
| Streamlit frontend | PASS | Healthy on `http://localhost:8501`; application health endpoint returned 200 |
| LegacyCRM / ModernCRM demo | PASS | Healthy on `http://localhost:8001`; 250 synthetic legacy records |

The isolated live database `migrateiq_acceptance` was created in the Docker PostgreSQL server.
`alembic upgrade head` created the schema from scratch and recorded revision `0001_initial`.
The application database also reports revision `0001_initial`; backend startup now applies any
future pending migrations automatically.

The host mapping was changed from `5432` to `55432` because another local PostgreSQL container
already owned `5432`. This does not change the container-to-container connection on port `5432`.

## End-to-end results

### CSV to PostgreSQL (small error-bearing sample)

This flow used the live FastAPI service, worker, upload volume, and PostgreSQL database.

| Measurement | Result |
|---|---:|
| Source rows | 6 |
| Processed | 6 |
| Inserted | 3 |
| Updated | 0 |
| Skipped as duplicate | 1 |
| Rejected by validation | 2 |
| Destination rows queried in PostgreSQL | 3 |
| Worker batches | 3 |
| Wall time | 1.693 s |
| Reconciliation | MATCHED; 3 verified, 0 missing, 0 duplicate keys, 0 field mismatches |

The test included source preview, schema detection, saved mapping, transformation preview,
validation preview, queued worker execution, direct PostgreSQL count verification, and
post-load reconciliation.

### REST API to REST API

The source was the authenticated, paginated LegacyCRM endpoint. The destination was ModernCRM,
using actual POST requests and the configured create-or-update behavior.

| Measurement | Result |
|---|---:|
| LegacyCRM source rows | 250 |
| Processed | 250 |
| Inserted into ModernCRM | 250 |
| Updated / skipped / rejected | 0 / 0 / 0 |
| ModernCRM rows read back | 250 |
| Worker batches | 5 |
| Wall time | 1.898 s |
| Reconciliation | MATCHED; 250 verified, no missing, duplicate, or mismatched records |

### Generated 10,000-record migration

The generated data is synthetic and includes duplicate business keys, malformed emails,
missing identifiers, inconsistent formats, nulls, and unexpected columns.

| Measurement | First run | Identical rerun |
|---|---:|---:|
| Source rows | 10,000 | 10,000 |
| Processed | 10,000 | 10,000 |
| Inserted | 9,933 | 0 |
| Updated | 0 | 0 |
| Skipped | 10 | 9,943 |
| Rejected | 57 | 57 |
| Destination rows after run | 9,933 | 9,933 |
| Duplicate destination rows created | n/a | **0** |
| Worker batches | 20 | 20 |
| Wall time | **10.480 s** | **6.548 s** |
| Reconciliation | MATCHED, 9,933 verified | MATCHED, 9,933 verified |

The totals reconcile on both runs. On the rerun, 9,933 existing valid records plus 10 duplicate
source rows were skipped; the same 57 invalid records were rejected.

### Checkpoint interruption and recovery

PASS. A second 10,000-row job was interrupted by stopping the worker after a durable checkpoint
at transformed offset 2,050. Restarting the worker detected the orphaned `running` job, requeued
it, resumed from its stored checkpoint, and completed at offset 9,990. Idempotent upserts
prevented duplicate destination records. A startup audit-log entry confirmed recovery.

The transformed offset is 9,990 rather than 10,000 because 10 duplicate source rows are removed
by the configured de-duplication step; the job's processed counter correctly reports all 10,000
input rows.

### Intentional reconciliation mismatch

PASS. One persisted destination `full_name` value was changed directly in PostgreSQL.
Reconciliation changed from `matched` to `mismatched`, reported exactly one field mismatch,
and verified the remaining 9,932 records. The original value was restored after the test.

### Reports

PASS. For the completed rerun, the complete JSON report endpoint returned HTTP 200 with
3,351,009 bytes. The rejected-record CSV returned HTTP 200 with 5,958 bytes and 58 lines
(header plus 57 rejected rows). Both download controls also rendered on the Streamlit report page.

## Streamlit acceptance

Browser-driver automation was not available. The official Streamlit `AppTest` harness was used
to execute the Python UI, widget callbacks, forms, reruns, and live backend calls. The deployed
Streamlit health endpoint was separately verified over HTTP.

| Page / control group | Result |
|---|---:|
| Dashboard and actual-history metrics/chart | PASS |
| Projects create form, source/destination dropdowns, deletion confirmation and delete | PASS |
| Data Sources file uploader, upload/inspect, role/type dropdowns, save connection | PASS |
| Saved connection selection and live PostgreSQL connection test | PASS |
| Schema Mapping project selection, AI toggle, generate, editor data, approve/save | PASS |
| Transform & Validate cleaning suggestions, before/after preview, validation preview | PASS |
| Execution project/batch controls, start migration, status refresh | PASS |
| Reports job selection, reconciliation action, audit data and download controls | PASS |

All seven pages rendered without an application exception. The deterministic UI mapping test
confirmed these mappings: `cust_id -> customer_id`, `cust_name -> full_name`,
`mobile_no -> phone`, `email_id -> email`, and `join_date -> created_at`; unrelated fields were
left unmapped. Its validation preview reported 100 valid and 0 invalid in the first 100 rows.

A migration launched from the Execution page completed with 10,000 processed, 9,943 updates,
and 57 expected validation rejects. The Reports page then returned `Reconciliation: matched`.

**Manual visual check still recommended:** AppTest verifies behavior but not browser-specific
pixel layout, responsive breakpoints, keyboard navigation, or the operating-system file chooser.
The file uploader itself was exercised programmatically with real CSV bytes.

## Gemini verification

PASS. A configured Gemini credential was available. Mapping was called with column names only
(no customer rows or unredacted PII), returned provider `gemini`, and produced four structured
suggestions. The credential was not included in application responses or the QA report.

The provider returned a transient HTTP 503 during an earlier attempt. That exposed and led to a
fix in the fallback boundary: provider failures now return a deterministic result and a sanitized
warning containing only the exception type. A later live attempt succeeded against Gemini.

## Automated verification

| Check | Result |
|---|---:|
| Full Pytest suite with live `TEST_DATABASE_URL` | **19 passed** |
| Non-integration tests | **18 passed** |
| Disposable PostgreSQL integration test | **1 passed** |
| Ruff | PASS, no findings |
| `compileall` for `src`, `frontend`, and `scripts` | PASS |

Coverage added during acceptance includes orphaned-job recovery, secret-safe Gemini fallback,
and the exact legacy-to-modern deterministic mapping that failed in the UI path. Reusable live
harnesses are in `scripts/live_acceptance.py`, `scripts/ui_acceptance.py`, and
`scripts/ui_data_sources_acceptance.py`.

## Failures found and fixed

1. **Local PostgreSQL port collision:** moved the host binding to `55432` and updated docs.
2. **Incomplete Compose readiness:** added meaningful health checks for worker and frontend;
   backend now waits for PostgreSQL and applies Alembic migrations before serving.
3. **Interrupted jobs remained `running`:** worker startup now requeues orphaned running jobs
   from their durable checkpoints and records an audit warning.
4. **PostgreSQL destination test was superficial:** the API now opens the configured database
   and executes `SELECT 1`.
5. **Gemini 503 escaped as an API failure:** mapping, cleaning, and validation assistance now
   fall back safely without exposing provider messages or secrets.
6. **Deterministic aliases were underweighted:** canonical aliases now outrank misleading fuzzy
   matches; regression coverage locks the corrected mapping.
7. **Streamlit API deprecation warnings:** full-width tables/charts now use the supported
   `width="stretch"` option.
8. **Slow repeated Docker builds:** enabled the BuildKit pip cache mount.

## Known limitations and unverified areas

- No real-browser visual-regression or responsive-layout test was available. Functional widget
  execution was tested headlessly, and the deployed web service was healthy.
- The PostgreSQL destination is intentionally the `destination_records` table in the same
  PostgreSQL transaction as migration metadata. A connection configured for another PostgreSQL
  server is connectivity-tested, but this portfolio implementation does not load into an
  arbitrary external table. A separately hosted target would need explicit table/schema mapping
  and an outbox or equivalent consistency design.
- Recovery is designed for one worker. Multiple simultaneous workers would require leases,
  heartbeat expiry, and ownership fencing before requeuing `running` jobs safely.
- Connection secrets are excluded from API responses and logs but are stored in configuration
  JSON without application-level encryption at rest.
- The demo CRM is intentionally in-memory; restarting that service clears ModernCRM records.
- The UI does not currently expose every historical feature mentioned in the original product
  brief (for example project-name editing and log-severity filtering). This acceptance pass did
  not add new product features or redesign the interface.

## Repository state

No Git commit or push was performed. All acceptance changes remain in the working directory for
review.
