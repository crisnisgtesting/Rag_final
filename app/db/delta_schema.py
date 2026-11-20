from __future__ import annotations

SCHEMA_VERSION = "1"

DELTA_TABLES: tuple[str, ...] = (
    "roles",
    "users",
    "user_roles",
    "api_tokens",
    "audit_log",
    "documents",
    "document_versions",
    "document_pages",
    "app_settings",
    "ingestion_jobs",
    "job_checkpoints",
    "request_logs",
    "vision_runs",
    "document_chunks",
    "chat_sessions",
    "chat_messages",
    "knowledge_entities",
    "knowledge_relationships",
    "workflows",
    "workflow_steps",
    "visual_references",
    "knowledge_rules",
    "knowledge_sources",
    "knowledge_conflicts",
    "page_markdown",
    "chat_responses",
    "response_sources",
    "feedback",
    "unanswered_questions",
    "vision_evaluations",
    "eval_set",
    "eval_results",
    "rag_schema_metadata",
)

TABLE_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS {table} USING DELTA AS
    SELECT
        CAST(NULL AS STRING) AS `role_code`,
        CAST(NULL AS STRING) AS `description`
    WHERE FALSE
    """,
    """
    CREATE TABLE IF NOT EXISTS {table} USING DELTA AS
    SELECT
        CAST(NULL AS STRING) AS `id`,
        CAST(NULL AS STRING) AS `username`,
        CAST(NULL AS STRING) AS `email`,
        CAST(NULL AS STRING) AS `full_name`,
        CAST(NULL AS STRING) AS `password_hash`,
        CAST(NULL AS BOOLEAN) AS `is_active`,
        CAST(NULL AS TIMESTAMP) AS `created_at`,
        CAST(NULL AS TIMESTAMP) AS `updated_at`,
        CAST(NULL AS TIMESTAMP) AS `last_login_at`
    WHERE FALSE
    """,
    """
    CREATE TABLE IF NOT EXISTS {table} USING DELTA AS
    SELECT
        CAST(NULL AS STRING) AS `user_id`,
        CAST(NULL AS STRING) AS `role_code`,
        CAST(NULL AS TIMESTAMP) AS `granted_at`
    WHERE FALSE
    """,
    """
    CREATE TABLE IF NOT EXISTS {table} USING DELTA AS
    SELECT
        CAST(NULL AS STRING) AS `id`,
        CAST(NULL AS STRING) AS `user_id`,
        CAST(NULL AS STRING) AS `name`,
        CAST(NULL AS STRING) AS `token_prefix`,
        CAST(NULL AS STRING) AS `token_hash`,
        CAST(NULL AS STRING) AS `scopes`,
        CAST(NULL AS TIMESTAMP) AS `created_at`,
        CAST(NULL AS TIMESTAMP) AS `expires_at`,
        CAST(NULL AS TIMESTAMP) AS `revoked_at`,
        CAST(NULL AS TIMESTAMP) AS `last_used_at`
    WHERE FALSE
    """,
    """
    CREATE TABLE IF NOT EXISTS {table} USING DELTA AS
    SELECT
        CAST(NULL AS BIGINT) AS `id`,
        CAST(NULL AS STRING) AS `actor_user_id`,
        CAST(NULL AS STRING) AS `action`,
        CAST(NULL AS STRING) AS `entity_type`,
        CAST(NULL AS STRING) AS `entity_id`,
        CAST(NULL AS STRING) AS `request_id`,
        CAST(NULL AS STRING) AS `metadata`,
        CAST(NULL AS TIMESTAMP) AS `created_at`
    WHERE FALSE
    """,
    """
    CREATE TABLE IF NOT EXISTS {table} USING DELTA AS
    SELECT
        CAST(NULL AS STRING) AS `id`,
        CAST(NULL AS STRING) AS `title`,
        CAST(NULL AS STRING) AS `filename`,
        CAST(NULL AS STRING) AS `document_type`,
        CAST(NULL AS STRING) AS `source`,
        CAST(NULL AS STRING) AS `description`,
        CAST(NULL AS STRING) AS `current_version_id`,
        CAST(NULL AS STRING) AS `status`,
        CAST(NULL AS STRING) AS `uploaded_by`,
        CAST(NULL AS TIMESTAMP) AS `created_at`,
        CAST(NULL AS TIMESTAMP) AS `updated_at`
    WHERE FALSE
    """,
    """
    CREATE TABLE IF NOT EXISTS {table} USING DELTA AS
    SELECT
        CAST(NULL AS STRING) AS `id`,
        CAST(NULL AS STRING) AS `document_id`,
        CAST(NULL AS INT) AS `version_number`,
        CAST(NULL AS STRING) AS `content_hash`,
        CAST(NULL AS STRING) AS `storage_path`,
        CAST(NULL AS STRING) AS `status`,
        CAST(NULL AS STRING) AS `approval_status`,
        CAST(NULL AS STRING) AS `metadata`,
        CAST(NULL AS TIMESTAMP) AS `uploaded_at`,
        CAST(NULL AS TIMESTAMP) AS `approved_at`,
        CAST(NULL AS STRING) AS `approved_by`,
        CAST(NULL AS TIMESTAMP) AS `superseded_at`,
        CAST(NULL AS INT) AS `page_count`,
        CAST(NULL AS INT) AS `chunk_count`,
        CAST(NULL AS TIMESTAMP) AS `indexed_at`
    WHERE FALSE
    """,
    """
    CREATE TABLE IF NOT EXISTS {table} USING DELTA AS
    SELECT
        CAST(NULL AS STRING) AS `id`,
        CAST(NULL AS STRING) AS `document_version_id`,
        CAST(NULL AS INT) AS `physical_page_number`,
        CAST(NULL AS STRING) AS `printed_page_number`,
        CAST(NULL AS STRING) AS `page_text`,
        CAST(NULL AS STRING) AS `page_image_path`,
        CAST(NULL AS STRING) AS `text_hash`,
        CAST(NULL AS STRING) AS `image_hash`,
        CAST(NULL AS STRING) AS `content_type`,
        CAST(NULL AS STRING) AS `processing_status`,
        CAST(NULL AS STRING) AS `vision_provider`,
        CAST(NULL AS STRING) AS `vision_model`,
        CAST(NULL AS DECIMAL(5,4)) AS `confidence`,
        CAST(NULL AS BOOLEAN) AS `needs_review`,
        CAST(NULL AS TIMESTAMP) AS `created_at`,
        CAST(NULL AS TIMESTAMP) AS `updated_at`,
        CAST(NULL AS STRING) AS `text_source`,
        CAST(NULL AS INT) AS `text_char_count`,
        CAST(NULL AS INT) AS `image_count`,
        CAST(NULL AS DECIMAL(7,5)) AS `image_coverage`,
        CAST(NULL AS DECIMAL(10,2)) AS `width_pt`,
        CAST(NULL AS DECIMAL(10,2)) AS `height_pt`,
        CAST(NULL AS INT) AS `render_dpi`,
        CAST(NULL AS STRING) AS `layout`,
        CAST(NULL AS STRING) AS `analysis`,
        CAST(NULL AS STRING) AS `analysis_provider`,
        CAST(NULL AS STRING) AS `analysis_model`,
        CAST(NULL AS INT) AS `analysis_latency_ms`,
        CAST(NULL AS STRING) AS `analysis_usage`,
        CAST(NULL AS TIMESTAMP) AS `analyzed_at`
    WHERE FALSE
    """,
    """
    CREATE TABLE IF NOT EXISTS {table} USING DELTA AS
    SELECT
        CAST(NULL AS STRING) AS `key`,
        CAST(NULL AS STRING) AS `value`,
        CAST(NULL AS STRING) AS `updated_by`,
        CAST(NULL AS TIMESTAMP) AS `updated_at`
    WHERE FALSE
    """,
    """
    CREATE TABLE IF NOT EXISTS {table} USING DELTA AS
    SELECT
        CAST(NULL AS STRING) AS `id`,
        CAST(NULL AS STRING) AS `document_version_id`,
        CAST(NULL AS STRING) AS `job_type`,
        CAST(NULL AS STRING) AS `status`,
        CAST(NULL AS INT) AS `total_pages`,
        CAST(NULL AS INT) AS `completed_pages`,
        CAST(NULL AS INT) AS `failed_pages`,
        CAST(NULL AS STRING) AS `selected_vision_provider`,
        CAST(NULL AS STRING) AS `selected_vision_model`,
        CAST(NULL AS DECIMAL(5,2)) AS `progress`,
        CAST(NULL AS STRING) AS `error_details`,
        CAST(NULL AS TIMESTAMP) AS `started_at`,
        CAST(NULL AS TIMESTAMP) AS `completed_at`,
        CAST(NULL AS TIMESTAMP) AS `created_at`,
        CAST(NULL AS TIMESTAMP) AS `updated_at`,
        CAST(NULL AS STRING) AS `stage`,
        CAST(NULL AS STRING) AS `config`,
        CAST(NULL AS STRING) AS `stats`,
        CAST(NULL AS BOOLEAN) AS `cancel_requested`,
        CAST(NULL AS TIMESTAMP) AS `heartbeat_at`,
        CAST(NULL AS INT) AS `resume_count`
    WHERE FALSE
    """,
    """
    CREATE TABLE IF NOT EXISTS {table} USING DELTA AS
    SELECT
        CAST(NULL AS BIGINT) AS `id`,
        CAST(NULL AS STRING) AS `job_id`,
        CAST(NULL AS STRING) AS `checkpoint_key`,
        CAST(NULL AS STRING) AS `checkpoint_data`,
        CAST(NULL AS TIMESTAMP) AS `created_at`
    WHERE FALSE
    """,
    """
    CREATE TABLE IF NOT EXISTS {table} USING DELTA AS
    SELECT
        CAST(NULL AS BIGINT) AS `id`,
        CAST(NULL AS STRING) AS `request_id`,
        CAST(NULL AS STRING) AS `user_id`,
        CAST(NULL AS STRING) AS `method`,
        CAST(NULL AS STRING) AS `endpoint`,
        CAST(NULL AS INT) AS `status_code`,
        CAST(NULL AS INT) AS `duration_ms`,
        CAST(NULL AS STRING) AS `error`,
        CAST(NULL AS STRING) AS `client_ip`,
        CAST(NULL AS TIMESTAMP) AS `created_at`
    WHERE FALSE
    """,
    """
    CREATE TABLE IF NOT EXISTS {table} USING DELTA AS
    SELECT
        CAST(NULL AS BIGINT) AS `id`,
        CAST(NULL AS STRING) AS `job_id`,
        CAST(NULL AS STRING) AS `document_page_id`,
        CAST(NULL AS STRING) AS `provider`,
        CAST(NULL AS STRING) AS `model`,
        CAST(NULL AS STRING) AS `status`,
        CAST(NULL AS INT) AS `latency_ms`,
        CAST(NULL AS STRING) AS `usage`,
        CAST(NULL AS STRING) AS `error`,
        CAST(NULL AS TIMESTAMP) AS `created_at`
    WHERE FALSE
    """,
    """
    CREATE TABLE IF NOT EXISTS {table} USING DELTA AS
    SELECT
        CAST(NULL AS STRING) AS `id`,
        CAST(NULL AS STRING) AS `document_version_id`,
        CAST(NULL AS STRING) AS `document_id`,
        CAST(NULL AS INT) AS `page_start`,
        CAST(NULL AS INT) AS `page_end`,
        CAST(NULL AS INT) AS `chunk_index`,
        CAST(NULL AS STRING) AS `section_number`,
        CAST(NULL AS STRING) AS `heading`,
        CAST(NULL AS STRING) AS `content_type`,
        CAST(NULL AS STRING) AS `content`,
        CAST(NULL AS STRING) AS `content_hash`,
        CAST(NULL AS INT) AS `char_count`,
        CAST(NULL AS INT) AS `token_estimate`,
        CAST(NULL AS STRING) AS `metadata`,
        CAST(NULL AS ARRAY<FLOAT>) AS `embedding`,
        CAST(NULL AS STRING) AS `embedding_model`,
        CAST(NULL AS INT) AS `embedding_dim`,
        CAST(NULL AS TIMESTAMP) AS `embedding_updated_at`,
        CAST(NULL AS STRING) AS `tsv`,
        CAST(NULL AS TIMESTAMP) AS `created_at`
    WHERE FALSE
    """,
    """
    CREATE TABLE IF NOT EXISTS {table} USING DELTA AS
    SELECT
        CAST(NULL AS STRING) AS `id`,
        CAST(NULL AS STRING) AS `user_id`,
        CAST(NULL AS STRING) AS `title`,
        CAST(NULL AS TIMESTAMP) AS `created_at`,
        CAST(NULL AS TIMESTAMP) AS `updated_at`,
        CAST(NULL AS STRING) AS `language`,
        CAST(NULL AS STRING) AS `summary`,
        CAST(NULL AS TIMESTAMP) AS `archived_at`
    WHERE FALSE
    """,
    """
    CREATE TABLE IF NOT EXISTS {table} USING DELTA AS
    SELECT
        CAST(NULL AS STRING) AS `id`,
        CAST(NULL AS STRING) AS `session_id`,
        CAST(NULL AS STRING) AS `role`,
        CAST(NULL AS STRING) AS `content`,
        CAST(NULL AS STRING) AS `citations`,
        CAST(NULL AS STRING) AS `retrieval`,
        CAST(NULL AS STRING) AS `provider`,
        CAST(NULL AS STRING) AS `model`,
        CAST(NULL AS STRING) AS `token_usage`,
        CAST(NULL AS INT) AS `latency_ms`,
        CAST(NULL AS TIMESTAMP) AS `created_at`
    WHERE FALSE
    """,
    """
    CREATE TABLE IF NOT EXISTS {table} USING DELTA AS
    SELECT
        CAST(NULL AS STRING) AS `id`,
        CAST(NULL AS STRING) AS `document_id`,
        CAST(NULL AS STRING) AS `document_version_id`,
        CAST(NULL AS STRING) AS `entity_type`,
        CAST(NULL AS STRING) AS `name`,
        CAST(NULL AS STRING) AS `normalized_name`,
        CAST(NULL AS STRING) AS `description`,
        CAST(NULL AS STRING) AS `attributes`,
        CAST(NULL AS ARRAY<STRING>) AS `aliases`,
        CAST(NULL AS DECIMAL(5,4)) AS `confidence`,
        CAST(NULL AS STRING) AS `status`,
        CAST(NULL AS INT) AS `source_page_start`,
        CAST(NULL AS INT) AS `source_page_end`,
        CAST(NULL AS STRING) AS `metadata`,
        CAST(NULL AS TIMESTAMP) AS `created_at`,
        CAST(NULL AS TIMESTAMP) AS `updated_at`,
        CAST(NULL AS TIMESTAMP) AS `approved_at`,
        CAST(NULL AS STRING) AS `approved_by`
    WHERE FALSE
    """,
    """
    CREATE TABLE IF NOT EXISTS {table} USING DELTA AS
    SELECT
        CAST(NULL AS STRING) AS `id`,
        CAST(NULL AS STRING) AS `document_id`,
        CAST(NULL AS STRING) AS `document_version_id`,
        CAST(NULL AS STRING) AS `from_entity_id`,
        CAST(NULL AS STRING) AS `to_entity_id`,
        CAST(NULL AS STRING) AS `relationship_type`,
        CAST(NULL AS STRING) AS `description`,
        CAST(NULL AS DECIMAL(5,4)) AS `confidence`,
        CAST(NULL AS STRING) AS `status`,
        CAST(NULL AS INT) AS `source_page_start`,
        CAST(NULL AS INT) AS `source_page_end`,
        CAST(NULL AS STRING) AS `metadata`,
        CAST(NULL AS TIMESTAMP) AS `created_at`,
        CAST(NULL AS TIMESTAMP) AS `updated_at`
    WHERE FALSE
    """,
    """
    CREATE TABLE IF NOT EXISTS {table} USING DELTA AS
    SELECT
        CAST(NULL AS STRING) AS `id`,
        CAST(NULL AS STRING) AS `document_id`,
        CAST(NULL AS STRING) AS `document_version_id`,
        CAST(NULL AS STRING) AS `workflow_key`,
        CAST(NULL AS STRING) AS `title`,
        CAST(NULL AS STRING) AS `normalized_title`,
        CAST(NULL AS STRING) AS `summary`,
        CAST(NULL AS STRING) AS `content_type`,
        CAST(NULL AS STRING) AS `prerequisites`,
        CAST(NULL AS STRING) AS `warnings`,
        CAST(NULL AS STRING) AS `exceptions`,
        CAST(NULL AS DECIMAL(5,4)) AS `confidence`,
        CAST(NULL AS STRING) AS `status`,
        CAST(NULL AS INT) AS `source_page_start`,
        CAST(NULL AS INT) AS `source_page_end`,
        CAST(NULL AS STRING) AS `metadata`,
        CAST(NULL AS TIMESTAMP) AS `created_at`,
        CAST(NULL AS TIMESTAMP) AS `updated_at`,
        CAST(NULL AS TIMESTAMP) AS `approved_at`,
        CAST(NULL AS STRING) AS `approved_by`
    WHERE FALSE
    """,
    """
    CREATE TABLE IF NOT EXISTS {table} USING DELTA AS
    SELECT
        CAST(NULL AS STRING) AS `id`,
        CAST(NULL AS STRING) AS `workflow_id`,
        CAST(NULL AS STRING) AS `document_page_id`,
        CAST(NULL AS INT) AS `step_number`,
        CAST(NULL AS STRING) AS `action`,
        CAST(NULL AS STRING) AS `description`,
        CAST(NULL AS STRING) AS `ui_element`,
        CAST(NULL AS STRING) AS `conditions`,
        CAST(NULL AS STRING) AS `warnings`,
        CAST(NULL AS STRING) AS `exceptions`,
        CAST(NULL AS STRING) AS `visual_callouts`,
        CAST(NULL AS DECIMAL(5,4)) AS `confidence`,
        CAST(NULL AS STRING) AS `status`,
        CAST(NULL AS INT) AS `source_page_number`,
        CAST(NULL AS STRING) AS `metadata`,
        CAST(NULL AS TIMESTAMP) AS `created_at`,
        CAST(NULL AS TIMESTAMP) AS `updated_at`
    WHERE FALSE
    """,
    """
    CREATE TABLE IF NOT EXISTS {table} USING DELTA AS
    SELECT
        CAST(NULL AS STRING) AS `id`,
        CAST(NULL AS STRING) AS `document_id`,
        CAST(NULL AS STRING) AS `document_version_id`,
        CAST(NULL AS STRING) AS `document_page_id`,
        CAST(NULL AS STRING) AS `workflow_step_id`,
        CAST(NULL AS STRING) AS `entity_id`,
        CAST(NULL AS INT) AS `callout_number`,
        CAST(NULL AS STRING) AS `element_type`,
        CAST(NULL AS STRING) AS `label`,
        CAST(NULL AS STRING) AS `action`,
        CAST(NULL AS STRING) AS `description`,
        CAST(NULL AS STRING) AS `bounding_box`,
        CAST(NULL AS INT) AS `page_number`,
        CAST(NULL AS STRING) AS `image_path`,
        CAST(NULL AS STRING) AS `status`,
        CAST(NULL AS STRING) AS `metadata`,
        CAST(NULL AS TIMESTAMP) AS `created_at`,
        CAST(NULL AS TIMESTAMP) AS `updated_at`
    WHERE FALSE
    """,
    """
    CREATE TABLE IF NOT EXISTS {table} USING DELTA AS
    SELECT
        CAST(NULL AS STRING) AS `id`,
        CAST(NULL AS STRING) AS `document_id`,
        CAST(NULL AS STRING) AS `document_version_id`,
        CAST(NULL AS STRING) AS `workflow_id`,
        CAST(NULL AS STRING) AS `rule_type`,
        CAST(NULL AS STRING) AS `rule_text`,
        CAST(NULL AS INT) AS `source_page_number`,
        CAST(NULL AS STRING) AS `status`,
        CAST(NULL AS STRING) AS `metadata`,
        CAST(NULL AS TIMESTAMP) AS `created_at`,
        CAST(NULL AS TIMESTAMP) AS `updated_at`
    WHERE FALSE
    """,
    """
    CREATE TABLE IF NOT EXISTS {table} USING DELTA AS
    SELECT
        CAST(NULL AS STRING) AS `id`,
        CAST(NULL AS STRING) AS `knowledge_type`,
        CAST(NULL AS STRING) AS `knowledge_id`,
        CAST(NULL AS STRING) AS `document_id`,
        CAST(NULL AS STRING) AS `document_version_id`,
        CAST(NULL AS STRING) AS `document_page_id`,
        CAST(NULL AS STRING) AS `document_chunk_id`,
        CAST(NULL AS INT) AS `page_number`,
        CAST(NULL AS STRING) AS `section_number`,
        CAST(NULL AS STRING) AS `quote`,
        CAST(NULL AS STRING) AS `metadata`,
        CAST(NULL AS TIMESTAMP) AS `created_at`
    WHERE FALSE
    """,
    """
    CREATE TABLE IF NOT EXISTS {table} USING DELTA AS
    SELECT
        CAST(NULL AS STRING) AS `id`,
        CAST(NULL AS STRING) AS `document_id`,
        CAST(NULL AS STRING) AS `conflict_type`,
        CAST(NULL AS STRING) AS `left_knowledge_type`,
        CAST(NULL AS STRING) AS `left_knowledge_id`,
        CAST(NULL AS STRING) AS `right_knowledge_type`,
        CAST(NULL AS STRING) AS `right_knowledge_id`,
        CAST(NULL AS STRING) AS `description`,
        CAST(NULL AS STRING) AS `status`,
        CAST(NULL AS STRING) AS `resolution`,
        CAST(NULL AS STRING) AS `detected_by`,
        CAST(NULL AS STRING) AS `metadata`,
        CAST(NULL AS TIMESTAMP) AS `created_at`,
        CAST(NULL AS TIMESTAMP) AS `updated_at`,
        CAST(NULL AS TIMESTAMP) AS `resolved_at`,
        CAST(NULL AS STRING) AS `resolved_by`
    WHERE FALSE
    """,
    """
    CREATE TABLE IF NOT EXISTS {table} USING DELTA AS
    SELECT
        CAST(NULL AS STRING) AS `id`,
        CAST(NULL AS STRING) AS `document_page_id`,
        CAST(NULL AS STRING) AS `document_version_id`,
        CAST(NULL AS STRING) AS `markdown`,
        CAST(NULL AS STRING) AS `content_hash`,
        CAST(NULL AS STRING) AS `generator`,
        CAST(NULL AS TIMESTAMP) AS `generated_at`
    WHERE FALSE
    """,
    """
    CREATE TABLE IF NOT EXISTS {table} USING DELTA AS
    SELECT
        CAST(NULL AS STRING) AS `id`,
        CAST(NULL AS STRING) AS `session_id`,
        CAST(NULL AS STRING) AS `user_id`,
        CAST(NULL AS STRING) AS `user_message_id`,
        CAST(NULL AS STRING) AS `assistant_message_id`,
        CAST(NULL AS STRING) AS `question`,
        CAST(NULL AS STRING) AS `answer`,
        CAST(NULL AS STRING) AS `language`,
        CAST(NULL AS STRING) AS `provider`,
        CAST(NULL AS STRING) AS `model`,
        CAST(NULL AS STRING) AS `retrieval`,
        CAST(NULL AS STRING) AS `token_usage`,
        CAST(NULL AS INT) AS `latency_ms`,
        CAST(NULL AS STRING) AS `citation_status`,
        CAST(NULL AS DECIMAL(5,4)) AS `confidence`,
        CAST(NULL AS STRING) AS `request_id`,
        CAST(NULL AS TIMESTAMP) AS `created_at`
    WHERE FALSE
    """,
    """
    CREATE TABLE IF NOT EXISTS {table} USING DELTA AS
    SELECT
        CAST(NULL AS STRING) AS `id`,
        CAST(NULL AS STRING) AS `response_id`,
        CAST(NULL AS INT) AS `citation_index`,
        CAST(NULL AS STRING) AS `source_kind`,
        CAST(NULL AS STRING) AS `chunk_id`,
        CAST(NULL AS STRING) AS `knowledge_type`,
        CAST(NULL AS STRING) AS `knowledge_id`,
        CAST(NULL AS STRING) AS `document_id`,
        CAST(NULL AS STRING) AS `version_id`,
        CAST(NULL AS INT) AS `page_start`,
        CAST(NULL AS INT) AS `page_end`,
        CAST(NULL AS STRING) AS `section_number`,
        CAST(NULL AS STRING) AS `heading`,
        CAST(NULL AS STRING) AS `excerpt`,
        CAST(NULL AS DECIMAL(10,6)) AS `score`,
        CAST(NULL AS STRING) AS `quote`,
        CAST(NULL AS STRING) AS `metadata`,
        CAST(NULL AS TIMESTAMP) AS `created_at`
    WHERE FALSE
    """,
    """
    CREATE TABLE IF NOT EXISTS {table} USING DELTA AS
    SELECT
        CAST(NULL AS STRING) AS `id`,
        CAST(NULL AS STRING) AS `response_id`,
        CAST(NULL AS STRING) AS `user_id`,
        CAST(NULL AS INT) AS `rating`,
        CAST(NULL AS STRING) AS `comment`,
        CAST(NULL AS TIMESTAMP) AS `created_at`,
        CAST(NULL AS TIMESTAMP) AS `updated_at`
    WHERE FALSE
    """,
    """
    CREATE TABLE IF NOT EXISTS {table} USING DELTA AS
    SELECT
        CAST(NULL AS STRING) AS `id`,
        CAST(NULL AS STRING) AS `user_id`,
        CAST(NULL AS STRING) AS `session_id`,
        CAST(NULL AS STRING) AS `response_id`,
        CAST(NULL AS STRING) AS `question`,
        CAST(NULL AS STRING) AS `language`,
        CAST(NULL AS STRING) AS `reason`,
        CAST(NULL AS STRING) AS `retrieval`,
        CAST(NULL AS STRING) AS `status`,
        CAST(NULL AS STRING) AS `resolution`,
        CAST(NULL AS TIMESTAMP) AS `created_at`,
        CAST(NULL AS TIMESTAMP) AS `updated_at`,
        CAST(NULL AS TIMESTAMP) AS `resolved_at`,
        CAST(NULL AS STRING) AS `resolved_by`
    WHERE FALSE
    """,
    """
    CREATE TABLE IF NOT EXISTS {table} USING DELTA AS
    SELECT
        CAST(NULL AS STRING) AS `id`,
        CAST(NULL AS STRING) AS `document_version_id`,
        CAST(NULL AS STRING) AS `document_page_id`,
        CAST(NULL AS STRING) AS `provider`,
        CAST(NULL AS STRING) AS `model`,
        CAST(NULL AS STRING) AS `evaluation_type`,
        CAST(NULL AS DECIMAL(5,4)) AS `accuracy_score`,
        CAST(NULL AS DECIMAL(5,4)) AS `completeness_score`,
        CAST(NULL AS DECIMAL(5,4)) AS `visual_score`,
        CAST(NULL AS STRING) AS `metrics`,
        CAST(NULL AS STRING) AS `notes`,
        CAST(NULL AS STRING) AS `created_by`,
        CAST(NULL AS TIMESTAMP) AS `created_at`
    WHERE FALSE
    """,
    """
    CREATE TABLE IF NOT EXISTS {table} USING DELTA AS
    SELECT
        CAST(NULL AS STRING) AS `id`,
        CAST(NULL AS STRING) AS `name`,
        CAST(NULL AS STRING) AS `question`,
        CAST(NULL AS ARRAY<STRING>) AS `expected_keywords`,
        CAST(NULL AS ARRAY<STRING>) AS `expected_document_ids`,
        CAST(NULL AS STRING) AS `expected_answer`,
        CAST(NULL AS STRING) AS `language`,
        CAST(NULL AS ARRAY<STRING>) AS `tags`,
        CAST(NULL AS BOOLEAN) AS `active`,
        CAST(NULL AS STRING) AS `created_by`,
        CAST(NULL AS TIMESTAMP) AS `created_at`,
        CAST(NULL AS TIMESTAMP) AS `updated_at`
    WHERE FALSE
    """,
    """
    CREATE TABLE IF NOT EXISTS {table} USING DELTA AS
    SELECT
        CAST(NULL AS STRING) AS `id`,
        CAST(NULL AS STRING) AS `run_id`,
        CAST(NULL AS STRING) AS `eval_id`,
        CAST(NULL AS STRING) AS `response_id`,
        CAST(NULL AS BOOLEAN) AS `passed`,
        CAST(NULL AS BOOLEAN) AS `citation_valid`,
        CAST(NULL AS DECIMAL(5,4)) AS `keyword_recall`,
        CAST(NULL AS BOOLEAN) AS `document_match`,
        CAST(NULL AS STRING) AS `answer`,
        CAST(NULL AS STRING) AS `citations`,
        CAST(NULL AS STRING) AS `metrics`,
        CAST(NULL AS INT) AS `latency_ms`,
        CAST(NULL AS TIMESTAMP) AS `created_at`
    WHERE FALSE
    """,
    """
    CREATE TABLE IF NOT EXISTS {table} USING DELTA AS
    SELECT
        CAST(NULL AS STRING) AS `key`,
        CAST(NULL AS STRING) AS `value`,
        CAST(NULL AS TIMESTAMP) AS `updated_at`
    WHERE FALSE
    """,
)
