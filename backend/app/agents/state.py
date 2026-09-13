from typing import Annotated, Any, NotRequired

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class EstadoConversacion(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]

    # Se mantiene como str para que el checkpointer lo serialice a JSON.
    asignatura_id: NotRequired[str]

    # Contexto opcional usado por el reformulador para desambiguar.
    descripcion_asignatura: NotRequired[str]
    titulos_documentos: NotRequired[list[str]]

    # Se combina con las reglas fijas del prompt; nunca las sustituye.
    prompt_redactor: NotRequired[str]

    consulta_reescrita: NotRequired[str]

    # El agente RAG fija uso_rag según el umbral de similitud coseno.
    docs_recuperados: NotRequired[list[dict[str, Any]]]
    uso_rag: NotRequired[bool]

    borrador: NotRequired[str]

    feedback_revisor: NotRequired[str]
    iteracion: NotRequired[int]
