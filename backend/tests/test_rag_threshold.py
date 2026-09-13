"""
Tests del umbral de similitud del Agente RAG — SPEC §RF-IA2, ARCHITECTURE §3.

Contrato:
  - El Agente RAG recupera chunks con la consulta_reescrita.
  - Calcula la similitud coseno del top-1 (similarity = 1 − distance).
  - Si similarity >= RAG_RELEVANCE_THRESHOLD → uso_rag=True, docs_recuperados poblado.
  - Si similarity < RAG_RELEVANCE_THRESHOLD  → uso_rag=False, docs_recuperados vacío.
  - Retrieval vacío (asignatura sin docs)    → uso_rag=False, docs_recuperados vacío.
  - uso_rag siempre presente en el estado de salida.

Estrategia de test:
  - Tests unitarios del nodo invocado directamente (sin Ollama, sin BD).
  - Se mockean _agente_rag_embedder, retrieve_chunks y AsyncSessionLocal.
"""

import uuid
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage

# ── helpers ─────────────────────────────────────────────────────────────────────

_ASIG_ID = str(uuid.uuid4())


def _estado(pregunta: str = "¿qué es un componente?") -> dict[str, Any]:
    return {
        "messages": [HumanMessage(content=pregunta)],
        "asignatura_id": _ASIG_ID,
        "consulta_reescrita": pregunta,
    }


def _chunk(similarity: float, texto: str = "fragmento de ejemplo") -> dict[str, Any]:
    """Devuelve un chunk sintético con el campo similarity dado."""
    return {
        "chunk_id": str(uuid.uuid4()),
        "texto": texto,
        "chunk_index": 0,
        "doc_nombre": "apuntes.pdf",
        "page": 1,
        "distance": 1.0 - similarity,
        "similarity": similarity,
    }


def _mock_agente_rag_deps(chunks: list[dict[str, Any]]) -> tuple[MagicMock, AsyncMock]:
    """Devuelve (mock_embedder, mock_retrieve) configurados para el test."""
    mock_embedder = MagicMock()
    mock_embedder.aembed_query = AsyncMock(return_value=[0.1] * 1024)

    mock_retrieve = AsyncMock(return_value=chunks)
    return mock_embedder, mock_retrieve


@asynccontextmanager
async def _fake_session_ctx(*_args: Any, **_kwargs: Any):  # type: ignore[misc]
    """Reemplaza AsyncSessionLocal() para evitar conexión a BD real."""
    yield MagicMock()


# ── tests ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_similitud_sobre_umbral_activa_rag() -> None:
    """Chunk con similarity > threshold → uso_rag=True y docs_recuperados poblado."""
    from app.agents.graph import _build_agente_rag_node
    from app.core.config import settings

    similarity_alta = settings.rag_relevance_threshold + 0.1
    chunks = [_chunk(similarity_alta, "contenido relevante")]
    mock_embedder, mock_retrieve = _mock_agente_rag_deps(chunks)

    nodo = _build_agente_rag_node()

    with (
        patch("app.agents.graph._agente_rag_embedder", mock_embedder),
        patch("app.agents.graph.retrieve_chunks", mock_retrieve),
        patch("app.agents.graph.AsyncSessionLocal", _fake_session_ctx),
    ):
        resultado = await nodo(_estado())

    assert resultado["uso_rag"] is True
    assert len(resultado["docs_recuperados"]) == 1
    assert resultado["docs_recuperados"][0]["texto"] == "contenido relevante"


@pytest.mark.asyncio
async def test_similitud_bajo_umbral_desactiva_rag() -> None:
    """Chunk con similarity < threshold → uso_rag=False y docs_recuperados vacío."""
    from app.agents.graph import _build_agente_rag_node
    from app.core.config import settings

    similarity_baja = settings.rag_relevance_threshold - 0.1
    chunks = [_chunk(similarity_baja, "contenido poco relevante")]
    mock_embedder, mock_retrieve = _mock_agente_rag_deps(chunks)

    nodo = _build_agente_rag_node()

    with (
        patch("app.agents.graph._agente_rag_embedder", mock_embedder),
        patch("app.agents.graph.retrieve_chunks", mock_retrieve),
        patch("app.agents.graph.AsyncSessionLocal", _fake_session_ctx),
    ):
        resultado = await nodo(_estado())

    assert resultado["uso_rag"] is False
    assert resultado["docs_recuperados"] == []


@pytest.mark.asyncio
async def test_similitud_igual_al_umbral_activa_rag() -> None:
    """Similitud exactamente igual al umbral activa RAG (condición >=)."""
    from app.agents.graph import _build_agente_rag_node
    from app.core.config import settings

    chunks = [_chunk(settings.rag_relevance_threshold)]
    mock_embedder, mock_retrieve = _mock_agente_rag_deps(chunks)

    nodo = _build_agente_rag_node()

    with (
        patch("app.agents.graph._agente_rag_embedder", mock_embedder),
        patch("app.agents.graph.retrieve_chunks", mock_retrieve),
        patch("app.agents.graph.AsyncSessionLocal", _fake_session_ctx),
    ):
        resultado = await nodo(_estado())

    assert resultado["uso_rag"] is True


@pytest.mark.asyncio
async def test_retrieval_vacio_desactiva_rag() -> None:
    """Asignatura sin documentos indexados → uso_rag=False, docs_recuperados vacío."""
    from app.agents.graph import _build_agente_rag_node

    mock_embedder, mock_retrieve = _mock_agente_rag_deps([])

    nodo = _build_agente_rag_node()

    with (
        patch("app.agents.graph._agente_rag_embedder", mock_embedder),
        patch("app.agents.graph.retrieve_chunks", mock_retrieve),
        patch("app.agents.graph.AsyncSessionLocal", _fake_session_ctx),
    ):
        resultado = await nodo(_estado())

    assert resultado["uso_rag"] is False
    assert resultado["docs_recuperados"] == []


@pytest.mark.asyncio
async def test_uso_rag_siempre_presente_en_estado() -> None:
    """uso_rag está siempre en el resultado, independientemente de la ruta."""
    from app.agents.graph import _build_agente_rag_node
    from app.core.config import settings

    nodo = _build_agente_rag_node()

    # Caso: sin asignatura_id
    estado_sin_asig = {"messages": [HumanMessage(content="¿hola?")]}
    resultado_sin_asig = await nodo(estado_sin_asig)
    assert "uso_rag" in resultado_sin_asig
    assert resultado_sin_asig["uso_rag"] is False

    # Caso: con embedder None
    with patch("app.agents.graph._agente_rag_embedder", None):
        resultado_sin_embedder = await nodo(_estado())
    assert "uso_rag" in resultado_sin_embedder
    assert resultado_sin_embedder["uso_rag"] is False

    # Caso: con retrieval por encima del umbral
    similarity_alta = settings.rag_relevance_threshold + 0.1
    mock_embedder, mock_retrieve = _mock_agente_rag_deps([_chunk(similarity_alta)])
    with (
        patch("app.agents.graph._agente_rag_embedder", mock_embedder),
        patch("app.agents.graph.retrieve_chunks", mock_retrieve),
        patch("app.agents.graph.AsyncSessionLocal", _fake_session_ctx),
    ):
        resultado_con_docs = await nodo(_estado())
    assert "uso_rag" in resultado_con_docs
    assert resultado_con_docs["uso_rag"] is True
