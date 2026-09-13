"""
Tests del endpoint de chat — SPEC §RF-E3, CU-4, CU-5.

Contrato esperado:
  POST /chat/{asignatura_id}   body: {"mensaje": str}
  → 200  content-type: text/event-stream
         al menos un evento  data: <contenido no vacío>
  → 401  sin token
  → 403  alumno no matriculado
  → 404  asignatura inexistente

TDD: los tests se escriben antes que la implementación; deben fallar en rojo.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.db.models import Mensaje as MensajeModel
from app.db.models import RolMensaje, RolUsuario, Usuario

_PASS = "Segura1234!"


# ── helpers ────────────────────────────────────────────────────────────────────


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


async def _user_id(client: AsyncClient, token: str) -> str:
    r = await client.get("/auth/me", headers=_auth(token))
    id_: str = r.json()["id"]
    return id_


async def _setup_asignatura_con_alumno(
    client: AsyncClient,
    db_session: AsyncSession,
) -> tuple[str, str, str]:
    """Crea profesor, asignatura y alumno matriculado.

    Devuelve (token_alumno, token_prof, asig_id).
    """
    token_prof, _ = await _register_login(client, db_session, "prof@uni.es", "profesor", "Profesor")
    token_alumno, _ = await _register_login(client, db_session, "alumno@uni.es", "alumno", "Alumno")

    r = await client.post(
        "/asignaturas",
        json={"nombre": "Álgebra Lineal", "descripcion": "Vectores y matrices"},
        headers=_auth(token_prof),
    )
    asig_id: str = r.json()["id"]

    await client.post(
        f"/asignaturas/{asig_id}/alumnos",
        json={"email": "alumno@uni.es"},
        headers=_auth(token_prof),
    )
    return token_alumno, token_prof, asig_id


# ── tests ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_chat_devuelve_stream_sse_con_datos(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """
    Alumno matriculado recibe un stream SSE con al menos un evento data: no vacío.
    Verifica RF-E3 (respuestas en streaming). Requiere Ollama real (invoca el grafo).
    """
    token_alumno, _, asig_id = await _setup_asignatura_con_alumno(client, db_session)

    data_events: list[str] = []
    async with client.stream(
        "POST",
        f"/chat/{asig_id}",
        json={"mensaje": "¿Qué es un vector?"},
        headers=_auth(token_alumno),
    ) as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")

        async for line in response.aiter_lines():
            if line.startswith("data:"):
                content = line[len("data:") :].strip()
                if content:
                    data_events.append(content)

    assert data_events, "No se recibió ningún evento data: no vacío en el stream SSE"


@pytest.mark.asyncio
async def test_chat_sin_token_devuelve_401(client: AsyncClient, db_session: AsyncSession) -> None:
    _, _, asig_id = await _setup_asignatura_con_alumno(client, db_session)

    r = await client.post(f"/chat/{asig_id}", json={"mensaje": "Hola"})

    assert r.status_code == 401


@pytest.mark.asyncio
async def test_chat_alumno_no_matriculado_devuelve_403(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, token_prof, asig_id = await _setup_asignatura_con_alumno(client, db_session)
    token_b, _ = await _register_login(client, db_session, "otro@uni.es", "alumno", "Otro")

    r = await client.post(
        f"/chat/{asig_id}",
        json={"mensaje": "Intento de acceso"},
        headers=_auth(token_b),
    )

    assert r.status_code == 403


@pytest.mark.asyncio
async def test_chat_asignatura_inexistente_devuelve_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    token_alumno, _, _ = await _setup_asignatura_con_alumno(client, db_session)
    fake_id = "00000000-0000-0000-0000-000000000000"

    r = await client.post(
        f"/chat/{fake_id}",
        json={"mensaje": "Hola"},
        headers=_auth(token_alumno),
    )

    assert r.status_code == 404


# ── tests: disponibilidad de Ollama y métricas del turno assistant ─────────────


class _FakeGraph:
    """Grafo falso: emite un token del nodo redactor y expone un estado final fijo.

    Evita depender de Ollama real para probar la comprobación de disponibilidad
    (RF-E3) y la persistencia de métricas (uso_rag/n_iteraciones/latencia_ms).
    """

    def __init__(self, uso_rag: bool, iteracion: int, token: str = "Hola") -> None:
        self._uso_rag = uso_rag
        self._iteracion = iteracion
        self._token = token

    async def astream_events(self, *_args: object, **_kwargs: object):
        yield {
            "event": "on_chat_model_stream",
            "metadata": {"langgraph_node": "redactor"},
            "run_id": "run-1",
            "data": {"chunk": SimpleNamespace(content=self._token)},
        }

    async def aget_state(self, _config: object) -> SimpleNamespace:
        return SimpleNamespace(values={"uso_rag": self._uso_rag, "iteracion": self._iteracion})


@pytest.mark.asyncio
async def test_chat_ollama_no_disponible_devuelve_503(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """RF-E3: si Ollama está caído, 503 con mensaje claro antes de abrir el stream SSE."""
    token_alumno, _, asig_id = await _setup_asignatura_con_alumno(client, db_session)

    with patch("app.api.chat.ollama_disponible", AsyncMock(return_value=False)):
        r = await client.post(
            f"/chat/{asig_id}",
            json={"mensaje": "Hola"},
            headers=_auth(token_alumno),
        )

    assert r.status_code == 503
    assert "no está disponible" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_chat_provider_openai_no_comprueba_ollama(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Con LLM_PROVIDER=openai, la comprobación de Ollama no se ejecuta y el
    flujo de chat normal sigue devolviendo SSE (no se bloquea sin motivo)."""
    from app.core.config import settings

    token_alumno, _, asig_id = await _setup_asignatura_con_alumno(client, db_session)
    monkeypatch.setattr(settings, "llm_provider", "openai")

    async def _fallar_si_se_llama(*_a: object, **_kw: object) -> bool:
        raise AssertionError("ollama_disponible no debe llamarse con LLM_PROVIDER=openai")

    with (
        patch("app.api.chat.ollama_disponible", _fallar_si_se_llama),
        patch(
            "app.api.chat.build_graph",
            return_value=_FakeGraph(uso_rag=False, iteracion=0, token="Hola"),
        ),
    ):
        data_events: list[str] = []
        async with client.stream(
            "POST",
            f"/chat/{asig_id}",
            json={"mensaje": "Hola"},
            headers=_auth(token_alumno),
        ) as response:
            assert response.status_code == 200
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    content = line[len("data:") :].strip()
                    if content:
                        data_events.append(content)

    assert data_events == ["Hola"]


@pytest.mark.asyncio
async def test_chat_flujo_normal_sigue_devolviendo_sse(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Con Ollama disponible, el chat sigue devolviendo un stream SSE normal."""
    token_alumno, _, asig_id = await _setup_asignatura_con_alumno(client, db_session)

    with (
        patch("app.api.chat.ollama_disponible", AsyncMock(return_value=True)),
        patch(
            "app.api.chat.build_graph",
            return_value=_FakeGraph(uso_rag=False, iteracion=0, token="Hola"),
        ),
    ):
        data_events: list[str] = []
        async with client.stream(
            "POST",
            f"/chat/{asig_id}",
            json={"mensaje": "Hola"},
            headers=_auth(token_alumno),
        ) as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers.get("content-type", "")
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    content = line[len("data:") :].strip()
                    if content:
                        data_events.append(content)

    assert data_events == ["Hola"]


@pytest.mark.asyncio
async def test_chat_persiste_uso_rag_iteraciones_y_latencia(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """El Mensaje del asistente persiste uso_rag/n_iteraciones/latencia_ms tomados
    del estado final de LangGraph, no valores fijos ni derivados de otra parte."""
    token_alumno, _, asig_id = await _setup_asignatura_con_alumno(client, db_session)
    alumno_id = await _user_id(client, token_alumno)

    with (
        patch("app.api.chat.ollama_disponible", AsyncMock(return_value=True)),
        patch(
            "app.api.chat.build_graph",
            return_value=_FakeGraph(uso_rag=True, iteracion=2, token="Respuesta con RAG."),
        ),
    ):
        async with client.stream(
            "POST",
            f"/chat/{asig_id}",
            json={"mensaje": "¿Qué es un vector?"},
            headers=_auth(token_alumno),
        ) as response:
            assert response.status_code == 200
            async for _line in response.aiter_lines():
                pass

    # Acotar por asignatura_id/alumno_id, no solo por rol: en una BD con
    # historial real acumulado (no un contenedor vacío), "rol == assistant"
    # sin más filtro puede devolver mensajes de otras asignaturas/alumnos.
    result = await db_session.execute(
        select(MensajeModel).where(
            MensajeModel.rol == RolMensaje.assistant,
            MensajeModel.asignatura_id == uuid.UUID(asig_id),
            MensajeModel.alumno_id == uuid.UUID(alumno_id),
        )
    )
    mensaje_asistente = result.scalar_one()
    assert mensaje_asistente.uso_rag is True
    assert mensaje_asistente.n_iteraciones == 2
    assert mensaje_asistente.latencia_ms is not None
    assert mensaje_asistente.latencia_ms > 0


# ── tests: GET /chat/{asignatura_id}/historial ──────────────────────────────────


@pytest.mark.asyncio
async def test_historial_propio_vacio(client: AsyncClient, db_session: AsyncSession) -> None:
    """Alumno matriculado obtiene lista vacía si no tiene mensajes (RF-E4)."""
    token_alumno, _, asig_id = await _setup_asignatura_con_alumno(client, db_session)

    r = await client.get(f"/chat/{asig_id}/historial", headers=_auth(token_alumno))

    assert r.status_code == 200
    assert r.json() == {"mensajes": [], "has_more": False}


@pytest.mark.asyncio
async def test_historial_propio_devuelve_mensajes_ordenados(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """El historial devuelve los mensajes propios del alumno en orden cronológico."""
    from app.db.models import Mensaje as MensajeModel
    from app.db.models import RolMensaje

    token_prof, _ = await _register_login(client, db_session, "prof_hist@uni.es", "profesor")
    token_alumno, alumno_id = await _register_login(client, db_session, "alu_hist@uni.es", "alumno")

    r = await client.post(
        "/asignaturas",
        json={"nombre": "Historia", "descripcion": "desc"},
        headers=_auth(token_prof),
    )
    asig_id: str = r.json()["id"]
    await client.post(
        f"/asignaturas/{asig_id}/alumnos",
        json={"email": "alu_hist@uni.es"},
        headers=_auth(token_prof),
    )

    asig_uuid = uuid.UUID(asig_id)
    alu_uuid = uuid.UUID(alumno_id)
    db_session.add(
        MensajeModel(
            id=uuid.uuid4(),
            alumno_id=alu_uuid,
            asignatura_id=asig_uuid,
            rol=RolMensaje.user,
            contenido="¿Qué causó la Primera Guerra Mundial?",
        )
    )
    db_session.add(
        MensajeModel(
            id=uuid.uuid4(),
            alumno_id=alu_uuid,
            asignatura_id=asig_uuid,
            rol=RolMensaje.assistant,
            contenido="La Primera Guerra Mundial fue causada por...",
        )
    )
    await db_session.commit()

    r = await client.get(f"/chat/{asig_id}/historial", headers=_auth(token_alumno))

    assert r.status_code == 200
    body = r.json()
    assert body["has_more"] is False
    data = body["mensajes"]
    assert len(data) == 2
    assert data[0]["rol"] == "user"
    assert data[1]["rol"] == "assistant"
    assert "Primera Guerra" in data[0]["contenido"]


@pytest.mark.asyncio
async def test_historial_propio_401_sin_token(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Sin autenticación devuelve 401."""
    _, _, asig_id = await _setup_asignatura_con_alumno(client, db_session)

    r = await client.get(f"/chat/{asig_id}/historial")

    assert r.status_code == 401


@pytest.mark.asyncio
async def test_historial_propio_403_no_matriculado(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Alumno no matriculado obtiene 403 (RNF-6)."""
    _, _, asig_id = await _setup_asignatura_con_alumno(client, db_session)
    token_otro, _ = await _register_login(client, db_session, "ajeno_hist@uni.es", "alumno")

    r = await client.get(f"/chat/{asig_id}/historial", headers=_auth(token_otro))

    assert r.status_code == 403


@pytest.mark.asyncio
async def test_historial_propio_404_asignatura_inexistente(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """404 si la asignatura no existe."""
    token_alumno, _, _ = await _setup_asignatura_con_alumno(client, db_session)
    fake_id = str(uuid.uuid4())

    r = await client.get(f"/chat/{fake_id}/historial", headers=_auth(token_alumno))

    assert r.status_code == 404


# ── tests: paginación por cursor (RF-E7) ────────────────────────────────────


async def _crear_mensajes(
    db_session: AsyncSession,
    alumno_id: uuid.UUID,
    asignatura_id: uuid.UUID,
    n: int,
) -> list[uuid.UUID]:
    """Crea `n` mensajes con timestamps estrictamente crecientes (msg-0 .. msg-{n-1}).

    Timestamps explícitos y distintos (no el default `now()` del server, que
    coincidiría entre mensajes de la misma transacción) para poder razonar
    sobre el orden cronológico exacto en las aserciones de paginación.
    """
    from datetime import UTC, datetime, timedelta

    from app.db.models import Mensaje as MensajeModel
    from app.db.models import RolMensaje

    base = datetime(2026, 1, 1, tzinfo=UTC)
    ids: list[uuid.UUID] = []
    for i in range(n):
        msg_id = uuid.uuid4()
        db_session.add(
            MensajeModel(
                id=msg_id,
                alumno_id=alumno_id,
                asignatura_id=asignatura_id,
                rol=RolMensaje.user,
                contenido=f"msg-{i}",
                creado_en=base + timedelta(minutes=i),
            )
        )
        ids.append(msg_id)
    await db_session.commit()
    return ids


@pytest.mark.asyncio
async def test_historial_propio_pagina_inicial_devuelve_ultimos_n(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Sin `before`, devuelve los `limit` mensajes más recientes (por defecto 20)."""
    token_alumno, _, asig_id = await _setup_asignatura_con_alumno(client, db_session)
    await _crear_mensajes(
        db_session, uuid.UUID(await _user_id(client, token_alumno)), uuid.UUID(asig_id), 25
    )

    r = await client.get(f"/chat/{asig_id}/historial", headers=_auth(token_alumno))

    assert r.status_code == 200
    body = r.json()
    assert body["has_more"] is True
    contenidos = [m["contenido"] for m in body["mensajes"]]
    assert len(contenidos) == 20
    # Los 20 más recientes de 25 (msg-0..msg-24) son msg-5..msg-24, en orden cronológico.
    assert contenidos == [f"msg-{i}" for i in range(5, 25)]


@pytest.mark.asyncio
async def test_historial_propio_cursor_before_devuelve_pagina_anterior(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    token_alumno, _, asig_id = await _setup_asignatura_con_alumno(client, db_session)
    await _crear_mensajes(
        db_session, uuid.UUID(await _user_id(client, token_alumno)), uuid.UUID(asig_id), 12
    )

    r1 = await client.get(
        f"/chat/{asig_id}/historial", params={"limit": 5}, headers=_auth(token_alumno)
    )
    assert r1.status_code == 200
    pagina1 = r1.json()
    assert pagina1["has_more"] is True
    assert [m["contenido"] for m in pagina1["mensajes"]] == [f"msg-{i}" for i in range(7, 12)]

    cursor = pagina1["mensajes"][0]["id"]  # el más antiguo de la página 1 (msg-7)
    r2 = await client.get(
        f"/chat/{asig_id}/historial",
        params={"limit": 5, "before": cursor},
        headers=_auth(token_alumno),
    )
    assert r2.status_code == 200
    pagina2 = r2.json()
    assert pagina2["has_more"] is True
    assert [m["contenido"] for m in pagina2["mensajes"]] == [f"msg-{i}" for i in range(2, 7)]


@pytest.mark.asyncio
async def test_historial_propio_fin_del_historial_has_more_false(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    token_alumno, _, asig_id = await _setup_asignatura_con_alumno(client, db_session)
    await _crear_mensajes(
        db_session, uuid.UUID(await _user_id(client, token_alumno)), uuid.UUID(asig_id), 12
    )

    cursor_r = await client.get(
        f"/chat/{asig_id}/historial", params={"limit": 5}, headers=_auth(token_alumno)
    )
    cursor = cursor_r.json()["mensajes"][0]["id"]  # msg-7
    pagina2 = (
        await client.get(
            f"/chat/{asig_id}/historial",
            params={"limit": 5, "before": cursor},
            headers=_auth(token_alumno),
        )
    ).json()
    cursor2 = pagina2["mensajes"][0]["id"]  # msg-2

    r3 = await client.get(
        f"/chat/{asig_id}/historial",
        params={"limit": 5, "before": cursor2},
        headers=_auth(token_alumno),
    )
    assert r3.status_code == 200
    pagina3 = r3.json()
    assert pagina3["has_more"] is False
    assert [m["contenido"] for m in pagina3["mensajes"]] == ["msg-0", "msg-1"]


@pytest.mark.asyncio
async def test_historial_propio_limit_fuera_de_rango_devuelve_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    token_alumno, _, asig_id = await _setup_asignatura_con_alumno(client, db_session)

    r_bajo = await client.get(
        f"/chat/{asig_id}/historial", params={"limit": 4}, headers=_auth(token_alumno)
    )
    r_alto = await client.get(
        f"/chat/{asig_id}/historial", params={"limit": 21}, headers=_auth(token_alumno)
    )

    assert r_bajo.status_code == 422
    assert r_alto.status_code == 422
