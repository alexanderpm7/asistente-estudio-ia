import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DocumentoResponse(BaseModel):
    model_config = ConfigDict(from_attributes=False)

    id: uuid.UUID
    nombre: str
    asignatura_id: uuid.UUID
    subido_en: datetime
    n_chunks: int
