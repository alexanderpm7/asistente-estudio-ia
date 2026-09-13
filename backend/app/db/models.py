import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# BGE-M3 produce embeddings densos de 1024 dimensiones (no 768).
# Cambiar este valor si se sustituye el modelo de embeddings.
_EMBEDDING_DIM: int = 1024


class RolUsuario(StrEnum):
    alumno = "alumno"
    profesor = "profesor"
    admin = "admin"


class RolMensaje(StrEnum):
    user = "user"
    assistant = "assistant"


class Usuario(Base):
    __tablename__ = "usuarios"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    nombre: Mapped[str] = mapped_column(String(255), nullable=False)
    rol: Mapped[RolUsuario] = mapped_column(SAEnum(RolUsuario, name="rol_usuario"), nullable=False)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Un profesor puede gestionar varias asignaturas; RESTRICT impide borrar
    # un usuario que tenga asignaturas asignadas.
    asignaturas_como_profesor: Mapped[list["Asignatura"]] = relationship(back_populates="profesor")
    matriculas: Mapped[list["Matricula"]] = relationship(
        back_populates="alumno", cascade="all, delete-orphan"
    )
    mensajes: Mapped[list["Mensaje"]] = relationship(
        back_populates="alumno", cascade="all, delete-orphan"
    )
    password_set_tokens: Mapped[list["PasswordSetToken"]] = relationship(
        back_populates="usuario", cascade="all, delete-orphan"
    )


class PasswordSetToken(Base):
    """Token de un solo uso para que un usuario recién creado fije su contraseña.

    RF-A3: la contraseña inicial de un usuario dado de
    alta por un admin (individual o por CSV) nunca se muestra en claro; se
    envía por correo un enlace con este token. `token_hash` guarda el hash
    (no el token en claro, igual que `password_hash` con las contraseñas)
    para que una fuga de la base de datos no exponga tokens utilizables.
    """

    __tablename__ = "password_set_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    usuario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expira_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    usado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    usuario: Mapped["Usuario"] = relationship(back_populates="password_set_tokens")


class Asignatura(Base):
    __tablename__ = "asignaturas"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nombre: Mapped[str] = mapped_column(String(255), nullable=False)
    descripcion: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Se combina con las reglas fijas del prompt y solo lo edita su propietario.
    prompt_redactor: Mapped[str | None] = mapped_column(Text, nullable=True)
    profesor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="RESTRICT"),
        nullable=False,
    )
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    profesor: Mapped["Usuario"] = relationship(back_populates="asignaturas_como_profesor")
    matriculas: Mapped[list["Matricula"]] = relationship(
        back_populates="asignatura", cascade="all, delete-orphan"
    )
    mensajes: Mapped[list["Mensaje"]] = relationship(
        back_populates="asignatura", cascade="all, delete-orphan"
    )
    documentos: Mapped[list["Documento"]] = relationship(
        back_populates="asignatura", cascade="all, delete-orphan"
    )
    chunks: Mapped[list["DocumentoChunk"]] = relationship(
        back_populates="asignatura", cascade="all, delete-orphan"
    )


class Matricula(Base):
    __tablename__ = "matriculas"

    alumno_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="CASCADE"),
        primary_key=True,
    )
    asignatura_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("asignaturas.id", ondelete="CASCADE"),
        primary_key=True,
    )
    matriculado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    alumno: Mapped["Usuario"] = relationship(back_populates="matriculas")
    asignatura: Mapped["Asignatura"] = relationship(back_populates="matriculas")


class Mensaje(Base):
    __tablename__ = "mensajes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    alumno_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="CASCADE"),
        nullable=False,
    )
    asignatura_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("asignaturas.id", ondelete="CASCADE"),
        nullable=False,
    )
    rol: Mapped[RolMensaje] = mapped_column(SAEnum(RolMensaje, name="rol_mensaje"), nullable=False)
    contenido: Mapped[str] = mapped_column(Text, nullable=False)
    # clock_timestamp() evita empates: now() sería idéntico en toda la transacción.
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )
    uso_rag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Solo relevantes cuando uso_rag=True
    n_iteraciones: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latencia_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    alumno: Mapped["Usuario"] = relationship(back_populates="mensajes")
    asignatura: Mapped["Asignatura"] = relationship(back_populates="mensajes")

    __table_args__ = (
        # Patrón de acceso principal: historial de un alumno en una asignatura
        Index("ix_mensajes_alumno_asignatura", "alumno_id", "asignatura_id"),
    )


class Documento(Base):
    __tablename__ = "documentos"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asignatura_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("asignaturas.id", ondelete="CASCADE"),
        nullable=False,
    )
    nombre: Mapped[str] = mapped_column(String(255), nullable=False)
    ruta: Mapped[str] = mapped_column(String(1024), nullable=False)
    subido_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    asignatura: Mapped["Asignatura"] = relationship(back_populates="documentos")
    chunks: Mapped[list["DocumentoChunk"]] = relationship(
        back_populates="documento", cascade="all, delete-orphan"
    )


class DocumentoChunk(Base):
    __tablename__ = "document_chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    documento_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documentos.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Desnormalizado para filtrar por asignatura en el retrieval RAG sin join
    asignatura_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("asignaturas.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    texto: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(_EMBEDDING_DIM), nullable=False)
    # Metadatos del chunk: número de página, sección, etc.
    chunk_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )

    documento: Mapped["Documento"] = relationship(back_populates="chunks")
    asignatura: Mapped["Asignatura"] = relationship(back_populates="chunks")

    __table_args__ = (
        # Filtro principal del retrieval RAG; el índice HNSW sobre `embedding`
        # se añade en la migración correspondiente con CREATE INDEX ... USING hnsw.
        Index("ix_document_chunks_asignatura_id", "asignatura_id"),
    )
