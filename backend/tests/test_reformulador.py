"""
Tests del nodo Reformulador — SPEC §RF-IA1, ARCHITECTURE §3.

Contrato:
  - El Reformulador reescribe la pregunta del alumno para optimizar el retrieval.
  - Produce consulta_reescrita no vacía en todos los casos.
  - Si hay contexto (descripcion_asignatura, titulos_documentos), lo usa para
    desambiguar terminología. Si el contexto está vacío, reescribe solo a partir
    de la pregunta.
  - Si el LLM no está disponible, devuelve la consulta original sin modificar.
  - Los tokens de thinking (<think>…</think>) emitidos por qwen3 se eliminan
    antes de extraer la consulta reescrita.

Estrategia de test:
  - Tests unitarios del nodo invocado directamente (sin Ollama, sin BD).
  - El LLM se mockea vía patch sobre _reformulador_llm_instance.
  - El mock devuelve la respuesta a través de ainvoke() (texto plano, sin
    with_structured_output) para reflejar la implementación actual.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage

# ── helper ─────────────────────────────────────────────────────────────────────


def _estado_reformulador(
    pregunta: str = "¿qué es un componente?",
    descripcion: str = "",
    titulos: list[str] | None = None,
) -> dict:
    return {
        "messages": [HumanMessage(content=pregunta)],
        "descripcion_asignatura": descripcion,
        "titulos_documentos": titulos if titulos is not None else [],
    }


def _mock_llm(reescrita: str = "¿qué es un componente Angular?") -> MagicMock:
    """Mock de ChatOllama cuyo ainvoke devuelve un mensaje con content=reescrita."""
    mock_response = MagicMock()
    mock_response.content = reescrita

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)
    return mock_llm


# ── tests ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reformulador_produce_consulta_reescrita_no_vacia() -> None:
    """El nodo produce un campo consulta_reescrita no vacío para una pregunta simple."""
    from app.agents.graph import _build_reformulador_node

    nodo = _build_reformulador_node()
    estado = _estado_reformulador("¿qué es un componente?")

    mock_respuesta = _mock_llm("componente Angular definición")
    with patch("app.agents.graph._reformulador_llm_instance", mock_respuesta):
        resultado = await nodo(estado)

    assert "consulta_reescrita" in resultado
    assert resultado["consulta_reescrita"] != ""


@pytest.mark.asyncio
async def test_reformulador_con_contexto_de_asignatura() -> None:
    """Con descripción y títulos de documentos, el LLM recibe el contexto en el prompt."""
    from app.agents.graph import _build_reformulador_node

    nodo = _build_reformulador_node()
    estado = _estado_reformulador(
        pregunta="¿qué es?",
        descripcion="Desarrollo web con Angular y TypeScript",
        titulos=["Angular Components Guide", "Services in Angular", "RxJS Basics"],
    )
    mock_llm = _mock_llm("definición de componente en Angular")

    with patch("app.agents.graph._reformulador_llm_instance", mock_llm):
        resultado = await nodo(estado)

    assert resultado["consulta_reescrita"] != ""
    # El LLM debe haber sido invocado con un SystemMessage que incluye el contexto.
    mock_llm.ainvoke.assert_called_once()
    call_args = mock_llm.ainvoke.call_args
    mensajes = call_args[0][0]
    system_content = mensajes[0].content
    assert "Angular" in system_content or "Services" in system_content or "RxJS" in system_content


@pytest.mark.asyncio
async def test_reformulador_con_contexto_vacio() -> None:
    """Sin descripción ni documentos el reformulador sigue produciendo consulta_reescrita."""
    from app.agents.graph import _build_reformulador_node

    nodo = _build_reformulador_node()
    estado = _estado_reformulador(
        pregunta="¿cómo funciona el ciclo de vida?",
        descripcion="",
        titulos=[],
    )

    mock_ciclo = _mock_llm("ciclo de vida del componente")
    with patch("app.agents.graph._reformulador_llm_instance", mock_ciclo):
        resultado = await nodo(estado)

    assert resultado["consulta_reescrita"] != ""


@pytest.mark.asyncio
async def test_reformulador_sin_llm_usa_consulta_original() -> None:
    """Si _reformulador_llm_instance es None, se devuelve la pregunta original intacta."""
    from app.agents.graph import _build_reformulador_node

    nodo = _build_reformulador_node()
    pregunta = "¿qué es el two-way binding?"
    estado = _estado_reformulador(pregunta)

    with patch("app.agents.graph._reformulador_llm_instance", None):
        resultado = await nodo(estado)

    assert resultado["consulta_reescrita"] == pregunta


@pytest.mark.asyncio
async def test_reformulador_fallo_llm_usa_consulta_original() -> None:
    """Si el LLM lanza una excepción, el fallback es la consulta original."""
    from app.agents.graph import _build_reformulador_node

    nodo = _build_reformulador_node()
    pregunta = "¿qué son los servicios?"
    estado = _estado_reformulador(pregunta)

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(side_effect=RuntimeError("Ollama no disponible"))

    with patch("app.agents.graph._reformulador_llm_instance", mock_llm):
        resultado = await nodo(estado)

    assert resultado["consulta_reescrita"] == pregunta


@pytest.mark.asyncio
async def test_reformulador_elimina_tokens_thinking() -> None:
    """Los tokens <think>…</think> de qwen3 se eliminan de la consulta reescrita."""
    from app.agents.graph import _build_reformulador_node

    nodo = _build_reformulador_node()
    estado = _estado_reformulador("¿qué es un servicio?")

    thinking_response = (
        "<think>Voy a analizar la pregunta...</think>\ndefinición de servicio Angular"
    )
    mock_llm = _mock_llm(thinking_response)

    with patch("app.agents.graph._reformulador_llm_instance", mock_llm):
        resultado = await nodo(estado)

    assert "<think>" not in resultado["consulta_reescrita"]
    assert resultado["consulta_reescrita"] == "definición de servicio Angular"
