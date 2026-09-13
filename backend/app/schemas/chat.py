from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    mensaje: str = Field(min_length=1, max_length=4096)
