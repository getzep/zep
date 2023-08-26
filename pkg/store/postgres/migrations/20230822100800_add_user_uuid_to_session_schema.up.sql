DO $$
BEGIN
    IF NOT EXISTS(
        SELECT
            1
        FROM
            information_schema.columns
        WHERE
            table_name = 'session'
            AND column_name = 'user_id') THEN
    ALTER TABLE session
        ADD COLUMN user_id UUID;
END IF;
END
$$;

--bun:split
DO $$
BEGIN
    IF NOT EXISTS(
        SELECT
            1
        FROM
            pg_indexes
        WHERE
            tablename = 'session'
            AND indexname = 'session_user_id_idx') THEN
    CREATE INDEX session_user_id_idx ON session(user_id);
END IF;
END
$$;

--bun:split
DO $$
BEGIN
    IF NOT EXISTS(
        SELECT
            1
        FROM
            information_schema.columns
        WHERE
            table_name = 'session'
            AND column_name = 'id') THEN
    ALTER TABLE session
        ADD COLUMN id BIGSERIAL;
END IF;
END
$$;

