"""Recuperación de chunks relevantes del almacén vectorial pgvector.

Implementa la búsqueda por similitud coseno sobre document_chunks
filtrando por asignatura_id (RF-IA2, ARCHITECTURE §5) y, opcionalmente,
por documento_id (RF-IA6, agente de repaso).
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Documento, DocumentoChunk


async def retrieve_chunks(
    query_embedding: list[float],
    asignatura_id: uuid.UUID,
    session: AsyncSession,
    documento_id: uuid.UUID | None = None,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """Devuelve los top_k chunks más próximos al query_embedding (coseno).

    Cada elemento del resultado contiene:
      chunk_id, texto, chunk_index, doc_nombre, page, distance (0 = idéntico).
    Solo se devuelven chunks de la asignatura indicada (RNF-6). Si se indica
    documento_id, se restringe además a los chunks de ese documento (RF-IA6).
    """
    dist_expr = DocumentoChunk.embedding.cosine_distance(query_embedding)

    stmt = (
        select(
            DocumentoChunk.id,
            DocumentoChunk.texto,
            DocumentoChunk.chunk_index,
            DocumentoChunk.chunk_metadata,
            Documento.nombre.label("doc_nombre"),
            dist_expr.label("distance"),
        )
        .join(Documento, DocumentoChunk.documento_id == Documento.id)
        .where(DocumentoChunk.asignatura_id == asignatura_id)
        .order_by(dist_expr)
        .limit(top_k)
    )
    if documento_id is not None:
        stmt = stmt.where(DocumentoChunk.documento_id == documento_id)

    rows = (await session.execute(stmt)).all()

    return [
        {
            "chunk_id": str(row.id),
            "texto": row.texto,
            "chunk_index": row.chunk_index,
            "doc_nombre": row.doc_nombre,
            "page": (row.chunk_metadata or {}).get("page", 1),
            "distance": float(row.distance),
            # pgvector devuelve distancia coseno; la convertimos en similitud.
            "similarity": 1.0 - float(row.distance),
        }
        for row in rows
    ]
