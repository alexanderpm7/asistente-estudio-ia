"""
Endpoint del agente de repaso — SPEC §RF-E6, RF-IA6, CU-9.

POST /asignaturas/{id}/repaso
  - Solo alumnos matriculados en la asignatura (RNF-6).
  - Requiere el query param documento_id: el alumno elige un documento
    concreto de la asignatura; 404 si no existe o no pertenece a ella.
  - Recupera los top-10 chunks de ese documento con una consulta genérica
    de resumen.
  - Llama al LLM con with_structured_output (temperatura 0, máx 800 tokens).
  - Devuelve JSON con flashcards y preguntas de práctica.
  - 400 si el documento no tiene chunks indexados.

Separado del grafo conversacional de chat porque es una generación estructurada
puntual, sin checkpointer ni thread_id.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from langchain_core.messages import HumanMessage
from langchain_ollama import OllamaEmbeddings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_current_user
from app.core.llm_factory import get_llm_client
from app.db.models import Asignatura, Documento, Matricula, RolUsuario, Usuario
from app.db.session import get_session
from app.rag.retrieval import retrieve_chunks
from app.schemas.repaso import Flashcard, Pregunta, RepasoResponse

router = APIRouter(prefix="/asignaturas", tags=["repaso"])

_REPASO_QUERY = (
    "Resumen general de los conceptos, definiciones y temas principales de la asignatura"
)
_TOP_K_REPASO = 10


async def _generar_repaso_llm(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    """Llama al LLM con with_structured_output para generar flashcards y preguntas.

    Temperatura 0 y num_predict=800 según SPEC §8. Lanza excepción si Ollama
    no está disponible; el endpoint la convierte en HTTP 503.
    """
    from pydantic import BaseModel as _Base

    class _Flashcard(_Base):
        anverso: str
        reverso: str

    class _Pregunta(_Base):
        enunciado: str
        opciones: list[str]
        respuesta_correcta: int

    class _RepasoOutput(_Base):
        flashcards: list[_Flashcard]
        preguntas: list[_Pregunta]

    contexto = "\n\n".join(
        f"[{c['doc_nombre']} p.{c.get('page', '?')}]: {c['texto'][:500]}" for c in chunks
    )
    prompt = (
        "Eres un asistente educativo. A partir de los siguientes fragmentos de apuntes, "
        "genera exactamente 5  flashcards y 5 preguntas de práctica "
        "tipo test con 4 opciones cada una.\n\n"
        f"Fragmentos:\n{contexto}\n\n"
        "Responde en español. Genera contenido educativo útil y variado."
    )

    llm = get_llm_client(temperature=0, max_output_tokens=800)
    structured = llm.with_structured_output(_RepasoOutput)
    result: _RepasoOutput = await structured.ainvoke([HumanMessage(content=prompt)])  # type: ignore[assignment]
    return result.model_dump()


@router.post(
    "/{asignatura_id}/repaso",
    response_model=RepasoResponse,
    status_code=status.HTTP_200_OK,
)
async def generar_repaso(
    asignatura_id: uuid.UUID,
    documento_id: uuid.UUID = Query(...),
    session: AsyncSession = Depends(get_session),
    current_user: Usuario = Depends(get_current_user),
) -> RepasoResponse:
    """Genera flashcards y preguntas de práctica para un documento.

    Solo accesible por alumnos matriculados (RNF-6). El documento_id debe
    pertenecer a la asignatura (404 si no). Devuelve HTTP 400 si el
    documento no tiene chunks indexados.
    """
    result = await session.execute(select(Asignatura).where(Asignatura.id == asignatura_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asignatura no encontrada.",
        )

    if current_user.rol != RolUsuario.alumno:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo los alumnos pueden generar el repaso.",
        )
    mat = await session.execute(
        select(Matricula).where(
            Matricula.alumno_id == current_user.id,
            Matricula.asignatura_id == asignatura_id,
        )
    )
    if mat.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No estás matriculado en esta asignatura.",
        )

    doc_result = await session.execute(
        select(Documento).where(
            Documento.id == documento_id,
            Documento.asignatura_id == asignatura_id,
        )
    )
    if doc_result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Documento no encontrado en esta asignatura.",
        )

    embedder = OllamaEmbeddings(
        model=settings.embedding_model,
        base_url=settings.ollama_host,
    )
    query_embedding: list[float] = await embedder.aembed_query(_REPASO_QUERY)
    chunks = await retrieve_chunks(
        query_embedding=query_embedding,
        asignatura_id=asignatura_id,
        session=session,
        documento_id=documento_id,
        top_k=_TOP_K_REPASO,
    )

    if not chunks:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=("El documento no tiene contenido indexado. "),
        )

    try:
        salida = await _generar_repaso_llm(chunks)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El servicio de IA no está disponible. Inténtalo de nuevo más tarde.",
        ) from exc

    flashcards = [Flashcard(**fc) for fc in salida.get("flashcards", [])]
    preguntas = [Pregunta(**pq) for pq in salida.get("preguntas", [])]

    return RepasoResponse(
        asignatura_id=asignatura_id,
        flashcards=flashcards,
        preguntas=preguntas,
    )
