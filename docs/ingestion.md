# Data Ingestion (Phase 3)

The ingestion layer accepts externally produced SOC datasets, validates and
normalizes them against canonical per-type schemas, and persists them
transactionally. It is the trusted boundary between untrusted uploads and the
database: nothing reaches a table until it has passed structural and semantic
validation, and every import is all-or-nothing.

Scope is deliberately narrow. Phase 3 is ingestion, validation, normalization,
and safe persistence only. It performs no analytics, detection, benchmarking,
scoring, or reporting.

## Architecture

The HTTP layer is thin; all logic lives in `app/ingestion`, split by concern:

| Module | Responsibility |
| --- | --- |
| `upload.py` | Extension allowlist, bounded in-memory read, safe filename handling |
| `parsing.py` | UTF-8 decode, CSV/JSON parsing into string-typed rows |
| `types.py` | Declarative schema primitives (`FieldSpec`, `ForeignKeySpec`, `TemporalRule`, `DatasetSchema`) |
| `schemas.py` | The canonical schema registry — one `DatasetSchema` per dataset type |
| `normalization.py` | Controlled per-field conversion to canonical typed values |
| `validation.py` | Format-neutral structural + semantic validation engine |
| `errors.py` | Error codes, severities, and the structured `ValidationReport` |
| `persistence.py` | Bulk insert / replace within a caller-owned transaction |
| `service.py` | Orchestration: `validate(...)` and `import_dataset(...)`, audit record |

CSV and JSON are parsed into the same intermediate representation (ordered
columns + rows of strings) and then run through one validation engine, so both
formats are proven to apply identical business rules.

## Supported formats

CSV and JSON only. No ZIP, Excel, Parquet, XML, YAML, binary, executable, or
remote-URL inputs are accepted. Uploaded content is never executed and is never
deserialized through unsafe mechanisms (no pickle).

- **CSV**: a header row naming columns, then one row per record. Parsed with
  pandas using `dtype=str`, `keep_default_na=False`, `na_filter=False` so pandas
  performs no type inference — every value arrives as a string and is converted
  only by the normalization layer.
- **JSON**: either a bare array of record objects, or an object of the form
  `{"records": [ ... ]}`. Record types are never inferred from JSON keys; the
  dataset type is always supplied explicitly by the client.

The declared format comes from the file extension (`.csv` / `.json`). The
`Content-Type` header is treated only as a UX hint and never drives a decision.
The actual bytes are then inspected: content that does not match the declared
format (for example a JSON body uploaded as `.csv`, or non-UTF-8 bytes) is
rejected with a `content_format_mismatch` error.

## Dataset types

The dataset type is an explicit, allowlisted parameter on every request. There
is no inference and no arbitrary table name is ever interpolated into SQL or a
filesystem path. The nine supported types correspond to the Phase 2 domain
model:

`entities`, `assets`, `alerts`, `investigations`, `investigation_actions`,
`escalations`, `remediations`, `telemetry`, `performance_metrics`.

`GET /api/v1/ingestion/dataset-types` returns the list with each type's required
and optional columns. (The Phase 2 `ground_truth` evaluation file is
intentionally **not** ingestible.)

One request imports one dataset type from one file into one table. Multi-table
loads are performed by importing each type in dependency order (parents first).

## Canonical schemas

Each dataset type has a single `DatasetSchema` (in `schemas.py`) that both
formats share. A schema declares, per field:

- the canonical type (`STRING`, `INT`, `FLOAT`, `BOOLEAN`, `DATETIME`, `ENUM`);
- whether it is required and whether it may be null;
- allowed enum values (sourced from the Phase 2 enums, the authoritative
  vocabulary);
- identifier format (e.g. `^ENT-\d+$`, `^ALR-\d+$`);
- numeric bounds (`min_value` / `max_value`);
- the primary key and any additional unique columns;
- foreign keys to other dataset types;
- temporal ordering rules (which timestamp must not precede which).

Identifier prefixes: `ENT`, `AST`, `ALR`, `INV`, `ACT`, `ESC`, `REM`, `TLM`,
`PMET`, `ANALYST`. Ratio metrics use the canonical `[0, 1]` range; `mttr_hours`
and count/duration fields are `>= 0`; `activity_level` is `>= 0` with no upper
bound.

## Validation

Validation is reported in two severity classes.

**Structural errors** (the dataset's shape is wrong — reported first, and when
present the dataset is rejected before per-row semantic checks run):

- unsupported format or dataset type
- malformed CSV / malformed JSON / invalid JSON structure
- content/format mismatch
- empty dataset
- missing required column / unknown column
- invalid field type

**Semantic errors** (shape is correct but values are invalid):

- missing required value
- invalid enum value
- invalid identifier format
- invalid or naive timestamp
- value out of range / negative value
- temporal order violation
- duplicate primary key or duplicate unique value
- missing foreign-key reference

Foreign-key and append-duplicate checks run against the current database state:
the referenced ID universe and the existing primary keys are loaded before
validation, so an `append` that would duplicate an existing row, or a child row
that references a non-existent parent, is caught.

## Normalization

Normalization is controlled and non-destructive — no silent coercion, no
guessing:

- strings are whitespace-trimmed; blank/absent optional values become a
  controlled null;
- enums are matched case-insensitively to the authoritative value set and
  stored in canonical casing;
- booleans are parsed only from an explicit allowlist
  (`true/1/yes/y/t` ↔ `false/0/no/n/f`);
- integers reject booleans and accept integral floats only; floats reject
  booleans, `NaN`, and infinities;
- timestamps are parsed from ISO-8601 (space or `T` separator, trailing `Z`
  accepted) and stored as timezone-aware UTC.

### Timestamp policy

Internally all timestamps are timezone-aware UTC. A timestamp with no offset is
**naive** and is **rejected by default** (`naive_timestamp`) rather than guessed.
A request may opt in with `assume_naive_utc=true`, which interprets naive
timestamps as UTC. Timestamps that are not parseable at all are rejected as
`invalid_timestamp`.

## Upload security controls

- **Extension allowlist** (`.csv`, `.json`) — not a denylist. Unsupported
  extensions are rejected with HTTP 415.
- **Content inspection** — the declared format is verified against the actual
  bytes; the client's `Content-Type` and filename are never trusted for
  decisions.
- **Bounded reads** — the upload is read in chunks and rejected with HTTP 413 as
  soon as it exceeds `SATSA_MAX_UPLOAD_BYTES` (default 25 MiB). Oversized files
  are never fully buffered and there is no silent truncation.
- **No temp files** — content is held in a bounded in-memory buffer; no uploaded
  bytes are written to disk, so there is no path traversal or cleanup surface.
  The filename is reduced to a safe basename and used only as response metadata.
- **No execution / no unsafe deserialization** of uploaded content.

## Import behavior and transactions

Two import modes, both explicit:

- `append` — add rows to the existing table; repeating an import that would
  reinsert existing primary keys is rejected (no accidental duplicates).
- `replace` — delete the existing rows for that dataset type, then insert the
  new set, within the same transaction.

Imports are **all-or-nothing**. The import endpoint always re-validates (it
never trusts a prior `validate` call); if validation fails nothing is written.
If a write fails despite passing validation — for example a database unique or
foreign-key constraint — the whole transaction is rolled back and the database
is left exactly as it was. SQLite foreign-key enforcement is enabled on every
connection, so the database is the final integrity backstop beneath the
application checks. Inserts use SQLAlchemy 2.x bulk statements with no raw SQL
string interpolation.

### Audit record

Every import attempt writes an `ImportRecord` (retrievable at
`GET /api/v1/ingestion/imports/{import_id}`) with a server-generated id, dataset
type, mode, status, received/completed timestamps, row/accepted/rejected/error
counts, and a safe summary. The audit record is committed in its own
transaction so it survives a rolled-back data import. Raw file contents,
absolute paths, and full invalid-row data are never stored.

## API

Base path: `/api/v1/ingestion`.

| Method & path | Purpose |
| --- | --- |
| `GET /dataset-types` | List supported dataset types and their columns |
| `POST /validate` | Validate an upload and return a structured report; no writes |
| `POST /import` | Re-validate and transactionally import an upload |
| `GET /imports/{import_id}` | Retrieve a prior import's audit record |

`POST /validate` and `POST /import` accept `multipart/form-data`:

- `file` — the CSV or JSON upload
- `dataset_type` — required, one of the allowlisted types
- `mode` — `append` (default) or `replace`
- `assume_naive_utc` — optional boolean (default `false`)

### HTTP status mapping

| Status | Meaning |
| --- | --- |
| 200 | Request processed. For `validate`, inspect `is_valid`; for a well-formed upload with invalid *content*, the report describes why |
| 400 | Malformed request / unsupported dataset type / invalid mode |
| 413 | Upload exceeds the configured size limit |
| 415 | Unsupported file extension |
| 422 | Import rejected because the dataset failed validation |
| 500 | Import rolled back by a database constraint after passing validation |

### Report format

Responses are machine-readable and safe. The report carries counts
(`row_count`, `accepted_count`, `rejected_count`, structural/semantic/total
error counts) and a capped `errors` list. Each error has a `row`, `field`,
`code`, and a concise human message. The detail list is capped at
`SATSA_MAX_REPORTED_ERRORS` (default 100) while `total_error_count` still
reports the true total. Responses never contain full invalid-row contents,
stack traces, filesystem paths, the database URL, or any credential.

Example `validate` response (invalid enum value):

```json
{
  "dataset_type": "entities",
  "detected_format": "json",
  "is_valid": false,
  "row_count": 1,
  "accepted_count": 0,
  "rejected_count": 1,
  "structural_error_count": 0,
  "semantic_error_count": 1,
  "total_error_count": 1,
  "returned_error_count": 1,
  "errors": [
    {"row": 1, "field": "sector", "code": "invalid_enum_value",
     "severity": "SEMANTIC", "message": "value is not an allowed option"}
  ],
  "filename": "entities.json"
}
```

Example successful `import` response:

```json
{
  "import_id": "IMP-0f1e2d3c4b5a6978",
  "dataset_type": "entities",
  "mode": "REPLACE",
  "status": "COMPLETED",
  "committed": true,
  "row_count": 6,
  "accepted_count": 6,
  "rejected_count": 0,
  "error_count": 0,
  "returned_error_count": 0,
  "summary": "Imported 6 entities row(s)."
}
```

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `SATSA_MAX_UPLOAD_BYTES` | `26214400` (25 MiB) | Reject larger uploads with 413 |
| `SATSA_MAX_REPORTED_ERRORS` | `100` | Cap on echoed error detail (total still reported) |

## Local examples

See [development.md](development.md#data-ingestion) for runnable `curl`
examples and the ingestion test commands.
