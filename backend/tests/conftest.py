"""
Fixtures compartidas para la suite de tests.

Estrategia de aislamiento: cada test recibe una sesión SQLAlchemy vinculada
a una conexión con una transacción abierta. Al terminar el test se hace
ROLLBACK, por lo que ningún dato persiste en la BD real.

join_transaction_mode="create_savepoint" permite que los route handlers llamen
a session.commit() sin cerrar la transacción exterior (usa SAVEPOINT interno).
"""

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import app.db.models  # noqa: F401 — registra modelos en Base.metadata
from app.core.config import settings
from app.db.session import get_session
from app.main import app

# Motor compartido por toda la sesión de tests; las conexiones son por test.
_engine = create_async_engine(settings.database_url, pool_pre_ping=True)


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Transacción por test con rollback garantizado al finalizar."""
    async with _engine.connect() as conn:
        await conn.begin()
        session = AsyncSession(
            bind=conn,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        )
        try:
            yield session
        finally:
            await session.close()
            await conn.rollback()


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Cliente HTTP con la sesión de test inyectada vía dependency_overrides."""

    async def _override_get_session() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_session] = _override_get_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
