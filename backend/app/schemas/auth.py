import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.db.models import RolUsuario


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    nombre: str = Field(min_length=1, max_length=255)
    rol: RolUsuario = RolUsuario.alumno

    @field_validator("rol")
    @classmethod
    def _solo_alumno(cls, v: RolUsuario) -> RolUsuario:
        """El registro público solo admite el rol alumno.

        Profesor y admin se crean desde el panel de administración o el
        script de bootstrap CLI (`backend/scripts/crear_admin.py`), nunca
        por autorregistro público.
        """
        if v != RolUsuario.alumno:
            raise ValueError(
                "El registro público solo admite el rol 'alumno'. Los roles "
                "'profesor' y 'admin' se crean desde el panel de administración "
                "o el script de bootstrap."
            )
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    nombre: str
    rol: RolUsuario
    activo: bool
    creado_en: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105


class EstablecerPasswordRequest(BaseModel):
    token: str
    password: str = Field(min_length=8)
