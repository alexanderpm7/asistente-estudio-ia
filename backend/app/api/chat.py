"""
Router de chat — SPEC §RF-E3, CU-4, CU-5.

POST /chat/{asignatura_id}
  - Verifica que el alumno está matriculado en la asignatura (RNF-6).
  - Invoca el grafo de agentes con astream_events() para obtener tokens.
  - Devuelve un stream SSE (text/event-stream): data: <token>
  - Al terminar persiste usuario→mensajes y assistant→mensajes en la BD.
  - Registra la traza en Langfuse con spans por nodo y metadata (ARCHITECTURE §8).
"""

import time
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from langchain_core.messages import HumanMessage
from langfuse import Langfuse
from langfuse.client import StatefulSpanClient, StatefulTraceClient
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.agents.graph import build_graph, pg_conn_string
from app.core.config import settings
from app.core.deps import get_current_user
from app.core.llm_factory import ollama_disponible
from app.db.models import Asignatura, Documento, Matricula, Mensaje, RolMensaje, RolUsuario, Usuario
from app.db.pagination import paginar_mensajes
from app.db.session import get_session
from app.schemas.chat import ChatRequest
from app.schemas.mensajes import HistorialResponse

router = APIRouter(prefix="/chat", tags=["chat"])

# Cliente Langfuse con lazy-init; None si las credenciales no están configuradas.
# Se inicializa una sola vez por proceso (no por petición).
_langfuse: Langfuse | None = None


def _get_langfuse() -> Langfuse | None:
    """Devuelve el cliente Langfuse o None si las credenciales no están configuradas."""
    global _langfuse
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        return None
    if _langfuse is None:
        _langfuse = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
    return _langfuse


async def _verificar_acceso_alumno(
    current_user: Usuario,
    asignatura_id: uuid.UUID,
    session: AsyncSession,
) -> None:
    """404 si la asignatura no existe; 403 si el usuario no es alumno matriculado."""
    result = await session.execute(select(Asignatura).where(Asignatura.id == asignatura_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asignatura no encontrada.",
        )
    if current_user.rol != RolUsuario.alumno:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo los alumnos pueden usar el chat.",
        )
    result = await session.execute(
        select(Matricula).where(
            Matricula.alumno_id == current_user.id,
            Matricula.asignatura_id == asignatura_id,
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No estás matriculado en esta asignatura.",
        )


def _build_input_msg(
    mensaje_texto: str,
    asignatura_id: uuid.UUID,
    descripcion_asignatura: str,
    titulos_documentos: list[str],
    prompt_redactor: str,
) -> dict[str, Any]:
    """Construye el estado inicial de una invocación del grafo.

    Reinicia explícitamente el estado de trabajo de ESTE turno (`iteracion`,
    `feedback_revisor`, `borrador`, `docs_recuperados`, `consulta_reescrita`,
    `uso_rag`): sin esto, LangGraph los hereda del último checkpoint
    persistido del `thread_id` (el turno anterior de la misma conversación),
    no de un estado vacío. El caso crítico es `iteracion`: si queda un valor
    >= `max_revision_iterations` de un turno previo, `_decide_ruta_revisor`
    entra directamente en el tope duro (RNF-5) y el Revisor aprueba sin
    evaluar fidelidad real — bug reproducido en test_estado_turno.py.

    `messages` NO se reinicia aquí: su reducer `add_messages` es la memoria
    real de la conversación (RF-IA5) y debe acumularse entre turnos.
    """
    return {
        "messages": [HumanMessage(content=mensaje_texto)],
        "asignatura_id": str(asignatura_id),
        # Contexto opcional para el reformulador.
        "descripcion_asignatura": descripcion_asignatura,
        "titulos_documentos": titulos_documentos,
        # Prompt de estilo del redactor.
        "prompt_redactor": prompt_redactor,
        # Estado de trabajo del turno — ver docstring.
        "consulta_reescrita": "",
        "docs_recuperados": [],
        "uso_rag": False,
        "borrador": "",
        "feedback_revisor": "",
        "iteracion": 0,
    }


@router.post("/{asignatura_id}")
async def chat(
    asignatura_id: uuid.UUID,
    body: ChatRequest,
    session: AsyncSession = Depends(get_session),
    current_user: Usuario = Depends(get_current_user),
) -> Response:
    await _verificar_acceso_alumno(current_user, asignatura_id, session)

    # Evita abrir un SSE que se cortaría si Ollama no está disponible.
    if settings.llm_provider == "ollama" and not await ollama_disponible():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El asistente no está disponible en este momento. "
            "Inténtalo de nuevo en unos minutos.",
        )

    # Capturamos valores primitivos para el closure; no pasamos objetos ORM.
    user_id: uuid.UUID = current_user.id
    thread_id = f"{user_id}:{asignatura_id}"
    # Elimina espacios y caracteres de control en los extremos antes del prompt.
    mensaje_texto = body.mensaje.strip()

    # Contexto de la asignatura para el reformulador.
    # descripcion: campo de la asignatura; puede ser None en BD.
    # titulos_documentos: nombres de los documentos activos de la asignatura.
    result_asig = await session.execute(select(Asignatura).where(Asignatura.id == asignatura_id))
    asignatura_obj = result_asig.scalar_one_or_none()
    descripcion_asignatura: str = (asignatura_obj.descripcion or "") if asignatura_obj else ""
    # Prompt personalizado por el profesor propietario.
    prompt_redactor: str = (asignatura_obj.prompt_redactor or "") if asignatura_obj else ""

    result_docs = await session.execute(
        select(Documento.nombre).where(Documento.asignatura_id == asignatura_id)
    )
    titulos_documentos: list[str] = list(result_docs.scalars().all())

    async def generate() -> AsyncGenerator[dict[str, str], None]:
        lf = _get_langfuse()
        trace: StatefulTraceClient | None = None
        # Spans activos indexados por run_id del evento LangGraph.
        node_spans: dict[str, StatefulSpanClient] = {}
        tokens: list[str] = []
        inicio = time.monotonic()

        async with AsyncPostgresSaver.from_conn_string(pg_conn_string()) as checkpointer:
            await checkpointer.setup()
            graph = build_graph(checkpointer)

            config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
            input_msg = _build_input_msg(
                mensaje_texto,
                asignatura_id,
                descripcion_asignatura,
                titulos_documentos,
                prompt_redactor,
            )

            if lf is not None:
                trace = lf.trace(
                    name="chat",
                    user_id=str(user_id),
                    session_id=thread_id,
                    input=mensaje_texto,
                    metadata={"asignatura_id": str(asignatura_id)},
                )

            async for event in graph.astream_events(input_msg, config, version="v2"):
                event_type: str = event.get("event", "")
                meta: dict[str, Any] = event.get("metadata", {})
                node: str = meta.get("langgraph_node", "")
                run_id: str = str(event.get("run_id", ""))

                # Crear y cerrar spans por nodo para capturar latencia.
                # event.get("name") == node descarta las funciones de arista
                # condicional (_decide_ruta_redactor, _decide_ruta_revisor):
                # LangGraph las etiqueta con el mismo langgraph_node que el
                # nodo del que salen (add_conditional_edges), pero son una
                # ejecución de LangChain distinta (run_id propio, ~0 ms de
                # duración) — sin este filtro generaban un span fantasma
                # duplicado por cada nodo con arista condicional en Langfuse.
                if trace is not None and node and run_id and event.get("name") == node:
                    if event_type == "on_chain_start" and run_id not in node_spans:
                        node_spans[run_id] = trace.span(
                            name=node,
                            start_time=datetime.now(UTC),
                        )
                    elif event_type == "on_chain_end" and run_id in node_spans:
                        node_spans[run_id].end(end_time=datetime.now(UTC))

                # Solo se envían al cliente los tokens del nodo redactor.
                # El revisor también llama al LLM pero su salida es feedback
                # interno, no parte de la respuesta al alumno.
                if event_type == "on_chat_model_stream" and node == "redactor":
                    chunk = event["data"]["chunk"]
                    raw = chunk.content
                    token = raw if isinstance(raw, str) else ""
                    if token:
                        tokens.append(token)
                        yield {"data": token}

            # Estado final del grafo (dentro del async with para que el
            # checkpointer siga abierto al llamar a aget_state). Se recupera
            # siempre, no solo con Langfuse configurado: uso_rag/iteracion
            # alimentan las métricas persistidas en el Mensaje del asistente.
            n_iter = 0
            uso_rag = False
            try:
                final_state = await graph.aget_state(config)
                n_iter = final_state.values.get("iteracion") or 0
                uso_rag = bool(final_state.values.get("uso_rag"))
            except Exception:  # noqa: S110
                pass  # Sin estado final, se persisten las métricas por defecto.

            if trace is not None and lf is not None:
                try:
                    trace.update(
                        output="".join(tokens),
                        metadata={
                            "asignatura_id": str(asignatura_id),
                            "user_id": str(user_id),
                            "n_iteraciones_revisor": n_iter,
                            "uso_rag": uso_rag,
                        },
                    )
                except Exception:  # noqa: S110
                    pass  # Errores de Langfuse nunca interrumpen el flujo principal.
                finally:
                    lf.flush()

        latencia_ms = int((time.monotonic() - inicio) * 1000)

        # La persistencia ocurre fuera del with del checkpointer; la sesión
        # SQLAlchemy sigue abierta porque el request FastAPI aún está activo.
        respuesta = "".join(tokens)
        session.add(
            Mensaje(
                id=uuid.uuid4(),
                alumno_id=user_id,
                asignatura_id=asignatura_id,
                rol=RolMensaje.user,
                contenido=mensaje_texto,
            )
        )
        if respuesta:
            session.add(
                Mensaje(
                    id=uuid.uuid4(),
                    alumno_id=user_id,
                    asignatura_id=asignatura_id,
                    rol=RolMensaje.assistant,
                    contenido=respuesta,
                    uso_rag=uso_rag,
                    # n_iteraciones solo es significativo cuando uso_rag=True
                    # (el Revisor no se ejecuta en el camino sin RAG).
                    n_iteraciones=n_iter if uso_rag else None,
                    latencia_ms=latencia_ms,
                )
            )
        await session.commit()

    return EventSourceResponse(generate())


@router.get(
    "/{asignatura_id}/historial",
    response_model=HistorialResponse,
)
async def get_historial_propio(
    asignatura_id: uuid.UUID,
    limit: int = Query(default=20, ge=5, le=20),
    before: uuid.UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    current_user: Usuario = Depends(get_current_user),
) -> HistorialResponse:
    """Historial paginado del alumno autenticado en una asignatura (RF-E4, RF-E7).

    Reutiliza _verificar_acceso_alumno: 404 si la asignatura no existe,
    403 si el usuario no es alumno o no está matriculado (RNF-6).

    Sin `before`, devuelve los `limit` mensajes más recientes. Con `before`
    (id del mensaje más antiguo ya cargado), devuelve la página anterior.
    """
    await _verificar_acceso_alumno(current_user, asignatura_id, session)
    condiciones = [Mensaje.alumno_id == current_user.id, Mensaje.asignatura_id == asignatura_id]
    mensajes, has_more = await paginar_mensajes(session, condiciones, limit, before)
    return HistorialResponse(mensajes=mensajes, has_more=has_more)
