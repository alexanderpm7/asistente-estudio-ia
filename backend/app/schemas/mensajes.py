import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.db.models import RolMensaje


class MensajeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    alumno_id: uuid.UUID
    asignatura_id: uuid.UUID
    rol: RolMensaje
    contenido: str
    creado_en: datetime
    uso_rag: bool
    n_iteraciones: int | None
    latencia_ms: int | None


class HistorialResponse(BaseModel):
    """Página de historial paginado por cursor.

    `mensajes` en orden cronológico (más antiguo primero); `has_more`
    indica si quedan mensajes más antiguos que los devueltos en esta página.
    """

    mensajes: list[MensajeResponse]
    has_more: bool
