from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.agents.graph import pg_conn_string
from app.api.admin import router as admin_router
from app.api.asignaturas import router as asignaturas_router
from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.repaso import router as repaso_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Inicializa las tablas antes de aceptar peticiones.
    async with AsyncPostgresSaver.from_conn_string(pg_conn_string()) as checkpointer:
        await checkpointer.setup()
    yield


app = FastAPI(
    title="Asistente de Estudio — Backend",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(auth_router)
app.include_router(asignaturas_router)
app.include_router(chat_router)
app.include_router(repaso_router)
app.include_router(admin_router)


@app.get("/health", tags=["infra"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
