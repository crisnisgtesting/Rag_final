-- Phase 6: vision comparison, retrieval/chat evaluation and operations.

CREATE TABLE IF NOT EXISTS vision_evaluations (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_version_id UUID NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
    document_page_id    UUID REFERENCES document_pages(id) ON DELETE CASCADE,
    provider            TEXT NOT NULL,
    model               TEXT NOT NULL,
    evaluation_type     TEXT NOT NULL DEFAULT 'manual'
                        CHECK (evaluation_type IN ('manual', 'benchmark', 'regression')),
    accuracy_score      NUMERIC(5, 4) CHECK (accuracy_score BETWEEN 0 AND 1),
    completeness_score  NUMERIC(5, 4) CHECK (completeness_score BETWEEN 0 AND 1),
    visual_score        NUMERIC(5, 4) CHECK (visual_score BETWEEN 0 AND 1),
    metrics             JSONB NOT NULL DEFAULT '{}'::jsonb,
    notes               TEXT,
    created_by          UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_vision_evaluations_version
    ON vision_evaluations(document_version_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_vision_evaluations_page
    ON vision_evaluations(document_page_id);

CREATE TABLE IF NOT EXISTS eval_set (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                TEXT NOT NULL,
    question            TEXT NOT NULL,
    expected_keywords   TEXT[] NOT NULL DEFAULT '{}',
    expected_document_ids UUID[] NOT NULL DEFAULT '{}',
    expected_answer     TEXT,
    language            TEXT NOT NULL DEFAULT 'en',
    tags                TEXT[] NOT NULL DEFAULT '{}',
    active              BOOLEAN NOT NULL DEFAULT TRUE,
    created_by          UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_eval_set_active
    ON eval_set(active, name);

CREATE TABLE IF NOT EXISTS eval_results (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id              UUID NOT NULL,
    eval_id             UUID NOT NULL REFERENCES eval_set(id) ON DELETE CASCADE,
    response_id         UUID REFERENCES chat_responses(id) ON DELETE SET NULL,
    passed              BOOLEAN NOT NULL DEFAULT FALSE,
    citation_valid      BOOLEAN NOT NULL DEFAULT FALSE,
    keyword_recall     NUMERIC(5, 4) NOT NULL DEFAULT 0
                        CHECK (keyword_recall BETWEEN 0 AND 1),
    document_match      BOOLEAN NOT NULL DEFAULT FALSE,
    answer              TEXT,
    citations           JSONB NOT NULL DEFAULT '[]'::jsonb,
    metrics             JSONB NOT NULL DEFAULT '{}'::jsonb,
    latency_ms          INTEGER,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_eval_results_run
    ON eval_results(run_id, created_at);
CREATE INDEX IF NOT EXISTS ix_eval_results_eval
    ON eval_results(eval_id, created_at DESC);

DROP TRIGGER IF EXISTS trg_eval_set_updated_at ON eval_set;
CREATE TRIGGER trg_eval_set_updated_at
    BEFORE UPDATE ON eval_set
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
