"""LLMService tests using a stub chat model - no network, no real key."""

import logging

import pytest
from langchain_openai import ChatOpenAI
from pydantic import ValidationError

from app.config import Settings
from app.models import CandidateProfile
from app.services.llm_service import LLMConfigError, LLMError, LLMService

FAKE_KEY = "sk-or-fake-key-for-tests"


class StubChatModel:
    def __init__(self, result):
        self.result = result
        self.messages = None

    def with_structured_output(self, schema, **kwargs):
        return self

    def invoke(self, messages, config=None):
        self.messages = messages
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _service(key: str | None = None, chat_model=None) -> LLMService:
    settings = Settings(_env_file=None, openrouter_api_key=key)
    return LLMService(settings=settings, chat_model=chat_model)


def test_missing_api_key_raises_config_error():
    with pytest.raises(LLMConfigError, match="OPENROUTER_API_KEY"):
        _service(None).structured_call("s", "u", CandidateProfile)


def test_builds_openrouter_client_from_settings_without_network():
    model = _service(FAKE_KEY)._get_chat_model()
    assert isinstance(model, ChatOpenAI)
    assert model.openai_api_base == "https://openrouter.ai/api/v1"
    assert model.model_name == "nvidia/nemotron-3.5-lightning:free"
    assert FAKE_KEY not in repr(model)


def test_structured_call_returns_parsed_model():
    stub = StubChatModel(CandidateProfile(name="Alex Rivera"))
    result = _service(chat_model=stub).structured_call(
        "system",
        "user",
        CandidateProfile,
    )
    assert result.name == "Alex Rivera"
    assert [m.type for m in stub.messages] == ["system", "human"]


def test_provider_error_is_wrapped_without_leaking_details():
    stub = StubChatModel(RuntimeError(f"auth failed for {FAKE_KEY}"))
    with pytest.raises(LLMError) as exc_info:
        _service(chat_model=stub).structured_call(
            "s",
            "u",
            CandidateProfile,
        )
    assert FAKE_KEY not in str(exc_info.value)


def test_wrong_output_type_raises():
    stub = StubChatModel({"name": "not a model"})
    with pytest.raises(LLMError):
        _service(chat_model=stub).structured_call(
            "s",
            "u",
            CandidateProfile,
        )


def test_invalid_llm_output_is_wrapped():
    # Simulates the LLM omitting the required `name` field.
    try:
        CandidateProfile(skills=["Python"])
    except ValidationError as exc:
        validation_error = exc

    with pytest.raises(LLMError):
        _service(
            chat_model=StubChatModel(validation_error)
        ).structured_call(
            "s",
            "u",
            CandidateProfile,
        )


def test_missing_usage_metadata_is_logged_as_unavailable(caplog):
    """Missing provider usage must not be reported as zero token usage."""

    stub = StubChatModel(CandidateProfile(name="Alex Rivera"))

    with caplog.at_level(logging.INFO):
        result = _service(chat_model=stub).structured_call(
            "system",
            "user",
            CandidateProfile,
        )

    assert result.name == "Alex Rivera"
    assert "token_usage_unavailable=true" in caplog.text
    assert "input_tokens=0" not in caplog.text
    assert "output_tokens=0" not in caplog.text
    assert "total_tokens=0" not in caplog.text


def test_missing_usage_metadata_does_not_log_fake_zero_cost(caplog):
    """Missing usage metadata must not produce an estimated $0 LLM cost."""

    settings = Settings(
        _env_file=None,
        openrouter_api_key=FAKE_KEY,
        llm_input_cost_per_million=3.0,
        llm_output_cost_per_million=15.0,
    )

    stub = StubChatModel(CandidateProfile(name="Alex Rivera"))
    service = LLMService(
        settings=settings,
        chat_model=stub,
    )

    with caplog.at_level(logging.INFO):
        result = service.structured_call(
            "system",
            "user",
            CandidateProfile,
        )

    assert result.name == "Alex Rivera"
    assert "token_usage_unavailable=true" in caplog.text
    assert "estimated_cost_usd=" not in caplog.text
