-- v4: pgvector extension for semantic search
--
-- ✅ Backward compatible — CREATE EXTENSION IF NOT EXISTS, ADD COLUMN IF NOT EXISTS,
--    CREATE INDEX (new column with NULL default, old queries unaffected)
--
-- Adds the pgvector extension and an embedding column to the memories
-- table for vector similarity search. The ivfflat index accelerates
-- approximate nearest-neighbor queries using cosine distance.

CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE memories ADD COLUMN IF NOT EXISTS embedding vector(1536);

CREATE INDEX IF NOT EXISTS idx_memories_embedding
    ON memories USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
