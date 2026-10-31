"""Provider registry. Models are referenced as ``<provider>/<model>``, e.g. ``sim/model-a``,
``anthropic/claude-opus-5-5``, ``openai/gpt-5-mini``, ``ollama/llama3.2``, ``openrouter/vendor/model``."""

from __future__ import annotations

import os

from ..config import ProviderEndpoint
from .base import ChatRequest, ChatResponse, Provider, ProviderError
from .pricing import cost_usd, default_price_table

BUILTIN_ENDPOINTS: dict[str, ProviderEndpoint] = {
    "openai": ProviderEndpoint(api_key_env="OPENAI_API_KEY", max_tokens_param="max_completion_tokens"),
    "openrouter": ProviderEndpoint(base_url="https://openrouter.ai/api/v1", api_key_env="OPENROUTER_API_KEY"),
    "groq": ProviderEndpoint(base_url="https://api.groq.com/openai/v1", api_key_env="GROQ_API_KEY"),
    "gemini": ProviderEndpoint(
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/", api_key_env="GEMINI_API_KEY"
    ),
    "ollama": ProviderEndpoint(base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")),
    "vllm": ProviderEndpoint(base_url=os.environ.get("VLLM_BASE_URL", "http://localhost:8001/v1")),
    "anthropic": ProviderEndpoint(kind="anthropic", api_key_env="ANTHROPIC_API_KEY"),
}


def parse_model_ref(ref: str) -> tuple[str, str]:
    if "/" not in ref:
        raise ValueError(f"model reference {ref!r} must look like '<provider>/<model>' (e.g. sim/model-a)")
    provider, model = ref.split("/", 1)
    return provider, model


def same_model(requested: str | None, served: str | None) -> bool:
    """Served model matches the request (allowing dated-snapshot / ':tag' resolution)."""
    if not requested or not served:
        return True
    req, srv = requested.split("/", 1)[-1].lower(), served.split("/", 1)[-1].lower()
    return srv == req or srv.startswith(req + "-") or srv.startswith(req + ":") or srv.startswith(req + "@")


class ProviderRegistry:
    """Creates and caches provider clients for one process."""

    def __init__(self, endpoints: dict[str, ProviderEndpoint] | None = None) -> None:
        self.endpoints = {**BUILTIN_ENDPOINTS, **(endpoints or {})}
        self._cache: dict[tuple[str, bool], Provider] = {}

    def get(self, provider: str, *, judge: bool = False) -> Provider:
        key = (provider, judge)
        if key in self._cache:
            return self._cache[key]
        instance: Provider
        if provider == "sim":
            from .sim import SimProvider

            instance = SimProvider()
        else:
            endpoint = self.endpoints.get(provider)
            if endpoint is None:
                raise ProviderError(
                    f"unknown provider {provider!r}; configure it under 'providers:'", transient=False
                )
            api_key = os.environ.get(endpoint.api_key_env) if endpoint.api_key_env else None
            if endpoint.kind == "anthropic":
                from .anthropic_provider import AnthropicProvider

                instance = AnthropicProvider(api_key=api_key, base_url=endpoint.base_url, fallbacks=judge)
            else:
                from .openai_compat import OpenAICompatProvider

                instance = OpenAICompatProvider(
                    provider,
                    base_url=endpoint.base_url,
                    api_key=api_key,
                    max_tokens_param=endpoint.max_tokens_param,
                )
        self._cache[key] = instance
        return instance

    def for_model(self, model_ref: str, *, judge: bool = False) -> tuple[Provider, str]:
        provider, model = parse_model_ref(model_ref)
        return self.get(provider, judge=judge), model

    async def aclose(self) -> None:
        for provider in self._cache.values():
            await provider.aclose()
        self._cache.clear()


_default_registry: ProviderRegistry | None = None


def default_registry() -> ProviderRegistry:
    global _default_registry
    if _default_registry is None:
        _default_registry = ProviderRegistry()
    return _default_registry


__all__ = [
    "BUILTIN_ENDPOINTS",
    "ChatRequest",
    "ChatResponse",
    "Provider",
    "ProviderError",
    "ProviderRegistry",
    "cost_usd",
    "default_price_table",
    "default_registry",
    "parse_model_ref",
    "same_model",
]
