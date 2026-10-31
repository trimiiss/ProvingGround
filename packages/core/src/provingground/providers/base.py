"""Provider interface: one async ``chat`` call, normalized across vendors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..models import Message, ToolCall, ToolSpec, Usage

StopReason = Literal["end", "max_tokens", "tool_use", "refusal", "content_filter", "other"]


class ChatRequest(BaseModel):
    model: str  # model id without the provider prefix
    messages: list[Message]
    system: str | None = None
    tools: list[ToolSpec] = Field(default_factory=list)
    max_tokens: int | None = None
    temperature: float | None = None  # ignored by providers that reject sampling params
    effort: str | None = None  # Anthropic `output_config.effort`
    response_schema: dict[str, Any] | None = None  # JSON schema for structured output
    metadata: dict[str, Any] = Field(default_factory=dict)  # hints (e.g. task) - never sent upstream


class ChatResponse(BaseModel):
    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    model_served: str
    stop_reason: StopReason = "end"
    latency_ms: float
    provider_content: list[dict[str, Any]] | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    def as_assistant_message(self) -> Message:
        return Message(
            role="assistant",
            content=self.text,
            tool_calls=list(self.tool_calls),
            provider_content=self.provider_content,
        )


class ProviderError(Exception):
    """A failed provider call. ``transient`` errors are retried by the runner."""

    def __init__(self, message: str, *, transient: bool, status_code: int | None = None) -> None:
        super().__init__(message)
        self.transient = transient
        self.status_code = status_code


class Provider(ABC):
    name: str = "provider"
    #: Same request -> same response. Deterministic providers are not response-cached.
    deterministic: bool = False

    @abstractmethod
    async def chat(self, request: ChatRequest) -> ChatResponse: ...

    async def aclose(self) -> None:  # noqa: B027 - optional hook
        pass
