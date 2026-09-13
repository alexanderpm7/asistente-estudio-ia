"""
Evaluación de calidad del RAG — SPEC §RNF-4, ARCHITECTURE §5.

Verifica que el documento correcto aparece en los top-5 chunks recuperados
en ≥ 80 % de las preguntas del conjunto de evaluación (19 pares
pregunta/documento en tests/fixtures/rag_eval.json).

Requiere Ollama corriendo en localhost:11434 con bge-m3 descargado.
Marcado como 'integration': excluir con `pytest -m "not integration"`
para la suite rápida sin servicios externos.
"""

import json
import uuid
from pathlib import Path

import pytest
from langchain_ollama import OllamaEmbeddings
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Asignatura, Documento, RolUsuario, Usuario
from app.rag.ingest import ingest_document
from app.rag.retrieval import retrieve_chunks

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
_DOCS_DIR = _FIXTURES_DIR / "docs"
_EVAL_JSON = _FIXTURES_DIR / "rag_eval.json"

pytestmark = pytest.mark.integration


async def test_rag_eval_rnf4(db_session: AsyncSession) -> None:
    """
    Indexa los PDFs de evaluación con embeddings reales (BGE-M3) y verifica
    que el documento correcto aparece en el top-5 recuperado en ≥ 80 % de las
    preguntas. Cumple RNF-4.

    Pasos:
      1. Crear profesor y asignatura en la BD de test aislada.
      2. Indexar cada PDF de fixtures/docs/ via ingest_document (embeddings Ollama).
      3. Para cada pregunta de rag_eval.json, embeber la consulta y recuperar top-5.
      4. Comprobar si el documento esperado aparece entre los chunks recuperados.
      5. Imprimir porcentaje final y assert >= 80 %.
    """
    # ── 1. Entidades de BD ────────────────────────────────────────────────────
    prof = Usuario(
        email="eval_rag@test.es",
        password_hash="x",
        nombre="Prof RAG Eval",
        rol=RolUsuario.profesor,
    )
    db_session.add(prof)
    await db_session.flush()

    asig = Asignatura(nombre="Asignatura RAG Eval", descripcion=None, profesor_id=prof.id)
    db_session.add(asig)
    await db_session.flush()

    # ── 2. Indexar PDFs de evaluación ─────────────────────────────────────────
    pdf_paths = sorted(_DOCS_DIR.glob("*.pdf"))
    assert pdf_paths, f"No se encontraron PDFs en {_DOCS_DIR}"

    for pdf_path in pdf_paths:
        doc = Documento(
            id=uuid.uuid4(),
            asignatura_id=asig.id,
            nombre=pdf_path.name,
            ruta=f"uploads/eval/{pdf_path.name}",
        )
        db_session.add(doc)
        await db_session.flush()

        n_chunks = await ingest_document(
            pdf_bytes=pdf_path.read_bytes(),
            documento_id=doc.id,
            asignatura_id=asig.id,
            session=db_session,
        )
        print(f"\n  Indexado {pdf_path.name}: {n_chunks} chunks")

    # ── 3. Embedder para las consultas (mismo modelo que la ingesta) ──────────
    embedder = OllamaEmbeddings(
        model=settings.embedding_model,
        base_url=settings.ollama_host,
    )

    # ── 4. Evaluar cada par pregunta / documento esperado ─────────────────────
    eval_pairs: list[dict[str, str]] = json.loads(_EVAL_JSON.read_text())
    hits = 0
    results_log: list[str] = []

    for pair in eval_pairs:
        pregunta: str = pair["pregunta"]
        doc_esperado: str = pair["documento_esperado"]

        query_emb: list[float] = await embedder.aembed_query(pregunta)
        chunks = await retrieve_chunks(
            query_embedding=query_emb,
            asignatura_id=asig.id,
            session=db_session,
            top_k=5,
        )

        docs_recuperados = [c["doc_nombre"] for c in chunks]
        encontrado = doc_esperado in docs_recuperados

        if encontrado:
            hits += 1

        mark = "HIT " if encontrado else "MISS"
        results_log.append(f"  [{mark}] {doc_esperado} | {pregunta[:70]}")

    # ── 5. Resultado ──────────────────────────────────────────────────────────
    total = len(eval_pairs)
    pct = hits / total * 100 if total else 0.0

    print("\n── Resultados RNF-4 ──────────────────────────────────────────────")
    print("\n".join(results_log))
    print(f"\nRNF-4: {hits}/{total} documentos correctos en top-5 ({pct:.1f} %)")
    print(f"Umbral: 80 % — {'APROBADO' if pct >= 80.0 else 'SUSPENDIDO'}")

    assert pct >= 80.0, (
        f"RNF-4 no superado: solo {hits}/{total} ({pct:.1f} %) preguntas "
        f"recuperan el documento correcto en top-5 (umbral: 80 %)"
    )
