DROP INDEX IF EXISTS session_user_uuid_idx;

--bun:split
ALTER TABLE session
    DROP COLUMN IF EXISTS user_uuid;

