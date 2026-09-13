"""
Tests del módulo de retrieval RAG — SPEC §RF-IA2, ARCHITECTURE §5.

Estrategia: se insertan DocumentoChunk con embeddings sintéticos conocidos
(vectores unitarios ortogonales) y se verifica que la búsqueda coseno devuelve
el chunk correcto como top-1. Sin Ollama: los embeddings son literales de prueba.

TDD: tests escritos antes que la implementación; deben fallar en rojo.
"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Asignatura, Documento, DocumentoChunk, RolUsuario, Usuario
from app.rag.retrieval import retrieve_chunks

_DIM = 1024  # Dimensión de BGE-M3


# ── helpers ────────────────────────────────────────────────────────────────────


def _unit_vec(pos: int, dim: int = _DIM) -> list[float]:
    """Vector unitario con 1.0 en la posición `pos` y 0 en el resto."""
    v = [0.0] * dim
    v[pos] = 1.0
    return v


async def _setup_chunks(
    session: AsyncSession,
    n_chunks: int = 3,
) -> tuple[uuid.UUID, list[uuid.UUID]]:
    """Crea asignatura + documento + n_chunks con embeddings unitarios ortogonales.

    Devuelve (asignatura_id, [chunk_id_0, chunk_id_1, ...]).
    chunk_i tiene embedding = _unit_vec(i), i.e. máxima similitud coseno
    con _unit_vec(i) y mínima con cualquier otro.
    """
    prof = Usuario(
        email=f"prof_ret_{uuid.uuid4().hex[:6]}@test.es",
        password_hash="x",
        nombre="Prof Retrieval",
        rol=RolUsuario.profesor,
    )
    session.add(prof)
    await session.flush()

    asig = Asignatura(nombre="Retrieval Test", descripcion=None, profesor_id=prof.id)
    session.add(asig)
    await session.flush()

    doc = Documento(
        asignatura_id=asig.id,
        nombre="apuntes_retrieval.pdf",
        ruta="/uploads/apuntes_retrieval.pdf",
    )
    session.add(doc)
    await session.flush()

    chunk_ids = []
    for i in range(n_chunks):
        chunk = DocumentoChunk(
            documento_id=doc.id,
            asignatura_id=asig.id,
            chunk_index=i,
            texto=f"Texto del chunk número {i}",
            embedding=_unit_vec(i),
            chunk_metadata={"page": i + 1, "chunk_start": i * 370},
        )
        session.add(chunk)
        await session.flush()
        chunk_ids.append(chunk.id)

    return asig.id, chunk_ids


# ── tests ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_retrieve_devuelve_top1_correcto(db_session: AsyncSession) -> None:
    """
    Con 3 chunks de embeddings ortogonales, querying con _unit_vec(1)
    devuelve chunk_index=1 como top-1 con distancia coseno ≈ 0.
    Verifica RF-IA2 (recuperación filtrada por asignatura).
    """
    asig_id, _ = await _setup_chunks(db_session, n_chunks=3)

    # Consulta idéntica al embedding del chunk 1 → distancia coseno = 0
    results = await retrieve_chunks(
        query_embedding=_unit_vec(1),
        asignatura_id=asig_id,
        session=db_session,
    )

    assert len(results) > 0, "La búsqueda no devolvió resultados"
    top1 = results[0]
    assert top1["chunk_index"] == 1, (
        f"Se esperaba chunk_index=1 en top-1, se obtuvo {top1['chunk_index']}"
    )
    assert top1["distance"] < 0.01, f"Distancia coseno esperada ≈0, obtenida {top1['distance']:.4f}"


@pytest.mark.asyncio
async def test_retrieve_devuelve_maximo_top_k(db_session: AsyncSession) -> None:
    """Con 3 chunks y top_k=2, se devuelven exactamente 2 resultados."""
    asig_id, _ = await _setup_chunks(db_session, n_chunks=3)

    results = await retrieve_chunks(
        query_embedding=_unit_vec(0),
        asignatura_id=asig_id,
        session=db_session,
        top_k=2,
    )

    assert len(results) == 2


@pytest.mark.asyncio
async def test_retrieve_incluye_nombre_documento(db_session: AsyncSession) -> None:
    """Cada resultado incluye el nombre del documento (para las citas, RF-E5)."""
    asig_id, _ = await _setup_chunks(db_session)

    results = await retrieve_chunks(
        query_embedding=_unit_vec(0),
        asignatura_id=asig_id,
        session=db_session,
    )

    for chunk in results:
        assert chunk["doc_nombre"] == "apuntes_retrieval.pdf"
        assert "page" in chunk
        assert isinstance(chunk["page"], int)


@pytest.mark.asyncio
async def test_retrieve_filtra_por_asignatura(db_session: AsyncSession) -> None:
    """
    Los chunks de una asignatura no aparecen en la búsqueda de otra.
    Verifica la autorización a nivel de datos (RNF-6).
    """
    asig_a_id, _ = await _setup_chunks(db_session, n_chunks=2)
    asig_b_id, _ = await _setup_chunks(db_session, n_chunks=2)

    results_a = await retrieve_chunks(
        query_embedding=_unit_vec(0),
        asignatura_id=asig_a_id,
        session=db_session,
        top_k=10,
    )
    results_b = await retrieve_chunks(
        query_embedding=_unit_vec(0),
        asignatura_id=asig_b_id,
        session=db_session,
        top_k=10,
    )

    chunk_ids_a = {r["chunk_id"] for r in results_a}
    chunk_ids_b = {r["chunk_id"] for r in results_b}
    assert chunk_ids_a.isdisjoint(chunk_ids_b), (
        "Los chunks de asignatura A aparecen en resultados de asignatura B"
    )


@pytest.mark.asyncio
async def test_retrieve_sin_chunks_devuelve_lista_vacia(db_session: AsyncSession) -> None:
    """Una asignatura sin documentos indexados devuelve lista vacía (sin error)."""
    prof = Usuario(
        email=f"prof_empty_{uuid.uuid4().hex[:6]}@test.es",
        password_hash="x",
        nombre="Prof Empty",
        rol=RolUsuario.profesor,
    )
    db_session.add(prof)
    await db_session.flush()

    asig = Asignatura(nombre="Sin docs", descripcion=None, profesor_id=prof.id)
    db_session.add(asig)
    await db_session.flush()

    results = await retrieve_chunks(
        query_embedding=_unit_vec(0),
        asignatura_id=asig.id,
        session=db_session,
    )

    assert results == []
