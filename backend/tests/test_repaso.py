"""
Tests del endpoint POST /asignaturas/{id}/repaso — SPEC §RF-E6, RF-IA6, CU-9.

Contrato:
  → 200  alumno matriculado + documento_id válido: JSON con flashcards y
         preguntas generadas a partir de ese documento (agente mockeado)
  → 422  falta el query param documento_id
  → 401  sin token
  → 403  alumno no matriculado en la asignatura
  → 404  asignatura no encontrada
  → 404  documento_id inexistente o de otra asignatura
  → 400  documento sin chunks indexados

TDD: tests escritos antes que la implementación; deben fallar en rojo.
RNF-6: solo alumnos matriculados pueden generar el repaso de su asignatura,
y el documento_id indicado debe pertenecer a esa asignatura.
"""

import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.db.models import Documento, DocumentoChunk, RolUsuario, Usuario

_PASS = "Segura1234!"
_DIM = 1024  # Dimensión de BGE-M3


def _unit_vec(pos: int, dim: int = _DIM) -> list[float]:
    """Vector unitario con 1.0 en la posición `pos` y 0 en el resto."""
    v = [0.0] * dim
    v[pos] = 1.0
    return v


# ── helpers ───────────────────────────────────────────────────────────────────


async def _register_login(
    client: AsyncClient,
    db_session: AsyncSession,
    email: str,
    rol: str,
    nombre: str = "Test",
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
        json={"nombre": nombre, "descripcion": "test"},
        headers=_auth(token),
    )
    return str(r.json()["id"])


async def _matricular(client: AsyncClient, token_prof: str, asig_id: str, email: str) -> None:
    await client.post(
        f"/asignaturas/{asig_id}/alumnos",
        json={"email": email},
        headers=_auth(token_prof),
    )


def _fake_chunks() -> list[dict]:
    """Devuelve una lista mínima de chunks simulados para el agente."""
    return [
        {"texto": f"Contenido chunk {i}", "doc_nombre": "apuntes.pdf", "page": 1} for i in range(3)
    ]


def _fake_repaso_output() -> dict:
    """Respuesta estructurada que devuelve el LLM mockeado."""
    return {
        "flashcards": [
            {"anverso": "¿Qué es la energía cinética?", "reverso": "Es la energía del movimiento."},
            {"anverso": "¿Qué es la energía potencial?", "reverso": "Es la energía almacenada."},
            {"anverso": "¿Qué es la masa?", "reverso": "Cantidad de materia de un cuerpo."},
            {"anverso": "¿Qué es la velocidad?", "reverso": "Distancia por unidad de tiempo."},
            {"anverso": "¿Qué es la aceleración?", "reverso": "Cambio de velocidad en el tiempo."},
        ],
        "preguntas": [
            {
                "enunciado": "¿Cuál es la unidad de energía en el SI?",
                "opciones": ["Julio", "Newton", "Pascal", "Voltio"],
                "respuesta_correcta": 0,
            },
            {
                "enunciado": "¿Qué fórmula expresa la energía cinética?",
                "opciones": ["E=mc²", "E=½mv²", "F=ma", "P=mv"],
                "respuesta_correcta": 1,
            },
            {
                "enunciado": "¿Qué conserva el principio de conservación de la energía?",
                "opciones": ["La masa", "La velocidad", "La energía total", "La temperatura"],
                "respuesta_correcta": 2,
            },
        ],
    }


# ── tests ─────────────────────────────────────────────────────────────────────


async def test_repaso_401_sin_token(client: AsyncClient) -> None:
    """Sin JWT → 401."""
    fake_id = uuid.uuid4()
    r = await client.post(
        f"/asignaturas/{fake_id}/repaso", params={"documento_id": str(uuid.uuid4())}
    )
    assert r.status_code == 401


async def test_repaso_sin_documento_id_devuelve_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Falta el query param documento_id → 422 (validación de FastAPI)."""
    token, _ = await _register_login(client, db_session, "alu_rep_422@uni.es", "alumno")
    fake_id = uuid.uuid4()
    r = await client.post(f"/asignaturas/{fake_id}/repaso", headers=_auth(token))
    assert r.status_code == 422


async def test_repaso_404_asignatura_no_existe(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """UUID válido pero asignatura inexistente → 404."""
    token, _ = await _register_login(client, db_session, "alu_rep1@uni.es", "alumno")
    fake_id = uuid.uuid4()
    r = await client.post(
        f"/asignaturas/{fake_id}/repaso",
        params={"documento_id": str(uuid.uuid4())},
        headers=_auth(token),
    )
    assert r.status_code == 404


async def test_repaso_403_alumno_no_matriculado(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Alumno sin matrícula en la asignatura → 403."""
    token_prof, _ = await _register_login(client, db_session, "prof_rep1@uni.es", "profesor")
    token_alu, _ = await _register_login(client, db_session, "alu_rep2@uni.es", "alumno")
    asig_id = await _crear_asignatura(client, token_prof, "Química")
    # No matriculamos al alumno

    r = await client.post(
        f"/asignaturas/{asig_id}/repaso",
        params={"documento_id": str(uuid.uuid4())},
        headers=_auth(token_alu),
    )
    assert r.status_code == 403


async def test_repaso_documento_id_inexistente_devuelve_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """documento_id que no existe en ningún sitio → 404."""
    token_prof, _ = await _register_login(client, db_session, "prof_rep_404a@uni.es", "profesor")
    token_alu, _ = await _register_login(client, db_session, "alu_rep_404a@uni.es", "alumno")
    asig_id = await _crear_asignatura(client, token_prof, "Doc Inexistente")
    await _matricular(client, token_prof, asig_id, "alu_rep_404a@uni.es")

    r = await client.post(
        f"/asignaturas/{asig_id}/repaso",
        params={"documento_id": str(uuid.uuid4())},
        headers=_auth(token_alu),
    )
    assert r.status_code == 404


async def test_repaso_documento_id_de_otra_asignatura_devuelve_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """documento_id existe pero pertenece a otra asignatura → 404 (RNF-6)."""
    token_prof, _ = await _register_login(client, db_session, "prof_rep_404b@uni.es", "profesor")
    token_alu, _ = await _register_login(client, db_session, "alu_rep_404b@uni.es", "alumno")
    asig_id = await _crear_asignatura(client, token_prof, "Asignatura A")
    otra_asig_id = await _crear_asignatura(client, token_prof, "Asignatura B")
    await _matricular(client, token_prof, asig_id, "alu_rep_404b@uni.es")

    otro_doc = Documento(
        asignatura_id=uuid.UUID(otra_asig_id),
        nombre="otro.pdf",
        ruta="/uploads/otro.pdf",
    )
    db_session.add(otro_doc)
    await db_session.commit()

    r = await client.post(
        f"/asignaturas/{asig_id}/repaso",
        params={"documento_id": str(otro_doc.id)},
        headers=_auth(token_alu),
    )
    assert r.status_code == 404


async def test_repaso_400_documento_sin_chunks_indexados(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """documento_id válido y de la asignatura, pero sin chunks indexados → 400.

    No requiere Ollama: se mockea el embedder (OllamaEmbeddings) y el
    retrieval real (sin mockear) devuelve lista vacía al no haber chunks.
    """
    token_prof, _ = await _register_login(client, db_session, "prof_rep2@uni.es", "profesor")
    token_alu, _ = await _register_login(client, db_session, "alu_rep3@uni.es", "alumno")
    asig_id = await _crear_asignatura(client, token_prof, "Historia")
    await _matricular(client, token_prof, asig_id, "alu_rep3@uni.es")

    doc = Documento(asignatura_id=uuid.UUID(asig_id), nombre="vacio.pdf", ruta="/uploads/vacio.pdf")
    db_session.add(doc)
    await db_session.commit()

    fake_embedder = AsyncMock()
    fake_embedder.aembed_query.return_value = _unit_vec(0)

    with patch("app.api.repaso.OllamaEmbeddings", return_value=fake_embedder):
        r = await client.post(
            f"/asignaturas/{asig_id}/repaso",
            params={"documento_id": str(doc.id)},
            headers=_auth(token_alu),
        )

    assert r.status_code == 400
    assert "documento" in r.json()["detail"].lower()


async def test_repaso_filtra_retrieval_por_documento_id(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Con documento_id, solo se recuperan chunks de ese documento (SPEC §RF-E6).

    Inserta chunks reales de dos documentos distintos en la misma asignatura
    y mockea solo el embedder (no retrieve_chunks) para comprobar el filtro
    SQL real por documento_id.
    """
    token_prof, _ = await _register_login(client, db_session, "prof_rep_doc@uni.es", "profesor")
    token_alu, _ = await _register_login(client, db_session, "alu_rep_doc@uni.es", "alumno")
    asig_id = await _crear_asignatura(client, token_prof, "Multi-Documento")
    await _matricular(client, token_prof, asig_id, "alu_rep_doc@uni.es")
    asig_uuid = uuid.UUID(asig_id)

    doc1 = Documento(asignatura_id=asig_uuid, nombre="doc1.pdf", ruta="/uploads/doc1.pdf")
    doc2 = Documento(asignatura_id=asig_uuid, nombre="doc2.pdf", ruta="/uploads/doc2.pdf")
    db_session.add_all([doc1, doc2])
    await db_session.flush()

    db_session.add_all(
        [
            DocumentoChunk(
                documento_id=doc1.id,
                asignatura_id=asig_uuid,
                chunk_index=0,
                texto="Contenido del documento 1",
                embedding=_unit_vec(0),
                chunk_metadata={"page": 1},
            ),
            DocumentoChunk(
                documento_id=doc2.id,
                asignatura_id=asig_uuid,
                chunk_index=0,
                texto="Contenido del documento 2",
                embedding=_unit_vec(0),
                chunk_metadata={"page": 1},
            ),
        ]
    )
    await db_session.commit()

    capturado: dict[str, Any] = {}

    async def _fake_generar_repaso_llm(chunks: list[dict]) -> dict:
        capturado["chunks"] = chunks
        return _fake_repaso_output()

    fake_embedder = AsyncMock()
    fake_embedder.aembed_query.return_value = _unit_vec(0)

    with (
        patch("app.api.repaso.OllamaEmbeddings", return_value=fake_embedder),
        patch("app.api.repaso._generar_repaso_llm", new=_fake_generar_repaso_llm),
    ):
        r = await client.post(
            f"/asignaturas/{asig_id}/repaso",
            params={"documento_id": str(doc1.id)},
            headers=_auth(token_alu),
        )

    assert r.status_code == 200, r.text
    assert capturado["chunks"], "No se llamó al generador con chunks"
    assert all(c["doc_nombre"] == "doc1.pdf" for c in capturado["chunks"])


@pytest.mark.integration
async def test_repaso_happy_path_devuelve_flashcards_y_preguntas(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Alumno matriculado con documentos → 200 con flashcards y preguntas.

    Requiere Ollama real: el endpoint embede la consulta genérica de resumen
    (OllamaEmbeddings, sin mockear) antes de llegar al `retrieve_chunks` mockeado.
    """
    token_prof, _ = await _register_login(client, db_session, "prof_rep3@uni.es", "profesor")
    token_alu, _ = await _register_login(client, db_session, "alu_rep4@uni.es", "alumno")
    asig_id = await _crear_asignatura(client, token_prof, "Biología")
    await _matricular(client, token_prof, asig_id, "alu_rep4@uni.es")

    doc = Documento(
        asignatura_id=uuid.UUID(asig_id), nombre="apuntes.pdf", ruta="/uploads/apuntes.pdf"
    )
    db_session.add(doc)
    await db_session.commit()

    salida = _fake_repaso_output()

    # Mock de retrieve_chunks (devuelve chunks simulados) y del LLM con_structured_output
    with (
        patch("app.api.repaso.retrieve_chunks", new=AsyncMock(return_value=_fake_chunks())),
        patch("app.api.repaso._generar_repaso_llm", new=AsyncMock(return_value=salida)),
    ):
        r = await client.post(
            f"/asignaturas/{asig_id}/repaso",
            params={"documento_id": str(doc.id)},
            headers=_auth(token_alu),
        )

    assert r.status_code == 200, r.text
    data = r.json()

    assert "flashcards" in data
    assert "preguntas" in data
    assert len(data["flashcards"]) >= 1
    assert len(data["preguntas"]) >= 1

    fc = data["flashcards"][0]
    assert "anverso" in fc and "reverso" in fc

    pq = data["preguntas"][0]
    assert "enunciado" in pq
    assert "opciones" in pq
    assert "respuesta_correcta" in pq
    assert isinstance(pq["respuesta_correcta"], int)
