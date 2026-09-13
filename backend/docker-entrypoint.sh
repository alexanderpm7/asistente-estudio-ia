#!/bin/sh
# Entrypoint de producción. Aplica las migraciones
# pendientes antes de arrancar el servidor, para que un despliegue con una
# base de datos nueva (o adelantada) quede consistente sin un paso manual
# aparte.
set -e

alembic upgrade head

# Inicializa las tablas de checkpoint de LangGraph una sola vez, en un único
# proceso secuencial, antes de que Gunicorn arranque varios workers. El
# propio checkpointer.setup() se vuelve a llamar desde el lifespan de cada
# worker (app/main.py) y desde app/api/chat.py, pero eso es seguro una vez
# las tablas ya existen: `CREATE TABLE IF NOT EXISTS` no es atómico en
# Postgres bajo concurrencia — varios workers arrancando a la vez contra una
# BD nueva pueden chocar creando la misma tabla y tirar el contenedor entero
# con un UniqueViolation en pg_type.
python -c "
import asyncio

from app.agents.graph import pg_conn_string
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver


async def main() -> None:
    async with AsyncPostgresSaver.from_conn_string(pg_conn_string()) as checkpointer:
        await checkpointer.setup()


asyncio.run(main())
"

# Gunicorn + UvicornWorker, en vez de uvicorn suelto: da varios
# procesos worker que atienden peticiones en paralelo real (sin GIL
# compartido entre ellos), útil bajo carga concurrente.
#
# Matiz importante: el cuello de botella de la
# inferencia LLM es la GPU de Ollama, no el proceso Python del backend — una
# petición de chat sigue esperando a que Ollama genere, tenga el backend
# cuantos workers tenga. El paralelismo de Gunicorn beneficia a los
# endpoints que NO invocan al LLM (CRUD de asignaturas, historial, auth),
# que ya no quedan bloqueados detrás de una petición de chat lenta en el
# mismo proceso; no reduce la latencia de generación del chat en sí (eso
# requeriría una cola visible y medición de concurrencia, fuera de alcance
# aquí).
#
# GUNICORN_WORKERS configurable por entorno; 3 es un valor conservador para
# un backend que apenas hace CPU-bound work propio (delega en Ollama/Postgres).
exec gunicorn app.main:app \
    -k uvicorn.workers.UvicornWorker \
    -w "${GUNICORN_WORKERS:-3}" \
    -b 0.0.0.0:8000
