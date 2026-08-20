from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    """Payload accepted by the Nari client."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=50_000)
    task_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="Explicit Manus task continuation ID. Omit to create a new chat task.",
    )


class ManusErrorPayload(BaseModel):
    code: str = "unknown_error"
    message: str = "Manus API request failed"


class ManusFailureResponse(BaseModel):
    ok: bool = False
    request_id: str | None = None
    error: ManusErrorPayload


class ChatResponse(BaseModel):
    """Safe task metadata plus the assistant text from Manus task events."""

    ok: bool = True
    request_id: str
    task_id: str
    task_title: str | None = None
    task_url: str | None = None
    share_url: str | None = None
    share_visibility: str | None = None
    response: str | None = None
    raw: dict[str, Any] | None = None
