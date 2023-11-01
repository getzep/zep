DO $$
BEGIN
    IF EXISTS(
        SELECT
        FROM
            pg_tables
        WHERE
            tablename = 'message_embedding') THEN
    ALTER TABLE message_embedding
        ADD COLUMN IF NOT EXISTS embedding vector(1536);
END IF;
END
$$;


DO $$
BEGIN
    IF EXISTS(
        SELECT
        FROM
            pg_tables
        WHERE
            tablename = 'summary_embedding') THEN
    ALTER TABLE summary_embedding
        ADD COLUMN IF NOT EXISTS embedding vector(1536);
END IF;
END
$$;

