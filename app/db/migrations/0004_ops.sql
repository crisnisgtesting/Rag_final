-- Operational tables: settings, durable ingestion jobs and request logs.

CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      JSONB NOT NULL,
    updated_by UUID REFERENCES users(id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ingestion_jobs (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_version_id      UUID NOT NULL
                             REFERENCES document_versions(id) ON DELETE CASCADE,
    job_type                 TEXT NOT NULL,
    status                   TEXT NOT NULL DEFAULT 'queued'
                             CHECK (status IN (
                                 'queued', 'running', 'completed', 'failed',
                                 'cancelled', 'paused'
                             )),
    total_pages              INTEGER NOT NULL DEFAULT 0,
    completed_pages          INTEGER NOT NULL DEFAULT 0,
    failed_pages             INTEGER NOT NULL DEFAULT 0,
    selected_vision_provider TEXT NOT NULL,
    selected_vision_model    TEXT NOT NULL,
    progress                 NUMERIC(5, 2) NOT NULL DEFAULT 0
                             CHECK (progress BETWEEN 0 AND 100),
    error_details            JSONB,
    started_at               TIMESTAMPTZ,
    completed_at             TIMESTAMPTZ,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_ingestion_jobs_status
    ON ingestion_jobs(status, created_at);
CREATE INDEX IF NOT EXISTS ix_ingestion_jobs_version
    ON ingestion_jobs(document_version_id);

DROP TRIGGER IF EXISTS trg_ingestion_jobs_updated_at ON ingestion_jobs;
CREATE TRIGGER trg_ingestion_jobs_updated_at
    BEFORE UPDATE ON ingestion_jobs
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS job_checkpoints (
    id             BIGSERIAL PRIMARY KEY,
    job_id         UUID NOT NULL REFERENCES ingestion_jobs(id) ON DELETE CASCADE,
    checkpoint_key TEXT NOT NULL,
    checkpoint_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (job_id, checkpoint_key)
);

CREATE TABLE IF NOT EXISTS request_logs (
    id          BIGSERIAL PRIMARY KEY,
    request_id  TEXT NOT NULL,
    user_id     UUID REFERENCES users(id) ON DELETE SET NULL,
    method      TEXT NOT NULL,
    endpoint    TEXT NOT NULL,
    status_code INTEGER,
    duration_ms INTEGER,
    error       TEXT,
    client_ip   TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_request_logs_created_at
    ON request_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS ix_request_logs_request_id
    ON request_logs(request_id);
