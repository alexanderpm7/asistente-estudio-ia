"""
Tests del pipeline de ingesta de documentos PDF — SPEC §RF-P4, ARCHITECTURE §5 (RAG).

Contrato esperado de `ingest_document`:
  - Recibe bytes de un PDF, IDs de documento/asignatura y la sesión de BD.
  - Extrae texto, trocea en chunks por página (una página = un chunk, sin solape),
    genera embeddings con BGE-M3 (mockeados en test) en batches de 8 y persiste
    en document_chunks.
  - Devuelve el número de chunks creados (>0 para un PDF con contenido).
  - Cada chunk tiene: texto no vacío, embedding de 1024 dimensiones, asignatura_id
    correcto y chunk_index secuencial.

TDD: el test se escribe antes que la implementación; debe fallar en rojo.
"""

import uuid
from unittest.mock import AsyncMock, patch

import fitz  # PyMuPDF
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Asignatura, Documento, DocumentoChunk, RolUsuario, Usuario

# ── helpers ────────────────────────────────────────────────────────────────────


def _make_pdf(text: str) -> bytes:
    """Crea un PDF sintético de una página con el texto dado."""
    doc = fitz.open()
    page = doc.new_page()
    rect = fitz.Rect(36, 36, 559, 806)  # A4 menos márgenes
    page.insert_textbox(rect, text, fontsize=10)
    return doc.tobytes()


def _make_pdf_multipage(*pages: str) -> bytes:
    """Crea un PDF sintético con una página por cada texto dado."""
    doc = fitz.open()
    rect = fitz.Rect(36, 36, 559, 806)
    for text in pages:
        page = doc.new_page()
        page.insert_textbox(rect, text, fontsize=10)
    return doc.tobytes()


async def _fake_embed(texts: list[str]) -> list[list[float]]:
    """Sustituto del embedder de Ollama: devuelve vectores unitarios de 1024 dims."""
    return [[0.1] * 1024 for _ in texts]


# ── fixtures de BD ─────────────────────────────────────────────────────────────


async def _setup_entities(
    session: AsyncSession,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Crea un profesor, una asignatura y un documento en la BD de test.

    Devuelve (documento_id, asignatura_id).
    """
    prof = Usuario(
        email="prof_ingest@test.es",
        password_hash="x",
        nombre="Prof Ingesta",
        rol=RolUsuario.profesor,
    )
    session.add(prof)
    await session.flush()

    asig = Asignatura(nombre="Física I", descripcion=None, profesor_id=prof.id)
    session.add(asig)
    await session.flush()

    doc = Documento(
        asignatura_id=asig.id,
        nombre="apuntes_fisica.pdf",
        ruta="/uploads/apuntes_fisica.pdf",
    )
    session.add(doc)
    await session.flush()

    return doc.id, asig.id


# ── tests ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ingest_crea_chunks_con_embeddings(db_session: AsyncSession) -> None:
    """
    Un PDF con contenido suficiente produce al menos un DocumentoChunk con
    texto no vacío, embedding de 1024 dimensiones y asignatura_id correcto.
    Verifica RF-P4 (indexación automática al subir documento).
    """
    from app.rag.ingest import ingest_document

    documento_id, asignatura_id = await _setup_entities(db_session)

    # PDF sintético: ~500 palabras → esperar ≥1 chunk
    pdf_bytes = _make_pdf(("palabra " * 100 + "\n") * 5)

    with patch("app.rag.ingest._embed_batch", new=AsyncMock(side_effect=_fake_embed)):
        n_chunks = await ingest_document(
            pdf_bytes=pdf_bytes,
            documento_id=documento_id,
            asignatura_id=asignatura_id,
            session=db_session,
        )

    assert n_chunks > 0, "Se esperaba al menos un chunk para un PDF con contenido"

    result = await db_session.execute(
        select(DocumentoChunk).where(DocumentoChunk.documento_id == documento_id)
    )
    chunks = list(result.scalars().all())

    assert len(chunks) == n_chunks

    for chunk in chunks:
        assert chunk.texto.strip(), "El texto del chunk no debe estar vacío"
        assert chunk.embedding is not None, "El embedding no debe ser None"
        assert len(chunk.embedding) == 1024, "BGE-M3 produce vectores de 1024 dims"
        assert chunk.asignatura_id == asignatura_id
        assert chunk.documento_id == documento_id


@pytest.mark.asyncio
async def test_ingest_chunks_son_secuenciales(db_session: AsyncSession) -> None:
    """
    Los chunk_index se asignan 0, 1, 2, … sin huecos.
    """
    from app.rag.ingest import ingest_document

    documento_id, asignatura_id = await _setup_entities(db_session)

    # PDF de 2 páginas → ≥2 chunks con strategy="page" (o con strategy="size")
    pdf_bytes = _make_pdf_multipage(
        "texto de prueba para chunking " * 34,
        "segunda página de prueba para chunking " * 34,
    )

    with patch("app.rag.ingest._embed_batch", new=AsyncMock(side_effect=_fake_embed)):
        n_chunks = await ingest_document(
            pdf_bytes=pdf_bytes,
            documento_id=documento_id,
            asignatura_id=asignatura_id,
            session=db_session,
        )

    assert n_chunks >= 2, f"Se esperaban ≥2 chunks, se obtuvieron {n_chunks}"

    result = await db_session.execute(
        select(DocumentoChunk)
        .where(DocumentoChunk.documento_id == documento_id)
        .order_by(DocumentoChunk.chunk_index)
    )
    chunks = list(result.scalars().all())
    indices = [c.chunk_index for c in chunks]
    expected = list(range(n_chunks))
    assert indices == expected, f"Índices esperados 0..{n_chunks - 1}, obtenidos {indices}"


@pytest.mark.asyncio
async def test_ingest_pdf_vacio_devuelve_cero_chunks(db_session: AsyncSession) -> None:
    """
    Un PDF sin texto no debe crear ningún chunk ni lanzar excepción.
    """
    from app.rag.ingest import ingest_document

    documento_id, asignatura_id = await _setup_entities(db_session)

    # PDF vacío (página en blanco)
    pdf_bytes = _make_pdf("")

    with patch("app.rag.ingest._embed_batch", new=AsyncMock(side_effect=_fake_embed)):
        n_chunks = await ingest_document(
            pdf_bytes=pdf_bytes,
            documento_id=documento_id,
            asignatura_id=asignatura_id,
            session=db_session,
        )

    assert n_chunks == 0

    result = await db_session.execute(
        select(DocumentoChunk).where(DocumentoChunk.documento_id == documento_id)
    )
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_ingest_llama_embed_en_batches(db_session: AsyncSession) -> None:
    """
    Con más de EMBED_BATCH_SIZE chunks, _embed_batch se llama varias veces
    (una por batch), no una sola vez con todos los chunks.
    """
    from app.rag.ingest import EMBED_BATCH_SIZE, ingest_document

    documento_id, asignatura_id = await _setup_entities(db_session)

    # PDF muy largo para garantizar >EMBED_BATCH_SIZE chunks
    # CHUNK_SIZE_WORDS=370, necesitamos >8 chunks → >8*370=2960 palabras únicas
    pdf_bytes = _make_pdf(("token " * 370 + "\n") * (EMBED_BATCH_SIZE + 2))

    mock_embed = AsyncMock(side_effect=_fake_embed)
    with patch("app.rag.ingest._embed_batch", new=mock_embed):
        n_chunks = await ingest_document(
            pdf_bytes=pdf_bytes,
            documento_id=documento_id,
            asignatura_id=asignatura_id,
            session=db_session,
        )

    expected_calls = (n_chunks + EMBED_BATCH_SIZE - 1) // EMBED_BATCH_SIZE
    assert mock_embed.call_count == expected_calls, (
        f"Se esperaban {expected_calls} llamadas a _embed_batch "
        f"para {n_chunks} chunks con batch_size={EMBED_BATCH_SIZE}, "
        f"se obtuvieron {mock_embed.call_count}"
    )
