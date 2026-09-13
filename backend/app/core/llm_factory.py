"""Fábrica de cliente LLM.

Centraliza la construcción del cliente LangChain para ocultar el proveedor:

    "ollama" (por defecto) — modelos locales vía ChatOllama.
    "openai" — ChatOpenAI de langchain-openai.

Cada proveedor nombra distinto el límite de tokens de salida (Ollama:
`num_predict`, OpenAI: `max_tokens`); `get_llm_client` expone un único
parámetro `max_output_tokens` y lo traduce al nombre que espera cada cliente.
"""

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from app.core.config import settings


def get_llm_client(
    *,
    temperature: float | None = None,
    max_output_tokens: int | None = None,
    streaming: bool = False,
    reasoning: bool | str | None = None,
) -> BaseChatModel:
    """Construye el cliente LLM según `settings.llm_provider`.

    `reasoning` solo se aplica a Ollama: con `reasoning=True`, Qwen3 separa el
    razonamiento interno del contenido final y `chunk.content` en streaming ya
    no incluye bloques `<think>...</think>` (ver Redactor/Revisor en
    `app/agents/graph.py`). Se ignora con `llm_provider="openai"` para no
    romper la compatibilidad con `ChatOpenAI`, que no admite ese parámetro.

    Lanza `ValueError` si `llm_provider` no es "ollama" ni "openai".
    """
    provider = settings.llm_provider

    if provider == "ollama":
        ollama_kwargs: dict[str, object] = {
            "model": settings.llm_model,
            "base_url": settings.ollama_host,
            "streaming": streaming,
        }
        if temperature is not None:
            ollama_kwargs["temperature"] = temperature
        if max_output_tokens is not None:
            ollama_kwargs["num_predict"] = max_output_tokens
        if reasoning is not None:
            ollama_kwargs["reasoning"] = reasoning
        return ChatOllama(**ollama_kwargs)

    if provider == "openai":
        openai_kwargs: dict[str, object] = {
            "model": settings.openai_model,
            "api_key": settings.openai_api_key,
            "streaming": streaming,
        }
        if temperature is not None:
            openai_kwargs["temperature"] = temperature
        if max_output_tokens is not None:
            openai_kwargs["max_tokens"] = max_output_tokens
        return ChatOpenAI(**openai_kwargs)

    raise ValueError(
        f"LLM_PROVIDER no soportado: {provider!r}. Valores admitidos: 'ollama', 'openai'."
    )


async def ollama_disponible(timeout: float = 2.0) -> bool:
    """Comprueba rápidamente la disponibilidad de Ollama.

    GET con timeout corto a la raíz de `settings.ollama_host` (Ollama responde
    "Ollama is running" ahí sin coste de cargar ningún modelo). Devuelve
    `False` ante cualquier error de conexión/timeout, nunca lanza — permite
    usarla como comprobación previa a abrir el stream SSE del chat sin que un
    fallo de red se confunda con una excepción no controlada.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout) as http_client:
            response = await http_client.get(settings.ollama_host)
        return response.status_code < 500
    except httpx.HTTPError:
        return False
