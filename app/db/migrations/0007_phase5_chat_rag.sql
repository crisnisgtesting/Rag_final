-- Phase 5: grounded RAG responses, citation audit, feedback and unanswered work.

ALTER TABLE chat_sessions
    ADD COLUMN IF NOT EXISTS language TEXT NOT NULL DEFAULT 'en',
    ADD COLUMN IF NOT EXISTS summary TEXT,
    ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS chat_responses (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id          UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    user_message_id     UUID REFERENCES chat_messages(id) ON DELETE SET NULL,
    assistant_message_id UUID REFERENCES chat_messages(id) ON DELETE SET NULL,
    question            TEXT NOT NULL,
    answer              TEXT NOT NULL,
    language            TEXT NOT NULL DEFAULT 'en',
    provider            TEXT,
    model               TEXT,
    retrieval           JSONB NOT NULL DEFAULT '{}'::jsonb,
    token_usage         JSONB NOT NULL DEFAULT '{}'::jsonb,
    latency_ms          INTEGER,
    citation_status     TEXT NOT NULL DEFAULT 'unchecked'
                        CHECK (citation_status IN ('valid', 'invalid', 'unchecked', 'none')),
    confidence          NUMERIC(5, 4),
    request_id          TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_chat_responses_session
    ON chat_responses(session_id, created_at);
CREATE INDEX IF NOT EXISTS ix_chat_responses_user
    ON chat_responses(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_chat_responses_citation
    ON chat_responses(citation_status);

CREATE TABLE IF NOT EXISTS response_sources (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    response_id         UUID NOT NULL REFERENCES chat_responses(id) ON DELETE CASCADE,
    citation_index      INTEGER NOT NULL,
    source_kind         TEXT NOT NULL DEFAULT 'chunk'
                        CHECK (source_kind IN ('chunk', 'knowledge')),
    chunk_id            UUID REFERENCES document_chunks(id) ON DELETE SET NULL,
    knowledge_type      TEXT,
    knowledge_id        UUID,
    document_id         UUID REFERENCES documents(id) ON DELETE SET NULL,
    version_id          UUID REFERENCES document_versions(id) ON DELETE SET NULL,
    page_start          INTEGER,
    page_end            INTEGER,
    section_number      TEXT,
    heading             TEXT,
    excerpt             TEXT,
    score               NUMERIC(10, 6),
    quote               TEXT,
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (response_id, citation_index)
);

CREATE INDEX IF NOT EXISTS ix_response_sources_response
    ON response_sources(response_id, citation_index);
CREATE INDEX IF NOT EXISTS ix_response_sources_chunk
    ON response_sources(chunk_id);
CREATE INDEX IF NOT EXISTS ix_response_sources_knowledge
    ON response_sources(knowledge_type, knowledge_id);

CREATE TABLE IF NOT EXISTS feedback (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    response_id         UUID NOT NULL REFERENCES chat_responses(id) ON DELETE CASCADE,
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    rating              SMALLINT NOT NULL CHECK (rating BETWEEN -1 AND 1),
    comment             TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (response_id, user_id)
);

CREATE INDEX IF NOT EXISTS ix_feedback_rating
    ON feedback(rating, created_at DESC);

CREATE TABLE IF NOT EXISTS unanswered_questions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID REFERENCES users(id) ON DELETE SET NULL,
    session_id          UUID REFERENCES chat_sessions(id) ON DELETE SET NULL,
    response_id         UUID REFERENCES chat_responses(id) ON DELETE SET NULL,
    question            TEXT NOT NULL,
    language            TEXT NOT NULL DEFAULT 'en',
    reason              TEXT NOT NULL DEFAULT 'no_evidence'
                        CHECK (reason IN ('no_evidence', 'invalid_citations',
                                          'insufficient_context', 'provider_error',
                                          'other')),
    retrieval           JSONB NOT NULL DEFAULT '{}'::jsonb,
    status              TEXT NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open', 'in_review', 'resolved', 'ignored')),
    resolution          TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at         TIMESTAMPTZ,
    resolved_by         UUID REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS ix_unanswered_questions_status
    ON unanswered_questions(status, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_unanswered_questions_user
    ON unanswered_questions(user_id, created_at DESC);

DROP TRIGGER IF EXISTS trg_feedback_updated_at ON feedback;
CREATE TRIGGER trg_feedback_updated_at
    BEFORE UPDATE ON feedback
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_unanswered_questions_updated_at ON unanswered_questions;
CREATE TRIGGER trg_unanswered_questions_updated_at
    BEFORE UPDATE ON unanswered_questions
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
