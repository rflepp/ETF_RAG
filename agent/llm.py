"""Chat model selection. Production: Mistral (free tier) with Gemini (billed) as fallback only for requests the
gateway marks as paid-allowed. Development: a local Ollama model. Tests: a scripted fake model."""
from __future__ import annotations

import os

from langchain_core.language_models import BaseChatModel


def make(name: str) -> BaseChatModel:
    if name == "mistral":
        from langchain_mistralai import ChatMistralAI
        return ChatMistralAI(model=os.environ.get("MISTRAL_MODEL", "mistral-small-latest"), temperature=0.1,
                             max_retries=1, timeout=60)
    if name == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model=os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest"), temperature=0.1,
                                      max_retries=1, google_api_key=os.environ.get("GEMINI_API_KEY"))
    if name == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(model=os.environ.get("OLLAMA_MODEL", "qwen3:8b"), temperature=0.1, reasoning=False,
                          num_ctx=16384)
    raise ValueError(f"unknown model provider {name}")


def chain(paid_allowed: bool) -> list[str]:
    """Providers to try in order, from env: LLM_CHAIN (default 'mistral'); 'gemini' appended for paid requests."""
    names = [n.strip() for n in os.environ.get("LLM_CHAIN", "mistral").split(",") if n.strip()]
    if paid_allowed and "gemini" not in names and os.environ.get("GEMINI_API_KEY"):
        names.append("gemini")
    return names


def with_tools(names: list[str], tools: list) -> object:
    """Bind tools to every provider and chain them as fallbacks (a provider error moves on to the next)."""
    models = [make(n).bind_tools(tools) for n in names]
    return models[0].with_fallbacks(models[1:]) if len(models) > 1 else models[0]
