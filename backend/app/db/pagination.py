"""Paginación por cursor del historial de mensajes.

Cursor de un solo campo (`before`, el `id` del mensaje más antiguo ya
cargado) resuelto internamente a un par `(creado_en, id)` para comparar con
`tuple_()`. El desempate por `id` evita el bug clásico de paginación por
fecha: dos mensajes de un mismo turno de chat pueden compartir exactamente
el mismo `creado_en` (se insertan en la misma transacción, que ve `now()`
como constante — ver app/api/chat.py), y una comparación solo por fecha
podría duplicar o saltarse una fila cuando el corte de página cae justo en
ese empate.
"""

import uuid
from collections.abc import Sequence

from fastapi import HTTPException, status
from sqlalchemy import ColumnElement, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Mensaje


async def paginar_mensajes(
    session: AsyncSession,
    condiciones: Sequence[ColumnElement[bool]],
    limit: int,
    before: uuid.UUID | None,
) -> tuple[list[Mensaje], bool]:
    """Devuelve (mensajes en orden cronológico, has_more) aplicando el cursor.

    `condiciones` son las cláusulas WHERE de pertenencia (y, si aplica,
    de rango de fechas) ya decididas por el llamador; se reutilizan tal
    cual para resolver el cursor, de modo que `before` solo puede apuntar
    a un mensaje dentro del mismo ámbito autorizado — nunca a un mensaje de
    otro alumno, otra asignatura o fuera del rango de fechas filtrado.
    """
    if before is not None:
        cursor_stmt = select(Mensaje).where(Mensaje.id == before, *condiciones)
        cursor = (await session.execute(cursor_stmt)).scalar_one_or_none()
        if cursor is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="El cursor 'before' no corresponde a un mensaje accesible.",
            )
        condiciones = [
            *condiciones,
            tuple_(Mensaje.creado_en, Mensaje.id) < (cursor.creado_en, cursor.id),
        ]

    stmt = (
        select(Mensaje)
        .where(*condiciones)
        .order_by(Mensaje.creado_en.desc(), Mensaje.id.desc())
        .limit(limit + 1)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    rows.reverse()
    return rows, has_more
