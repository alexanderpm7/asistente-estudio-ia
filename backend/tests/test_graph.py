"""
Test de integración del grafo de agentes (flujo básico).

Prerrequisito: Ollama corriendo con el modelo configurado en LLM_MODEL (.env).
El test invoca el grafo completo (redactor → END) y verifica que la respuesta
no está vacía. No usa la fixture `client` ni la BD de tests: gestiona su
propia conexión a Postgres vía AsyncPostgresSaver.
"""

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.agents.graph import build_graph, pg_conn_string


@pytest.mark.integration
@pytest.mark.asyncio
async def test_redactor_responde_no_vacio() -> None:
    conn_str = pg_conn_string()
    async with AsyncPostgresSaver.from_conn_string(conn_str) as checkpointer:
        await checkpointer.setup()
        graph = build_graph(checkpointer)

        config = {"configurable": {"thread_id": "test_user:test_asignatura"}}
        resultado = await graph.ainvoke(
            {"messages": [HumanMessage(content="Di hola en una sola palabra.")]},
            config=config,
        )

    messages = resultado["messages"]
    assert messages, "El grafo no devolvió ningún mensaje"
    last_content = messages[-1].content
    assert last_content, "La respuesta del redactor está vacía"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_redactor_no_incluye_bloques_think() -> None:
    """El LLM compartido por Redactor/Revisor se construye con reasoning=True:
    Qwen3 separa el razonamiento interno, así que el contenido final no debe
    incluir bloques <think>...</think> (a diferencia del Reformulador, que sí
    los limpia manualmente por regex al no usar reasoning)."""
    conn_str = pg_conn_string()
    async with AsyncPostgresSaver.from_conn_string(conn_str) as checkpointer:
        await checkpointer.setup()
        graph = build_graph(checkpointer)

        config = {"configurable": {"thread_id": "test_user:test_asignatura_think"}}
        resultado = await graph.ainvoke(
            {"messages": [HumanMessage(content="Di hola en una sola palabra.")]},
            config=config,
        )

    last_content = str(resultado["messages"][-1].content)
    assert "<think>" not in last_content
    assert "</think>" not in last_content
