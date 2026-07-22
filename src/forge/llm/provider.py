"""Provider-agnostic chat model factory.

Three tiers, selected by ``LLMRole`` (cahier 12.2):

* ``ROUTER``   — supervisor routing, intent and guardrail classification. Cheap.
* ``REASONER`` — Planner and Reviewer. Strong reasoning, the token-fat path.
* ``CODER``    — Editor and test generation.

Free-tier reality that drives the default: all free Gemini models draw on a
shared ~250k tokens/minute pool, against roughly 6-12k TPM on Groq. A single
Reviewer call carrying a 15-20k-token ContextPack exceeds Groq's per-minute
budget on its own, so Gemini is the default and Groq the fallback.

These quotas moved twice in under two months and Google no longer publishes a
stable table — re-verify in the AI Studio dashboard the week of the demo.

The cahier asks for distinct model families for Editor and Reviewer, so that a
critic does not share the author's blind spots. That is worth having but it is
last on the cut list (docs/descope-v1.md §12) — under one provider both roles
collapse onto the same family and the limitation gets stated in the report.
"""

from __future__ import annotations

from langchain_core.globals import set_llm_cache
from langchain_core.language_models.chat_models import BaseChatModel

from forge.config import LLMProvider, LLMRole, Settings, get_settings
from forge.llm.cache import FixtureLLMCache

_cache_installed = False


def _install_cache() -> None:
    global _cache_installed
    if not _cache_installed:
        set_llm_cache(FixtureLLMCache())
        _cache_installed = True


def build_llm(
    role: LLMRole,
    *,
    temperature: float = 0.0,
    settings: Settings | None = None,
) -> BaseChatModel:
    """Return the chat model configured for ``role`` under the active provider.

    Imports are deferred per provider so that an SDK for a provider you are not
    using never has to be importable.
    """
    settings = settings or get_settings()
    _install_cache()
    model = settings.model_name(role)

    match settings.llm_provider:
        case LLMProvider.GEMINI:
            from langchain_google_genai import ChatGoogleGenerativeAI

            return ChatGoogleGenerativeAI(
                model=model,
                google_api_key=settings.google_api_key or None,
                temperature=temperature,
                cache=True,
            )

        case LLMProvider.GROQ:
            from langchain_groq import ChatGroq

            return ChatGroq(
                model=model,
                api_key=settings.groq_api_key or None,
                temperature=temperature,
                cache=True,
            )

        case LLMProvider.OLLAMA:
            from langchain_ollama import ChatOllama

            return ChatOllama(
                model=model,
                base_url=settings.ollama_base_url,
                temperature=temperature,
                cache=True,
            )

    raise ValueError(f"Unknown provider: {settings.llm_provider}")
