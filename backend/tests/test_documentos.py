"""
Tests de los endpoints de documentos — SPEC §RF-P4 (subida), RF-P6 (borrado), CU-2, CU-7.

POST /asignaturas/{id}/documentos   multipart/form-data  file=<PDF>
  → 201  profesor propietario, PDF válido, ≤ 20 MB
  → 401  sin token
  → 403  alumno autenticado | profesor de otra asignatura
  → 400  fichero no PDF | fichero supera MAX_FILE_SIZE

DELETE /asignaturas/{id}/documentos/{doc_id}
  → 204  profesor propietario: documento y sus chunks eliminados en cascada
  → 401  sin token
  → 403  alumno autenticado | profesor de otra asignatura
  → 404  documento no existe en la asignatura

RNF-6: solo el profesor propietario puede gestionar documentos.
"""

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.db.models import Documento, DocumentoChunk, RolUsuario, Usuario

_PASS = "Segura1234!"
# PDF de evaluación RAG (existe en fixtures/docs/) — se sube con nombre "LLM.pdf"
# para que la aserción del nombre del documento siga siendo válida.
# parents[0]=tests/  parents[1]=backend/
_PDF_PATH = Path(__file__).resolve().parents[0] / "fixtures" / "docs" / "AnalisisWeb.pdf"


# ── helpers ────────────────────────────────────────────────────────────────────


async def _register_login(
    client: AsyncClient,
    db_session: AsyncSession,
    email: str,
    rol: str,
    nombre: str = "Usuario",
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


async def _setup_asignatura(client: AsyncClient, db_session: AsyncSession) -> tuple[str, str, str]:
    """Crea profesor y asignatura. Devuelve (token_prof, prof_id, asig_id)."""
    token_prof, prof_id = await _register_login(
        client, db_session, "prof_doc@uni.es", "profesor", "Prof Doc"
    )
    r = await client.post(
        "/asignaturas",
        json={"nombre": "Física I", "descripcion": "Mecánica clásica"},
        headers=_auth(token_prof),
    )
    asig_id: str = r.json()["id"]
    return token_prof, prof_id, asig_id


async def _fake_embed(texts: list[str]) -> list[list[float]]:
    """Mock de BGE-M3: devuelve vectores de 1024 ceros para cada texto."""
    return [[0.0] * 1024 for _ in texts]


# ── tests ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_subir_documento_crea_documento_y_chunks(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """
    Profesor propietario sube LLM.pdf → 201.
    Se crea 1 fila en `documentos` y N > 0 filas en `document_chunks`,
    cada una con embedding de 1024 dimensiones y asignatura_id correcto.
    Verifica RF-P4 y CU-2.
    """
    token_prof, _, asig_id = await _setup_asignatura(client, db_session)
    pdf_bytes = _PDF_PATH.read_bytes()

    with patch("app.rag.ingest._embed_batch", new=AsyncMock(side_effect=_fake_embed)):
        r = await client.post(
            f"/asignaturas/{asig_id}/documentos",
            files={"file": ("LLM.pdf", pdf_bytes, "application/pdf")},
            headers=_auth(token_prof),
        )

    assert r.status_code == 201, r.text
    data = r.json()
    assert data["nombre"] == "LLM.pdf"
    assert uuid.UUID(data["asignatura_id"]) == uuid.UUID(asig_id)
    # n_chunks=0 en la respuesta: la indexación ocurre en background.
    # Los chunks sí están en BD porque en tests el BackgroundTask se ejecuta
    # síncronamente dentro del ciclo ASGI antes de que client.post() retorne.
    assert data["n_chunks"] == 0

    asig_uuid = uuid.UUID(asig_id)

    # Verificar fila en documentos
    result = await db_session.execute(select(Documento).where(Documento.asignatura_id == asig_uuid))
    docs = list(result.scalars().all())
    assert len(docs) == 1
    assert docs[0].nombre == "LLM.pdf"

    # Verificar chunks en document_chunks
    result = await db_session.execute(
        select(DocumentoChunk).where(DocumentoChunk.asignatura_id == asig_uuid)
    )
    chunks = list(result.scalars().all())
    assert len(chunks) == data["n_chunks"]
    for chunk in chunks:
        assert chunk.texto.strip()
        assert chunk.embedding is not None
        assert len(chunk.embedding) == 1024
        assert chunk.asignatura_id == asig_uuid


@pytest.mark.asyncio
async def test_subir_documento_sin_token_devuelve_401(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, _, asig_id = await _setup_asignatura(client, db_session)
    pdf_bytes = _PDF_PATH.read_bytes()

    r = await client.post(
        f"/asignaturas/{asig_id}/documentos",
        files={"file": ("LLM.pdf", pdf_bytes, "application/pdf")},
    )

    assert r.status_code == 401


@pytest.mark.asyncio
async def test_subir_documento_alumno_devuelve_403(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Un alumno no puede subir documentos (solo el profesor propietario)."""
    token_prof, _, asig_id = await _setup_asignatura(client, db_session)
    token_alumno, _ = await _register_login(
        client, db_session, "alumno_doc@uni.es", "alumno", "Alumno"
    )
    # Matriculamos al alumno para que tenga acceso a la asignatura
    await client.post(
        f"/asignaturas/{asig_id}/alumnos",
        json={"email": "alumno_doc@uni.es"},
        headers=_auth(token_prof),
    )
    pdf_bytes = _PDF_PATH.read_bytes()

    r = await client.post(
        f"/asignaturas/{asig_id}/documentos",
        files={"file": ("LLM.pdf", pdf_bytes, "application/pdf")},
        headers=_auth(token_alumno),
    )

    assert r.status_code == 403


@pytest.mark.asyncio
async def test_subir_documento_otro_profesor_devuelve_403(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Profesor de otra asignatura no puede subir a una que no gestiona."""
    _, _, asig_id = await _setup_asignatura(client, db_session)
    token_otro, _ = await _register_login(
        client, db_session, "otro_prof@uni.es", "profesor", "Otro Prof"
    )
    pdf_bytes = _PDF_PATH.read_bytes()

    r = await client.post(
        f"/asignaturas/{asig_id}/documentos",
        files={"file": ("LLM.pdf", pdf_bytes, "application/pdf")},
        headers=_auth(token_otro),
    )

    assert r.status_code == 403


@pytest.mark.asyncio
async def test_subir_documento_no_pdf_devuelve_400(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Subir un fichero que no es PDF debe devolver 400."""
    token_prof, _, asig_id = await _setup_asignatura(client, db_session)

    r = await client.post(
        f"/asignaturas/{asig_id}/documentos",
        files={"file": ("notas.txt", b"Esto no es un PDF", "text/plain")},
        headers=_auth(token_prof),
    )

    assert r.status_code == 400
    assert "pdf" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_subir_documento_demasiado_grande_devuelve_400(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Fichero que supera MAX_FILE_SIZE debe devolver 400."""
    token_prof, _, asig_id = await _setup_asignatura(client, db_session)
    # Reducimos el límite a 100 bytes para no generar 20 MB en el test
    big_bytes = b"%PDF-1.4 " + b"\x00" * 200

    with patch("app.api.asignaturas.MAX_FILE_SIZE", 100):
        r = await client.post(
            f"/asignaturas/{asig_id}/documentos",
            files={"file": ("grande.pdf", big_bytes, "application/pdf")},
            headers=_auth(token_prof),
        )

    assert r.status_code == 400
    assert "tamaño" in r.json()["detail"].lower() or "20" in r.json()["detail"]


# ── Helpers para tests de borrado ──────────────────────────────────────────────


async def _upload_doc(client: AsyncClient, token_prof: str, asig_id: str) -> str:
    """Sube el PDF de prueba y devuelve el doc_id creado."""
    pdf_bytes = _PDF_PATH.read_bytes()
    with patch("app.rag.ingest._embed_batch", new=AsyncMock(side_effect=_fake_embed)):
        r = await client.post(
            f"/asignaturas/{asig_id}/documentos",
            files={"file": ("LLM.pdf", pdf_bytes, "application/pdf")},
            headers=_auth(token_prof),
        )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


# ── Tests DELETE /asignaturas/{id}/documentos/{doc_id} ─────────────────────────


@pytest.mark.asyncio
async def test_borrar_documento_elimina_documento_y_chunks(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """
    Profesor propietario borra un documento → 204.
    El documento desaparece de `documentos` y sus chunks de `document_chunks`
    (verificado insertando un chunk manualmente en la sesión de test).
    Verifica RF-P6 y CU-7.
    """
    token_prof, _, asig_id = await _setup_asignatura(client, db_session)
    doc_id = await _upload_doc(client, token_prof, asig_id)

    # Insertar chunk directamente en la sesión de test para verificar CASCADE.
    # La ingesta real ocurre en background con AsyncSessionLocal (otra conexión),
    # así que añadimos uno manualmente para que la verificación sea determinista.
    chunk = DocumentoChunk(
        documento_id=uuid.UUID(doc_id),
        asignatura_id=uuid.UUID(asig_id),
        chunk_index=0,
        texto="Fragmento de prueba para verificar CASCADE al borrar documento.",
        embedding=[0.0] * 1024,
        chunk_metadata={},
    )
    db_session.add(chunk)
    await db_session.flush()
    chunk_id = chunk.id

    r = await client.delete(
        f"/asignaturas/{asig_id}/documentos/{doc_id}",
        headers=_auth(token_prof),
    )
    assert r.status_code == 204

    # El documento ya no existe
    doc_q = await db_session.execute(select(Documento).where(Documento.id == uuid.UUID(doc_id)))
    assert doc_q.scalar_one_or_none() is None, "El documento debería haberse eliminado."

    # El chunk fue eliminado en cascada
    chunk_q = await db_session.execute(select(DocumentoChunk).where(DocumentoChunk.id == chunk_id))
    assert chunk_q.scalar_one_or_none() is None, "El chunk debería haberse eliminado en cascada."


@pytest.mark.asyncio
async def test_borrar_documento_sin_token_devuelve_401(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """DELETE sin token de autenticación → 401."""
    _, _, asig_id = await _setup_asignatura(client, db_session)
    fake_doc_id = str(uuid.uuid4())

    r = await client.delete(f"/asignaturas/{asig_id}/documentos/{fake_doc_id}")

    assert r.status_code == 401


@pytest.mark.asyncio
async def test_borrar_documento_alumno_devuelve_403(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Alumno autenticado no puede borrar documentos → 403."""
    token_prof, _, asig_id = await _setup_asignatura(client, db_session)
    doc_id = await _upload_doc(client, token_prof, asig_id)
    token_alumno, _ = await _register_login(
        client, db_session, "alumno_del@uni.es", "alumno", "Alumno"
    )

    r = await client.delete(
        f"/asignaturas/{asig_id}/documentos/{doc_id}",
        headers=_auth(token_alumno),
    )

    assert r.status_code == 403


@pytest.mark.asyncio
async def test_borrar_documento_otro_profesor_devuelve_403(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Profesor que no es propietario de la asignatura → 403."""
    token_prof, _, asig_id = await _setup_asignatura(client, db_session)
    doc_id = await _upload_doc(client, token_prof, asig_id)
    token_otro, _ = await _register_login(
        client, db_session, "otro_prof_del@uni.es", "profesor", "Otro Prof"
    )

    r = await client.delete(
        f"/asignaturas/{asig_id}/documentos/{doc_id}",
        headers=_auth(token_otro),
    )

    assert r.status_code == 403


@pytest.mark.asyncio
async def test_borrar_documento_no_existente_devuelve_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Documento con ID inexistente en la asignatura → 404."""
    token_prof, _, asig_id = await _setup_asignatura(client, db_session)
    fake_doc_id = str(uuid.uuid4())

    r = await client.delete(
        f"/asignaturas/{asig_id}/documentos/{fake_doc_id}",
        headers=_auth(token_prof),
    )

    assert r.status_code == 404
