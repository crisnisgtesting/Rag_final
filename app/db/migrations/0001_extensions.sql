-- Required PostgreSQL extensions.
--
-- pgvector is required and must be installed.
CREATE EXTENSION IF NOT EXISTS vector;

-- pg_trgm and unaccent are used by later retrieval phases. Some minimal
-- development PostgreSQL distributions do not ship the contrib extensions, so
-- their absence is tolerated locally. Production must install contrib.
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS pg_trgm;
EXCEPTION
    WHEN feature_not_supported OR undefined_file THEN
        RAISE NOTICE 'pg_trgm is unavailable on this PostgreSQL installation';
END;
$$;

DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS unaccent;
EXCEPTION
    WHEN feature_not_supported OR undefined_file THEN
        RAISE NOTICE 'unaccent is unavailable on this PostgreSQL installation';
END;
$$;
