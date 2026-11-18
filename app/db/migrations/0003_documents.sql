-- Document management and page-level processing metadata.

CREATE TABLE IF NOT EXISTS documents (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title              TEXT NOT NULL,
    filename           TEXT NOT NULL,
    document_type      TEXT NOT NULL
                       CHECK (document_type IN ('pdf', 'docx', 'markdown')),
    source             TEXT,
    description        TEXT,
    current_version_id UUID,
    status             TEXT NOT NULL DEFAULT 'draft'
                       CHECK (status IN ('draft', 'processing', 'active', 'archived')),
    uploaded_by        UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

DROP TRIGGER IF EXISTS trg_documents_updated_at ON documents;
CREATE TRIGGER trg_documents_updated_at
    BEFORE UPDATE ON documents
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS document_versions (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id      UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version_number   INTEGER NOT NULL CHECK (version_number > 0),
    content_hash     TEXT NOT NULL,
    storage_path     TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'uploaded'
                     CHECK (status IN ('uploaded', 'processing', 'ready', 'failed')),
    approval_status  TEXT NOT NULL DEFAULT 'draft'
                     CHECK (approval_status IN (
                         'draft', 'pending_review', 'approved', 'rejected',
                         'deprecated', 'superseded'
                     )),
    metadata         JSONB NOT NULL DEFAULT '{}'::jsonb,
    uploaded_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_at      TIMESTAMPTZ,
    approved_by      UUID REFERENCES users(id) ON DELETE SET NULL,
    superseded_at    TIMESTAMPTZ,
    UNIQUE (document_id, version_number)
);

CREATE INDEX IF NOT EXISTS ix_document_versions_document
    ON document_versions(document_id);
CREATE INDEX IF NOT EXISTS ix_document_versions_hash
    ON document_versions(content_hash);
CREATE INDEX IF NOT EXISTS ix_document_versions_approval
    ON document_versions(document_id, approval_status);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_documents_current_version'
    ) THEN
        ALTER TABLE documents
            ADD CONSTRAINT fk_documents_current_version
            FOREIGN KEY (current_version_id)
            REFERENCES document_versions(id) ON DELETE SET NULL;
    END IF;
END;
$$;

CREATE TABLE IF NOT EXISTS document_pages (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_version_id UUID NOT NULL
                        REFERENCES document_versions(id) ON DELETE CASCADE,
    physical_page_number INTEGER NOT NULL CHECK (physical_page_number > 0),
    printed_page_number TEXT,
    page_text           TEXT,
    page_image_path     TEXT,
    text_hash           TEXT,
    image_hash          TEXT,
    content_type        TEXT NOT NULL DEFAULT 'unknown',
    processing_status   TEXT NOT NULL DEFAULT 'pending'
                        CHECK (processing_status IN (
                            'pending', 'processing', 'completed', 'failed',
                            'skipped', 'needs_review'
                        )),
    vision_provider     TEXT,
    vision_model        TEXT,
    confidence          NUMERIC(5, 4) CHECK (confidence BETWEEN 0 AND 1),
    needs_review        BOOLEAN NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_version_id, physical_page_number)
);

CREATE INDEX IF NOT EXISTS ix_document_pages_version
    ON document_pages(document_version_id, physical_page_number);
CREATE INDEX IF NOT EXISTS ix_document_pages_text_hash
    ON document_pages(text_hash);
CREATE INDEX IF NOT EXISTS ix_document_pages_image_hash
    ON document_pages(image_hash);
CREATE INDEX IF NOT EXISTS ix_document_pages_status
    ON document_pages(processing_status);

DROP TRIGGER IF EXISTS trg_document_pages_updated_at ON document_pages;
CREATE TRIGGER trg_document_pages_updated_at
    BEFORE UPDATE ON document_pages
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
