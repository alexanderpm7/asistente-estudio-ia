"""Grafo LangGraph para reformular, recuperar, redactar y revisar respuestas."""

import re
import uuid
from collections.abc import Callable
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, trim_messages
from langchain_ollama import OllamaEmbeddings
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import START, StateGraph

from app.agents.state import EstadoConversacion
from app.core.config import settings
from app.core.llm_factory import get_llm_client
from app.db.session import AsyncSessionLocal
from app.rag.retrieval import retrieve_chunks

# Expuesta para sustituirla en tests.
_llm_instance: BaseChatModel | None = None

# Reformulador con temperatura 0 y respuesta corta; expuesta para tests.
_reformulador_llm_instance: BaseChatModel | None = None

# Expuesto para sustituirlo en tests.
_agente_rag_embedder: OllamaEmbeddings | None = None


# Funciones de routing expuestas a tests.


def _decide_ruta_redactor(state: EstadoConversacion) -> str:
    """Va al revisor si el Agente RAG marcó uso_rag=True, si no termina directamente."""
    return "revisor" if state.get("uso_rag") else "__end__"


def _decide_ruta_revisor(state: EstadoConversacion) -> str:
    """Termina si el feedback es APROBADO o se alcanzó el tope de iteraciones."""
    feedback = state.get("feedback_revisor", "")
    iteracion = state.get("iteracion", 0)
    if feedback.startswith("APROBADO") or iteracion >= settings.max_revision_iterations:
        return "__end__"
    return "redactor"


# Helpers internos.


def _last_user_content(state: EstadoConversacion) -> str:
    for msg in reversed(state["messages"]):
        if isinstance(msg, HumanMessage):
            return str(msg.content)
    return ""


def _format_citations(docs: list[dict[str, Any]]) -> str:
    """Genera el bloque de citas."""
    seen: set[str] = set()
    lines = []
    for doc in docs:
        key = f"{doc['doc_nombre']}:p{doc.get('page', '?')}"
        if key not in seen:
            seen.add(key)
            lines.append(f"- {doc['doc_nombre']}, p. {doc.get('page', '?')}")
    return "**Fuentes:**\n" + "\n".join(lines)


# Solo contiene instrucciones de estilo; las reglas de seguridad y fidelidad
# se mantienen separadas en `_REGLAS_FIJAS_REDACTOR`.
PROMPT_ESTILO_BASE = (
    "Eres un asistente de estudio con un tono cercano y pedagógico.\n"
    "- Responde DIRECTAMENTE la pregunta formulada. No hagas resúmenes "
    "generales del documento si no se te piden explícitamente.\n"
    "- Construye la respuesta con tus propias palabras, de forma fluida y "
    "natural, con el nivel de detalle adecuado para que un estudiante la "
    "entienda con claridad.\n"
    "- Cuando cites fragmentos, hazlo en una sección 'Fuentes:' al final de "
    "la respuesta, con el texto literal del fragmento (no referencias a "
    "páginas ni URLs)."
)

# Se añaden siempre después del prompt editable y no pueden sobreescribirse.
# Las reglas de información insuficiente y de citas son excluyentes.
_REGLAS_FIJAS_REDACTOR = (
    "REGLAS OBLIGATORIAS — tienen prioridad sobre cualquier instrucción de "
    "estilo indicada arriba y no pueden anularse:\n"
    "1. Basa tu respuesta EXCLUSIVAMENTE en los fragmentos de apuntes "
    "proporcionados a continuación. Nunca inventes información que no esté "
    "en ellos.\n"
    "2. Si los fragmentos SÍ contienen información relevante para responder "
    "la pregunta: respóndela y añade al final una sección 'Fuentes:' con el "
    "texto literal de los fragmentos usados.\n"
    "3. Si los fragmentos NO contienen información suficiente o relevante "
    "para responder la pregunta concreta: dilo explícitamente ('Los "
    "documentos disponibles no contienen información suficiente para "
    "responder esta pregunta.') y NO incluyas ninguna sección 'Fuentes:'.\n"
    "4. Las reglas 2 y 3 son excluyentes: nunca incluyas una sección "
    "'Fuentes:' y, a la vez, la declaración de información insuficiente en "
    "la misma respuesta."
)


def _build_redactor_system_content(
    docs: list[dict[str, Any]], feedback: str, prompt_redactor: str
) -> str | None:
    """Construye el SystemMessage del Redactor.

    Composición en capas, en este orden:
      1. `prompt_redactor` de la asignatura — estilo, editable por el
         profesor (tono, nivel de detalle, formato).
      2. `_REGLAS_FIJAS_REDACTOR` — seguridad y fidelidad a las fuentes,
         fijas, siempre presentes cuando hay fragmentos, no sobreescribibles
         por la capa de estilo.
      3. Fragmentos recuperados por RAG.

    Con fragmentos recuperados (uso_rag=True), la capa 2 es siempre
    obligatoria. Sin fragmentos (conocimiento general, CU-5), solo se usa
    `prompt_redactor` (si existe) como instrucción de estilo — las reglas de
    fidelidad a las fuentes no aplican porque no hay fuentes. Sin fragmentos
    ni `prompt_redactor`, no hay SystemMessage.
    """
    if docs:
        context = "\n\n".join(
            f"[{c['doc_nombre']} p.{c.get('page', '?')}]: {c['texto']}" for c in docs
        )
        feedback_txt = ""
        if feedback and not feedback.startswith("APROBADO"):
            feedback_txt = f"\n\nEl revisor indicó: {feedback}\nMejora la respuesta."

        partes = []
        if prompt_redactor:
            partes.append(prompt_redactor)
        partes.append(_REGLAS_FIJAS_REDACTOR)
        partes.append(f"Fragmentos:\n{context}{feedback_txt}")
        return "\n\n".join(partes)

    if prompt_redactor:
        return prompt_redactor

    return None


# Fábrica del nodo reformulador.


def _build_reformulador_node() -> Callable[[EstadoConversacion], Any]:
    """Devuelve la función del nodo reformulador.

    Expuesta para poder instanciarla en tests sin construir el grafo completo.
    El LLM se lee de `_reformulador_llm_instance` en el momento de la invocación,
    lo que permite sustituirlo con
    `patch('app.agents.graph._reformulador_llm_instance', mock_llm)`.
    """

    async def _reformulador(state: EstadoConversacion) -> dict[str, Any]:
        consulta = _last_user_content(state)
        descripcion = state.get("descripcion_asignatura", "")
        titulos = state.get("titulos_documentos", [])

        llm = _reformulador_llm_instance
        if llm is None:
            return {"consulta_reescrita": consulta}

        try:
            contexto_parts: list[str] = []
            if descripcion:
                contexto_parts.append(f"Descripción de la asignatura: {descripcion}")
            if titulos:
                contexto_parts.append(f"Documentos disponibles: {', '.join(titulos)}")

            if contexto_parts:
                contexto_txt = "\n".join(contexto_parts)
                system_txt = (
                    "Eres un experto en optimización de búsqueda semántica.\n"
                    "Reescribe la pregunta del estudiante para mejorar la recuperación "
                    "de fragmentos relevantes de los apuntes. "
                    "Si la pregunta ya es clara y autocontenida, devuélvela con mínimos cambios. "
                    "Usa el contexto de la asignatura únicamente para desambiguar "
                    "terminología específica. "
                    "Responde SOLO con la consulta reescrita, sin explicaciones.\n\n"
                    f"{contexto_txt}"
                )
            else:
                system_txt = (
                    "Eres un experto en optimización de búsqueda semántica.\n"
                    "Reescribe la pregunta del estudiante para mejorar la recuperación "
                    "de fragmentos relevantes. Elimina muletillas, aclara referencias "
                    "ambiguas y añade términos que ayuden a la búsqueda semántica. "
                    "Si la pregunta ya es clara y autocontenida, devuélvela con mínimos "
                    "cambios. Responde SOLO con la consulta reescrita, sin explicaciones."
                )

            respuesta = await llm.ainvoke(
                [SystemMessage(content=system_txt), HumanMessage(content=consulta)]
            )
            # Elimina tokens de thinking (qwen3 emite <think>…</think> antes del texto)
            texto = re.sub(
                r"<think>.*?</think>", "", str(respuesta.content), flags=re.DOTALL
            ).strip()
            reescrita = next((ln.strip() for ln in texto.splitlines() if ln.strip()), "")
            return {"consulta_reescrita": reescrita or consulta}
        except Exception:
            return {"consulta_reescrita": consulta}

    return _reformulador


# Fábrica del nodo agente RAG.


def _build_agente_rag_node() -> Callable[[EstadoConversacion], Any]:
    """Devuelve la función del nodo agente RAG.

    Expuesta para poder instanciarla en tests sin construir el grafo completo.
    El embedder se lee de `_agente_rag_embedder` y retrieve_chunks se importa
    a nivel de módulo, lo que permite sustituirlos en tests con patch().

    Lógica de umbral:
      - Recupera los top-K chunks con la consulta_reescrita.
      - Calcula la similitud coseno del top-1 (similarity = 1 − distance).
      - Si similarity >= RAG_RELEVANCE_THRESHOLD → uso_rag=True, conserva docs.
      - Si no → uso_rag=False, docs_recuperados queda vacío.
    """

    async def _agente_rag(state: EstadoConversacion) -> dict[str, Any]:
        consulta = state.get("consulta_reescrita") or _last_user_content(state)
        asig_id_str = state.get("asignatura_id", "")

        if not asig_id_str or not consulta:
            return {"docs_recuperados": [], "uso_rag": False}

        embedder = _agente_rag_embedder
        if embedder is None:
            return {"docs_recuperados": [], "uso_rag": False}

        try:
            query_embedding = await embedder.aembed_query(consulta)
            asig_uuid = uuid.UUID(asig_id_str)
            async with AsyncSessionLocal() as session:
                chunks = await retrieve_chunks(
                    query_embedding, asig_uuid, session, top_k=settings.rag_top_k_chat
                )
        except Exception:
            chunks = []

        if not chunks:
            return {"docs_recuperados": [], "uso_rag": False}

        top1_similarity = chunks[0].get("similarity", 0.0)
        if top1_similarity >= settings.rag_relevance_threshold:
            return {"docs_recuperados": chunks, "uso_rag": True}
        else:
            return {"docs_recuperados": [], "uso_rag": False}

    return _agente_rag


# Fábrica del nodo redactor.


def _build_redactor_node() -> Callable[[EstadoConversacion], Any]:
    """Devuelve la función del nodo redactor.

    Expuesta para poder testearlo sin construir el grafo completo. El LLM se
    lee de `_llm_instance` en el momento de la invocación (mismo patrón que
    el Revisor, con el que comparte instancia), lo que permite sustituirlo
    con `patch('app.agents.graph._llm_instance', mock_llm)`.
    """

    async def _redactor(state: EstadoConversacion) -> dict[str, list[BaseMessage] | str]:
        """Genera la respuesta e incluye citas cuando hay documentos."""
        docs = state.get("docs_recuperados", [])
        feedback = state.get("feedback_revisor", "")
        prompt_redactor = state.get("prompt_redactor", "")

        mensajes_base: list[BaseMessage] = list(state["messages"])

        system_content = _build_redactor_system_content(docs, feedback, prompt_redactor)
        mensajes_con_ctx: list[BaseMessage] = (
            [SystemMessage(content=system_content)] + mensajes_base
            if system_content
            else mensajes_base
        )

        mensajes_recortados = trim_messages(
            mensajes_con_ctx,
            max_tokens=settings.max_context_tokens,
            token_counter="approximate",  # noqa: S106
            strategy="last",
            start_on="human",
            include_system=True,
            allow_partial=False,
        )

        llm = _llm_instance
        if llm is None:
            return {"messages": [], "borrador": ""}

        respuesta = await llm.ainvoke(mensajes_recortados)
        borrador = str(respuesta.content)

        # Añade citas estructuradas si el LLM no las incluye.
        if docs and "Fuentes:" not in borrador and "fuentes:" not in borrador.lower():
            borrador += "\n\n" + _format_citations(docs)
            respuesta_final = respuesta.__class__(content=borrador)
        else:
            respuesta_final = respuesta

        return {"messages": [respuesta_final], "borrador": borrador}

    return _redactor


# Fábrica del nodo revisor.


def _build_revisor_node() -> Callable[[EstadoConversacion], Any]:
    """Devuelve la función del nodo revisor.

    Expuesta para poder instanciarlo en tests sin construir el grafo completo.
    El LLM se lee de `_llm_instance` en el momento de la invocación, lo que
    permite sustituirlo con `patch('app.agents.graph._llm_instance', mock_llm)`.
    """

    async def _revisor(state: EstadoConversacion) -> dict[str, Any]:
        iteracion = state.get("iteracion", 0) + 1

        # Evita llamar al LLM una vez alcanzado el máximo.
        if iteracion > settings.max_revision_iterations:
            return {
                "feedback_revisor": "APROBADO (máximo de iteraciones alcanzado)",
                "iteracion": iteracion,
            }

        docs = state.get("docs_recuperados", [])
        borrador = state.get("borrador", "")
        if not docs or not borrador:
            return {"feedback_revisor": "APROBADO", "iteracion": iteracion}

        docs_txt = "\n\n".join(
            f"[{c['doc_nombre']} p.{c.get('page', '?')}]: {c['texto'][:300]}" for c in docs[:3]
        )
        prompt = (
            "Eres un evaluador de calidad de respuestas educativas.\n"
            "Comprueba si la respuesta del asistente está bien fundamentada "
            "en los fragmentos de los apuntes.\n\n"
            f"Fragmentos:\n{docs_txt}\n\n"
            f"Respuesta del asistente:\n{borrador}\n\n"
            "Responde ÚNICAMENTE con 'APROBADO' si la respuesta es fiel, "
            "o 'DEFICIENTE: <razón breve>' si contiene información no fundamentada."
        )

        llm = _llm_instance
        if llm is None:
            return {"feedback_revisor": "APROBADO", "iteracion": iteracion}

        respuesta = await llm.ainvoke([HumanMessage(content=prompt)])
        feedback = str(respuesta.content).strip()
        return {"feedback_revisor": feedback, "iteracion": iteracion}

    return _revisor


def build_graph(checkpointer: BaseCheckpointSaver[Any]) -> Any:
    """Compila el grafo completo con los 4 nodos y las aristas condicionales.

    El thread_id se pasa en cada invocación:
        config = {"configurable": {"thread_id": f"{user_id}:{asignatura_id}"}}

    Los objetos de LangChain (cliente LLM vía get_llm_client, OllamaEmbeddings)
    se crean aquí para que el cliente httpx interno quede ligado al event loop
    activo en el momento de la llamada, evitando errores de 'Event loop is
    closed' entre tests.
    """
    global _llm_instance, _reformulador_llm_instance, _agente_rag_embedder

    # reasoning=True: Qwen3 separa el razonamiento interno del contenido final,
    # así chunk.content en streaming no incluye bloques <think>...</think>
    # (evita que se filtren al alumno en el Redactor y al feedback del Revisor).
    llm = get_llm_client(streaming=True, reasoning=True)
    _llm_instance = llm

    # LLM del reformulador: temperatura 0, sin streaming, respuesta JSON corta.
    reformulador_llm = get_llm_client(temperature=0, max_output_tokens=100)
    _reformulador_llm_instance = reformulador_llm

    _agente_rag_embedder = OllamaEmbeddings(
        model=settings.embedding_model,
        base_url=settings.ollama_host,
    )

    _reformulador = _build_reformulador_node()

    _agente_rag = _build_agente_rag_node()

    _redactor = _build_redactor_node()

    _revisor = _build_revisor_node()

    builder: StateGraph[EstadoConversacion] = StateGraph(EstadoConversacion)

    builder.add_node("reformulador", _reformulador)  # type: ignore[call-overload]
    builder.add_node("agente_rag", _agente_rag)  # type: ignore[call-overload]
    builder.add_node("redactor", _redactor)  # type: ignore[call-overload]
    builder.add_node("revisor", _revisor)  # type: ignore[call-overload]

    builder.add_edge(START, "reformulador")
    builder.add_edge("reformulador", "agente_rag")
    builder.add_edge("agente_rag", "redactor")
    builder.add_conditional_edges("redactor", _decide_ruta_redactor)
    builder.add_conditional_edges("revisor", _decide_ruta_revisor)

    return builder.compile(checkpointer=checkpointer)


def pg_conn_string() -> str:
    """Convierte el DATABASE_URL de SQLAlchemy al formato que espera AsyncPostgresSaver."""
    return settings.database_url.replace("postgresql+psycopg", "postgresql", 1)
