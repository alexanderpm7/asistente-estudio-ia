import uuid

from pydantic import BaseModel, ConfigDict


class Flashcard(BaseModel):
    model_config = ConfigDict(from_attributes=False)
    anverso: str
    reverso: str


class Pregunta(BaseModel):
    model_config = ConfigDict(from_attributes=False)
    enunciado: str
    opciones: list[str]
    respuesta_correcta: int


class RepasoResponse(BaseModel):
    model_config = ConfigDict(from_attributes=False)
    asignatura_id: uuid.UUID
    flashcards: list[Flashcard]
    preguntas: list[Pregunta]
