"""
Tests de la fábrica de cliente LLM — RF-IA7.

Contrato:
  - `llm_provider="ollama"` (por defecto) → devuelve una instancia de ChatOllama.
  - `llm_provider="openai"` con API key presente → devuelve una instancia de ChatOpenAI.
  - `llm_provider` no soportado → lanza ValueError con un mensaje claro.

No se hacen llamadas reales a ningún proveedor: solo se verifica el tipo de
instancia devuelta y su configuración (model, temperature, límite de tokens).
"""

import pytest
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from pydantic import ValidationError

from app.core.config import Settings, settings
from app.core.llm_factory import get_llm_client, ollama_disponible

_SETTINGS_BASE_KWARGS = {
    "database_url": "postgresql+psycopg://x:x@localhost/x",
    "secret_key": "test-secret",
}


def test_get_llm_client_ollama_por_defecto(monkeypatch: pytest.MonkeyPatch) -> None:
    """provider="ollama" (por defecto) devuelve una instancia de ChatOllama."""
    monkeypatch.setattr(settings, "llm_provider", "ollama")

    client = get_llm_client()

    assert isinstance(client, ChatOllama)
    assert client.model == settings.llm_model
    assert client.base_url == settings.ollama_host


def test_get_llm_client_ollama_traduce_temperatura_y_max_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """temperature y max_output_tokens se traducen a temperature/num_predict en Ollama."""
    monkeypatch.setattr(settings, "llm_provider", "ollama")

    client = get_llm_client(temperature=0, max_output_tokens=100, streaming=True)

    assert isinstance(client, ChatOllama)
    assert client.temperature == 0
    assert client.num_predict == 100


def test_get_llm_client_openai_con_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """provider="openai" con API key presente devuelve una instancia de ChatOpenAI."""
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "openai_api_key", "sk-test-123")
    monkeypatch.setattr(settings, "openai_model", "gpt-4o-mini")

    client = get_llm_client()

    assert isinstance(client, ChatOpenAI)
    assert client.model_name == "gpt-4o-mini"


def test_get_llm_client_openai_traduce_temperatura_y_max_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """temperature y max_output_tokens se traducen a temperature/max_tokens en OpenAI."""
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "openai_api_key", "sk-test-123")

    client = get_llm_client(temperature=0, max_output_tokens=300)

    assert isinstance(client, ChatOpenAI)
    assert client.temperature == 0
    assert client.max_tokens == 300


def test_get_llm_client_ollama_reasoning_true_se_transmite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """reasoning=True se pasa a ChatOllama para que Qwen3 separe el razonamiento
    interno del contenido final (evita <think>...</think> en chunk.content)."""
    monkeypatch.setattr(settings, "llm_provider", "ollama")

    client = get_llm_client(reasoning=True)

    assert isinstance(client, ChatOllama)
    assert client.reasoning is True


def test_get_llm_client_ollama_sin_reasoning_no_lo_fija(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sin pasar `reasoning` (None), ChatOllama conserva su valor por defecto."""
    monkeypatch.setattr(settings, "llm_provider", "ollama")

    client = get_llm_client()

    assert isinstance(client, ChatOllama)
    assert client.reasoning is None


def test_get_llm_client_openai_ignora_reasoning(monkeypatch: pytest.MonkeyPatch) -> None:
    """reasoning solo aplica a Ollama: con provider=openai no se transmite (ChatOpenAI
    espera un dict para ese campo, no un bool; pasarlo rompería la construcción)."""
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "openai_api_key", "sk-test-123")

    client = get_llm_client(reasoning=True)

    assert isinstance(client, ChatOpenAI)
    assert client.reasoning is None


def test_get_llm_client_provider_no_soportado_lanza_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un llm_provider no reconocido lanza ValueError con mensaje claro."""
    monkeypatch.setattr(settings, "llm_provider", "anthropic")

    with pytest.raises(ValueError, match="LLM_PROVIDER no soportado"):
        get_llm_client()


def test_settings_openai_sin_api_key_falla_al_construir() -> None:
    """RF-IA7: LLM_PROVIDER=openai sin OPENAI_API_KEY falla al construir
    Settings (equivalente al arranque de la aplicación), no en la primera petición
    al LLM."""
    with pytest.raises(ValidationError, match="OPENAI_API_KEY es obligatorio"):
        Settings(**_SETTINGS_BASE_KWARGS, llm_provider="openai", openai_api_key="")


def test_settings_openai_con_api_key_no_falla() -> None:
    """Con OPENAI_API_KEY presente, LLM_PROVIDER=openai construye Settings sin error."""
    s = Settings(**_SETTINGS_BASE_KWARGS, llm_provider="openai", openai_api_key="sk-test-123")

    assert s.llm_provider == "openai"
    assert s.openai_api_key == "sk-test-123"


def test_settings_ollama_sin_api_key_no_falla() -> None:
    """Con LLM_PROVIDER=ollama (por defecto), no se exige OPENAI_API_KEY."""
    s = Settings(**_SETTINGS_BASE_KWARGS, llm_provider="ollama", openai_api_key="")

    assert s.llm_provider == "ollama"


# ── ollama_disponible (RF-E3) ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ollama_disponible_false_si_no_hay_conexion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sin nada escuchando en el host configurado, devuelve False (nunca lanza)."""
    monkeypatch.setattr(settings, "ollama_host", "http://127.0.0.1:1")

    assert await ollama_disponible(timeout=0.5) is False
