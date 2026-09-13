"""
Tests de autenticación — SPEC §RF-E1.

Registro y login con correo y contraseña. Los tests se escriben antes que
la implementación (TDD): deben fallar en rojo hasta que existan los endpoints.
"""

import pytest
from httpx import AsyncClient

REGISTER = "/auth/register"
LOGIN = "/auth/login"
ME = "/auth/me"

_USER = {
    "email": "alumno@universidad.es",
    "password": "ContraseñaSegura123!",
    "nombre": "Ada Lovelace",
    "rol": "alumno",
}


# ---------------------------------------------------------------------------
# Registro (POST /auth/register)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_devuelve_201_y_datos_publicos(client: AsyncClient) -> None:
    """Registrar un usuario nuevo devuelve 201 con email, nombre e id."""
    r = await client.post(REGISTER, json=_USER)

    assert r.status_code == 201
    body = r.json()
    assert body["email"] == _USER["email"]
    assert body["nombre"] == _USER["nombre"]
    assert "id" in body
    # Nunca se devuelve la contraseña
    assert "password" not in body
    assert "password_hash" not in body


@pytest.mark.asyncio
async def test_register_email_duplicado_devuelve_409(client: AsyncClient) -> None:
    """Intentar registrar el mismo correo dos veces devuelve 409 Conflict."""
    await client.post(REGISTER, json=_USER)
    r = await client.post(REGISTER, json=_USER)

    assert r.status_code == 409


@pytest.mark.asyncio
async def test_register_payload_invalido_devuelve_422(client: AsyncClient) -> None:
    """Payload sin email devuelve 422 Unprocessable Entity (validación Pydantic)."""
    r = await client.post(REGISTER, json={"password": "abc", "nombre": "X"})

    assert r.status_code == 422


@pytest.mark.asyncio
async def test_register_rol_profesor_devuelve_422(client: AsyncClient) -> None:
    """El registro público rechaza el rol 'profesor' (registro cerrado)."""
    r = await client.post(
        REGISTER,
        json={**_USER, "email": "falso_prof@universidad.es", "rol": "profesor"},
    )

    assert r.status_code == 422


@pytest.mark.asyncio
async def test_register_rol_admin_devuelve_422(client: AsyncClient) -> None:
    """El registro público rechaza el rol 'admin' (registro cerrado)."""
    r = await client.post(
        REGISTER,
        json={**_USER, "email": "falso_admin@universidad.es", "rol": "admin"},
    )

    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Login (POST /auth/login)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_credenciales_correctas_devuelve_200_con_token(
    client: AsyncClient,
) -> None:
    """Login con credenciales válidas devuelve 200 y un JWT bearer token."""
    await client.post(REGISTER, json=_USER)
    r = await client.post(LOGIN, json={"email": _USER["email"], "password": _USER["password"]})

    assert r.status_code == 200
    body = r.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_password_incorrecto_devuelve_401(client: AsyncClient) -> None:
    """Contraseña incorrecta devuelve 401 Unauthorized."""
    await client.post(REGISTER, json=_USER)
    r = await client.post(LOGIN, json={"email": _USER["email"], "password": "mal_password"})

    assert r.status_code == 401


@pytest.mark.asyncio
async def test_login_email_inexistente_devuelve_401(client: AsyncClient) -> None:
    """Email no registrado devuelve 401 (no se distingue de password incorrecto)."""
    r = await client.post(
        LOGIN, json={"email": "noexiste@universidad.es", "password": "cualquiera"}
    )

    assert r.status_code == 401


# ---------------------------------------------------------------------------
# GET /auth/me
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_me_devuelve_datos_del_usuario_autenticado(client: AsyncClient) -> None:
    """GET /auth/me devuelve email, nombre y rol del usuario con token válido."""
    await client.post(REGISTER, json=_USER)
    login_r = await client.post(
        LOGIN, json={"email": _USER["email"], "password": _USER["password"]}
    )
    token = login_r.json()["access_token"]

    r = await client.get(ME, headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 200
    body = r.json()
    assert body["email"] == _USER["email"]
    assert body["nombre"] == _USER["nombre"]
    assert body["rol"] == _USER["rol"]
    assert "password_hash" not in body


@pytest.mark.asyncio
async def test_me_sin_token_devuelve_401(client: AsyncClient) -> None:
    r = await client.get(ME)
    assert r.status_code == 401
