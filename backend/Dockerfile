# Imagen de producción del backend.
# En desarrollo el backend corre nativo con `uvicorn --reload` (ver el
# comentario en docker-compose.yml); esta imagen solo se usa junto con
# docker-compose.prod.yml, y arranca con Gunicorn + UvicornWorker vía
# docker-entrypoint.sh, nunca con --reload.
FROM python:3.12-slim

WORKDIR /app

# psycopg[binary] y pymupdf traen wheels precompiladas para linux/amd64 y
# linux/arm64: no hacen falta herramientas de compilación adicionales.
COPY pyproject.toml alembic.ini ./
COPY app ./app
COPY scripts ./scripts
COPY docker-entrypoint.sh ./

RUN pip install --no-cache-dir . \
    && chmod +x docker-entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["./docker-entrypoint.sh"]
