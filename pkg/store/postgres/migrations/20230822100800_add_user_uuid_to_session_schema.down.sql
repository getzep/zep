DROP INDEX IF EXISTS session_user_id_idx;

--bun:split
ALTER TABLE session
    DROP COLUMN IF EXISTS user_id;

--bun:split
ALTER TABLE session
    DROP COLUMN IF EXISTS id;

