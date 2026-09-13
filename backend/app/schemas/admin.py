from pydantic import BaseModel, EmailStr, Field

from app.db.models import RolUsuario


class UsuarioCreateAdmin(BaseModel):
    email: EmailStr
    nombre: str = Field(min_length=1, max_length=255)
    rol: RolUsuario


class UsuarioUpdateAdmin(BaseModel):
    email: EmailStr | None = None
    rol: RolUsuario | None = None
    activo: bool | None = None


class ImportarUsuarioError(BaseModel):
    fila: int
    email: str | None
    error: str


class ImportarUsuariosResultado(BaseModel):
    creados: int
    errores: list[ImportarUsuarioError]
