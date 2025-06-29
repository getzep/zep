CREATE TABLE IF NOT EXISTS "users"
(
    "uuid"         uuid        NOT NULL DEFAULT gen_random_uuid(),
    "id"           BIGSERIAL,
    "created_at"   timestamptz NOT NULL DEFAULT current_timestamp,
    "updated_at"   timestamptz          DEFAULT current_timestamp,
    "deleted_at"   timestamptz,
    "user_id"      VARCHAR     NOT NULL,
    "email"        VARCHAR,
    "first_name"   VARCHAR,
    "last_name"    VARCHAR,
    "project_uuid" uuid        NOT NULL,
    "metadata"     jsonb,
    PRIMARY KEY ("uuid"),
    UNIQUE ("user_id")
);

CREATE TYPE role_type_enum AS ENUM (
    'norole',
    'system',
    'assistant',
    'user',
    'function',
    'tool'
    );

CREATE TABLE IF NOT EXISTS "sessions"
(
    "uuid"         uuid        NOT NULL DEFAULT gen_random_uuid(),
    "id"           BIGSERIAL,
    "session_id"   VARCHAR     NOT NULL,
    "created_at"   timestamptz NOT NULL DEFAULT current_timestamp,
    "updated_at"   timestamptz NOT NULL DEFAULT current_timestamp,
    "deleted_at"   timestamptz,
    "ended_at"     timestamptz,
    "metadata"     jsonb,
    "user_id"      VARCHAR,
    "project_uuid" uuid        NOT NULL,
    PRIMARY KEY ("uuid"),
    UNIQUE ("session_id"),
    FOREIGN KEY ("user_id") REFERENCES "users" ("user_id") ON UPDATE NO ACTION ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS "messages"
(
    "uuid"         uuid        NOT NULL  DEFAULT gen_random_uuid(),
    "id"           BIGSERIAL,
    "created_at"   timestamptz NOT NULL  DEFAULT current_timestamp,
    "updated_at"   timestamptz           DEFAULT current_timestamp,
    "deleted_at"   timestamptz,
    "session_id"   VARCHAR     NOT NULL,
    "project_uuid" uuid        NOT NULL,
    "role"         VARCHAR     NOT NULL,
    "role_type"    role_type_enum DEFAULT 'norole',
    "content"      VARCHAR     NOT NULL,
    "token_count"  BIGINT      NOT NULL,
    "metadata"     jsonb,
    PRIMARY KEY ("uuid"),
    FOREIGN KEY ("session_id") REFERENCES "sessions" ("session_id") ON UPDATE NO ACTION ON DELETE CASCADE
);



CREATE INDEX IF NOT EXISTS "user_user_id_idx" ON "users" ("user_id");
CREATE INDEX IF NOT EXISTS "user_email_idx" ON "users" ("email");
CREATE INDEX IF NOT EXISTS "memstore_session_id_idx" ON "messages" ("session_id");
CREATE INDEX IF NOT EXISTS "memstore_id_idx" ON "messages" ("id");
CREATE INDEX IF NOT EXISTS "memstore_session_id_project_uuid_deleted_at_idx" ON "messages" ("session_id", "project_uuid", "deleted_at");
CREATE INDEX IF NOT EXISTS "session_user_id_idx" ON "sessions" ("user_id");
CREATE INDEX IF NOT EXISTS "session_id_project_uuid_deleted_at_idx" ON "sessions" ("session_id", "project_uuid", "deleted_at");
