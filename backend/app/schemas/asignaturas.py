import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.core.config import settings


class AsignaturaCreate(BaseModel):
    nombre: str = Field(min_length=1, max_length=255)
    descripcion: str | None = None
    # Se combina con las reglas fijas del prompt; nunca las sustituye.
    prompt_redactor: str | None = Field(
        default=None, max_length=settings.prompt_redactor_max_length
    )


class AsignaturaUpdate(BaseModel):
    nombre: str | None = Field(default=None, min_length=1, max_length=255)
    descripcion: str | None = None
    prompt_redactor: str | None = Field(
        default=None, max_length=settings.prompt_redactor_max_length
    )


class AsignaturaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    nombre: str
    descripcion: str | None
    profesor_id: uuid.UUID


class PromptRedactorResponse(BaseModel):
    """Respuesta del prompt de redacción de una asignatura."""

    model_config = ConfigDict(from_attributes=True)

    prompt_redactor: str | None


class MatriculaCreate(BaseModel):
    email: EmailStr


class MatriculaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    alumno_id: uuid.UUID
    asignatura_id: uuid.UUID


class AlumnoMatriculadoResponse(BaseModel):
    alumno_id: uuid.UUID
    email: str
    nombre: str
    matriculado_en: datetime
