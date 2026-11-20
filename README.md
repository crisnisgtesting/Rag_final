# FMM Knowledge and Operational Assistant (CRIS)

Internal, multilingual technical assistant for the FMM machine. The system is
Databricks-native: Delta tables are the source of truth for documents, extracted
knowledge, settings, users and audit history.

**Current status: Phases 1 to 6 implemented.** The system ingests PDF, DOCX and
Markdown sources, organizes the extracted content into reviewable knowledge
(workflows, steps, rules, entities, visual references), answers questions with
grounded citations, records feedback and unanswered questions, accepts voice
questions, runs evaluation sets and ships with a deployment runbook. The
19-document corpus (15 PDFs plus 4 Markdown sources) is ingested, approved and
searchable. See the phase implementation logs at the end of this file.

## Stack

- Python 3.11, FastAPI, Pydantic v2, Databricks SQL Connector
- Databricks SQL, Delta tables, AI Search, UC Volumes, Model Serving
- Streamlit frontend
- PyMuPDF for PDF extraction and page rendering
- BAAI/bge-m3 through ONNX Runtime (the managed machine blocks PyTorch)
- Vision providers: Qwen3-VL, Gemini, Databricks Model Serving
- Databricks Model Serving chat with strict source grounding and citations
- faster-whisper for local voice transcription (optional)

No Docker. No Redis. No LangChain.

## Requirements

- Python 3.11 (3.11.x only)
- Databricks workspace with SQL warehouse, Unity Catalog and UC Volume access.
- Service principal OAuth credentials with SQL, Model Serving, Unity Catalog,
  Jobs and AI Search scopes. PATs and PostgreSQL are not used by the runtime.

## Quick start

### Windows (PowerShell)

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe scripts\dev_db.py start
.\.venv\Scripts\python.exe -m app.db.migrate
Copy-Item config\.env.example config\.env   # if config\.env does not exist
```

Edit `config/.env` and set at least `GEMINI_API_KEY` and
`GEMINI_VISION_MODEL` when using Gemini, or the Qwen/Databricks values for the
other providers. Generate a JWT secret with:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Install the Phase 2 ML dependencies and download the BGE-M3 ONNX model once:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-ml.txt
$env:HF_HUB_DISABLE_XET='1'
.\.venv\Scripts\python.exe scripts\download_models.py --backend onnx
```

Create an administrator, then start the API and UI in **separate terminals**
(the API must stay running while the UI is used):

```powershell
# Terminal 1: database (leave running if not already started)
.\.venv\Scripts\python.exe scripts\dev_db.py start
# Terminal 2: API (leave running)
.\.venv\Scripts\python.exe -m app
# Terminal 3: UI (leave running)
.\.venv\Scripts\python.exe -m streamlit run frontend\streamlit_app.py
```

Create the first administrator once, with the password entered privately:

```powershell
.\.venv\Scripts\python.exe scripts\create_admin.py --username admin --email you@example.com
```

API: http://127.0.0.1:8000 (docs at `/docs` in non-production).
UI: http://127.0.0.1:8501.

`scripts/dev_setup.ps1` automates dependency installation, database startup,
migrations and validation.

### Linux

```bash
./scripts/install_dependencies.sh --dev
venv/bin/python scripts/dev_db.py start
venv/bin/python -m app.db.migrate
cp config/.env.example config/.env
venv/bin/python scripts/create_admin.py --username admin
./scripts/start_api.sh
./scripts/start_ui.sh
```

On production Databricks Apps or Linux hosts, configure
`DATABRICKS_SQL_HTTP_PATH` and the OAuth credentials below; there is no
PostgreSQL server to start.

## Configuration

All configuration is environment based and read from `config/.env` (never
committed). See `config/.env.example` for every variable. Secrets are held in
memory and are never persisted to application storage, returned by the API or
written to logs.

Databricks SQL, Model Serving and AI Search use Service Principal OAuth, not
personal access tokens. Set `DATABRICKS_HOST`, `DATABRICKS_CLIENT_ID`,
`DATABRICKS_SQL_CLIENT_SECRET`, `DATABRICKS_SQL_HTTP_PATH`,
`DATABRICKS_SQL_OAUTH_SCOPE=sql`, `DATABRICKS_OAUTH_SCOPE=model-serving`,
`DATABRICKS_MODEL_SERVING_ENDPOINT`, `DATABRICKS_VECTOR_SEARCH_ENDPOINT` and
`DATABRICKS_AI_SEARCH_OAUTH_SCOPE=ai-search`. The AI Search index remains blank
until the endpoint type and index are verified; local BGE-M3 cosine search stays
active while unavailable.

Vision provider selection:

- `VISION_PROVIDER` sets the environment default (`databricks`, `qwen` or
  `gemini`).
- Administrators can override it at runtime in the UI; the override is stored
  in `app_settings` and applies to newly scheduled work.
- Selection is explicit. There is no automatic fallback between providers or
  models.
- The provider and model used for each processing run are recorded with the
  run. Admins and the ingestion CLI can override the model per job.
- Keep `VISION_MAX_OUTPUT_TOKENS` at `8192`: Gemini's structured page output
  includes reasoning tokens, and `4096` truncates large responses into invalid
  JSON. `gemini-4-argon` is a working fallback for pages that Gemini 3.x refuses
  with `finishReason=RECITATION`; set it explicitly for that job.

Databricks endpoints must use the verified `openai_chat` content-parts payload
style.

`CHAT_PROVIDER` defaults to `databricks`; `DATABRICKS_MODEL_SERVING_ENDPOINT`
sets the model name reported by API responses and logs. Keep
`DATABRICKS_MODEL_SUPPORTS_PARAMETERS=false` until the endpoint's request schema
is verified.

## Database

Migrations are plain SQL files in `app/db/migrations` and are tracked in
`schema_migrations` with SHA-256 checksums. Applied migrations must never be
edited.

```bash
python -m app.db.migrate            # apply pending migrations
python -m app.db.migrate --status   # show applied/pending
```

Tables: users, roles, user_roles, api_tokens, audit_log, documents,
document_versions, document_pages, app_settings, ingestion_jobs,
job_checkpoints, vision_runs, document_chunks, chat_sessions, chat_messages,
request_logs, knowledge_entities, knowledge_relationships, workflows,
workflow_steps, visual_references, knowledge_rules, knowledge_sources,
knowledge_conflicts, page_markdown, chat_responses, response_sources, feedback,
unanswered_questions, vision_evaluations, eval_set and eval_results.

## Ingestion workflow

1. Put the official PDFs, DOCX or Markdown files in `data/documents/` or upload
   them from the Documents page.
2. Register them with **Scan inbox** (admin) or `POST /documents/scan`.
3. Start ingestion. Selective vision means only image-heavy or low-text pages
   are sent to the configured vision provider; use `--vision-mode all` only when
   every page needs analysis. Vision analysis only runs for PDF pages.
4. Review extracted pages and chunks, then approve the version. Until approval,
   the version is draft-only and cannot be retrieved by search or chat.

Markdown and DOCX sources are structured: each heading becomes a logical page,
procedural bullets become steps, and the heading path is preserved as metadata.

Re-ingesting a revised document reuses the previous page analyses for pages
whose extracted text is unchanged, so only changed pages are re-analyzed.

Page images are rendered at `INGESTION_RENDER_DPI` (default 200, supported
range 200-300). When the DPI changes, regenerate the stored images without
repeating the provider calls:

```powershell
.\.venv\Scripts\python.exe scripts\rerender_pages.py --all --dpi 200
```

Scanned pages have no digital text, so the vision provider is the primary
understanding path. Optional OCR supplements it: install `paddleocr` and set
`INGESTION_OCR_ENABLED=true` to recover machine-readable text for those pages.
Without an OCR engine the pages keep their vision-only state and ingestion
continues.

A single document can also be ingested from the command line:

```powershell
.\.venv\Scripts\python.exe scripts\ingest_document.py --file "data/documents/IRWMS.pdf"
.\.venv\Scripts\python.exe scripts\ingest_document.py --file "data/documents/IRWMS.pdf" --approve
.\.venv\Scripts\python.exe scripts\ingest_document.py --file "data/documents/guide.md" --approve
```

Knowledge organization is rebuilt from page analyses with:

```powershell
.\.venv\Scripts\python.exe scripts\build_knowledge.py --approve
```

## API

- `GET /health/live` - process liveness
- `GET /health/ready` - Databricks SQL readiness (HTTP 503 when not ready)
- `POST /auth/login` - JWT access token
- `GET /auth/me` - current user
- `GET /admin/vision/providers` - provider configuration (no secrets)
- `PUT /admin/settings/vision-provider` - select provider (admin)
- `POST /admin/vision/test` - provider connectivity test (admin)
- `GET /admin/audit-log` - recent audit events (admin)
- `GET /admin/operations/health` - counters, recent jobs and voice status (admin)
- `GET /admin/evals`, `POST /admin/evals`, `POST /admin/evals/run`,
  `GET /admin/evals/results` - evaluation sets and results (admin)
- `POST /admin/vision/evaluations` - record a vision quality evaluation (admin)
- `GET /documents`, `POST /documents/scan`, `POST /documents/upload` - registry
- `GET /documents/{id}`, `/documents/versions/{id}/pages`, `.../chunks` - detail
- `POST /documents/versions/{id}/ingest` - queue ingestion (admin)
- `POST /documents/versions/{id}/approve`, `.../reject` - approval (admin)
- `GET /jobs`, `GET /jobs/{id}` - job inspection
- `POST /jobs/{id}/cancel`, `.../resume` - job control (admin)
- `POST /search` - hybrid retrieval over approved versions, with optional
  `document_ids`, `document_types` and `content_types` filters
- `GET /knowledge/summary`, `/knowledge/workflows`, `/knowledge/workflows/{id}`,
  `/knowledge/workflows/{id}/export`, `POST /knowledge/workflows/{id}/status`,
  `POST /knowledge/versions/{id}/rebuild`, `POST /knowledge/versions/{id}/approve`,
  `GET /knowledge/conflicts`, `POST /knowledge/conflicts/{id}/resolve`
- `POST /chat` - grounded answer with citations
- `POST /chat/stream` - Server-Sent Events (`meta`, `token`, `citations`, `done`)
- `POST /chat/responses/{id}/feedback` - answer feedback
- `GET /chat/unanswered`, `POST /chat/unanswered/{id}/resolve` (admin)
- `POST /chat/transcribe` - microphone dictation transcription
- `GET /chat/voice/capability` - whether local transcription is available
- `GET /chat/sessions`, `GET /chat/sessions/{id}` - conversation history

All errors use a stable envelope with a request identifier.

## Tests

```bash
python -m pytest              # unit + integration (integration skips without a DB)
python -m pytest tests/unit -q
ruff check .
```

Integration tests expect the development database from `scripts/dev_db.py`.
Override with `TEST_DATABASE_URL`.

## Platform notes

- On Windows, psycopg async requires the selector event loop. Always start the
  API with `python -m app`; direct `uvicorn app.main:app` will fail on Windows
  because uvicorn creates the event loop before importing the application.
- Minimal PostgreSQL builds may not ship `pg_trgm` and `unaccent`. The
  development migration tolerates their absence; production must install
  contrib.
- Windows Application Control policies on some managed machines block the
  compiled `pyarrow` DLL, `mypy` and PyTorch's `c10.dll`. Streamlit's
  `st.dataframe`, `st.table` and `st.data_editor` all require `pyarrow`. The UI
  therefore renders static tables with `frontend.ui.render_table` (Markdown) and
  uses `st.json` for structured data. `tests/unit/test_frontend_constraints.py`
  fails if an Arrow-backed element is reintroduced. `mypy` remains unavailable
  on such machines; `ruff` and `pytest` are the supported checks.
- Because PyTorch is blocked, BGE-M3 runs through its official ONNX export using
  `onnxruntime`. `requirements-ml.txt` intentionally excludes
  `sentence-transformers` and `torch`; download the ONNX files with
  `scripts/download_models.py --backend onnx`.
- The UI no longer emits “No secrets found”. `frontend.ui.api_base_url` reads
  `API_BASE_URL` first, only accesses `st.secrets` when a `secrets.toml` file
  actually exists, and otherwise falls back to `http://127.0.0.1:8000`.

## Repository layout

```
app/            FastAPI application, auth, services, vision adapters, DB
config/         .env.example (committed) and .env (local only)
data/           documents, rendered pages, temporary work
deploy/         deployment runbook and systemd units
frontend/       Streamlit application and pages
models/         local model weights (later phases)
scripts/        database, migrations, admin, startup and validation helpers
tests/          unit and integration tests
```

## Phase 1 implementation log

Last updated: 2026-11-12. Phase 1 is complete; document ingestion, retrieval,
chat and voice remain out of scope until Phase 2.

### Delivered components

- Application: FastAPI app factory with lifespan startup/shutdown, request ID
  middleware (incoming IDs sanitized), security headers, request body-size
  limits, request logging and sanitized exception envelopes.
- Authentication and access control: Argon2 password hashing, JWT access
  tokens, timing-equalized login for unknown users, in-process login rate
  limiting, current-user endpoint, role-based access and administrator checks.
- Database: psycopg 3 async pool, SQL migration runner with SHA-256 checksums,
  and migrations `0001`-`0004` covering extensions, core tables, request logs,
  indexes and the Phase 1 settings/audit schema. JSONB writes use psycopg
  `Jsonb` wrapping.
- Settings and audit: database-backed vision-provider override applied
  atomically with its audit event; recent audit events exposed to admins.
- Vision layer: provider schemas and page-result validation, prompt templates,
  image preparation, retrying HTTP client, Qwen, Gemini, Databricks and fake
  adapters, a provider factory, and secret masking in provider summaries.
  Provider selection is explicit with no automatic fallback, and the
  provider/model are recorded for each run.
- API endpoints: `/health/live`, `/health/ready`, `/health`, `/auth/login`,
  `/auth/me`, `/admin/vision/providers`, `/admin/settings/vision-provider`,
  `/admin/vision/test` and `/admin/audit-log`.
- Streamlit UI: login and session handling, sidebar account state, Phase 1 home
  page, administrator vision-provider page (selection, connectivity test,
  Markdown provider table and audit log) and system-status page.
- Operations: database start/stop helpers, environment validation, migration,
  admin bootstrap, API/UI/health-check, backup and restore scripts, plus
  `scripts/dev_setup.ps1`.
- Tests: 49 tests covering unit behaviour and database-backed integration flows.
  A frontend guard test prevents reintroduction of Arrow-backed Streamlit
  tables.

### Fixes applied during Phase 1

- Windows: installed the selector event loop policy and added `app/__main__.py`;
  always run the API with `python -m app`.
- Configuration: `CORS_ORIGINS` uses `NoDecode` so comma-separated env values
  parse correctly.
- Database: wrapped JSONB parameters; scripts and tests use `dict_row`; the
  baseline migration tolerates missing `pg_trgm`/`unaccent` in minimal builds.
- Authentication: added a dummy Argon2 verification path so unknown usernames do
  not have a timing advantage.
- Settings: provider update and audit insert share one transaction.
- Secrets: provider summaries mask credentials; no secrets are returned, logged
  or stored in PostgreSQL.
- Streamlit warnings: `api_base_url` now checks for a real `secrets.toml` before
  touching `st.secrets`, removing the “No secrets found” message.
- Blocked DLL workaround: managed Windows Application Control blocks the
  `pyarrow` DLL. The UI now uses Markdown tables and `st.json` instead of
  `st.dataframe`/`st.table`, and `tests/unit/test_frontend_constraints.py`
  enforces this.

### Verification snapshot (2026-11-12)

- `ruff check .`: clean.
- `python -m pytest -q`: 49 passed, 1 third-party `anyio` deprecation warning.
- API `/health/ready`: HTTP 200, `status=ready`, PostgreSQL 16.2,
  pgvector 0.6.2.
- Streamlit home page: no exceptions in Streamlit `AppTest`.
- Admin Vision page: no exceptions in `AppTest`; both Markdown tables and both
  expanders rendered without importing `pyarrow`.
- Local administrator bootstrap: one administrator exists; credentials are not
  recorded in this repository.

### Outstanding environment configuration (not code)

- Fill `GEMINI_API_KEY` / `GEMINI_VISION_MODEL` or the Qwen/Databricks values in
  `config/.env` before testing a real provider.
- Place the official FMM manual PDF in `data/documents/` for Phase 2 ingestion.
- Production PostgreSQL must include contrib extensions (`pg_trgm`, `unaccent`).

## Phase 2 implementation log

Last updated: 2026-11-13. Phase 2 is complete; the full corpus has been
ingested, approved and validated with live Gemini vision, retrieval and chat.

### Delivered components

- Database: migration `0005_phase2.sql` applied with page-analysis columns,
  ingestion job state, `vision_runs`, `document_chunks` (pgvector HNSW plus
  generated `tsv`) and chat session/message tables.
- Ingestion: PDF extraction and rendering with PyMuPDF, selective-vision
  classification, structured chunking, duplicate detection by SHA-256, draft
  registration, job creation, advisory-lock runner, resumable checkpoints,
  cancellation, failure persistence, page/analysis/vision-run storage and
  batched embeddings. Markdown and DOCX sources use the same pipeline with
  structured extractors instead of page rendering.
- Retrieval: BGE-M3 dense embeddings through ONNX Runtime plus PostgreSQL
  full-text lexical retrieval, fused with reciprocal rank fusion. Filters
  restrict results to approved, active versions; optional document filters are
  supported.
- Chat: provider abstraction for Gemini and Qwen, grounded system prompt,
  numbered source context, citation objects with page/section metadata, session
  history and persisted messages.
- API: document registry, scan/upload, version pages/chunks, ingest,
  approve/reject, job list/detail/cancel/resume, search and chat endpoints.
- UI: Documents page (scan/upload, ingest, job control, approval, pages/chunks)
  and Chat page (sessions, grounded answers, citation tables), plus updated home
  and sidebar for Phase 2.
- Operations: `scripts/download_models.py` (ONNX mode) and
  `scripts/ingest_document.py` for single-document ingestion.
- Tests: fake-provider ingestion end-to-end test and a new hybrid-search plus
  grounded-chat integration test.

### Later additions

- Page rendering uses 200 DPI by default (supported range 200-300).
  `scripts/rerender_pages.py` regenerates stored page images at the current
  DPI and refreshes the page image hashes without repeating provider calls.
  The whole PDF corpus was re-rendered at 200 DPI on 2026-11-13.
- Incremental re-analysis: a new version reuses the previous page analyses for
  pages whose extracted text hash is unchanged, so only changed pages are sent
  to the vision provider.
- Optional OCR for scanned pages (`app/ingestion/ocr.py`). It supplements the
  vision analysis, is disabled by default and reports a clear configuration
  error when no engine is installed.

### Full-corpus result

- 17 PDF files, 15 unique documents, 2 exact duplicate pairs skipped by SHA-256.
- 109 pages, 78 with digital text, 100 with images, 25 scanned.
- All 15 documents are `active`, their sole versions are `ready` and
  `approved`, with 0 failed pages.
- 108 chunks and 104 completed vision runs across the corpus, including
  re-ingestion of pages whose first analysis needed a larger token budget or a
  different model.
- Duplicate pairs: `CAModule_FailureCauseMaster (1).pdf` and
  `Hot_Axle_train_Parting_Manual (1).pdf` match their non-suffixed copies; the
  sorted scan keeps the `(1)` copies as canonical and skips the others.

### Fixes applied during Phase 2

- PyMuPDF is imported as `pymupdf` to remove the deprecated `fitz` warning.
- Vision mode is configurable per job (`auto`, `all`, `none`) rather than fixed
  globally.
- Ingestion job runner accepts string or UUID job IDs and uses explicit
  rollback/commit handling so failed pages do not leave an aborted transaction.
- ONNX embedding was adopted after the managed Application Control policy
  blocked PyTorch's `c10.dll`; `requirements-ml.txt` no longer requires torch.
- `VISION_MAX_OUTPUT_TOKENS` defaults to `8192`; `4096` truncated a large
  structured page response and produced invalid JSON.
- Vector-only or blank pages with no extractable text are now classified for
  vision instead of being silently skipped.
- The vision provider factory now honors the model selected on the ingestion
  job; previously the environment default was always used.
- `approve_version` is idempotent: re-approving an already-approved version
  restores the document to `active` and points it at that version.
- `scripts/ingest_document.py` skips exact duplicate content instead of
  scheduling another ingestion job on the existing version.
- `scripts/ingest_document.py` sets the Windows selector event loop policy and
  initializes the database pool before registering a document.

### Verification snapshot (2026-11-13)

- BGE-M3 ONNX model loaded with `onnxruntime`; produced normalized
  1024-dimensional vectors.
- Full corpus ingested and approved: 15/15 documents `active`, 15/15 versions
  `ready` and `approved`, 108 chunks, 0 failed pages.
- Live Gemini vision validated on the vision-only `CAModule_DepotInfraDetails`
  page (2050 input / 1469 output tokens) and across the corpus.
- `CAModule_AddWagonToMaster` passed after raising the output-token limit to
  8192; `FMM_Introduction` passed with `gemini-4-argon` after 3.x models returned
  `RECITATION`.
- Real hybrid retrieval returned the expected sources for add-wagon, hot-axle
  parting, depot-infrastructure and IRWMS wagon-numbering queries.
- Live grounded Gemini chat answered the add-wagon question with page-level
  citations (`gemini-3.8-flash`, 8 retrieved chunks, 8 citations).
- Fake-provider ingestion and retrieval/chat integration tests pass, including
  approval gating, duplicate-safe ingest, citation persistence and
  idempotent approval.
- `ruff check app frontend scripts tests`: clean.
- `python -m pytest -q`: 51 passed, 1 third-party `anyio` deprecation warning.

### Operational notes

- The configured `GEMINI_API_KEY` is valid; use
  `Settings.gemini_api_key.get_secret_value()` when building headers. Calling
  `str()` on the `SecretStr` returns a mask and causes a misleading HTTP 401.
- The provider/model choice is explicit. `gemini-2.5-flash` is unavailable to
  new users, and some FMM pages require `gemini-4-argon` because 3.x models
  return `finishReason=RECITATION`.
- Production PostgreSQL must include contrib extensions if future lexical
  ranking needs `pg_trgm` or `unaccent`.

## Phase 3 implementation log
Last updated: 2026-11-13. Phase 3 delivers normalized, reviewable knowledge
organization in the database. Source pages, chunks and vision analyses remain
the evidence of record.

### Delivered components

- Migration `0006_phase3_knowledge.sql`: `knowledge_entities`,
  `knowledge_relationships`, `workflows`, `workflow_steps`,
  `visual_references`, `knowledge_rules`, `knowledge_sources`,
  `knowledge_conflicts` and `page_markdown`, with indexes and updated-at
  triggers.
- `app/knowledge/extractor.py`: deterministic normalization. Entities come
  from vision `domain_knowledge`, relationships from co-occurrence on the same
  page, workflows from page groups, steps from vision `workflow_steps` or
  structured text steps, rules from `business_rules`, `prerequisites`,
  `validations`, `exceptions` and `warnings`, and visuals from
  `visual_elements` linked to the matching step callout.
- `app/knowledge/service.py`: summary counters, workflow listing and detail,
  status changes with audit trail, version approval, conflict resolution,
  Markdown export and manual overrides.
- `app/api/routers/knowledge.py`: summary, workflows, workflow detail and
  export, status, rebuild, approve and conflict endpoints.
- `scripts/build_knowledge.py`: rebuilds knowledge for every ready version and
  can approve it in the same run.

### Corpus result

- 149 entities, 152 relationships, 85 workflows, 287 steps, 687 visual
  references, 181 rules, 545 knowledge sources and 101 page Markdown records
  across the 19 approved versions.
- 19 conflicts detected and left open for reviewer decision. No conflicts are
  auto-resolved.

## Phase 4 implementation log

Last updated: 2026-11-13. Phase 4 makes retrieval knowledge-aware and
filterable.

### Delivered components

- `app/retrieval/knowledge.py`: matches query tokens against approved
  entities, workflows and rules, and maps the matched knowledge to the chunks
  that back it through `knowledge_sources.document_chunk_id`.
- `app/retrieval/search.py`: knowledge-aware boosting of the fused hybrid
  ranking, plus optional cross-encoder reranking. Filters for document ids,
  document types and content types are applied to both the dense and the
  lexical leg.
- `app/retrieval/reranker.py`: opt-in ONNX cross-encoder reranker. It is
  disabled by default, and a missing or failing model leaves the hybrid
  ranking unchanged rather than silently degrading results.

### Verification snapshot (2026-11-13)

- Knowledge boost verified live: "attendance group marking" promotes the
  approved `Procedure for Updating Attendance Groups` workflow chunk; "hot axle
  parting precautions" promotes the Hot Axle parting workflow and entity
  chunks.
- 545 of 545 knowledge sources are linked to their source chunk.
- Live Gemini chat answered an employee-transfer question with 8 chunks, 8
  citations and `citation_status=valid`.

## Phase 5 implementation log

Last updated: 2026-11-13. Phase 5 delivers grounded RAG with guardrails,
streaming and feedback.

### Delivered components

- Migration `0007_phase5_chat_rag.sql`: `chat_responses`, `response_sources`,
  `feedback` and `unanswered_questions`, plus language, summary and archive
  columns on `chat_sessions`.
- `app/chat/service.py`: query condensation from history, language detection,
  citation validation, abstention handling, durable answer and source
  persistence, unanswered-question tracking, feedback and a Server-Sent Events
  generator. The answer is stored before the stream is replayed so a dropped
  client never leaves a partial citation record.
- `app/api/routers/chat.py`: `/chat/stream`, response feedback, unanswered
  question administration and `/chat/transcribe`.

### Guardrails

- The answer is generated only from retrieved approved chunks.
- Citations are validated against the retrieved set; answers that cite
  nothing are recorded as `invalid` and raised as unanswered questions.
- When no chunk matches, the assistant says so instead of answering from
  general knowledge.

## Phase 6 implementation log

Last updated: 2026-11-13. Phase 6 delivers voice input, quality operations,
evaluation and deployment.

### Delivered components

- Migration `0008_phase6_voice_eval.sql`: `vision_evaluations`, `eval_set` and
  `eval_results`.
- `app/voice/transcribe.py`: optional faster-whisper transcription with size
  limits and an explicit configuration error when the model is unavailable.
- `app/evaluation/service.py`: evaluation sets, evaluation runs scoring
  keyword recall, expected-document match and citation validity.
- `app/api/routers/admin.py`: evaluation endpoints, vision quality recording
  and an operations health payload.
- Frontend: Knowledge page, Quality and operations page, Search page, chat
  streaming, feedback buttons and microphone dictation.
- `deploy/DEPLOYMENT.md` and `deploy/systemd/*.service`: installation,
  configuration, migrations, service units, reverse proxy configuration
  (including `proxy_buffering off` for the stream endpoint), backup, restore,
  monitoring, upgrade and rollback.

### Microphone dictation

The Chat page records the prompt straight from the browser microphone with
`st.audio_input`: press the microphone, speak the question and press stop.
The recording is transcribed by the local faster-whisper model and the
recognized text is placed in the question box, where it can be edited before
asking. A new recording replaces the previous transcript unless the text was
edited, in which case the speech is appended.

- `GET /chat/voice/capability` reports whether transcription is available;
  the Chat and System Status pages use it to enable or disable the microphone.
- `POST /chat/transcribe` accepts the recording and returns the text,
  detected language, segments and latency.
- Transcription runs locally on the CPU, so no audio leaves the host. The
  `small` model takes roughly 4 seconds for a short question on CPU.
- Install the dependency with
  `.\.venv\Scripts\python.exe -m pip install -r requirements-ml.txt`; the
  model is downloaded on first use.

### Verification snapshot (2026-11-13)

- `ruff check app frontend scripts tests`: clean.
- `python -m pytest -q`: 91 passed, 1 third-party `anyio` deprecation warning.
- All eight Streamlit pages render without exceptions in `AppTest`.
- Live dictation check: a spoken question recorded from the microphone was
  transcribed as "How do I mark daily attendance for my group?" and the
  resulting chat answer cited `update_attendance_groups.md` with
  `citation_status=valid`.

### Outstanding environment configuration (not code)

- The 500-page PDF is not uploaded yet. Its manual vision review, pages 45-54
  evaluation and corpus benchmarks start when it is supplied.
- The optional cross-encoder reranker needs its ONNX export under `MODEL_DIR`
  before `RETRIEVAL_RERANKER_ENABLED=1` can be used.
- `faster-whisper` voice transcription needs `requirements-ml.txt` installed on
  the target host. It is installed in the development environment.

