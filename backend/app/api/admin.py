import csv
import io
import logging
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile, status
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.core.email import crear_token_establecer_password, enviar_email_establecer_password
from app.core.security import hash_password
from app.db.models import PasswordSetToken, RolUsuario, Usuario
from app.db.session import get_session
from app.schemas.admin import (
    ImportarUsuarioError,
    ImportarUsuariosResultado,
    UsuarioCreateAdmin,
    UsuarioUpdateAdmin,
)
from app.schemas.auth import UserResponse

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

_COLUMNAS_CSV_ESPERADAS = {"email", "nombre", "rol"}
MAX_CSV_SIZE: int = 2 * 1024 * 1024  # 2 MB — de sobra para miles de filas email,nombre,rol
MAX_CSV_ROWS: int = 5000


def _require_admin(user: Usuario) -> None:
    if user.rol != RolUsuario.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Solo administradores.")


async def _get_usuario_o_404(usuario_id: uuid.UUID, session: AsyncSession) -> Usuario:
    result = await session.execute(select(Usuario).where(Usuario.id == usuario_id))
    usuario = result.scalar_one_or_none()
    if usuario is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado.")
    return usuario


async def _crear_usuario_con_token(
    email: str, nombre: str, rol: RolUsuario, session: AsyncSession
) -> tuple[Usuario, str]:
    """Crea el usuario y su token de establecimiento de contraseña en una transacción.

    La contraseña real se genera con `secrets` y nunca se comunica (RF-A3):
    el usuario la fija por primera vez a través del enlace del correo.
    Puede lanzar `IntegrityError` (email duplicado) sin hacer rollback —
    responsabilidad del llamador, que sabe si debe abortar o seguir con la
    siguiente fila de un lote.
    """
    password_temporal = secrets.token_urlsafe(24)
    usuario = Usuario(
        id=uuid.uuid4(),
        email=email,
        password_hash=hash_password(password_temporal),
        nombre=nombre,
        rol=rol,
    )
    session.add(usuario)
    token = await crear_token_establecer_password(usuario, session)
    await session.commit()
    await session.refresh(usuario)
    return usuario, token


async def _enviar_email_o_avisar(usuario: Usuario, token: str) -> None:
    """Envía el correo de establecimiento de contraseña; nunca rompe el alta.

    Best-effort: si el SMTP falla, se registra en el log pero el usuario ya
    está creado.
    """
    try:
        await enviar_email_establecer_password(usuario, token)
    except Exception:
        _log.warning(
            "No se pudo enviar el email de establecimiento de contraseña a %s", usuario.email
        )


@router.get("/usuarios", response_model=list[UserResponse])
async def listar_usuarios(
    rol: RolUsuario | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    current_user: Usuario = Depends(get_current_user),
) -> list[Usuario]:
    """CRUD de usuarios, solo para administradores y filtrable por `rol`."""
    _require_admin(current_user)
    stmt = select(Usuario).order_by(Usuario.creado_en)
    if rol is not None:
        stmt = stmt.where(Usuario.rol == rol)
    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.post("/usuarios", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def crear_usuario(
    body: UsuarioCreateAdmin,
    session: AsyncSession = Depends(get_session),
    current_user: Usuario = Depends(get_current_user),
) -> Usuario:
    """Crea un usuario con rol arbitrario, solo para administradores.

    El usuario se crea sin contraseña comunicada; recibe un correo con el
    enlace para establecer la suya propia (token de un solo uso).
    """
    _require_admin(current_user)
    try:
        usuario, token = await _crear_usuario_con_token(body.email, body.nombre, body.rol, session)
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El correo ya está registrado.",
        ) from None

    await _enviar_email_o_avisar(usuario, token)
    return usuario


@router.post("/usuarios/importar", response_model=ImportarUsuariosResultado)
async def importar_usuarios(
    file: UploadFile,
    session: AsyncSession = Depends(get_session),
    current_user: Usuario = Depends(get_current_user),
) -> ImportarUsuariosResultado:
    """Crea usuarios masivamente desde un CSV, solo para administradores.

    Columnas esperadas: `email`, `nombre`, `rol`. Cada fila se valida y
    crea de forma independiente: una fila inválida (rol desconocido, email
    duplicado, etc.) se reporta como error sin abortar el resto del lote.
    Cada usuario creado recibe el correo de establecimiento de contraseña
    igual que en el alta individual (RF-A3).
    """
    _require_admin(current_user)

    if file.filename and not file.filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="El fichero debe ser un CSV (.csv)."
        )

    # Rechazo temprano según el tamaño declarado por el cliente.
    if file.size is not None and file.size > MAX_CSV_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El fichero supera el tamaño máximo permitido de 2 MB.",
        )

    raw = await file.read()
    if len(raw) > MAX_CSV_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El fichero supera el tamaño máximo permitido de 2 MB.",
        )
    try:
        texto = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El fichero no es un CSV de texto válido (UTF-8).",
        ) from None

    reader = csv.DictReader(io.StringIO(texto))
    if reader.fieldnames is None or not _COLUMNAS_CSV_ESPERADAS.issubset(set(reader.fieldnames)):
        columnas = sorted(_COLUMNAS_CSV_ESPERADAS)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cabecera CSV inválida: se esperan las columnas {columnas}.",
        )

    creados = 0
    errores: list[ImportarUsuarioError] = []

    for fila_num, row in enumerate(reader, start=1):
        if fila_num > MAX_CSV_ROWS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"El CSV supera el máximo de {MAX_CSV_ROWS} filas.",
            )
        email = (row.get("email") or "").strip()
        try:
            datos = UsuarioCreateAdmin(
                email=email,
                nombre=(row.get("nombre") or "").strip(),
                rol=(row.get("rol") or "").strip(),
            )
        except ValidationError as exc:
            errores.append(
                ImportarUsuarioError(
                    fila=fila_num, email=email or None, error=exc.errors()[0]["msg"]
                )
            )
            continue

        try:
            usuario, token = await _crear_usuario_con_token(
                datos.email, datos.nombre, datos.rol, session
            )
        except IntegrityError:
            await session.rollback()
            errores.append(
                ImportarUsuarioError(
                    fila=fila_num, email=datos.email, error="El correo ya está registrado."
                )
            )
            continue

        await _enviar_email_o_avisar(usuario, token)
        creados += 1

    return ImportarUsuariosResultado(creados=creados, errores=errores)


@router.patch("/usuarios/{usuario_id}", response_model=UserResponse)
async def editar_usuario(
    usuario_id: uuid.UUID,
    body: UsuarioUpdateAdmin,
    session: AsyncSession = Depends(get_session),
    current_user: Usuario = Depends(get_current_user),
) -> Usuario:
    """Edita email, rol y/o estado activo, solo para administradores.

    Un admin no puede quitarse a sí mismo el rol admin ni desactivarse
    (mismo motivo que el bloqueo de autoeliminación: no dejar el sistema
    sin ningún administrador que pueda revertir el cambio).
    """
    _require_admin(current_user)
    usuario = await _get_usuario_o_404(usuario_id, session)

    if usuario_id == current_user.id:
        rol_final = body.rol if body.rol is not None else usuario.rol
        activo_final = body.activo if body.activo is not None else usuario.activo
        if rol_final != RolUsuario.admin or not activo_final:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No puedes quitarte a ti mismo el rol admin ni desactivar tu propia cuenta.",
            )

    email_cambia = body.email is not None and body.email != usuario.email
    if body.email is not None:
        usuario.email = body.email
    if body.rol is not None:
        usuario.rol = body.rol
    if body.activo is not None:
        usuario.activo = body.activo

    try:
        if email_cambia:
            # Invalida el token pendiente si cambia el email asociado.
            await session.execute(
                update(PasswordSetToken)
                .where(PasswordSetToken.usuario_id == usuario.id, PasswordSetToken.usado.is_(False))
                .values(usado=True)
            )
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El correo ya está registrado.",
        ) from None
    await session.refresh(usuario)
    return usuario


@router.delete("/usuarios/{usuario_id}", status_code=status.HTTP_204_NO_CONTENT)
async def eliminar_usuario(
    usuario_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: Usuario = Depends(get_current_user),
) -> Response:
    """Elimina un usuario, solo para administradores.

    Un admin no puede eliminarse a sí mismo, para no dejar el sistema sin
    ningún administrador que pueda gestionar usuarios.
    """
    _require_admin(current_user)
    if usuario_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No puedes eliminar tu propio usuario.",
        )
    usuario = await _get_usuario_o_404(usuario_id, session)
    await session.delete(usuario)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No se puede eliminar: el usuario tiene asignaturas u otros datos asociados.",
        ) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)
