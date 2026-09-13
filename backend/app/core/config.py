from pathlib import Path

from pydantic import ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# El .env está en la raíz del proyecto, un nivel por encima de backend.
_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    database_url: str

    secret_key: str
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    ollama_host: str = "http://localhost:11434"
    llm_model: str = "qwen2.5:1.5b"
    embedding_model: str = "bge-m3"

    # Ollama es el proveedor por defecto; OpenAI es opcional.
    llm_provider: str = "ollama"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    max_revision_iterations: int = 2
    # Límite conservador para el contexto del modelo local.
    max_context_tokens: int = 2048

    prompt_redactor_max_length: int = 2000

    rag_top_k_chat: int = 8
    rag_relevance_threshold: float = 0.42

    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # Se usa para construir enlaces en correos.
    frontend_url: str = "http://localhost:3000"

    # En desarrollo apunta a Mailpit; en producción se sustituyen estas variables.
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "no-reply@asistente-estudio.local"

    password_set_token_ttl_hours: int = 48

    @field_validator("openai_api_key")
    @classmethod
    def _validar_openai_api_key(cls, v: str, info: ValidationInfo) -> str:
        """Falla al arrancar (no en la primera petición) si falta la API key.

        RF-IA7: con LLM_PROVIDER=openai, OPENAI_API_KEY es
        obligatorio. Detectarlo aquí evita descubrir la configuración
        incompleta en mitad de una conversación del alumno.

        Validador de campo (no de modelo completo): así, si falla, el error
        de pydantic solo incluye el valor de `openai_api_key`, no el resto
        de `Settings` (SECRET_KEY, DATABASE_URL con contraseña, etc.).
        """
        if info.data.get("llm_provider") == "openai" and not v.strip():
            raise ValueError(
                "OPENAI_API_KEY es obligatorio cuando LLM_PROVIDER=openai. "
                "Defínelo en .env o cambia LLM_PROVIDER a 'ollama'."
            )
        return v


settings = Settings()
