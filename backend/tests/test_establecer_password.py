"""
Tests del endpoint POST /auth/establecer-password — SPEC §3.4 RF-A3.

Contrato:
  → 204  token válido, no caducado y no usado: fija la contraseña
  → 400  token inexistente, caducado o ya usado

Endpoint público: el usuario aún no tiene contraseña utilizable.
TDD: los tests se escriben antes que la implementación; deben fallar en rojo.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, hash_reset_token, verify_password
from app.db.models import PasswordSetToken, RolUsuario, Usuario

ESTABLECER = "/auth/establecer-password"

_NUEVA_PASS = "NuevaSegura1234!"


async def _crear_usuario_con_token(
    db_session: AsyncSession,
    email: str,
    *,
    token: str = "token-de-prueba-valido",
    expira_en: datetime | None = None,
    usado: bool = False,
) -> Usuario:
    usuario = Usuario(
        id=uuid.uuid4(),
        email=email,
        password_hash=hash_password("PasswordInicialNoUsable123!"),
        nombre="Test",
        rol=RolUsuario.alumno,
    )
    db_session.add(usuario)
    await db_session.flush()

    registro = PasswordSetToken(
        id=uuid.uuid4(),
        usuario_id=usuario.id,
        token_hash=hash_reset_token(token),
        expira_en=expira_en or (datetime.now(UTC) + timedelta(hours=48)),
        usado=usado,
    )
    db_session.add(registro)
    await db_session.flush()
    return usuario


@pytest.mark.asyncio
async def test_token_valido_establece_la_password(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    usuario = await _crear_usuario_con_token(db_session, "valido@uni.es", token="token-valido-1")

    r = await client.post(ESTABLECER, json={"token": "token-valido-1", "password": _NUEVA_PASS})

    assert r.status_code == 204, r.text

    await db_session.refresh(usuario)
    assert verify_password(_NUEVA_PASS, usuario.password_hash)

    # Login con la nueva contraseña funciona
    r_login = await client.post(
        "/auth/login", json={"email": "valido@uni.es", "password": _NUEVA_PASS}
    )
    assert r_login.status_code == 200


@pytest.mark.asyncio
async def test_token_marca_como_usado_tras_establecer_password(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _crear_usuario_con_token(db_session, "marcaje@uni.es", token="token-marcaje-1")

    r = await client.post(ESTABLECER, json={"token": "token-marcaje-1", "password": _NUEVA_PASS})
    assert r.status_code == 204

    result = await db_session.execute(
        select(PasswordSetToken).where(
            PasswordSetToken.token_hash == hash_reset_token("token-marcaje-1")
        )
    )
    registro = result.scalar_one()
    assert registro.usado is True


@pytest.mark.asyncio
async def test_token_caducado_devuelve_400(client: AsyncClient, db_session: AsyncSession) -> None:
    await _crear_usuario_con_token(
        db_session,
        "caducado@uni.es",
        token="token-caducado-1",
        expira_en=datetime.now(UTC) - timedelta(hours=1),
    )

    r = await client.post(ESTABLECER, json={"token": "token-caducado-1", "password": _NUEVA_PASS})

    assert r.status_code == 400
    assert "cadu" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_token_ya_usado_devuelve_400(client: AsyncClient, db_session: AsyncSession) -> None:
    await _crear_usuario_con_token(
        db_session, "yausado@uni.es", token="token-ya-usado-1", usado=True
    )

    r = await client.post(ESTABLECER, json={"token": "token-ya-usado-1", "password": _NUEVA_PASS})

    assert r.status_code == 400
    assert "utilizado" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_token_inexistente_devuelve_400(client: AsyncClient) -> None:
    r = await client.post(
        ESTABLECER, json={"token": "token-que-nunca-existio", "password": _NUEVA_PASS}
    )

    assert r.status_code == 400
    assert "inválido" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_establecer_password_payload_invalido_devuelve_422(client: AsyncClient) -> None:
    r = await client.post(ESTABLECER, json={"token": "x", "password": "corta"})
    assert r.status_code == 422
