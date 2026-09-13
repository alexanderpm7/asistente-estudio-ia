"""
Tests de los endpoints de administración de usuarios — SPEC §3.4, RF-A1, RF-A4.

Contrato de la API (solo rol admin, RNF-6):
  GET    /admin/usuarios        → 200  lista, filtrable por ?rol=
  POST   /admin/usuarios        → 201  crea usuario con rol arbitrario
  PATCH  /admin/usuarios/{id}   → 200  edita email/rol/activo
  DELETE /admin/usuarios/{id}   → 204  elimina

TDD: los tests se escriben antes que la implementación; deben fallar en rojo.
La importación CSV y el envío de email (RF-A2, RF-A3) también se prueban aquí.
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.db.models import RolUsuario, Usuario

_PASS = "Segura1234!"

USUARIOS = "/admin/usuarios"
IMPORTAR = "/admin/usuarios/importar"


@pytest.fixture(autouse=True)
def _mock_email(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Evita SMTP real en los tests: el envío de email es best-effort.

    Todos los tests de este fichero comparten este mock; los que quieran
    verificar el envío pueden pedirlo como parámetro y hacer aserciones.
    """
    mock = AsyncMock()
    monkeypatch.setattr("app.api.admin.enviar_email_establecer_password", mock)
    return mock


# ── helpers ───────────────────────────────────────────────────────────────────


async def _crear_usuario(
    db_session: AsyncSession,
    email: str,
    rol: RolUsuario,
    nombre: str = "Test",
    activo: bool = True,
) -> Usuario:
    """Crea un usuario directamente en BD con la contraseña conocida `_PASS`."""
    usuario = Usuario(
        id=uuid.uuid4(),
        email=email,
        password_hash=hash_password(_PASS),
        nombre=nombre,
        rol=rol,
        activo=activo,
    )
    db_session.add(usuario)
    await db_session.flush()
    return usuario


async def _login(client: AsyncClient, email: str) -> str:
    r = await client.post("/auth/login", json={"email": email, "password": _PASS})
    token: str = r.json()["access_token"]
    return token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ── GET /admin/usuarios ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_listar_usuarios_sin_token_devuelve_401(client: AsyncClient) -> None:
    r = await client.get(USUARIOS)
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_listar_usuarios_alumno_devuelve_403(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _crear_usuario(db_session, "alu_adm1@uni.es", RolUsuario.alumno)
    token = await _login(client, "alu_adm1@uni.es")

    r = await client.get(USUARIOS, headers=_auth(token))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_listar_usuarios_profesor_devuelve_403(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _crear_usuario(db_session, "prof_adm1@uni.es", RolUsuario.profesor)
    token = await _login(client, "prof_adm1@uni.es")

    r = await client.get(USUARIOS, headers=_auth(token))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_listar_usuarios_admin_devuelve_200_con_lista(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _crear_usuario(db_session, "admin_list1@uni.es", RolUsuario.admin)
    await _crear_usuario(db_session, "alu_list1@uni.es", RolUsuario.alumno)
    await _crear_usuario(db_session, "prof_list1@uni.es", RolUsuario.profesor)
    token = await _login(client, "admin_list1@uni.es")

    r = await client.get(USUARIOS, headers=_auth(token))

    assert r.status_code == 200
    emails = {u["email"] for u in r.json()}
    assert {"admin_list1@uni.es", "alu_list1@uni.es", "prof_list1@uni.es"} <= emails
    # Nunca se filtra la contraseña
    for u in r.json():
        assert "password" not in u
        assert "password_hash" not in u


@pytest.mark.asyncio
async def test_listar_usuarios_filtra_por_rol(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _crear_usuario(db_session, "admin_filt1@uni.es", RolUsuario.admin)
    await _crear_usuario(db_session, "alu_filt1@uni.es", RolUsuario.alumno)
    await _crear_usuario(db_session, "prof_filt1@uni.es", RolUsuario.profesor)
    await _crear_usuario(db_session, "prof_filt2@uni.es", RolUsuario.profesor)
    token = await _login(client, "admin_filt1@uni.es")

    r = await client.get(f"{USUARIOS}?rol=profesor", headers=_auth(token))

    assert r.status_code == 200
    data = r.json()
    assert all(u["rol"] == "profesor" for u in data)
    emails = {u["email"] for u in data}
    assert {"prof_filt1@uni.es", "prof_filt2@uni.es"} <= emails
    assert "alu_filt1@uni.es" not in emails


# ── POST /admin/usuarios ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_crear_usuario_sin_token_devuelve_401(client: AsyncClient) -> None:
    r = await client.post(USUARIOS, json={"email": "x@uni.es", "nombre": "X", "rol": "profesor"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_crear_usuario_alumno_devuelve_403(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _crear_usuario(db_session, "alu_adm2@uni.es", RolUsuario.alumno)
    token = await _login(client, "alu_adm2@uni.es")

    r = await client.post(
        USUARIOS,
        json={"email": "nuevo@uni.es", "nombre": "Nuevo", "rol": "profesor"},
        headers=_auth(token),
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_crear_usuario_profesor_devuelve_403(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _crear_usuario(db_session, "prof_adm2@uni.es", RolUsuario.profesor)
    token = await _login(client, "prof_adm2@uni.es")

    r = await client.post(
        USUARIOS,
        json={"email": "nuevo2@uni.es", "nombre": "Nuevo", "rol": "profesor"},
        headers=_auth(token),
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_admin_crea_usuario_con_rol_profesor_devuelve_201(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _crear_usuario(db_session, "admin_crea1@uni.es", RolUsuario.admin)
    token = await _login(client, "admin_crea1@uni.es")

    r = await client.post(
        USUARIOS,
        json={"email": "nuevo_prof@uni.es", "nombre": "Profesor Nuevo", "rol": "profesor"},
        headers=_auth(token),
    )

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email"] == "nuevo_prof@uni.es"
    assert body["rol"] == "profesor"
    assert body["activo"] is True
    assert "password" not in body
    assert "password_hash" not in body


@pytest.mark.asyncio
async def test_admin_crea_usuario_dispara_email_establecer_password(
    client: AsyncClient, db_session: AsyncSession, _mock_email: AsyncMock
) -> None:
    """Crear un usuario dispara el envío del correo de establecer contraseña (RF-A3)."""
    await _crear_usuario(db_session, "admin_email1@uni.es", RolUsuario.admin)
    token = await _login(client, "admin_email1@uni.es")

    r = await client.post(
        USUARIOS,
        json={"email": "recibe_email@uni.es", "nombre": "Recibe Email", "rol": "alumno"},
        headers=_auth(token),
    )

    assert r.status_code == 201, r.text
    _mock_email.assert_called_once()
    usuario_llamado, token_llamado = _mock_email.call_args.args
    assert usuario_llamado.email == "recibe_email@uni.es"
    assert isinstance(token_llamado, str) and token_llamado


@pytest.mark.asyncio
async def test_admin_crea_usuario_con_rol_admin_devuelve_201(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _crear_usuario(db_session, "admin_crea2@uni.es", RolUsuario.admin)
    token = await _login(client, "admin_crea2@uni.es")

    r = await client.post(
        USUARIOS,
        json={"email": "nuevo_admin@uni.es", "nombre": "Admin Nuevo", "rol": "admin"},
        headers=_auth(token),
    )

    assert r.status_code == 201, r.text
    assert r.json()["rol"] == "admin"


@pytest.mark.asyncio
async def test_admin_crea_usuario_con_rol_alumno_devuelve_201(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _crear_usuario(db_session, "admin_crea3@uni.es", RolUsuario.admin)
    token = await _login(client, "admin_crea3@uni.es")

    r = await client.post(
        USUARIOS,
        json={"email": "nuevo_alu@uni.es", "nombre": "Alumno Nuevo", "rol": "alumno"},
        headers=_auth(token),
    )

    assert r.status_code == 201, r.text
    assert r.json()["rol"] == "alumno"


@pytest.mark.asyncio
async def test_crear_usuario_email_duplicado_devuelve_409(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _crear_usuario(db_session, "admin_dup1@uni.es", RolUsuario.admin)
    await _crear_usuario(db_session, "existente@uni.es", RolUsuario.alumno)
    token = await _login(client, "admin_dup1@uni.es")

    r = await client.post(
        USUARIOS,
        json={"email": "existente@uni.es", "nombre": "Duplicado", "rol": "profesor"},
        headers=_auth(token),
    )

    assert r.status_code == 409


# ── PATCH /admin/usuarios/{id} ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_editar_usuario_sin_token_devuelve_401(client: AsyncClient) -> None:
    fake_id = str(uuid.uuid4())
    r = await client.patch(f"{USUARIOS}/{fake_id}", json={"activo": False})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_editar_usuario_alumno_devuelve_403(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    alu = await _crear_usuario(db_session, "alu_adm3@uni.es", RolUsuario.alumno)
    token = await _login(client, "alu_adm3@uni.es")

    r = await client.patch(f"{USUARIOS}/{alu.id}", json={"activo": False}, headers=_auth(token))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_editar_usuario_profesor_devuelve_403(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    prof = await _crear_usuario(db_session, "prof_adm3@uni.es", RolUsuario.profesor)
    token = await _login(client, "prof_adm3@uni.es")

    r = await client.patch(f"{USUARIOS}/{prof.id}", json={"activo": False}, headers=_auth(token))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_admin_edita_email_devuelve_200(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await _crear_usuario(db_session, "admin_edit1@uni.es", RolUsuario.admin)
    objetivo = await _crear_usuario(db_session, "viejo_email@uni.es", RolUsuario.alumno)
    token = await _login(client, admin.email)

    r = await client.patch(
        f"{USUARIOS}/{objetivo.id}",
        json={"email": "nuevo_email@uni.es"},
        headers=_auth(token),
    )

    assert r.status_code == 200, r.text
    assert r.json()["email"] == "nuevo_email@uni.es"


@pytest.mark.asyncio
async def test_admin_edita_rol_devuelve_200(client: AsyncClient, db_session: AsyncSession) -> None:
    admin = await _crear_usuario(db_session, "admin_edit2@uni.es", RolUsuario.admin)
    objetivo = await _crear_usuario(db_session, "ascenso@uni.es", RolUsuario.alumno)
    token = await _login(client, admin.email)

    r = await client.patch(
        f"{USUARIOS}/{objetivo.id}",
        json={"rol": "profesor"},
        headers=_auth(token),
    )

    assert r.status_code == 200, r.text
    assert r.json()["rol"] == "profesor"


@pytest.mark.asyncio
async def test_admin_edita_estado_activo_devuelve_200(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await _crear_usuario(db_session, "admin_edit3@uni.es", RolUsuario.admin)
    objetivo = await _crear_usuario(db_session, "a_desactivar@uni.es", RolUsuario.alumno)
    token = await _login(client, admin.email)

    r = await client.patch(
        f"{USUARIOS}/{objetivo.id}",
        json={"activo": False},
        headers=_auth(token),
    )

    assert r.status_code == 200, r.text
    assert r.json()["activo"] is False


@pytest.mark.asyncio
async def test_editar_usuario_inexistente_devuelve_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await _crear_usuario(db_session, "admin_edit4@uni.es", RolUsuario.admin)
    token = await _login(client, admin.email)
    fake_id = str(uuid.uuid4())

    r = await client.patch(
        f"{USUARIOS}/{fake_id}",
        json={"activo": False},
        headers=_auth(token),
    )

    assert r.status_code == 404


@pytest.mark.asyncio
async def test_editar_usuario_email_duplicado_devuelve_409(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await _crear_usuario(db_session, "admin_edit5@uni.es", RolUsuario.admin)
    otro = await _crear_usuario(db_session, "ocupado@uni.es", RolUsuario.alumno)
    objetivo = await _crear_usuario(db_session, "libre@uni.es", RolUsuario.alumno)
    token = await _login(client, admin.email)

    r = await client.patch(
        f"{USUARIOS}/{objetivo.id}",
        json={"email": otro.email},
        headers=_auth(token),
    )

    assert r.status_code == 409


@pytest.mark.asyncio
async def test_admin_no_puede_quitarse_a_si_mismo_el_rol_admin(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await _crear_usuario(db_session, "admin_self_rol@uni.es", RolUsuario.admin)
    token = await _login(client, admin.email)

    r = await client.patch(
        f"{USUARIOS}/{admin.id}",
        json={"rol": "alumno"},
        headers=_auth(token),
    )

    assert r.status_code == 400


@pytest.mark.asyncio
async def test_admin_no_puede_desactivarse_a_si_mismo(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await _crear_usuario(db_session, "admin_self_activo@uni.es", RolUsuario.admin)
    token = await _login(client, admin.email)

    r = await client.patch(
        f"{USUARIOS}/{admin.id}",
        json={"activo": False},
        headers=_auth(token),
    )

    assert r.status_code == 400


@pytest.mark.asyncio
async def test_admin_puede_editar_su_propio_email_sin_tocar_rol_ni_activo(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await _crear_usuario(db_session, "admin_self_email@uni.es", RolUsuario.admin)
    token = await _login(client, admin.email)

    r = await client.patch(
        f"{USUARIOS}/{admin.id}",
        json={"email": "admin_self_email_nuevo@uni.es"},
        headers=_auth(token),
    )

    assert r.status_code == 200, r.text
    assert r.json()["email"] == "admin_self_email_nuevo@uni.es"
    assert r.json()["rol"] == "admin"
    assert r.json()["activo"] is True


@pytest.mark.asyncio
async def test_cambiar_email_invalida_token_pendiente_de_establecer_password(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Cambiar el email de un usuario invalida cualquier token de establecer
    contraseña pendiente (evita que el enlace visto por el destinatario
    original siga sirviendo para la cuenta con el nuevo email)."""
    from app.core.email import crear_token_establecer_password

    admin = await _crear_usuario(db_session, "admin_edit6@uni.es", RolUsuario.admin)
    objetivo = await _crear_usuario(db_session, "email_viejo@uni.es", RolUsuario.alumno)
    token_pendiente = await crear_token_establecer_password(objetivo, db_session)
    await db_session.commit()
    token = await _login(client, admin.email)

    r = await client.patch(
        f"{USUARIOS}/{objetivo.id}",
        json={"email": "email_nuevo@uni.es"},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text

    r_set = await client.post(
        "/auth/establecer-password",
        json={"token": token_pendiente, "password": "NuevaPass123456!"},
    )
    assert r_set.status_code == 400


# ── DELETE /admin/usuarios/{id} ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_eliminar_usuario_sin_token_devuelve_401(client: AsyncClient) -> None:
    fake_id = str(uuid.uuid4())
    r = await client.delete(f"{USUARIOS}/{fake_id}")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_eliminar_usuario_alumno_devuelve_403(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    alu = await _crear_usuario(db_session, "alu_adm4@uni.es", RolUsuario.alumno)
    token = await _login(client, "alu_adm4@uni.es")

    r = await client.delete(f"{USUARIOS}/{alu.id}", headers=_auth(token))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_eliminar_usuario_profesor_devuelve_403(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    prof = await _crear_usuario(db_session, "prof_adm4@uni.es", RolUsuario.profesor)
    token = await _login(client, "prof_adm4@uni.es")

    r = await client.delete(f"{USUARIOS}/{prof.id}", headers=_auth(token))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_admin_elimina_usuario_devuelve_204(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await _crear_usuario(db_session, "admin_del1@uni.es", RolUsuario.admin)
    objetivo = await _crear_usuario(db_session, "a_borrar@uni.es", RolUsuario.alumno)
    token = await _login(client, admin.email)

    r = await client.delete(f"{USUARIOS}/{objetivo.id}", headers=_auth(token))

    assert r.status_code == 204

    # Ya no aparece en el listado
    r_list = await client.get(USUARIOS, headers=_auth(token))
    emails = {u["email"] for u in r_list.json()}
    assert "a_borrar@uni.es" not in emails


@pytest.mark.asyncio
async def test_admin_no_puede_eliminarse_a_si_mismo(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await _crear_usuario(db_session, "admin_self@uni.es", RolUsuario.admin)
    token = await _login(client, admin.email)

    r = await client.delete(f"{USUARIOS}/{admin.id}", headers=_auth(token))

    assert r.status_code == 400


@pytest.mark.asyncio
async def test_eliminar_usuario_inexistente_devuelve_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await _crear_usuario(db_session, "admin_del2@uni.es", RolUsuario.admin)
    token = await _login(client, admin.email)
    fake_id = str(uuid.uuid4())

    r = await client.delete(f"{USUARIOS}/{fake_id}", headers=_auth(token))

    assert r.status_code == 404


# ── POST /admin/usuarios/importar (CSV) ─────────────────────────────────────


def _csv_bytes(filas: list[str], header: str = "email,nombre,rol") -> bytes:
    contenido = "\n".join([header, *filas]) + "\n"
    return contenido.encode("utf-8")


@pytest.mark.asyncio
async def test_importar_usuarios_sin_token_devuelve_401(client: AsyncClient) -> None:
    csv_bytes = _csv_bytes(["a@uni.es,A,alumno"])
    r = await client.post(IMPORTAR, files={"file": ("usuarios.csv", csv_bytes, "text/csv")})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_importar_usuarios_alumno_devuelve_403(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _crear_usuario(db_session, "alu_imp1@uni.es", RolUsuario.alumno)
    token = await _login(client, "alu_imp1@uni.es")
    csv_bytes = _csv_bytes(["a@uni.es,A,alumno"])

    r = await client.post(
        IMPORTAR, files={"file": ("usuarios.csv", csv_bytes, "text/csv")}, headers=_auth(token)
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_importar_usuarios_profesor_devuelve_403(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _crear_usuario(db_session, "prof_imp1@uni.es", RolUsuario.profesor)
    token = await _login(client, "prof_imp1@uni.es")
    csv_bytes = _csv_bytes(["a@uni.es,A,alumno"])

    r = await client.post(
        IMPORTAR, files={"file": ("usuarios.csv", csv_bytes, "text/csv")}, headers=_auth(token)
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_importar_usuarios_csv_valido_crea_usuarios(
    client: AsyncClient, db_session: AsyncSession, _mock_email: AsyncMock
) -> None:
    admin = await _crear_usuario(db_session, "admin_imp1@uni.es", RolUsuario.admin)
    token = await _login(client, admin.email)
    csv_bytes = _csv_bytes(
        [
            "csv_alu1@uni.es,Alumno Uno,alumno",
            "csv_prof1@uni.es,Profesor Uno,profesor",
            "csv_admin1@uni.es,Admin Uno,admin",
        ]
    )

    r = await client.post(
        IMPORTAR, files={"file": ("usuarios.csv", csv_bytes, "text/csv")}, headers=_auth(token)
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["creados"] == 3
    assert body["errores"] == []
    assert _mock_email.call_count == 3

    r_list = await client.get(USUARIOS, headers=_auth(token))
    emails = {u["email"] for u in r_list.json()}
    assert {"csv_alu1@uni.es", "csv_prof1@uni.es", "csv_admin1@uni.es"} <= emails


@pytest.mark.asyncio
async def test_importar_usuarios_fila_invalida_no_bloquea_el_resto(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await _crear_usuario(db_session, "admin_imp2@uni.es", RolUsuario.admin)
    token = await _login(client, admin.email)
    csv_bytes = _csv_bytes(
        [
            "csv_ok1@uni.es,Válido Uno,alumno",
            "no-es-un-email,Inválido,alumno",
            "csv_ok2@uni.es,Válido Dos,profesor",
            "csv_ok3@uni.es,Válido Tres,rol_que_no_existe",
        ]
    )

    r = await client.post(
        IMPORTAR, files={"file": ("usuarios.csv", csv_bytes, "text/csv")}, headers=_auth(token)
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["creados"] == 2
    assert len(body["errores"]) == 2
    filas_con_error = {e["fila"] for e in body["errores"]}
    assert filas_con_error == {2, 4}

    r_list = await client.get(USUARIOS, headers=_auth(token))
    emails = {u["email"] for u in r_list.json()}
    assert {"csv_ok1@uni.es", "csv_ok2@uni.es"} <= emails
    assert "csv_ok3@uni.es" not in emails


@pytest.mark.asyncio
async def test_importar_usuarios_email_duplicado_reportado_como_error(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await _crear_usuario(db_session, "admin_imp3@uni.es", RolUsuario.admin)
    await _crear_usuario(db_session, "ya_existe_imp@uni.es", RolUsuario.alumno)
    token = await _login(client, admin.email)
    csv_bytes = _csv_bytes(
        [
            "csv_nuevo_imp@uni.es,Nuevo,alumno",
            "ya_existe_imp@uni.es,Duplicado,alumno",
        ]
    )

    r = await client.post(
        IMPORTAR, files={"file": ("usuarios.csv", csv_bytes, "text/csv")}, headers=_auth(token)
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["creados"] == 1
    assert len(body["errores"]) == 1
    assert body["errores"][0]["fila"] == 2
    assert body["errores"][0]["email"] == "ya_existe_imp@uni.es"


@pytest.mark.asyncio
async def test_importar_usuarios_fichero_no_csv_devuelve_400(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await _crear_usuario(db_session, "admin_imp4@uni.es", RolUsuario.admin)
    token = await _login(client, admin.email)

    r = await client.post(
        IMPORTAR,
        files={"file": ("usuarios.txt", b"esto no es un csv", "text/plain")},
        headers=_auth(token),
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_importar_usuarios_cabecera_invalida_devuelve_400(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await _crear_usuario(db_session, "admin_imp5@uni.es", RolUsuario.admin)
    token = await _login(client, admin.email)
    csv_malformado = b"columna_a,columna_b\nvalor1,valor2\n"

    r = await client.post(
        IMPORTAR,
        files={"file": ("usuarios.csv", csv_malformado, "text/csv")},
        headers=_auth(token),
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_importar_usuarios_fichero_demasiado_grande_devuelve_400(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await _crear_usuario(db_session, "admin_imp6@uni.es", RolUsuario.admin)
    token = await _login(client, admin.email)
    # Filas válidas con relleno hasta superar el límite de 2 MB.
    relleno = "X" * 100
    filas_grandes = [f"sobrecarga{i}@uni.es,{relleno},alumno" for i in range(25_000)]
    csv_grande = _csv_bytes(filas_grandes)
    assert len(csv_grande) > 2 * 1024 * 1024

    with patch("app.api.admin.MAX_CSV_ROWS", 100_000):
        r = await client.post(
            IMPORTAR,
            files={"file": ("usuarios.csv", csv_grande, "text/csv")},
            headers=_auth(token),
        )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_importar_usuarios_supera_maximo_de_filas_devuelve_400(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await _crear_usuario(db_session, "admin_imp7@uni.es", RolUsuario.admin)
    token = await _login(client, admin.email)
    filas = [f"maxfilas{i}@uni.es,Fila,alumno" for i in range(10)]
    csv_bytes = _csv_bytes(filas)

    with patch("app.api.admin.MAX_CSV_ROWS", 5):
        r = await client.post(
            IMPORTAR,
            files={"file": ("usuarios.csv", csv_bytes, "text/csv")},
            headers=_auth(token),
        )
    assert r.status_code == 400
