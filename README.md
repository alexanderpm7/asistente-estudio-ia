# Asistente de Estudio con IA

Plataforma web donde estudiantes chatean con un asistente IA por asignatura,
alimentado por los apuntes que sube el profesorado (RAG local con Ollama).

## Requisitos previos

- Docker y Docker Compose
- Python 3.12 con `python3-venv`
- Node.js 20+
- [Ollama](https://ollama.com) instalado en el host con acceso a GPU

## Arranque en local

### 1. Variables de entorno

```bash
cp .env.example .env
```

Edita `.env` y ajusta `SECRET_KEY`, `POSTGRES_PASSWORD` y `DATABASE_URL`.

### 2. Servicios de infraestructura (Docker)

```bash
docker compose up -d   # levanta PostgreSQL (pgvector), Langfuse y Mailpit
```

### 3. Backend

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

alembic upgrade head   # aplica migraciones
uvicorn app.main:app --reload
```

API disponible en `http://localhost:8000` · Swagger en `http://localhost:8000/docs`

### Primer administrador

El registro público solo permite crear cuentas de alumno; los profesores y
administradores se dan de alta desde el panel de administración. Para crear
el primer administrador (necesario para poder acceder a ese panel por
primera vez), ejecuta:

```bash
cd backend
source .venv/bin/activate
python scripts/crear_admin.py --email admin@tudominio.es --password TuPasswordSegura
```

Con esas credenciales, inicia sesión en `http://localhost:3000/login` (o
`http://localhost/login` en producción) para acceder al panel de
administración y dar de alta al resto de usuarios (profesores, alumnos) de
forma individual o mediante importación por CSV.

### 4. Modelos Ollama

```bash
ollama pull qwen3:8b   # LLM
ollama pull bge-m3     # embeddings
```

### 5. Frontend

```bash
cd frontend
npm install
npm run dev
```

Aplicación en `http://localhost:3000`

## Arranque en producción (Docker completo)

Con el `.env` ya configurado (paso 1 de arriba), levanta backend, frontend y
Nginx dockerizados además de la infraestructura base:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

- Ollama sigue corriendo en el host (no en un contenedor); el backend lo
  alcanza en `host.docker.internal:11434` automáticamente, sin tocar `.env`.
- Nginx es el único servicio que publica puerto al host: backend y frontend
  solo son accesibles a través de él.
- Aplicación disponible en `http://localhost` (puerto 80 — no `:3000`/`:8000`).

Parada: `docker compose -f docker-compose.yml -f docker-compose.prod.yml down`

## Comandos útiles

```bash
# Tests (con el venv activado)
cd backend && pytest

# Calidad de código
cd backend && ruff check --fix . && mypy app

# Nueva migración tras cambiar modelos
cd backend && alembic revision --autogenerate -m "descripción"

# Ver logs de Postgres
docker compose logs -f postgres
```

## Estructura

```
backend/app/
  agents/    nodos del grafo LangGraph
  api/       routers FastAPI
  core/      configuración y seguridad
  db/        modelos, sesión, migraciones Alembic
  rag/       ingesta, chunking, embeddings, retrieval
  schemas/   modelos Pydantic
frontend/src/
  app/       App Router de Next.js
```