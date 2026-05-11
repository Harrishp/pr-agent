from unittest.mock import AsyncMock, MagicMock, patch

import litellm
import pytest

import pr_agent.algo.ai_handlers.litellm_ai_handler as litellm_handler
from pr_agent.algo.ai_handlers.litellm_ai_handler import LiteLLMAIHandler


def create_settings(
    api_mode="responses",
    custom_reasoning_model=False,
    responses_store=False,
    responses_extra_body=None,
):
    openai_section = type("OpenAISection", (), {
        "key": "test-key",
        "api_base": "https://third-party.example.com/v1",
    })()

    return type("Settings", (), {
        "config": type("Config", (), {
            "reasoning_effort": None,
            "ai_timeout": 120,
            "custom_reasoning_model": custom_reasoning_model,
            "max_model_tokens": 32000,
            "verbosity_level": 0,
            "seed": -1,
            "get": lambda self, key, default=None: default,
        })(),
        "litellm": type("LiteLLM", (), {
            "get": lambda self, key, default=None: default,
        })(),
        "openai": openai_section,
        "get": lambda self, key, default=None: {
            "OPENAI.KEY": openai_section.key,
            "OPENAI.API_BASE": openai_section.api_base,
            "OPENAI.API_MODE": api_mode,
            "OPENAI.RESPONSES_STORE": responses_store,
            "OPENAI.RESPONSES_EXTRA_BODY": responses_extra_body,
        }.get(key, default),
    })()


def create_choices_response(content="ok", finish_reason="stop"):
    mock = MagicMock()
    mock.__getitem__ = lambda self, key: {
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}]
    }[key]
    mock.dict.return_value = {"choices": [{"message": {"content": content}, "finish_reason": finish_reason}]}
    return mock


class ResponseTextObj:
    def __init__(self, output_text="responses text", finish_reason="completed"):
        self.output_text = output_text
        self.finish_reason = finish_reason

    def dict(self):
        return {"output_text": self.output_text, "finish_reason": self.finish_reason}


class ResponseOutputObj:
    def __init__(self, text="nested responses text", finish_reason="completed"):
        self.output = [
            {
                "content": [{"text": text}],
                "finish_reason": finish_reason,
            }
        ]

    def dict(self):
        return {
            "output": [
                {
                    "content": [{"text": self.output[0]["content"][0]["text"]}],
                    "finish_reason": self.output[0]["finish_reason"],
                }
            ]
        }


@pytest.fixture(autouse=True)
def patch_logger():
    with patch("pr_agent.algo.ai_handlers.litellm_ai_handler.get_logger") as mock_log:
        yield mock_log.return_value


class TestLiteLLMResponsesMode:
    @pytest.mark.asyncio
    async def test_responses_mode_uses_aresponses_with_input(self, monkeypatch):
        settings = create_settings()
        monkeypatch.setattr(litellm_handler, "get_settings", lambda: settings)
        monkeypatch.setattr(litellm, "api_key", None)

        with patch.object(litellm, "aresponses", new_callable=AsyncMock, create=True) as mock_responses:
            mock_responses.return_value = ResponseTextObj("hello from responses", "stop")

            handler = LiteLLMAIHandler()
            resp, finish_reason = await handler.chat_completion(
                model="third-party-model",
                system="system prompt",
                user="user prompt",
            )

        assert resp == "hello from responses"
        assert finish_reason == "stop"
        call_kwargs = mock_responses.call_args.kwargs
        assert call_kwargs["model"] == "third-party-model"
        assert call_kwargs["api_base"] == "https://third-party.example.com/v1"
        assert call_kwargs["store"] is False
        assert call_kwargs["input"] == [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "user prompt"},
        ]
        assert "messages" not in call_kwargs
        assert call_kwargs["temperature"] == 0.2

    @pytest.mark.asyncio
    async def test_responses_mode_parses_nested_output(self, monkeypatch):
        settings = create_settings()
        monkeypatch.setattr(litellm_handler, "get_settings", lambda: settings)

        with patch.object(litellm, "aresponses", new_callable=AsyncMock, create=True) as mock_responses:
            mock_responses.return_value = ResponseOutputObj("nested text", "completed")

            handler = LiteLLMAIHandler()
            resp, finish_reason = await handler.chat_completion(
                model="third-party-model",
                system="system prompt",
                user="user prompt",
            )

        assert resp == "nested text"
        assert finish_reason == "completed"

    @pytest.mark.asyncio
    async def test_responses_mode_falls_back_to_choices_shape(self, monkeypatch):
        settings = create_settings()
        monkeypatch.setattr(litellm_handler, "get_settings", lambda: settings)

        with patch.object(litellm, "aresponses", new_callable=AsyncMock, create=True) as mock_responses:
            mock_responses.return_value = create_choices_response("fallback content", "stop")

            handler = LiteLLMAIHandler()
            resp, finish_reason = await handler.chat_completion(
                model="third-party-model",
                system="system prompt",
                user="user prompt",
            )

        assert resp == "fallback content"
        assert finish_reason == "stop"

    @pytest.mark.asyncio
    async def test_responses_mode_custom_reasoning_model_merges_prompts(self, monkeypatch):
        settings = create_settings(custom_reasoning_model=True)
        monkeypatch.setattr(litellm_handler, "get_settings", lambda: settings)

        with patch.object(litellm, "aresponses", new_callable=AsyncMock, create=True) as mock_responses:
            mock_responses.return_value = ResponseTextObj("merged", "stop")

            handler = LiteLLMAIHandler()
            await handler.chat_completion(
                model="third-party-model",
                system="system prompt",
                user="user prompt",
            )

        call_kwargs = mock_responses.call_args.kwargs
        assert call_kwargs["input"] == [
            {"role": "user", "content": "system prompt\n\n\nuser prompt"}
        ]
        assert "temperature" not in call_kwargs

    @pytest.mark.asyncio
    async def test_responses_mode_merges_extra_body(self, monkeypatch):
        settings = create_settings(responses_extra_body='{"provider_hint":"fast"}')
        monkeypatch.setattr(litellm_handler, "get_settings", lambda: settings)

        with patch.object(litellm, "aresponses", new_callable=AsyncMock, create=True) as mock_responses:
            mock_responses.return_value = ResponseTextObj("ok", "stop")

            handler = LiteLLMAIHandler()
            await handler.chat_completion(
                model="third-party-model",
                system="system prompt",
                user="user prompt",
            )

        call_kwargs = mock_responses.call_args.kwargs
        assert call_kwargs["provider_hint"] == "fast"

    @pytest.mark.asyncio
    async def test_responses_mode_falls_back_to_sync_responses(self, monkeypatch):
        settings = create_settings()
        monkeypatch.setattr(litellm_handler, "get_settings", lambda: settings)
        monkeypatch.delattr(litellm, "aresponses", raising=False)

        with patch.object(litellm, "responses", create=True) as mock_responses:
            mock_responses.return_value = ResponseTextObj("sync responses", "stop")

            handler = LiteLLMAIHandler()
            resp, finish_reason = await handler.chat_completion(
                model="third-party-model",
                system="system prompt",
                user="user prompt",
            )

        assert resp == "sync responses"
        assert finish_reason == "stop"
