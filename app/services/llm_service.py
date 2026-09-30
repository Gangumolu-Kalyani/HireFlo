"""Thin wrapper around the OpenRouter chat model.

Business logic (parsers, scoring) talks to this service, never to LangChain directly,
so it can be swapped for a fake in tests and reused later as a LangGraph node dependency.
"""

import re
from typing import TypeVar

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from app.config import Settings, get_settings

T = TypeVar("T", bound=BaseModel)

MAX_INPUT_CHARS = 50_000


class LLMError(Exception):
    """The LLM call failed or returned output that doesn't match the schema."""


class LLMConfigError(LLMError):
    """The LLM is not configured (e.g. missing API key)."""


class LLMService:
    def __init__(self, settings: Settings | None = None, chat_model: BaseChatModel | None = None):
        self._settings = settings or get_settings()
        self._chat_model = chat_model  # built lazily so importing never needs an API key

    def structured_call(self, system_prompt: str, user_content: str, schema: type[T]) -> T:
        """Send one system + user message and return the reply parsed into `schema`."""
        # function_calling is the most widely supported structured-output mode on OpenRouter.
        model = self._get_chat_model().with_structured_output(schema, method="function_calling")
        messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_content)]
        try:
            result = model.invoke(messages)
        except Exception as exc:
            # Only the exception type goes in the message; details stay on __cause__.
            raise LLMError(f"LLM call failed ({type(exc).__name__})") from exc
        if not isinstance(result, schema):
            raise LLMError(f"LLM returned {type(result).__name__}, expected {schema.__name__}")
        return result

    def _get_chat_model(self) -> BaseChatModel:
        if self._chat_model is None:
            self._chat_model = self._build_chat_model()
        return self._chat_model

    def _build_chat_model(self) -> BaseChatModel:
        api_key = self._settings.openrouter_api_key
        if api_key is None or not api_key.get_secret_value().strip():
            raise LLMConfigError(
                "OPENROUTER_API_KEY is not set. Copy .env.example to .env and add your key."
            )
        return ChatOpenAI(
            model=self._settings.llm_model,
            api_key=api_key,
            base_url=self._settings.openrouter_base_url,
            temperature=0,
            timeout=60,
            max_retries=2,
        )


def wrap_untrusted(text: str, tag: str) -> str:
    """Validate untrusted text and wrap it in <tag>...</tag> delimiters for the prompt.

    Any `<tag>` / `</tag>` inside the text is escaped so the content cannot "close"
    the data block early and smuggle instructions outside it.
    """
    if not text or not text.strip():
        raise ValueError(f"{tag} text is empty")
    if len(text) > MAX_INPUT_CHARS:
        raise ValueError(f"{tag} text is too long ({len(text)} > {MAX_INPUT_CHARS} chars)")
    escaped = re.sub(rf"<(\s*/?\s*{tag}\b)", r"&lt;\1", text, flags=re.IGNORECASE)
    return f"<{tag}>\n{escaped.strip()}\n</{tag}>"
