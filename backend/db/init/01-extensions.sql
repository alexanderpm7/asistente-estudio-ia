-- Se ejecuta automáticamente la primera vez que se crea el volumen de Postgres.
-- Habilita pgvector, el almacén vectorial del RAG.
CREATE EXTENSION IF NOT EXISTS vector;
