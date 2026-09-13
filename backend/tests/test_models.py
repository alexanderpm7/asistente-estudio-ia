"""
Verifica la estructura del esquema a partir de la metadata de SQLAlchemy.
No requiere conexión a base de datos.
"""

import pytest

import app.db.models  # noqa: F401 — asegura que los modelos se registran
from app.db.base import Base

# Columnas esperadas por tabla
_EXPECTED: dict[str, set[str]] = {
    "usuarios": {"id", "email", "password_hash", "nombre", "rol", "activo", "creado_en"},
    "asignaturas": {
        "id",
        "nombre",
        "descripcion",
        "prompt_redactor",
        "profesor_id",
        "creado_en",
    },
    "matriculas": {"alumno_id", "asignatura_id", "matriculado_en"},
    "mensajes": {
        "id",
        "alumno_id",
        "asignatura_id",
        "rol",
        "contenido",
        "creado_en",
        "uso_rag",
        "n_iteraciones",
        "latencia_ms",
    },
    "documentos": {"id", "asignatura_id", "nombre", "ruta", "subido_en"},
    "document_chunks": {
        "id",
        "documento_id",
        "asignatura_id",
        "chunk_index",
        "texto",
        "embedding",
        "chunk_metadata",
    },
    "password_set_tokens": {
        "id",
        "usuario_id",
        "token_hash",
        "expira_en",
        "usado",
        "creado_en",
    },
}


def test_all_tables_registered() -> None:
    registered = set(Base.metadata.tables.keys())
    assert registered == set(_EXPECTED.keys()), (
        f"Tablas inesperadas o faltantes.\n"
        f"  Esperadas: {set(_EXPECTED.keys())}\n"
        f"  Registradas: {registered}"
    )


@pytest.mark.parametrize("table_name,expected_cols", _EXPECTED.items())
def test_table_columns(table_name: str, expected_cols: set[str]) -> None:
    table = Base.metadata.tables[table_name]
    actual_cols = {col.name for col in table.columns}
    missing = expected_cols - actual_cols
    extra = actual_cols - expected_cols
    assert not missing, f"[{table_name}] columnas faltantes: {missing}"
    assert not extra, f"[{table_name}] columnas inesperadas: {extra}"


def test_primary_keys() -> None:
    tables = Base.metadata.tables
    assert {col.name for col in tables["usuarios"].primary_key} == {"id"}
    assert {col.name for col in tables["asignaturas"].primary_key} == {"id"}
    assert {col.name for col in tables["matriculas"].primary_key} == {"alumno_id", "asignatura_id"}
    assert {col.name for col in tables["mensajes"].primary_key} == {"id"}
    assert {col.name for col in tables["documentos"].primary_key} == {"id"}
    assert {col.name for col in tables["document_chunks"].primary_key} == {"id"}


def test_foreign_keys() -> None:
    tables = Base.metadata.tables

    def fk_targets(table_name: str) -> set[str]:
        return {fk.target_fullname for fk in tables[table_name].foreign_keys}

    assert "usuarios.id" in fk_targets("asignaturas")
    assert "usuarios.id" in fk_targets("matriculas")
    assert "asignaturas.id" in fk_targets("matriculas")
    assert "usuarios.id" in fk_targets("mensajes")
    assert "asignaturas.id" in fk_targets("mensajes")
    assert "asignaturas.id" in fk_targets("documentos")
    assert "documentos.id" in fk_targets("document_chunks")
    assert "asignaturas.id" in fk_targets("document_chunks")


def test_nullable_columns() -> None:
    tables = Base.metadata.tables
    cols = {col.name: col for col in tables["asignaturas"].columns}
    assert cols["descripcion"].nullable is True

    cols_msg = {col.name: col for col in tables["mensajes"].columns}
    assert cols_msg["n_iteraciones"].nullable is True
    assert cols_msg["latencia_ms"].nullable is True
    assert cols_msg["uso_rag"].nullable is False


def test_unique_constraints() -> None:
    from sqlalchemy import UniqueConstraint

    table = Base.metadata.tables["usuarios"]
    unique_cols = {
        col.name
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
        for col in constraint.columns
    }
    assert "email" in unique_cols
