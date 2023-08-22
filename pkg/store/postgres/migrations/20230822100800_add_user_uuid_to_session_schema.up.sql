DO $$
BEGIN
    IF NOT EXISTS(
        SELECT
            1
        FROM
            information_schema.columns
        WHERE
            table_name = 'session'
            AND column_name = 'user_uuid') THEN
    ALTER TABLE session
        ADD COLUMN user_uuid UUID;
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
            AND indexname = 'session_user_uuid_idx') THEN
    CREATE INDEX session_user_uuid_idx ON session(user_uuid);
END IF;
END
$$;

