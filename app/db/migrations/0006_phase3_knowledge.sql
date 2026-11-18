-- Phase 3: normalized, reviewable knowledge organization.
-- Source pages, chunks and vision analyses remain the evidence of record.

CREATE TABLE IF NOT EXISTS knowledge_entities (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id         UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    document_version_id UUID NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
    entity_type         TEXT NOT NULL,
    name                TEXT NOT NULL,
    normalized_name     TEXT NOT NULL,
    description         TEXT NOT NULL DEFAULT '',
    attributes          JSONB NOT NULL DEFAULT '{}'::jsonb,
    aliases             TEXT[] NOT NULL DEFAULT '{}',
    confidence          NUMERIC(5, 4) NOT NULL DEFAULT 0.5
                        CHECK (confidence BETWEEN 0 AND 1),
    status              TEXT NOT NULL DEFAULT 'proposed'
                        CHECK (status IN ('proposed', 'approved', 'rejected', 'superseded')),
    source_page_start   INTEGER,
    source_page_end     INTEGER,
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_at         TIMESTAMPTZ,
    approved_by         UUID REFERENCES users(id) ON DELETE SET NULL,
    UNIQUE (document_version_id, entity_type, normalized_name)
);

CREATE INDEX IF NOT EXISTS ix_knowledge_entities_document
    ON knowledge_entities(document_id, entity_type);
CREATE INDEX IF NOT EXISTS ix_knowledge_entities_version
    ON knowledge_entities(document_version_id, status);
CREATE INDEX IF NOT EXISTS ix_knowledge_entities_name
    ON knowledge_entities(normalized_name);

CREATE TABLE IF NOT EXISTS knowledge_relationships (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id         UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    document_version_id UUID NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
    from_entity_id      UUID REFERENCES knowledge_entities(id) ON DELETE CASCADE,
    to_entity_id        UUID REFERENCES knowledge_entities(id) ON DELETE CASCADE,
    relationship_type   TEXT NOT NULL,
    description         TEXT NOT NULL DEFAULT '',
    confidence          NUMERIC(5, 4) NOT NULL DEFAULT 0.5
                        CHECK (confidence BETWEEN 0 AND 1),
    status              TEXT NOT NULL DEFAULT 'proposed'
                        CHECK (status IN ('proposed', 'approved', 'rejected', 'superseded')),
    source_page_start   INTEGER,
    source_page_end     INTEGER,
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_knowledge_relationships_version
    ON knowledge_relationships(document_version_id, status);
CREATE INDEX IF NOT EXISTS ix_knowledge_relationships_from
    ON knowledge_relationships(from_entity_id);
CREATE INDEX IF NOT EXISTS ix_knowledge_relationships_to
    ON knowledge_relationships(to_entity_id);

CREATE TABLE IF NOT EXISTS workflows (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id         UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    document_version_id UUID NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
    workflow_key        TEXT NOT NULL,
    title               TEXT NOT NULL,
    normalized_title    TEXT NOT NULL,
    summary             TEXT NOT NULL DEFAULT '',
    content_type        TEXT NOT NULL DEFAULT 'procedure',
    prerequisites       JSONB NOT NULL DEFAULT '[]'::jsonb,
    warnings            JSONB NOT NULL DEFAULT '[]'::jsonb,
    exceptions          JSONB NOT NULL DEFAULT '[]'::jsonb,
    confidence          NUMERIC(5, 4) NOT NULL DEFAULT 0.5
                        CHECK (confidence BETWEEN 0 AND 1),
    status              TEXT NOT NULL DEFAULT 'proposed'
                        CHECK (status IN ('proposed', 'approved', 'rejected', 'superseded')),
    source_page_start   INTEGER,
    source_page_end     INTEGER,
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_at         TIMESTAMPTZ,
    approved_by         UUID REFERENCES users(id) ON DELETE SET NULL,
    UNIQUE (document_version_id, workflow_key)
);

CREATE INDEX IF NOT EXISTS ix_workflows_document
    ON workflows(document_id, status);
CREATE INDEX IF NOT EXISTS ix_workflows_version
    ON workflows(document_version_id, status);
CREATE INDEX IF NOT EXISTS ix_workflows_title
    ON workflows(normalized_title);

CREATE TABLE IF NOT EXISTS workflow_steps (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id         UUID NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    document_page_id    UUID REFERENCES document_pages(id) ON DELETE SET NULL,
    step_number         INTEGER NOT NULL CHECK (step_number > 0),
    action              TEXT NOT NULL,
    description         TEXT NOT NULL DEFAULT '',
    ui_element          TEXT,
    conditions          JSONB NOT NULL DEFAULT '[]'::jsonb,
    warnings            JSONB NOT NULL DEFAULT '[]'::jsonb,
    exceptions          JSONB NOT NULL DEFAULT '[]'::jsonb,
    visual_callouts     JSONB NOT NULL DEFAULT '[]'::jsonb,
    confidence          NUMERIC(5, 4) NOT NULL DEFAULT 0.5
                        CHECK (confidence BETWEEN 0 AND 1),
    status              TEXT NOT NULL DEFAULT 'proposed'
                        CHECK (status IN ('proposed', 'approved', 'rejected', 'superseded')),
    source_page_number  INTEGER,
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workflow_id, step_number)
);

CREATE INDEX IF NOT EXISTS ix_workflow_steps_workflow
    ON workflow_steps(workflow_id, step_number);
CREATE INDEX IF NOT EXISTS ix_workflow_steps_page
    ON workflow_steps(document_page_id);

CREATE TABLE IF NOT EXISTS visual_references (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id         UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    document_version_id UUID NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
    document_page_id    UUID REFERENCES document_pages(id) ON DELETE SET NULL,
    workflow_step_id    UUID REFERENCES workflow_steps(id) ON DELETE SET NULL,
    entity_id           UUID REFERENCES knowledge_entities(id) ON DELETE SET NULL,
    callout_number      INTEGER,
    element_type        TEXT NOT NULL DEFAULT 'other',
    label               TEXT NOT NULL DEFAULT '',
    action              TEXT,
    description         TEXT NOT NULL DEFAULT '',
    bounding_box        JSONB,
    page_number         INTEGER,
    image_path          TEXT,
    status              TEXT NOT NULL DEFAULT 'proposed'
                        CHECK (status IN ('proposed', 'approved', 'rejected', 'superseded')),
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_visual_references_version
    ON visual_references(document_version_id, status);
CREATE INDEX IF NOT EXISTS ix_visual_references_page
    ON visual_references(document_page_id);
CREATE INDEX IF NOT EXISTS ix_visual_references_step
    ON visual_references(workflow_step_id);

CREATE TABLE IF NOT EXISTS knowledge_rules (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id         UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    document_version_id UUID NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
    workflow_id         UUID REFERENCES workflows(id) ON DELETE CASCADE,
    rule_type           TEXT NOT NULL
                        CHECK (rule_type IN ('business_rule', 'prerequisite', 'validation',
                                             'exception', 'warning')),
    rule_text           TEXT NOT NULL,
    source_page_number  INTEGER,
    status              TEXT NOT NULL DEFAULT 'proposed'
                        CHECK (status IN ('proposed', 'approved', 'rejected', 'superseded')),
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_knowledge_rules_version
    ON knowledge_rules(document_version_id, rule_type, status);
CREATE INDEX IF NOT EXISTS ix_knowledge_rules_workflow
    ON knowledge_rules(workflow_id);

CREATE TABLE IF NOT EXISTS knowledge_sources (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    knowledge_type      TEXT NOT NULL
                        CHECK (knowledge_type IN ('entity', 'relationship', 'workflow',
                                                  'step', 'visual', 'rule', 'conflict')),
    knowledge_id        UUID NOT NULL,
    document_id         UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    document_version_id UUID NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
    document_page_id    UUID REFERENCES document_pages(id) ON DELETE SET NULL,
    document_chunk_id   UUID REFERENCES document_chunks(id) ON DELETE SET NULL,
    page_number         INTEGER,
    section_number      TEXT,
    quote               TEXT,
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_knowledge_sources_lookup
    ON knowledge_sources(knowledge_type, knowledge_id);
CREATE INDEX IF NOT EXISTS ix_knowledge_sources_version
    ON knowledge_sources(document_version_id);
CREATE INDEX IF NOT EXISTS ix_knowledge_sources_chunk
    ON knowledge_sources(document_chunk_id);

CREATE TABLE IF NOT EXISTS knowledge_conflicts (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id         UUID REFERENCES documents(id) ON DELETE CASCADE,
    conflict_type       TEXT NOT NULL,
    left_knowledge_type TEXT,
    left_knowledge_id   UUID,
    right_knowledge_type TEXT,
    right_knowledge_id  UUID,
    description         TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open', 'resolved', 'ignored')),
    resolution          TEXT,
    detected_by         TEXT NOT NULL DEFAULT 'system',
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at         TIMESTAMPTZ,
    resolved_by         UUID REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS ix_knowledge_conflicts_status
    ON knowledge_conflicts(status, created_at);
CREATE INDEX IF NOT EXISTS ix_knowledge_conflicts_document
    ON knowledge_conflicts(document_id);

CREATE TABLE IF NOT EXISTS page_markdown (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_page_id    UUID NOT NULL UNIQUE REFERENCES document_pages(id) ON DELETE CASCADE,
    document_version_id UUID NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
    markdown            TEXT NOT NULL,
    content_hash        TEXT NOT NULL,
    generator           TEXT NOT NULL DEFAULT 'vision',
    generated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_page_markdown_version
    ON page_markdown(document_version_id);

DROP TRIGGER IF EXISTS trg_knowledge_entities_updated_at ON knowledge_entities;
CREATE TRIGGER trg_knowledge_entities_updated_at
    BEFORE UPDATE ON knowledge_entities
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_knowledge_relationships_updated_at ON knowledge_relationships;
CREATE TRIGGER trg_knowledge_relationships_updated_at
    BEFORE UPDATE ON knowledge_relationships
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_workflows_updated_at ON workflows;
CREATE TRIGGER trg_workflows_updated_at
    BEFORE UPDATE ON workflows
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_workflow_steps_updated_at ON workflow_steps;
CREATE TRIGGER trg_workflow_steps_updated_at
    BEFORE UPDATE ON workflow_steps
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_visual_references_updated_at ON visual_references;
CREATE TRIGGER trg_visual_references_updated_at
    BEFORE UPDATE ON visual_references
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_knowledge_rules_updated_at ON knowledge_rules;
CREATE TRIGGER trg_knowledge_rules_updated_at
    BEFORE UPDATE ON knowledge_rules
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_knowledge_conflicts_updated_at ON knowledge_conflicts;
CREATE TRIGGER trg_knowledge_conflicts_updated_at
    BEFORE UPDATE ON knowledge_conflicts
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
