ALTER TABLE document_collection
    ADD COLUMN IF NOT EXISTS index_type text;

UPDATE document_collection
    SET index_type = 'ivfflat'
    WHERE index_type IS NULL;

