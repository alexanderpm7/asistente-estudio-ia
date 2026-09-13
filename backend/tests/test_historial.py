"""
Tests del endpoint de historial — SPEC §RF-P3, CU-6.

Contrato de la API:
  GET /asignaturas/{id}/historial/{alumno_id} → 200 | 403 | 404

RNF-6: solo el profesor propietario puede acceder al historial de su asignatura.
TDD: los tests se escriben antes que la implementación.
"""

import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.db.models import RolUsuario, Usuario

_PASS = "Segura1234!"


# ── helpers ───────────────────────────────────────────────────────────────────


async def _register_login(
    client: AsyncClient,
    db_session: AsyncSession,
    email: str,
    rol: str,
    nombre: str = "Usuario Test",
) -> tuple[str, str]:
    """Crea un usuario y hace login; devuelve (token, user_id).

    El registro publico (POST /auth/register) solo admite "alumno" desde el
    cierre del registro de roles privilegiados. Los roles
    profesor/admin se crean directamente en BD, igual que hara el bootstrap.
    """
    user_id: str
    if rol == "alumno":
        r = await client.post(
            "/auth/register",
            json={"email": email, "password": _PASS, "nombre": nombre, "rol": rol},
        )
        user_id = r.json()["id"]
    else:
        usuario = Usuario(
            id=uuid.uuid4(),
            email=email,
            password_hash=hash_password(_PASS),
            nombre=nombre,
            rol=RolUsuario(rol),
        )
        db_session.add(usuario)
        await db_session.flush()
        user_id = str(usuario.id)
    r_login = await client.post("/auth/login", json={"email": email, "password": _PASS})
    token: str = r_login.json()["access_token"]
    return token, user_id


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _crear_asignatura(client: AsyncClient, token: str, nombre: str = "Física") -> str:
    r = await client.post(
        "/asignaturas",
        json={"nombre": nombre, "descripcion": "desc"},
        headers=_auth(token),
    )
    return str(r.json()["id"])


async def _matricular(client: AsyncClient, token: str, asig_id: str, email: str) -> None:
    await client.post(
        f"/asignaturas/{asig_id}/alumnos",
        json={"email": email},
        headers=_auth(token),
    )


# ── tests ─────────────────────────────────────────────────────────────────────


async def test_historial_lista_vacia_sin_mensajes(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Profesor propietario obtiene lista vacía si el alumno no tiene mensajes."""
    token_prof, _ = await _register_login(client, db_session, "prof_h1@test.com", "profesor")
    _, alumno_id = await _register_login(client, db_session, "alu_h1@test.com", "alumno")
    asig_id = await _crear_asignatura(client, token_prof)
    await _matricular(client, token_prof, asig_id, "alu_h1@test.com")

    r = await client.get(
        f"/asignaturas/{asig_id}/historial/{alumno_id}",
        headers=_auth(token_prof),
    )
    assert r.status_code == 200
    assert r.json() == {"mensajes": [], "has_more": False}


async def test_historial_devuelve_mensajes_ordenados(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """El historial devuelve los mensajes del alumno ordenados por creado_en."""
    from app.db.models import Mensaje, RolMensaje

    token_prof, _ = await _register_login(client, db_session, "prof_h2@test.com", "profesor")
    _, alumno_id = await _register_login(client, db_session, "alu_h2@test.com", "alumno")
    asig_id = await _crear_asignatura(client, token_prof, "Química")
    await _matricular(client, token_prof, asig_id, "alu_h2@test.com")

    asig_uuid = uuid.UUID(asig_id)
    alu_uuid = uuid.UUID(alumno_id)
    db_session.add(
        Mensaje(
            id=uuid.uuid4(),
            alumno_id=alu_uuid,
            asignatura_id=asig_uuid,
            rol=RolMensaje.user,
            contenido="¿Qué es la fotosíntesis?",
        )
    )
    db_session.add(
        Mensaje(
            id=uuid.uuid4(),
            alumno_id=alu_uuid,
            asignatura_id=asig_uuid,
            rol=RolMensaje.assistant,
            contenido="La fotosíntesis es...",
        )
    )
    await db_session.commit()

    r = await client.get(
        f"/asignaturas/{asig_id}/historial/{alumno_id}",
        headers=_auth(token_prof),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["has_more"] is False
    data = body["mensajes"]
    assert len(data) == 2
    assert data[0]["contenido"] == "¿Qué es la fotosíntesis?"
    assert data[0]["rol"] == "user"
    assert data[1]["contenido"] == "La fotosíntesis es..."
    assert data[1]["rol"] == "assistant"


async def test_historial_403_profesor_ajeno(client: AsyncClient, db_session: AsyncSession) -> None:
    """Otro profesor no puede acceder al historial de una asignatura ajena."""
    token_prof1, _ = await _register_login(client, db_session, "prof_h3@test.com", "profesor")
    token_prof2, _ = await _register_login(client, db_session, "prof_h4@test.com", "profesor")
    _, alumno_id = await _register_login(client, db_session, "alu_h3@test.com", "alumno")
    asig_id = await _crear_asignatura(client, token_prof1)

    r = await client.get(
        f"/asignaturas/{asig_id}/historial/{alumno_id}",
        headers=_auth(token_prof2),
    )
    assert r.status_code == 403


async def test_historial_403_alumno_no_puede_acceder(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Un alumno no puede acceder al historial (solo profesores, RNF-6)."""
    token_prof, _ = await _register_login(client, db_session, "prof_h5@test.com", "profesor")
    token_alu, alumno_id = await _register_login(client, db_session, "alu_h4@test.com", "alumno")
    asig_id = await _crear_asignatura(client, token_prof)

    r = await client.get(
        f"/asignaturas/{asig_id}/historial/{alumno_id}",
        headers=_auth(token_alu),
    )
    assert r.status_code == 403


async def test_historial_404_asignatura_inexistente(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """404 si la asignatura no existe."""
    token_prof, _ = await _register_login(client, db_session, "prof_h6@test.com", "profesor")
    _, alumno_id = await _register_login(client, db_session, "alu_h5@test.com", "alumno")
    fake_id = str(uuid.uuid4())

    r = await client.get(
        f"/asignaturas/{fake_id}/historial/{alumno_id}",
        headers=_auth(token_prof),
    )
    assert r.status_code == 404


# ── tests: paginación y filtro de fechas (RF-P9) ────────────────────────────────


async def _crear_mensaje(
    db_session: AsyncSession,
    alumno_id: uuid.UUID,
    asignatura_id: uuid.UUID,
    contenido: str,
    creado_en: datetime,
) -> uuid.UUID:
    from app.db.models import Mensaje, RolMensaje

    msg_id = uuid.uuid4()
    db_session.add(
        Mensaje(
            id=msg_id,
            alumno_id=alumno_id,
            asignatura_id=asignatura_id,
            rol=RolMensaje.user,
            contenido=contenido,
            creado_en=creado_en,
        )
    )
    return msg_id


async def test_historial_filtro_fechas_correcto(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """`desde`/`hasta` acotan los mensajes devueltos al rango indicado."""
    token_prof, _ = await _register_login(client, db_session, "prof_fd1@test.com", "profesor")
    _, alumno_id = await _register_login(client, db_session, "alu_fd1@test.com", "alumno")
    asig_id = await _crear_asignatura(client, token_prof, "Filtro fechas")
    await _matricular(client, token_prof, asig_id, "alu_fd1@test.com")

    asig_uuid = uuid.UUID(asig_id)
    alu_uuid = uuid.UUID(alumno_id)
    await _crear_mensaje(
        db_session, alu_uuid, asig_uuid, "fuera-antes", datetime(2026, 1, 1, tzinfo=UTC)
    )
    await _crear_mensaje(
        db_session, alu_uuid, asig_uuid, "dentro", datetime(2026, 1, 10, tzinfo=UTC)
    )
    await _crear_mensaje(
        db_session, alu_uuid, asig_uuid, "fuera-despues", datetime(2026, 1, 20, tzinfo=UTC)
    )
    await db_session.commit()

    r = await client.get(
        f"/asignaturas/{asig_id}/historial/{alumno_id}",
        params={"desde": "2026-01-05", "hasta": "2026-01-15"},
        headers=_auth(token_prof),
    )

    assert r.status_code == 200
    body = r.json()
    assert [m["contenido"] for m in body["mensajes"]] == ["dentro"]
    assert body["has_more"] is False


async def test_historial_rango_vacio_devuelve_lista_vacia(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    token_prof, _ = await _register_login(client, db_session, "prof_fd2@test.com", "profesor")
    _, alumno_id = await _register_login(client, db_session, "alu_fd2@test.com", "alumno")
    asig_id = await _crear_asignatura(client, token_prof, "Rango vacio")
    await _matricular(client, token_prof, asig_id, "alu_fd2@test.com")

    await _crear_mensaje(
        db_session,
        uuid.UUID(alumno_id),
        uuid.UUID(asig_id),
        "unico",
        datetime(2026, 1, 10, tzinfo=UTC),
    )
    await db_session.commit()

    r = await client.get(
        f"/asignaturas/{asig_id}/historial/{alumno_id}",
        params={"desde": "2026-02-01", "hasta": "2026-02-05"},
        headers=_auth(token_prof),
    )

    assert r.status_code == 200
    assert r.json() == {"mensajes": [], "has_more": False}


async def test_historial_paginacion_combinada_con_filtro_fechas(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    token_prof, _ = await _register_login(client, db_session, "prof_fd3@test.com", "profesor")
    _, alumno_id = await _register_login(client, db_session, "alu_fd3@test.com", "alumno")
    asig_id = await _crear_asignatura(client, token_prof, "Paginacion y fechas")
    await _matricular(client, token_prof, asig_id, "alu_fd3@test.com")

    asig_uuid = uuid.UUID(asig_id)
    alu_uuid = uuid.UUID(alumno_id)
    # Fuera de rango, antes y después.
    await _crear_mensaje(
        db_session, alu_uuid, asig_uuid, "fuera-antes", datetime(2026, 2, 20, tzinfo=UTC)
    )
    await _crear_mensaje(
        db_session, alu_uuid, asig_uuid, "fuera-despues", datetime(2026, 3, 15, tzinfo=UTC)
    )
    # 8 mensajes dentro del rango [2026-03-01, 2026-03-08].
    for i in range(8):
        await _crear_mensaje(
            db_session, alu_uuid, asig_uuid, f"dia-{i}", datetime(2026, 3, 1 + i, tzinfo=UTC)
        )
    await db_session.commit()

    params = {"desde": "2026-03-01", "hasta": "2026-03-08", "limit": 5}
    r1 = await client.get(
        f"/asignaturas/{asig_id}/historial/{alumno_id}", params=params, headers=_auth(token_prof)
    )
    assert r1.status_code == 200
    pagina1 = r1.json()
    assert [m["contenido"] for m in pagina1["mensajes"]] == [f"dia-{i}" for i in range(3, 8)]
    assert pagina1["has_more"] is True

    cursor = pagina1["mensajes"][0]["id"]
    r2 = await client.get(
        f"/asignaturas/{asig_id}/historial/{alumno_id}",
        params={**params, "before": cursor},
        headers=_auth(token_prof),
    )
    assert r2.status_code == 200
    pagina2 = r2.json()
    assert [m["contenido"] for m in pagina2["mensajes"]] == [f"dia-{i}" for i in range(0, 3)]
    assert pagina2["has_more"] is False


async def test_historial_403_profesor_ajeno_con_filtro_fechas(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """La autorización de propietario se sigue exigiendo con filtros de fecha."""
    token_prof1, _ = await _register_login(client, db_session, "prof_fd4a@test.com", "profesor")
    token_prof2, _ = await _register_login(client, db_session, "prof_fd4b@test.com", "profesor")
    _, alumno_id = await _register_login(client, db_session, "alu_fd4@test.com", "alumno")
    asig_id = await _crear_asignatura(client, token_prof1, "Ajena")
    await _matricular(client, token_prof1, asig_id, "alu_fd4@test.com")

    r = await client.get(
        f"/asignaturas/{asig_id}/historial/{alumno_id}",
        params={"desde": "2020-01-01", "hasta": "2030-01-01"},
        headers=_auth(token_prof2),
    )
    assert r.status_code == 403


async def test_historial_filtro_fechas_no_filtra_mensajes_de_otro_alumno(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """El historial de un alumno no debe incluir mensajes de otro alumno en la
    misma asignatura y rango de fechas (RNF-6: sin fugas de datos)."""
    token_prof, _ = await _register_login(client, db_session, "prof_fd5@test.com", "profesor")
    _, alumno_x_id = await _register_login(client, db_session, "alu_fd5x@test.com", "alumno")
    _, alumno_y_id = await _register_login(client, db_session, "alu_fd5y@test.com", "alumno")
    asig_id = await _crear_asignatura(client, token_prof, "Compartida")
    await _matricular(client, token_prof, asig_id, "alu_fd5x@test.com")
    await _matricular(client, token_prof, asig_id, "alu_fd5y@test.com")

    asig_uuid = uuid.UUID(asig_id)
    momento = datetime(2026, 1, 10, tzinfo=UTC)
    await _crear_mensaje(db_session, uuid.UUID(alumno_x_id), asig_uuid, "de-x", momento)
    await _crear_mensaje(db_session, uuid.UUID(alumno_y_id), asig_uuid, "de-y", momento)
    await db_session.commit()

    r = await client.get(
        f"/asignaturas/{asig_id}/historial/{alumno_x_id}",
        params={"desde": "2026-01-05", "hasta": "2026-01-15"},
        headers=_auth(token_prof),
    )

    assert r.status_code == 200
    contenidos = [m["contenido"] for m in r.json()["mensajes"]]
    assert contenidos == ["de-x"]
    assert "de-y" not in contenidos


async def test_historial_por_defecto_ultima_semana(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Sin `desde`/`hasta`, se filtra por defecto a la última semana (RF-P9)."""
    token_prof, _ = await _register_login(client, db_session, "prof_fd6@test.com", "profesor")
    _, alumno_id = await _register_login(client, db_session, "alu_fd6@test.com", "alumno")
    asig_id = await _crear_asignatura(client, token_prof, "Default semana")
    await _matricular(client, token_prof, asig_id, "alu_fd6@test.com")

    asig_uuid = uuid.UUID(asig_id)
    alu_uuid = uuid.UUID(alumno_id)
    hace_10_dias = datetime.now(UTC) - timedelta(days=10)
    await _crear_mensaje(db_session, alu_uuid, asig_uuid, "antiguo", hace_10_dias)
    await _crear_mensaje(db_session, alu_uuid, asig_uuid, "reciente", datetime.now(UTC))
    await db_session.commit()

    r = await client.get(
        f"/asignaturas/{asig_id}/historial/{alumno_id}",
        headers=_auth(token_prof),
    )

    assert r.status_code == 200
    contenidos = [m["contenido"] for m in r.json()["mensajes"]]
    assert contenidos == ["reciente"]
