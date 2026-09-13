"""
Tests del bucle de revisión del grafo — SPEC §RF-IA4, RNF-5.

Contrato:
  - El Revisor evalúa la fidelidad del borrador respecto a los docs recuperados.
  - Si el feedback es DEFICIENTE y iteracion < MAX_REVISION_ITERATIONS, vuelve
    al Redactor. Si se alcanza el tope, termina aunque el feedback sea DEFICIENTE.
  - RNF-5: el bucle nunca supera MAX_REVISION_ITERATIONS iteraciones.

Estrategia de test:
  - Tests unitarios de las funciones de routing (sin Ollama, sin BD).
  - Test de integración del nodo revisor invocado directamente (sin Ollama:
    se verifica el tope duro que se aplica ANTES de llamar al LLM).

TDD: se escriben antes de la implementación; deben fallar en rojo.
"""

import pytest

from app.core.config import settings

# ── helpers ────────────────────────────────────────────────────────────────────


def _estado(
    iteracion: int = 0,
    feedback: str = "",
    docs: list | None = None,
    borrador: str = "Respuesta de prueba.",
) -> dict:
    return {
        "messages": [],
        "iteracion": iteracion,
        "feedback_revisor": feedback,
        "docs_recuperados": docs or [],
        "borrador": borrador,
    }


# ── tests unitarios de routing (sin Ollama) ─────────────────────────────────


def test_ruta_revisor_aprobado_va_a_end() -> None:
    """Si el feedback es APROBADO, el revisor dirige al END."""
    from app.agents.graph import _decide_ruta_revisor

    estado = _estado(feedback="APROBADO", iteracion=1)
    assert _decide_ruta_revisor(estado) == "__end__"


def test_ruta_revisor_deficiente_bajo_max_vuelve_a_redactor() -> None:
    """DEFICIENTE con iteracion < max → vuelve al Redactor."""
    from app.agents.graph import _decide_ruta_revisor

    iteracion_bajo_max = settings.max_revision_iterations - 1
    estado = _estado(feedback="DEFICIENTE: falta rigor", iteracion=iteracion_bajo_max)
    assert _decide_ruta_revisor(estado) == "redactor"


def test_ruta_revisor_deficiente_en_max_va_a_end() -> None:
    """DEFICIENTE con iteracion == max → END (tope duro, RNF-5)."""
    from app.agents.graph import _decide_ruta_revisor

    estado = _estado(
        feedback="DEFICIENTE: todavía incorrecto",
        iteracion=settings.max_revision_iterations,
    )
    assert _decide_ruta_revisor(estado) == "__end__"


def test_ruta_redactor_con_uso_rag_va_a_revisor() -> None:
    """Redactor con uso_rag=True dirige al Revisor."""
    from app.agents.graph import _decide_ruta_redactor

    estado = {**_estado(), "uso_rag": True}
    assert _decide_ruta_redactor(estado) == "revisor"


def test_ruta_redactor_sin_uso_rag_va_a_end() -> None:
    """Redactor con uso_rag=False dirige al END directamente."""
    from app.agents.graph import _decide_ruta_redactor

    estado = {**_estado(), "uso_rag": False}
    assert _decide_ruta_redactor(estado) == "__end__"


# ── test de integración del nodo revisor (sin LLM) ──────────────────────────


@pytest.mark.asyncio
async def test_revisor_nodo_aplica_tope_sin_llamar_llm() -> None:
    """
    Cuando iteracion ya está en el máximo, el nodo revisor devuelve
    feedback APROBADO sin llamar al LLM (tope duro pre-LLM, RNF-5).
    """
    from unittest.mock import AsyncMock, patch

    from app.agents.graph import _build_revisor_node

    nodo_revisor = _build_revisor_node()

    estado = _estado(
        docs=[{"texto": "contenido", "doc_nombre": "doc.pdf", "page": 1}],
        borrador="Respuesta del asistente.",
        iteracion=settings.max_revision_iterations,  # ya en el tope
    )

    mock_llm = AsyncMock()
    with patch("app.agents.graph._llm_instance", mock_llm):
        resultado = await nodo_revisor(estado)

    mock_llm.ainvoke.assert_not_called()
    assert "APROBADO" in resultado["feedback_revisor"]
    assert resultado["iteracion"] == settings.max_revision_iterations + 1


@pytest.mark.asyncio
async def test_revisor_nodo_llama_llm_bajo_max_iteraciones() -> None:
    """
    Cuando iteracion < max, el nodo revisor llama al LLM para evaluar fidelidad.
    """
    from unittest.mock import AsyncMock, patch

    from langchain_core.messages import AIMessage

    from app.agents.graph import _build_revisor_node

    nodo_revisor = _build_revisor_node()

    estado = _estado(
        docs=[{"texto": "Los mamíferos son vertebrados.", "doc_nombre": "bio.pdf", "page": 1}],
        borrador="Los mamíferos son vertebrados de sangre caliente.",
        iteracion=0,
    )

    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = AIMessage(content="APROBADO")

    with patch("app.agents.graph._llm_instance", mock_llm):
        resultado = await nodo_revisor(estado)

    mock_llm.ainvoke.assert_called_once()
    assert resultado["iteracion"] == 1
    assert resultado["feedback_revisor"] == "APROBADO"
