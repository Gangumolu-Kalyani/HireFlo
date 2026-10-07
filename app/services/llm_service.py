"""Thin wrapper around the OpenRouter chat model.

Business logic (parsers, scoring) talks to this service, never to LangChain directly,
so it can be swapped for a fake in tests and reused later as a LangGraph node dependency.
"""

import logging
import re
import time
from typing import TypeVar

from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from app.config import Settings, get_settings

T = TypeVar("T", bound=BaseModel)

MAX_INPUT_CHARS = 50_000

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """The LLM call failed or returned output that doesn't match the schema."""


class LLMConfigError(LLMError):
    """The LLM is not configured (e.g. missing API key)."""


class LLMService:
    def __init__(
        self,
        settings: Settings | None = None,
        chat_model: BaseChatModel | None = None,
    ):
        self._settings = settings or get_settings()
        self._chat_model = chat_model

    def structured_call(
        self,
        system_prompt: str,
        user_content: str,
        schema: type[T],
    ) -> T:
        """Send one system + user message and return the reply parsed into `schema`."""

        model = self._get_chat_model().with_structured_output(
            schema,
            method="function_calling",
        )

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_content),
        ]

        usage_callback = UsageMetadataCallbackHandler()
        start_time = time.perf_counter()

        try:
            result = model.invoke(
                messages,
                config={"callbacks": [usage_callback]},
            )

        except Exception as exc:
            latency_ms = (time.perf_counter() - start_time) * 1000

            logger.error(
                "LLM call failed | model=%s | schema=%s | "
                "latency_ms=%.2f | error_type=%s",
                self._settings.llm_model,
                schema.__name__,
                latency_ms,
                type(exc).__name__,
            )

            raise LLMError(
                f"LLM call failed ({type(exc).__name__})"
            ) from exc

        latency_ms = (time.perf_counter() - start_time) * 1000

        usage_metadata = usage_callback.usage_metadata

        if usage_metadata:
            input_tokens = 0
            output_tokens = 0
            total_tokens = 0

            for usage in usage_metadata.values():
                input_tokens += int(
                    usage.get("input_tokens", 0) or 0
                )
                output_tokens += int(
                    usage.get("output_tokens", 0) or 0
                )
                total_tokens += int(
                    usage.get("total_tokens", 0) or 0
                )

            # Some providers may omit total_tokens while still returning
            # separate input and output token counts.
            if total_tokens == 0 and (
                input_tokens or output_tokens
            ):
                total_tokens = input_tokens + output_tokens

            logger.info(
                "LLM call completed | model=%s | schema=%s | "
                "latency_ms=%.2f | input_tokens=%d | "
                "output_tokens=%d | total_tokens=%d",
                self._settings.llm_model,
                schema.__name__,
                latency_ms,
                input_tokens,
                output_tokens,
                total_tokens,
            )

            self._log_cost(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

        else:
            logger.warning(
                "LLM call completed | model=%s | schema=%s | "
                "latency_ms=%.2f | token_usage_unavailable=true",
                self._settings.llm_model,
                schema.__name__,
                latency_ms,
            )

            logger.info(
                "LLM cost unavailable | model=%s | "
                "token_usage_unavailable=true",
                self._settings.llm_model,
            )

        if not isinstance(result, schema):
            logger.error(
                "LLM response validation failed | "
                "model=%s | schema=%s | received_type=%s | latency_ms=%.2f",
                self._settings.llm_model,
                schema.__name__,
                type(result).__name__,
                latency_ms,
            )

            raise LLMError(
                f"LLM returned {type(result).__name__}, "
                f"expected {schema.__name__}"
            )

        return result

    def _log_cost(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        """Log estimated LLM cost when pricing is configured."""

        input_rate = self._settings.llm_input_cost_per_million
        output_rate = self._settings.llm_output_cost_per_million

        if input_rate is None or output_rate is None:
            logger.info(
                "LLM cost unavailable | model=%s | "
                "pricing_not_configured=true",
                self._settings.llm_model,
            )
            return

        input_cost = (
            input_tokens / 1_000_000
        ) * input_rate

        output_cost = (
            output_tokens / 1_000_000
        ) * output_rate

        total_cost = input_cost + output_cost

        logger.info(
            "LLM cost | model=%s | estimated_cost_usd=%.6f",
            self._settings.llm_model,
            total_cost,
        )

    def _get_chat_model(self) -> BaseChatModel:
        if self._chat_model is None:
            self._chat_model = self._build_chat_model()

        return self._chat_model

    def _build_chat_model(self) -> BaseChatModel:
        api_key = self._settings.openrouter_api_key

        if api_key is None or not api_key.get_secret_value().strip():
            raise LLMConfigError(
                "OPENROUTER_API_KEY is not set. "
                "Copy .env.example to .env and add your key."
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
    """Validate untrusted text and wrap it in <tag>...</tag> delimiters.

    Any `<tag>` / `</tag>` inside the text is escaped so the content cannot
    "close" the data block early and smuggle instructions outside it.
    """

    if not text or not text.strip():
        raise ValueError(f"{tag} text is empty")

    if len(text) > MAX_INPUT_CHARS:
        raise ValueError(
            f"{tag} text is too long "
            f"({len(text)} > {MAX_INPUT_CHARS} chars)"
        )

    escaped = re.sub(
        rf"<(\s*/?\s*{tag}\b)",
        r"&lt;\1",
        text,
        flags=re.IGNORECASE,
    )

    return f"<{tag}>\n{escaped.strip()}\n</{tag}>"
