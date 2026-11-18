-- Phase 2: document ingestion, page analysis, chunks, retrieval and chat.

ALTER TABLE document_versions
    ADD COLUMN IF NOT EXISTS page_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS chunk_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS indexed_at TIMESTAMPTZ;

ALTER TABLE document_pages
    ADD COLUMN IF NOT EXISTS text_source TEXT NOT NULL DEFAULT 'unknown',
    ADD COLUMN IF NOT EXISTS text_char_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS image_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS image_coverage NUMERIC(7, 5) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS width_pt NUMERIC(10, 2),
    ADD COLUMN IF NOT EXISTS height_pt NUMERIC(10, 2),
    ADD COLUMN IF NOT EXISTS render_dpi INTEGER,
    ADD COLUMN IF NOT EXISTS layout JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS analysis JSONB,
    ADD COLUMN IF NOT EXISTS analysis_provider TEXT,
    ADD COLUMN IF NOT EXISTS analysis_model TEXT,
    ADD COLUMN IF NOT EXISTS analysis_latency_ms INTEGER,
    ADD COLUMN IF NOT EXISTS analysis_usage JSONB,
    ADD COLUMN IF NOT EXISTS analyzed_at TIMESTAMPTZ;

ALTER TABLE ingestion_jobs
    ADD COLUMN IF NOT EXISTS stage TEXT NOT NULL DEFAULT 'queued',
    ADD COLUMN IF NOT EXISTS config JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS stats JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS resume_count INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS ix_ingestion_jobs_queue
    ON ingestion_jobs(status, created_at);
CREATE INDEX IF NOT EXISTS ix_ingestion_jobs_heartbeat
    ON ingestion_jobs(status, heartbeat_at);

CREATE TABLE IF NOT EXISTS vision_runs (
    id                BIGSERIAL PRIMARY KEY,
    job_id            UUID REFERENCES ingestion_jobs(id) ON DELETE CASCADE,
    document_page_id  UUID REFERENCES document_pages(id) ON DELETE CASCADE,
    provider          TEXT NOT NULL,
    model             TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'completed'
                      CHECK (status IN ('completed', 'failed', 'skipped')),
    latency_ms        INTEGER,
    usage             JSONB NOT NULL DEFAULT '{}'::jsonb,
    error             TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_vision_runs_job ON vision_runs(job_id);
CREATE INDEX IF NOT EXISTS ix_vision_runs_page ON vision_runs(document_page_id);

CREATE TABLE IF NOT EXISTS document_chunks (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_version_id UUID NOT NULL
                        REFERENCES document_versions(id) ON DELETE CASCADE,
    document_id         UUID NOT NULL
                        REFERENCES documents(id) ON DELETE CASCADE,
    page_start          INTEGER NOT NULL CHECK (page_start > 0),
    page_end            INTEGER NOT NULL CHECK (page_end >= page_start),
    chunk_index         INTEGER NOT NULL CHECK (chunk_index >= 0),
    section_number      TEXT,
    heading             TEXT,
    content_type        TEXT NOT NULL DEFAULT 'unknown',
    content             TEXT NOT NULL,
    content_hash        TEXT NOT NULL,
    char_count          INTEGER NOT NULL,
    token_estimate      INTEGER NOT NULL,
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    embedding           vector(1024),
    embedding_model     TEXT,
    embedding_dim       INTEGER,
    embedding_updated_at TIMESTAMPTZ,
    tsv                 tsvector GENERATED ALWAYS AS (
                            to_tsvector(
                                'simple',
                                coalesce(heading, '') || ' ' ||
                                coalesce(section_number, '') || ' ' ||
                                coalesce(content, '')
                            )
                        ) STORED,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_version_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS ix_document_chunks_version
    ON document_chunks(document_version_id, chunk_index);
CREATE INDEX IF NOT EXISTS ix_document_chunks_document
    ON document_chunks(document_id);
CREATE INDEX IF NOT EXISTS ix_document_chunks_hash
    ON document_chunks(content_hash);
CREATE INDEX IF NOT EXISTS ix_document_chunks_tsv
    ON document_chunks USING GIN (tsv);
CREATE INDEX IF NOT EXISTS ix_document_chunks_embedding
    ON document_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE TABLE IF NOT EXISTS chat_sessions (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title      TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_chat_sessions_user
    ON chat_sessions(user_id, updated_at DESC);

DROP TRIGGER IF EXISTS trg_chat_sessions_updated_at ON chat_sessions;
CREATE TRIGGER trg_chat_sessions_updated_at
    BEFORE UPDATE ON chat_sessions
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS chat_messages (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id   UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role         TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content      TEXT NOT NULL,
    citations    JSONB NOT NULL DEFAULT '[]'::jsonb,
    retrieval    JSONB NOT NULL DEFAULT '{}'::jsonb,
    provider     TEXT,
    model        TEXT,
    token_usage  JSONB NOT NULL DEFAULT '{}'::jsonb,
    latency_ms   INTEGER,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_chat_messages_session
    ON chat_messages(session_id, created_at);
