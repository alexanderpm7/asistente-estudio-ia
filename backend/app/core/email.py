import asyncio
import secrets
import smtplib
import uuid
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_reset_token
from app.db.models import PasswordSetToken, Usuario


def _send_sync(to: str, subject: str, body: str) -> None:
    """Envía el correo de forma síncrona (smtplib no tiene API async)."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg.set_content(body)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(msg)


async def send_email(to: str, subject: str, body: str) -> None:
    """Envía un correo por SMTP según la configuración del entorno.

    En desarrollo, `SMTP_HOST`/`SMTP_PORT` apuntan al contenedor Mailpit
    (docker-compose.yml); cambiar a un proveedor real en producción es solo
    cambiar estas variables, sin tocar código.
    """
    await asyncio.to_thread(_send_sync, to, subject, body)


async def crear_token_establecer_password(usuario: Usuario, session: AsyncSession) -> str:
    """Crea (sin commit) un token de un solo uso y lo añade a la sesión.

    Devuelve el token en claro (para incluirlo en el enlace del correo);
    solo su hash se persiste (RF-A3, ver PasswordSetToken).
    """
    token = secrets.token_urlsafe(32)
    registro = PasswordSetToken(
        id=uuid.uuid4(),
        usuario_id=usuario.id,
        token_hash=hash_reset_token(token),
        expira_en=datetime.now(UTC) + timedelta(hours=settings.password_set_token_ttl_hours),
    )
    session.add(registro)
    return token


async def enviar_email_establecer_password(usuario: Usuario, token: str) -> None:
    """Envía el correo con el enlace de establecimiento de contraseña."""
    enlace = f"{settings.frontend_url}/establecer-password?token={token}"
    asunto = "Establece tu contraseña"
    cuerpo = (
        f"Hola {usuario.nombre},\n\n"
        "Se ha creado una cuenta para ti en la plataforma de asistencia al estudio.\n"
        "Para establecer tu contraseña, visita el siguiente enlace:\n\n"
        f"{enlace}\n\n"
        f"Este enlace caduca en {settings.password_set_token_ttl_hours} horas "
        "y solo se puede usar una vez."
    )
    await send_email(usuario.email, asunto, cuerpo)
