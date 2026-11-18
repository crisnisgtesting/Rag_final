-- Authentication, authorization and audit trail.

CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TABLE IF NOT EXISTS roles (
    role_code   TEXT PRIMARY KEY,
    description TEXT NOT NULL DEFAULT ''
);

INSERT INTO roles (role_code, description) VALUES
    ('admin', 'Administrator'),
    ('user', 'Standard user')
ON CONFLICT (role_code) DO NOTHING;

CREATE TABLE IF NOT EXISTS users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username      TEXT NOT NULL,
    email         TEXT,
    full_name     TEXT,
    password_hash TEXT NOT NULL,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_users_username_lower
    ON users (lower(username));
CREATE UNIQUE INDEX IF NOT EXISTS ux_users_email_lower
    ON users (lower(email)) WHERE email IS NOT NULL;

DROP TRIGGER IF EXISTS trg_users_updated_at ON users;
CREATE TRIGGER trg_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS user_roles (
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role_code  TEXT NOT NULL REFERENCES roles(role_code) ON DELETE CASCADE,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, role_code)
);

CREATE TABLE IF NOT EXISTS api_tokens (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    token_prefix TEXT NOT NULL,
    token_hash   TEXT NOT NULL UNIQUE,
    scopes       JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ,
    revoked_at   TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_api_tokens_user ON api_tokens(user_id);

CREATE TABLE IF NOT EXISTS audit_log (
    id            BIGSERIAL PRIMARY KEY,
    actor_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    action        TEXT NOT NULL,
    entity_type   TEXT,
    entity_id     TEXT,
    request_id    TEXT,
    metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_audit_log_created_at ON audit_log(created_at DESC);
CREATE INDEX IF NOT EXISTS ix_audit_log_actor ON audit_log(actor_user_id);
