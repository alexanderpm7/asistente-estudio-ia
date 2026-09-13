import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.core.security import create_access_token, hash_password, hash_reset_token, verify_password
from app.db.models import PasswordSetToken, Usuario
from app.db.session import get_session
from app.schemas.auth import (
    EstablecerPasswordRequest,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    body: RegisterRequest,
    session: AsyncSession = Depends(get_session),
) -> Usuario:
    user = Usuario(
        id=uuid.uuid4(),
        email=body.email,
        password_hash=hash_password(body.password),
        nombre=body.nombre,
        rol=body.rol,
    )
    session.add(user)
    try:
        await session.commit()
        await session.refresh(user)
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El correo ya está registrado.",
        ) from None
    return user


@router.get("/me", response_model=UserResponse)
async def me(
    current_user: Usuario = Depends(get_current_user),
) -> Usuario:
    return current_user


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    result = await session.execute(select(Usuario).where(Usuario.email == body.email))
    user = result.scalar_one_or_none()

    # La respuesta uniforme evita enumerar usuarios.
    if user is None or not user.activo or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales incorrectas.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return TokenResponse(access_token=create_access_token(subject=str(user.id)))


@router.post("/establecer-password", status_code=status.HTTP_204_NO_CONTENT)
async def establecer_password(
    body: EstablecerPasswordRequest,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Fija la contraseña a partir del token de un solo uso.

    Endpoint público (el usuario aún no tiene contraseña utilizable):
    valida que el token exista, no esté caducado y no se haya usado ya.
    """
    token_hash = hash_reset_token(body.token)
    result = await session.execute(
        select(PasswordSetToken).where(PasswordSetToken.token_hash == token_hash)
    )
    registro = result.scalar_one_or_none()
    if registro is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Token inválido.")
    if registro.usado:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El enlace ya ha sido utilizado.",
        )
    if registro.expira_en < datetime.now(UTC):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="El enlace ha caducado."
        )

    usuario = await session.get(Usuario, registro.usuario_id)
    if usuario is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Token inválido.")

    usuario.password_hash = hash_password(body.password)
    registro.usado = True
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
