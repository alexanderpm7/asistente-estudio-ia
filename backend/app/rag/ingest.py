"""Pipeline de ingesta de documentos PDF para el RAG.

Flujo: extracción de texto (PyMuPDF) → chunking por página →
embeddings BGE-M3 vía Ollama (en batches) → persistencia en document_chunks.
"""

import uuid
from typing import Any

import fitz  # PyMuPDF
from langchain_ollama import OllamaEmbeddings
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import DocumentoChunk

EMBED_BATCH_SIZE: int = 8


def _extract_pages(pdf_bytes: bytes) -> list[tuple[str, int]]:
    """Extrae el texto de cada página del PDF.

    Devuelve lista de (texto_de_la_página, número_de_página_1based).
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    return [(page.get_text(), i + 1) for i, page in enumerate(doc)]


def _make_chunks_by_page(pages: list[tuple[str, int]]) -> list[dict[str, Any]]:
    """Una página del PDF = un chunk. Sin solape. Páginas vacías se descartan."""
    return [
        {"text": text, "metadata": {"page": page_num, "chunk_start": 0}}
        for text, page_num in pages
        if text.strip()
    ]


async def _embed_batch(texts: list[str]) -> list[list[float]]:
    """Genera embeddings BGE-M3 vía Ollama para un lote de textos."""
    embedder = OllamaEmbeddings(
        model=settings.embedding_model,
        base_url=settings.ollama_host,
    )
    result: list[list[float]] = await embedder.aembed_documents(texts)
    return result


async def ingest_document(
    *,
    pdf_bytes: bytes,
    documento_id: uuid.UUID,
    asignatura_id: uuid.UUID,
    session: AsyncSession,
) -> int:
    """Procesa un PDF y persiste los chunks con embeddings en document_chunks.

    Devuelve el número de chunks creados (0 si el PDF no contiene texto).
    """
    pages = _extract_pages(pdf_bytes)
    chunks = _make_chunks_by_page(pages)

    if not chunks:
        return 0

    texts = [c["text"] for c in chunks]

    # Procesa por lotes para no saturar Ollama.
    all_embeddings: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[start : start + EMBED_BATCH_SIZE]
        embeddings = await _embed_batch(batch)
        all_embeddings.extend(embeddings)

    for idx, (chunk, embedding) in enumerate(zip(chunks, all_embeddings, strict=True)):
        session.add(
            DocumentoChunk(
                documento_id=documento_id,
                asignatura_id=asignatura_id,
                chunk_index=idx,
                texto=chunk["text"],
                embedding=embedding,
                chunk_metadata=chunk["metadata"],
            )
        )

    await session.flush()
    return len(chunks)
