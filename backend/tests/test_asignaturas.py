"""
Tests de gestión de asignaturas — SPEC §RF-P1, RF-P2, CU-1.

Contrato de la API:
  POST   /asignaturas                          → 201  (profesor autenticado)
  GET    /asignaturas                          → 200  (lista filtrada por rol)
  GET    /asignaturas/{id}                     → 200 | 403 | 404
  PUT    /asignaturas/{id}                     → 200  (solo el profesor propietario)
  DELETE /asignaturas/{id}                     → 204  (solo el profesor propietario)
  POST   /asignaturas/{id}/alumnos             → 201  (alta por email, RF-P2)
  DELETE /asignaturas/{id}/alumnos/{alumno_id} → 204  (baja)

TDD: los tests se escriben antes que la implementación; deben fallar en rojo.
RNF-6: ningún usuario accede a datos que no le correspondan.
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_password
from app.db.models import Asignatura, RolUsuario, Usuario

# ── helpers ───────────────────────────────────────────────────────────────────

_PASS = "Segura1234!"


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


async def _crear_asignatura(
    client: AsyncClient,
    token: str,
    nombre: str = "Álgebra",
    descripcion: str = "Descripción de prueba",
) -> str:
    """Crea una asignatura como profesor y devuelve su id."""
    r = await client.post(
        "/asignaturas",
        json={"nombre": nombre, "descripcion": descripcion},
        headers=_auth(token),
    )
    return str(r.json()["id"])


# ── RF-P1: CRUD de asignaturas ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_profesor_crea_asignatura_devuelve_201(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    token, _ = await _register_login(client, db_session, "prof@uni.es", "profesor")

    r = await client.post(
        "/asignaturas",
        json={"nombre": "Cálculo I", "descripcion": "Derivadas e integrales"},
        headers=_auth(token),
    )

    assert r.status_code == 201
    body = r.json()
    assert body["nombre"] == "Cálculo I"
    assert "id" in body
    assert "profesor_id" in body

    # Sin prompt_redactor explícito, la asignatura nace con una copia del
    # estilo base (RF-P8): nunca queda None.
    from app.agents.graph import PROMPT_ESTILO_BASE

    asig = await db_session.get(Asignatura, body["id"])
    assert asig is not None
    assert asig.prompt_redactor == PROMPT_ESTILO_BASE


@pytest.mark.asyncio
async def test_crear_asignatura_sin_token_devuelve_401(client: AsyncClient) -> None:
    r = await client.post("/asignaturas", json={"nombre": "Sin Auth"})

    assert r.status_code == 401


@pytest.mark.asyncio
async def test_alumno_no_puede_crear_asignatura_devuelve_403(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    token, _ = await _register_login(client, db_session, "alumno@uni.es", "alumno")

    r = await client.post(
        "/asignaturas",
        json={"nombre": "Intruso"},
        headers=_auth(token),
    )

    assert r.status_code == 403


@pytest.mark.asyncio
async def test_profesor_lista_sus_asignaturas(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    token, _ = await _register_login(client, db_session, "prof@uni.es", "profesor")
    await _crear_asignatura(client, token, "Física I")
    await _crear_asignatura(client, token, "Física II")

    r = await client.get("/asignaturas", headers=_auth(token))

    assert r.status_code == 200
    nombres = [a["nombre"] for a in r.json()]
    assert "Física I" in nombres
    assert "Física II" in nombres


@pytest.mark.asyncio
async def test_get_asignatura_por_id_devuelve_200(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    token, _ = await _register_login(client, db_session, "prof@uni.es", "profesor")
    asig_id = await _crear_asignatura(client, token)

    r = await client.get(f"/asignaturas/{asig_id}", headers=_auth(token))

    assert r.status_code == 200
    assert r.json()["id"] == asig_id


@pytest.mark.asyncio
async def test_get_asignatura_inexistente_devuelve_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    token, _ = await _register_login(client, db_session, "prof@uni.es", "profesor")
    fake_id = "00000000-0000-0000-0000-000000000000"

    r = await client.get(f"/asignaturas/{fake_id}", headers=_auth(token))

    assert r.status_code == 404


@pytest.mark.asyncio
async def test_profesor_actualiza_asignatura_devuelve_200(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    token, _ = await _register_login(client, db_session, "prof@uni.es", "profesor")
    asig_id = await _crear_asignatura(client, token, "Nombre Viejo")

    r = await client.put(
        f"/asignaturas/{asig_id}",
        json={"nombre": "Nombre Nuevo", "descripcion": "Actualizada"},
        headers=_auth(token),
    )

    assert r.status_code == 200
    assert r.json()["nombre"] == "Nombre Nuevo"


@pytest.mark.asyncio
async def test_profesor_ajeno_no_puede_actualizar_devuelve_403(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    token_a, _ = await _register_login(client, db_session, "prof_a@uni.es", "profesor")
    token_b, _ = await _register_login(client, db_session, "prof_b@uni.es", "profesor")
    asig_id = await _crear_asignatura(client, token_a)

    r = await client.put(
        f"/asignaturas/{asig_id}",
        json={"nombre": "Hackeada"},
        headers=_auth(token_b),
    )

    assert r.status_code == 403


# ── RF-P8: prompt del redactor por asignatura ───────────────────────────────


@pytest.mark.asyncio
async def test_profesor_crea_asignatura_con_prompt_redactor(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """El profesor puede definir prompt_redactor al crear la asignatura."""
    token, _ = await _register_login(client, db_session, "prof_pr1@uni.es", "profesor")

    r = await client.post(
        "/asignaturas",
        json={
            "nombre": "Historia del Arte",
            "descripcion": "Prueba",
            "prompt_redactor": "Responde siempre con un tono cercano y con ejemplos.",
        },
        headers=_auth(token),
    )

    assert r.status_code == 201
    asig_id = r.json()["id"]

    asig = await db_session.get(Asignatura, asig_id)
    assert asig is not None
    assert asig.prompt_redactor == "Responde siempre con un tono cercano y con ejemplos."


@pytest.mark.asyncio
async def test_profesor_edita_prompt_redactor_devuelve_200(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """El profesor propietario puede editar prompt_redactor con PUT."""
    token, _ = await _register_login(client, db_session, "prof_pr2@uni.es", "profesor")
    asig_id = await _crear_asignatura(client, token, "Geografía")

    r = await client.put(
        f"/asignaturas/{asig_id}",
        json={"prompt_redactor": "Usa un lenguaje muy sencillo, apto para 1º de ESO."},
        headers=_auth(token),
    )

    assert r.status_code == 200

    asig = await db_session.get(Asignatura, asig_id)
    assert asig is not None
    assert asig.prompt_redactor == "Usa un lenguaje muy sencillo, apto para 1º de ESO."


@pytest.mark.asyncio
async def test_editar_prompt_redactor_no_afecta_a_otra_asignatura_ni_al_estilo_base(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """La copia de prompt_redactor es independiente por asignatura (RF-P8)."""
    from app.agents.graph import PROMPT_ESTILO_BASE

    token, _ = await _register_login(client, db_session, "prof_pr5@uni.es", "profesor")
    asig_a_id = await _crear_asignatura(client, token, "Latín")
    asig_b_id = await _crear_asignatura(client, token, "Griego")

    r = await client.put(
        f"/asignaturas/{asig_a_id}",
        json={"prompt_redactor": "Responde siempre citando autores clásicos."},
        headers=_auth(token),
    )
    assert r.status_code == 200

    asig_a = await db_session.get(Asignatura, asig_a_id)
    asig_b = await db_session.get(Asignatura, asig_b_id)
    assert asig_a is not None
    assert asig_b is not None
    assert asig_a.prompt_redactor == "Responde siempre citando autores clásicos."
    # La otra asignatura conserva su propia copia del estilo base, sin cambios.
    assert asig_b.prompt_redactor == PROMPT_ESTILO_BASE


@pytest.mark.asyncio
async def test_prompt_redactor_excede_longitud_maxima_devuelve_422(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Un prompt_redactor más largo que el máximo configurado se rechaza (422)."""
    token, _ = await _register_login(client, db_session, "prof_pr3@uni.es", "profesor")
    prompt_demasiado_largo = "x" * (settings.prompt_redactor_max_length + 1)

    r = await client.post(
        "/asignaturas",
        json={"nombre": "Química", "prompt_redactor": prompt_demasiado_largo},
        headers=_auth(token),
    )

    assert r.status_code == 422


@pytest.mark.asyncio
async def test_profesor_ajeno_no_puede_modificar_prompt_redactor(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Solo el profesor propietario puede modificar prompt_redactor (RF-P8)."""
    from app.agents.graph import PROMPT_ESTILO_BASE

    token_a, _ = await _register_login(client, db_session, "prof_pr4a@uni.es", "profesor")
    token_b, _ = await _register_login(client, db_session, "prof_pr4b@uni.es", "profesor")
    asig_id = await _crear_asignatura(client, token_a, "Filosofía")

    r = await client.put(
        f"/asignaturas/{asig_id}",
        json={"prompt_redactor": "Instrucciones coladas por un profesor ajeno."},
        headers=_auth(token_b),
    )

    assert r.status_code == 403

    asig = await db_session.get(Asignatura, asig_id)
    assert asig is not None
    # Sigue con su copia del estilo base, sin el intento de escritura ajeno.
    assert asig.prompt_redactor == PROMPT_ESTILO_BASE


# ── GET /asignaturas/{id}/prompt-redactor ───────────────────────────────────


@pytest.mark.asyncio
async def test_propietario_lee_prompt_redactor_devuelve_200(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """El profesor propietario puede leer el prompt_redactor de su asignatura."""
    from app.agents.graph import PROMPT_ESTILO_BASE

    token, _ = await _register_login(client, db_session, "prof_pr6@uni.es", "profesor")
    asig_id = await _crear_asignatura(client, token, "Latín II")

    r = await client.get(f"/asignaturas/{asig_id}/prompt-redactor", headers=_auth(token))

    assert r.status_code == 200
    assert r.json()["prompt_redactor"] == PROMPT_ESTILO_BASE


@pytest.mark.asyncio
async def test_leer_prompt_redactor_sin_token_devuelve_401(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    token, _ = await _register_login(client, db_session, "prof_pr7@uni.es", "profesor")
    asig_id = await _crear_asignatura(client, token, "Latín III")

    r = await client.get(f"/asignaturas/{asig_id}/prompt-redactor")

    assert r.status_code == 401


@pytest.mark.asyncio
async def test_profesor_ajeno_no_puede_leer_prompt_redactor_devuelve_403(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    token_a, _ = await _register_login(client, db_session, "prof_pr8a@uni.es", "profesor")
    token_b, _ = await _register_login(client, db_session, "prof_pr8b@uni.es", "profesor")
    asig_id = await _crear_asignatura(client, token_a, "Latín IV")

    r = await client.get(f"/asignaturas/{asig_id}/prompt-redactor", headers=_auth(token_b))

    assert r.status_code == 403


@pytest.mark.asyncio
async def test_alumno_no_puede_leer_prompt_redactor_devuelve_403(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """El prompt_redactor no debe quedar accesible a alumnos (RF-P8)."""
    token_prof, _ = await _register_login(client, db_session, "prof_pr9@uni.es", "profesor")
    token_alu, _ = await _register_login(client, db_session, "alu_pr9@uni.es", "alumno")
    asig_id = await _crear_asignatura(client, token_prof, "Latín V")
    await client.post(
        f"/asignaturas/{asig_id}/alumnos",
        json={"email": "alu_pr9@uni.es"},
        headers=_auth(token_prof),
    )

    r = await client.get(f"/asignaturas/{asig_id}/prompt-redactor", headers=_auth(token_alu))

    assert r.status_code == 403


@pytest.mark.asyncio
async def test_leer_prompt_redactor_asignatura_inexistente_devuelve_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    token, _ = await _register_login(client, db_session, "prof_pr10@uni.es", "profesor")
    fake_id = "00000000-0000-0000-0000-000000000000"

    r = await client.get(f"/asignaturas/{fake_id}/prompt-redactor", headers=_auth(token))

    assert r.status_code == 404


@pytest.mark.asyncio
async def test_profesor_elimina_asignatura_devuelve_204(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    token, _ = await _register_login(client, db_session, "prof@uni.es", "profesor")
    asig_id = await _crear_asignatura(client, token)

    r = await client.delete(f"/asignaturas/{asig_id}", headers=_auth(token))

    assert r.status_code == 204


@pytest.mark.asyncio
async def test_profesor_ajeno_no_puede_eliminar_devuelve_403(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    token_a, _ = await _register_login(client, db_session, "prof_a@uni.es", "profesor")
    token_b, _ = await _register_login(client, db_session, "prof_b@uni.es", "profesor")
    asig_id = await _crear_asignatura(client, token_a)

    r = await client.delete(f"/asignaturas/{asig_id}", headers=_auth(token_b))

    assert r.status_code == 403


# ── RF-P2: gestión de alumnos ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_matricular_alumno_por_email_devuelve_201(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    token_prof, _ = await _register_login(client, db_session, "prof@uni.es", "profesor")
    await _register_login(client, db_session, "alumno@uni.es", "alumno")
    asig_id = await _crear_asignatura(client, token_prof)

    r = await client.post(
        f"/asignaturas/{asig_id}/alumnos",
        json={"email": "alumno@uni.es"},
        headers=_auth(token_prof),
    )

    assert r.status_code == 201
    body = r.json()
    assert "alumno_id" in body
    assert body["asignatura_id"] == asig_id


@pytest.mark.asyncio
async def test_matricular_email_inexistente_devuelve_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    token_prof, _ = await _register_login(client, db_session, "prof@uni.es", "profesor")
    asig_id = await _crear_asignatura(client, token_prof)

    r = await client.post(
        f"/asignaturas/{asig_id}/alumnos",
        json={"email": "noexiste@uni.es"},
        headers=_auth(token_prof),
    )

    assert r.status_code == 404


@pytest.mark.asyncio
async def test_alumno_no_puede_matricular_devuelve_403(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    token_prof, _ = await _register_login(client, db_session, "prof@uni.es", "profesor")
    token_alumno, _ = await _register_login(client, db_session, "alumno@uni.es", "alumno")
    asig_id = await _crear_asignatura(client, token_prof)

    r = await client.post(
        f"/asignaturas/{asig_id}/alumnos",
        json={"email": "alumno@uni.es"},
        headers=_auth(token_alumno),
    )

    assert r.status_code == 403


@pytest.mark.asyncio
async def test_desmatricular_alumno_devuelve_204(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    token_prof, _ = await _register_login(client, db_session, "prof@uni.es", "profesor")
    _, alumno_id = await _register_login(client, db_session, "alumno@uni.es", "alumno")
    asig_id = await _crear_asignatura(client, token_prof)
    await client.post(
        f"/asignaturas/{asig_id}/alumnos",
        json={"email": "alumno@uni.es"},
        headers=_auth(token_prof),
    )

    r = await client.delete(
        f"/asignaturas/{asig_id}/alumnos/{alumno_id}",
        headers=_auth(token_prof),
    )

    assert r.status_code == 204


# ── Autocompletado de email al matricular ───────────────────────────────────


@pytest.mark.asyncio
async def test_buscar_alumnos_por_email_devuelve_coincidencias_parciales(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    token_prof, _ = await _register_login(client, db_session, "prof@uni.es", "profesor")
    await _register_login(client, db_session, "ana.garcia@uni.es", "alumno")
    await _register_login(client, db_session, "ana.lopez@uni.es", "alumno")
    await _register_login(client, db_session, "carlos@uni.es", "alumno")

    r = await client.get(
        "/asignaturas/alumnos/buscar",
        params={"q": "ana."},
        headers=_auth(token_prof),
    )

    assert r.status_code == 200
    emails = r.json()
    assert set(emails) == {"ana.garcia@uni.es", "ana.lopez@uni.es"}


@pytest.mark.asyncio
async def test_buscar_alumnos_por_email_no_devuelve_profesores_ni_admin(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Solo emails con rol alumno — nunca profesores ni admins (SPEC: sin
    datos adicionales sensibles, y solo el rol relevante para matricular)."""
    token_prof, _ = await _register_login(client, db_session, "prof.buscar@uni.es", "profesor")
    await _register_login(client, db_session, "buscar.otro@uni.es", "profesor")
    await _register_login(client, db_session, "buscar.admin@uni.es", "admin")
    await _register_login(client, db_session, "buscar.alumno@uni.es", "alumno")

    r = await client.get(
        "/asignaturas/alumnos/buscar",
        params={"q": "buscar."},
        headers=_auth(token_prof),
    )

    assert r.status_code == 200
    assert r.json() == ["buscar.alumno@uni.es"]


@pytest.mark.asyncio
async def test_buscar_alumnos_por_email_alumno_devuelve_403(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    token_alumno, _ = await _register_login(client, db_session, "alu.buscar@uni.es", "alumno")

    r = await client.get(
        "/asignaturas/alumnos/buscar",
        params={"q": "alu"},
        headers=_auth(token_alumno),
    )

    assert r.status_code == 403


@pytest.mark.asyncio
async def test_buscar_alumnos_por_email_sin_token_devuelve_401(client: AsyncClient) -> None:
    r = await client.get("/asignaturas/alumnos/buscar", params={"q": "alu"})

    assert r.status_code == 401


@pytest.mark.asyncio
async def test_buscar_alumnos_por_email_query_corta_devuelve_422(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """min_length=3: evita listar el registro completo de alumnos con una
    consulta demasiado corta (p. ej. una sola letra)."""
    token_prof, _ = await _register_login(client, db_session, "prof.corta@uni.es", "profesor")

    r = await client.get(
        "/asignaturas/alumnos/buscar",
        params={"q": "an"},
        headers=_auth(token_prof),
    )

    assert r.status_code == 422


# ── RNF-6: autorización por pertenencia ───────────────────────────────────


@pytest.mark.asyncio
async def test_alumno_no_puede_ver_asignatura_ajena_devuelve_403(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """
    Alumno B no matriculado en una asignatura no puede acceder a ella.
    Verifica RNF-6: un alumno solo accede a SUS asignaturas.
    """
    token_prof, _ = await _register_login(client, db_session, "prof@uni.es", "profesor")
    token_a, _ = await _register_login(client, db_session, "alumno_a@uni.es", "alumno", "Alumno A")
    token_b, _ = await _register_login(client, db_session, "alumno_b@uni.es", "alumno", "Alumno B")
    asig_id = await _crear_asignatura(client, token_prof, "Solo para A")

    # Solo el alumno A está matriculado
    await client.post(
        f"/asignaturas/{asig_id}/alumnos",
        json={"email": "alumno_a@uni.es"},
        headers=_auth(token_prof),
    )

    # Alumno A sí puede acceder
    r_a = await client.get(f"/asignaturas/{asig_id}", headers=_auth(token_a))
    assert r_a.status_code == 200

    # Alumno B NO puede acceder (RNF-6)
    r_b = await client.get(f"/asignaturas/{asig_id}", headers=_auth(token_b))
    assert r_b.status_code == 403


# ── GET /asignaturas/{id}/alumnos ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_listar_alumnos_devuelve_matriculados(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    token_prof, _ = await _register_login(client, db_session, "prof_la@uni.es", "profesor")
    _, alumno_id = await _register_login(
        client, db_session, "alu_la@uni.es", "alumno", "Ana García"
    )
    asig_id = await _crear_asignatura(client, token_prof)
    await client.post(
        f"/asignaturas/{asig_id}/alumnos",
        json={"email": "alu_la@uni.es"},
        headers=_auth(token_prof),
    )

    r = await client.get(f"/asignaturas/{asig_id}/alumnos", headers=_auth(token_prof))

    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["alumno_id"] == alumno_id
    assert data[0]["email"] == "alu_la@uni.es"
    assert data[0]["nombre"] == "Ana García"
    assert "matriculado_en" in data[0]


@pytest.mark.asyncio
async def test_listar_alumnos_403_profesor_ajeno(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    token_prof1, _ = await _register_login(client, db_session, "prof_la2@uni.es", "profesor")
    token_prof2, _ = await _register_login(client, db_session, "prof_la3@uni.es", "profesor")
    asig_id = await _crear_asignatura(client, token_prof1)

    r = await client.get(f"/asignaturas/{asig_id}/alumnos", headers=_auth(token_prof2))
    assert r.status_code == 403


# ── GET /asignaturas/{id}/documentos ──────────────────────────────────────


@pytest.mark.asyncio
async def test_listar_documentos_devuelve_lista_vacia(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    token_prof, _ = await _register_login(client, db_session, "prof_ld@uni.es", "profesor")
    asig_id = await _crear_asignatura(client, token_prof)

    r = await client.get(f"/asignaturas/{asig_id}/documentos", headers=_auth(token_prof))

    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_listar_documentos_403_profesor_ajeno(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    token_prof1, _ = await _register_login(client, db_session, "prof_ld2@uni.es", "profesor")
    token_prof2, _ = await _register_login(client, db_session, "prof_ld3@uni.es", "profesor")
    asig_id = await _crear_asignatura(client, token_prof1)

    r = await client.get(f"/asignaturas/{asig_id}/documentos", headers=_auth(token_prof2))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_listar_documentos_alumno_matriculado_devuelve_200(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Un alumno matriculado puede listar los documentos (selector de repaso, RF-E6)."""
    token_prof, _ = await _register_login(client, db_session, "prof_ld4@uni.es", "profesor")
    token_alu, _ = await _register_login(client, db_session, "alu_ld4@uni.es", "alumno")
    asig_id = await _crear_asignatura(client, token_prof)
    await client.post(
        f"/asignaturas/{asig_id}/alumnos",
        json={"email": "alu_ld4@uni.es"},
        headers=_auth(token_prof),
    )

    r = await client.get(f"/asignaturas/{asig_id}/documentos", headers=_auth(token_alu))

    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_listar_documentos_403_alumno_no_matriculado(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Un alumno no matriculado no puede listar los documentos (RNF-6)."""
    token_prof, _ = await _register_login(client, db_session, "prof_ld5@uni.es", "profesor")
    token_alu, _ = await _register_login(client, db_session, "alu_ld5@uni.es", "alumno")
    asig_id = await _crear_asignatura(client, token_prof)
    # No matriculamos al alumno

    r = await client.get(f"/asignaturas/{asig_id}/documentos", headers=_auth(token_alu))

    assert r.status_code == 403
