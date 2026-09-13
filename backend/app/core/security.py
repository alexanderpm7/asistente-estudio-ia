import hashlib
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import settings

_ph = PasswordHasher()


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, plain)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def hash_reset_token(token: str) -> str:
    """Hash determinista (SHA-256) para tokens de un solo uso (no contraseñas).

    A diferencia de `hash_password` (argon2, con sal, para contraseñas de
    usuario de baja entropía), los tokens de establecimiento de contraseña
    ya son aleatorios de alta entropía (`secrets.token_urlsafe`); un hash
    determinista permite buscarlos por igualdad en BD (`WHERE token_hash =
    ...`) sin tener que volver a hashear cada fila, igual que se hace
    habitualmente con API keys o tokens de sesión.
    """
    return hashlib.sha256(token.encode()).hexdigest()


def create_access_token(subject: str) -> str:
    expire = datetime.now(UTC) + timedelta(minutes=settings.access_token_expire_minutes)
    return jwt.encode(
        {"sub": subject, "exp": expire},
        settings.secret_key,
        algorithm=settings.algorithm,
    )
