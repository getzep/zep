ALTER TABLE document_collection
    ADD COLUMN IF NOT EXISTS index_type text;
