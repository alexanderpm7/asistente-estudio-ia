import logging
import uuid
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.graph import PROMPT_ESTILO_BASE
from app.core.deps import get_current_user
from app.db.models import (
    Asignatura,
    Documento,
    DocumentoChunk,
    Matricula,
    Mensaje,
    RolUsuario,
    Usuario,
)
from app.db.pagination import paginar_mensajes
from app.db.session import AsyncSessionLocal, get_session
from app.rag.ingest import ingest_document
from app.schemas.asignaturas import (
    AlumnoMatriculadoResponse,
    AsignaturaCreate,
    AsignaturaResponse,
    AsignaturaUpdate,
    MatriculaCreate,
    MatriculaResponse,
    PromptRedactorResponse,
)
from app.schemas.documentos import DocumentoResponse
from app.schemas.mensajes import HistorialResponse

MAX_FILE_SIZE: int = 20 * 1024 * 1024  # 20 MB
_ALLOWED_CONTENT_TYPES = {"application/pdf"}

router = APIRouter(prefix="/asignaturas", tags=["asignaturas"])


def _require_profesor(user: Usuario) -> None:
    if user.rol != RolUsuario.profesor:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Solo profesores.")


async def _get_asignatura_o_404(asignatura_id: uuid.UUID, session: AsyncSession) -> Asignatura:
    result = await session.execute(select(Asignatura).where(Asignatura.id == asignatura_id))
    asig = result.scalar_one_or_none()
    if asig is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Asignatura no encontrada."
        )
    return asig


def _require_propietario(asig: Asignatura, user: Usuario) -> None:
    if asig.profesor_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No eres el propietario.")


@router.post("", response_model=AsignaturaResponse, status_code=status.HTTP_201_CREATED)
async def crear_asignatura(
    body: AsignaturaCreate,
    session: AsyncSession = Depends(get_session),
    current_user: Usuario = Depends(get_current_user),
) -> Asignatura:
    _require_profesor(current_user)
    # Cada asignatura recibe una copia editable del estilo base si no se indica otro.
    prompt_redactor = (
        body.prompt_redactor if body.prompt_redactor is not None else PROMPT_ESTILO_BASE
    )
    asig = Asignatura(
        id=uuid.uuid4(),
        nombre=body.nombre,
        descripcion=body.descripcion,
        prompt_redactor=prompt_redactor,
        profesor_id=current_user.id,
    )
    session.add(asig)
    await session.commit()
    await session.refresh(asig)
    return asig


@router.get("", response_model=list[AsignaturaResponse])
async def listar_asignaturas(
    session: AsyncSession = Depends(get_session),
    current_user: Usuario = Depends(get_current_user),
) -> list[Asignatura]:
    if current_user.rol == RolUsuario.profesor:
        result = await session.execute(
            select(Asignatura).where(Asignatura.profesor_id == current_user.id)
        )
    else:
        result = await session.execute(
            select(Asignatura)
            .join(Matricula, Matricula.asignatura_id == Asignatura.id)
            .where(Matricula.alumno_id == current_user.id)
        )
    return list(result.scalars().all())


@router.get("/alumnos/buscar", response_model=list[str])
async def buscar_alumnos_por_email(
    q: str = Query(min_length=3, max_length=255),
    session: AsyncSession = Depends(get_session),
    current_user: Usuario = Depends(get_current_user),
) -> list[str]:
    """Autocompletado de email al matricular un alumno (formulario del profesor).

    Registrada antes de `/{asignatura_id}` a propósito: FastAPI resuelve las
    rutas de un router en orden de declaración, y `/alumnos/buscar` debe
    matchear antes que la ruta dinámica de más abajo (si no, "alumnos" se
    interpretaría como un asignatura_id).

    Solo profesores (cualquiera, no solo el propietario de una asignatura
    concreta: matricular requiere poder buscar entre todos los alumnos, no
    solo los ya matriculados en una asignatura). Devuelve como máximo 8
    emails de usuarios con rol alumno cuyo correo contiene `q`
    (insensible a mayúsculas) — nunca nombre, id ni ningún otro dato del
    alumno, para no exponer más de lo estrictamente necesario para rellenar
    un campo de email.
    """
    _require_profesor(current_user)
    result = await session.execute(
        select(Usuario.email)
        .where(Usuario.rol == RolUsuario.alumno, Usuario.email.ilike(f"%{q}%"))
        .order_by(Usuario.email)
        .limit(8)
    )
    return list(result.scalars().all())


@router.get("/{asignatura_id}", response_model=AsignaturaResponse)
async def get_asignatura(
    asignatura_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: Usuario = Depends(get_current_user),
) -> Asignatura:
    asig = await _get_asignatura_o_404(asignatura_id, session)
    if current_user.rol == RolUsuario.profesor:
        _require_propietario(asig, current_user)
    else:
        result = await session.execute(
            select(Matricula).where(
                Matricula.alumno_id == current_user.id,
                Matricula.asignatura_id == asig.id,
            )
        )
        if result.scalar_one_or_none() is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No tienes acceso.")
    return asig


@router.put("/{asignatura_id}", response_model=AsignaturaResponse)
async def actualizar_asignatura(
    asignatura_id: uuid.UUID,
    body: AsignaturaUpdate,
    session: AsyncSession = Depends(get_session),
    current_user: Usuario = Depends(get_current_user),
) -> Asignatura:
    _require_profesor(current_user)
    asig = await _get_asignatura_o_404(asignatura_id, session)
    _require_propietario(asig, current_user)
    if body.nombre is not None:
        asig.nombre = body.nombre
    if body.descripcion is not None:
        asig.descripcion = body.descripcion
    if body.prompt_redactor is not None:
        asig.prompt_redactor = body.prompt_redactor
    await session.commit()
    await session.refresh(asig)
    return asig


@router.get("/{asignatura_id}/prompt-redactor", response_model=PromptRedactorResponse)
async def get_prompt_redactor(
    asignatura_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: Usuario = Depends(get_current_user),
) -> Asignatura:
    """Lee el prompt de estilo del redactor del profesor propietario.

    No expuesto en `AsignaturaResponse`: ese schema lo comparten los alumnos
    matriculados vía `GET /asignaturas` y `GET /asignaturas/{id}`.
    """
    _require_profesor(current_user)
    asig = await _get_asignatura_o_404(asignatura_id, session)
    _require_propietario(asig, current_user)
    return asig


@router.delete("/{asignatura_id}", status_code=status.HTTP_204_NO_CONTENT)
async def eliminar_asignatura(
    asignatura_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: Usuario = Depends(get_current_user),
) -> Response:
    _require_profesor(current_user)
    asig = await _get_asignatura_o_404(asignatura_id, session)
    _require_propietario(asig, current_user)
    await session.delete(asig)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{asignatura_id}/alumnos",
    response_model=MatriculaResponse,
    status_code=status.HTTP_201_CREATED,
)
async def matricular_alumno(
    asignatura_id: uuid.UUID,
    body: MatriculaCreate,
    session: AsyncSession = Depends(get_session),
    current_user: Usuario = Depends(get_current_user),
) -> Matricula:
    _require_profesor(current_user)
    asig = await _get_asignatura_o_404(asignatura_id, session)
    _require_propietario(asig, current_user)
    result = await session.execute(
        select(Usuario).where(Usuario.email == body.email, Usuario.rol == RolUsuario.alumno)
    )
    alumno = result.scalar_one_or_none()
    if alumno is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alumno no encontrado.")
    matricula = Matricula(alumno_id=alumno.id, asignatura_id=asig.id)
    session.add(matricula)
    await session.commit()
    await session.refresh(matricula)
    return matricula


@router.delete("/{asignatura_id}/alumnos/{alumno_id}", status_code=status.HTTP_204_NO_CONTENT)
async def desmatricular_alumno(
    asignatura_id: uuid.UUID,
    alumno_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: Usuario = Depends(get_current_user),
) -> Response:
    _require_profesor(current_user)
    asig = await _get_asignatura_o_404(asignatura_id, session)
    _require_propietario(asig, current_user)
    result = await session.execute(
        select(Matricula).where(
            Matricula.alumno_id == alumno_id,
            Matricula.asignatura_id == asignatura_id,
        )
    )
    matricula = result.scalar_one_or_none()
    if matricula is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Matrícula no encontrada."
        )
    await session.delete(matricula)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{asignatura_id}/alumnos",
    response_model=list[AlumnoMatriculadoResponse],
)
async def listar_alumnos(
    asignatura_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: Usuario = Depends(get_current_user),
) -> list[AlumnoMatriculadoResponse]:
    _require_profesor(current_user)
    asig = await _get_asignatura_o_404(asignatura_id, session)
    _require_propietario(asig, current_user)
    result = await session.execute(
        select(Matricula, Usuario)
        .join(Usuario, Usuario.id == Matricula.alumno_id)
        .where(Matricula.asignatura_id == asignatura_id)
        .order_by(Matricula.matriculado_en)
    )
    return [
        AlumnoMatriculadoResponse(
            alumno_id=m.alumno_id,
            email=u.email,
            nombre=u.nombre,
            matriculado_en=m.matriculado_en,
        )
        for m, u in result.all()
    ]


@router.delete(
    "/{asignatura_id}/documentos/{documento_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def borrar_documento(
    asignatura_id: uuid.UUID,
    documento_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: Usuario = Depends(get_current_user),
) -> Response:
    """Borra un documento y sus chunks del RAG.

    Solo el profesor propietario de la asignatura puede borrar.
    El CASCADE en el modelo ORM y en la FK de document_chunks elimina los chunks.
    """
    _require_profesor(current_user)
    asig = await _get_asignatura_o_404(asignatura_id, session)
    _require_propietario(asig, current_user)
    result = await session.execute(
        select(Documento).where(
            Documento.id == documento_id,
            Documento.asignatura_id == asignatura_id,
        )
    )
    doc = result.scalar_one_or_none()
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Documento no encontrado."
        )
    await session.delete(doc)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{asignatura_id}/documentos",
    response_model=list[DocumentoResponse],
)
async def listar_documentos(
    asignatura_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: Usuario = Depends(get_current_user),
) -> list[DocumentoResponse]:
    """Lista los documentos de la asignatura.

    Accesible por el profesor propietario (gestión de documentos) y por
    cualquier alumno matriculado (selector de documento del modo repaso).
    """
    asig = await _get_asignatura_o_404(asignatura_id, session)
    if current_user.rol == RolUsuario.profesor:
        _require_propietario(asig, current_user)
    else:
        mat = await session.execute(
            select(Matricula).where(
                Matricula.alumno_id == current_user.id,
                Matricula.asignatura_id == asig.id,
            )
        )
        if mat.scalar_one_or_none() is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No tienes acceso.")
    from sqlalchemy import func as sqlfunc

    result = await session.execute(
        select(Documento, sqlfunc.count(DocumentoChunk.id).label("n_chunks"))
        .outerjoin(DocumentoChunk, DocumentoChunk.documento_id == Documento.id)
        .where(Documento.asignatura_id == asignatura_id)
        .group_by(Documento.id)
        .order_by(Documento.subido_en)
    )
    return [
        DocumentoResponse(
            id=doc.id,
            nombre=doc.nombre,
            asignatura_id=doc.asignatura_id,
            subido_en=doc.subido_en,
            n_chunks=n_chunks,
        )
        for doc, n_chunks in result.all()
    ]


@router.get(
    "/{asignatura_id}/historial/{alumno_id}",
    response_model=HistorialResponse,
)
async def get_historial_alumno(
    asignatura_id: uuid.UUID,
    alumno_id: uuid.UUID,
    limit: int = Query(default=20, ge=5, le=20),
    before: uuid.UUID | None = Query(default=None),
    desde: date | None = Query(default=None),
    hasta: date | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    current_user: Usuario = Depends(get_current_user),
) -> HistorialResponse:
    """Devuelve el historial paginado de un alumno en una asignatura.

    Solo accesible por el profesor propietario de la asignatura. Sin
    `desde`/`hasta`, filtra por defecto la última semana; si se indica
    alguno de los dos, solo se acota el límite indicado (el otro queda
    abierto). `limit`/`before` siguen el mismo patrón de cursor que
    `GET /chat/{asignatura_id}/historial`.
    """
    _require_profesor(current_user)
    asig = await _get_asignatura_o_404(asignatura_id, session)
    _require_propietario(asig, current_user)

    desde_dt: datetime | None
    hasta_dt: datetime | None
    if desde is None and hasta is None:
        hasta_dt = datetime.now(UTC)
        desde_dt = hasta_dt - timedelta(days=7)
    else:
        desde_dt = datetime.combine(desde, time.min, tzinfo=UTC) if desde else None
        hasta_dt = datetime.combine(hasta, time.max, tzinfo=UTC) if hasta else None

    condiciones = [Mensaje.alumno_id == alumno_id, Mensaje.asignatura_id == asignatura_id]
    if desde_dt is not None:
        condiciones.append(Mensaje.creado_en >= desde_dt)
    if hasta_dt is not None:
        condiciones.append(Mensaje.creado_en <= hasta_dt)

    mensajes, has_more = await paginar_mensajes(session, condiciones, limit, before)
    return HistorialResponse(mensajes=mensajes, has_more=has_more)


_log = logging.getLogger(__name__)


async def _ingest_background(
    pdf_bytes: bytes,
    documento_id: uuid.UUID,
    asignatura_id: uuid.UUID,
) -> None:
    """Genera embeddings y persiste chunks en background.

    Usa su propia sesión para no bloquear el HTTP request. Los errores se
    registran en el log pero no se propagan — el registro del documento ya
    fue confirmado por el endpoint.
    """
    async with AsyncSessionLocal() as session:
        try:
            await ingest_document(
                pdf_bytes=pdf_bytes,
                documento_id=documento_id,
                asignatura_id=asignatura_id,
                session=session,
            )
            await session.commit()
        except Exception:
            await session.rollback()
            _log.exception(
                "Error indexando documento %s (asignatura %s)",
                documento_id,
                asignatura_id,
            )


@router.post(
    "/{asignatura_id}/documentos",
    response_model=DocumentoResponse,
    status_code=status.HTTP_201_CREATED,
)
async def subir_documento(
    asignatura_id: uuid.UUID,
    file: UploadFile,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    current_user: Usuario = Depends(get_current_user),
) -> DocumentoResponse:
    """Sube un PDF y programa su indexación en segundo plano.

    Responde 201 inmediatamente con n_chunks=0; la generación de embeddings
    ocurre en _ingest_background sin bloquear el HTTP request.
    Solo el profesor propietario puede subir documentos.
    Valida: content-type == application/pdf, tamaño ≤ MAX_FILE_SIZE.
    """
    _require_profesor(current_user)
    asig = await _get_asignatura_o_404(asignatura_id, session)
    _require_propietario(asig, current_user)

    if file.content_type not in _ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Solo se aceptan ficheros PDF (application/pdf).",
        )

    # Rechazo temprano según el tamaño declarado por el cliente.
    if file.size is not None and file.size > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El fichero supera el tamaño máximo permitido de 20 MB.",
        )

    pdf_bytes = await file.read()
    if len(pdf_bytes) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El fichero supera el tamaño máximo permitido de 20 MB.",
        )

    # Comprueba la firma PDF para evitar contenido arbitrario con MIME falso.
    if not pdf_bytes.startswith(b"%PDF-"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Solo se aceptan ficheros PDF (application/pdf).",
        )

    # Usa solo el nombre final para evitar path traversal.
    safe_name = Path(file.filename or "documento.pdf").name or "documento.pdf"

    doc = Documento(
        asignatura_id=asignatura_id,
        nombre=safe_name,
        ruta=f"uploads/{asignatura_id}/{safe_name}",
    )
    session.add(doc)
    await session.flush()
    doc_id = doc.id

    background_tasks.add_task(
        _ingest_background,
        pdf_bytes=pdf_bytes,
        documento_id=doc_id,
        asignatura_id=asignatura_id,
    )

    await session.commit()
    await session.refresh(doc)

    return DocumentoResponse(
        id=doc.id,
        nombre=doc.nombre,
        asignatura_id=doc.asignatura_id,
        subido_en=doc.subido_en,
        n_chunks=0,
    )
