"""
Test de regresión — el estado de trabajo de un turno (`iteracion`,
`feedback_revisor`, `borrador`, `docs_recuperados`, `consulta_reescrita`,
`uso_rag`) no debe heredarse del turno anterior de la misma conversación.

Bug: `chat.py` construía el input inicial de cada invocación del grafo sin
reiniciar estos campos. LangGraph aplica los valores del input sobre el
último checkpoint del `thread_id` (no sobre un estado vacío), así que
`iteracion` se acumulaba turno a turno. Con `max_revision_iterations=2`
(valor por defecto), a partir del tercer turno con RAG en la misma
conversación `_decide_ruta_revisor` entraba directamente en el tope duro de
RNF-5 y el Revisor dejaba de evaluar fidelidad de verdad: devolvía
"APROBADO" sin llegar a llamar al LLM.

No requiere Ollama: el LLM compartido de Redactor/Revisor, el del
Reformulador y el embedder del Agente RAG se mockean (mismo patrón que
test_graph.py); solo necesita Postgres real para el checkpointer, como el
resto de tests no-integration que invocan build_graph().
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.agents.graph import build_graph, pg_conn_string

# ── mocks compartidos por los tres turnos ────────────────────────────────────


class _FakeEmbedder:
    async def aembed_query(self, texto: str) -> list[float]:
        return [0.0] * 1024


_CHUNKS_FIJOS = [
    {
        "chunk_id": "11111111-1111-1111-1111-111111111111",
        "texto": "Fragmento de prueba con contenido relevante.",
        "chunk_index": 0,
        "doc_nombre": "doc.pdf",
        "page": 1,
        "distance": 0.1,
        "similarity": 0.9,
    }
]


async def _fake_redactor_revisor_ainvoke(mensajes: list) -> AIMessage:
    """El Revisor usa un prompt con 'evaluador de calidad'; el Redactor no.
    Distinguirlos así permite reutilizar la misma instancia mockeada para
    ambos nodos, igual que en el grafo real (_llm_instance compartida)."""
    texto = " ".join(str(m.content) for m in mensajes)
    if "evaluador de calidad" in texto:
        return AIMessage(content="APROBADO")
    return AIMessage(content="Respuesta de prueba.\n\nFuentes:\ntexto de prueba")


def _fake_get_llm_client(
    *,
    temperature: float | None = None,
    max_output_tokens: int | None = None,
    streaming: bool = False,
    reasoning: bool | str | None = None,
) -> AsyncMock:
    """build_graph() llama a get_llm_client() dos veces: una con
    streaming=True (Redactor/Revisor) y otra sin streaming (Reformulador) —
    los mismos kwargs que en el código real distinguen qué mock devolver."""
    if streaming:
        mock = AsyncMock()
        mock.ainvoke = AsyncMock(side_effect=_fake_redactor_revisor_ainvoke)
        return mock
    mock = AsyncMock()
    mock.ainvoke = AsyncMock(return_value=AIMessage(content="consulta reescrita"))
    return mock


@pytest.mark.asyncio
async def test_iteracion_se_reinicia_entre_turnos_del_mismo_thread() -> None:
    """Tres turnos con RAG sobre el mismo thread_id: iteracion debe partir
    de 0 (y terminar en 1, tras una única pasada APROBADA del Revisor) en
    CADA turno, no acumularse (1, 2, 3...)."""
    from app.api.chat import _build_input_msg

    thread_id = f"test_reset_{uuid.uuid4().hex}:test_asignatura_reset"
    config = {"configurable": {"thread_id": thread_id}}
    asignatura_id = uuid.UUID("00000000-0000-0000-0000-000000000000")

    with (
        patch("app.agents.graph.get_llm_client", side_effect=_fake_get_llm_client),
        patch("app.agents.graph.OllamaEmbeddings", return_value=_FakeEmbedder()),
        patch("app.agents.graph.retrieve_chunks", AsyncMock(return_value=_CHUNKS_FIJOS)),
    ):
        async with AsyncPostgresSaver.from_conn_string(pg_conn_string()) as checkpointer:
            await checkpointer.setup()
            graph = build_graph(checkpointer)

            for turno in range(1, 4):
                input_msg = _build_input_msg(
                    mensaje_texto=f"Pregunta número {turno}",
                    asignatura_id=asignatura_id,
                    descripcion_asignatura="",
                    titulos_documentos=[],
                    prompt_redactor="",
                )
                await graph.ainvoke(input_msg, config=config)

                estado_final = await graph.aget_state(config)
                iteracion_final = estado_final.values["iteracion"]
                assert iteracion_final == 1, (
                    f"Turno {turno}: iteracion terminó en {iteracion_final}, "
                    "se ha acumulado del turno anterior en vez de reiniciarse a 0"
                )
