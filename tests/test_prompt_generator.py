import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import settings
from app.services import prompt_generator as prompt_generator_module
from app.services.prompt_generator import generate_scene_prompts, rewrite_or_split_scene


@pytest.mark.asyncio
async def test_generate_scene_prompts_returns_scenes():
    scenes = [
        {"narration_text": "場景一的旁白", "video_prompt": "A wide shot of a city"},
        {"narration_text": "場景二的旁白", "video_prompt": "Close-up of a person"},
    ]
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps({"scenes": scenes})
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]

    with patch("app.services.prompt_generator.get_openai_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_get_client.return_value = mock_client

        result = await generate_scene_prompts("Some summary")

    assert len(result) == 2
    assert result[0]["narration_text"] == "場景一的旁白"
    assert "duration_estimate" in result[0]
    _, kwargs = mock_client.chat.completions.create.await_args
    assert "Traditional Chinese (繁體中文) prompt for the Sora 2 video model" in kwargs["messages"][0]["content"]
    assert 'Return JSON in the shape {"scenes": [{"narration_text": "...", "video_prompt": "..."}]}' in kwargs["messages"][0]["content"]
    assert kwargs["max_completion_tokens"] == 4000
    assert "max_tokens" not in kwargs
    assert "temperature" not in kwargs


@pytest.mark.asyncio
async def test_generate_scene_prompts_handles_direct_array():
    scenes = [{"narration_text": "旁白", "video_prompt": "A scene"}]
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps(scenes)
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]

    with patch("app.services.prompt_generator.get_openai_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_get_client.return_value = mock_client

        result = await generate_scene_prompts("Summary")

    assert len(result) == 1


@pytest.mark.asyncio
async def test_generate_scene_prompts_supports_english_video_prompts():
    scenes = [{"narration_text": "旁白", "video_prompt": "A scene"}]
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps({"scenes": scenes})
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]

    with patch("app.services.prompt_generator.get_openai_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_get_client.return_value = mock_client

        await generate_scene_prompts("Summary", video_prompt_language="en")

    _, kwargs = mock_client.chat.completions.create.await_args
    assert '2. "video_prompt": An English prompt for the Sora 2 video model' in kwargs["messages"][0]["content"]


@pytest.mark.asyncio
async def test_rewrite_or_split_scene_uses_max_completion_tokens():
    scenes = [{"narration_text": "拆分後旁白", "video_prompt": "A rewritten scene"}]
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps({"scenes": scenes})
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]

    with patch("app.services.prompt_generator.get_openai_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_get_client.return_value = mock_client

        result = await rewrite_or_split_scene(
            "原始過長旁白",
            "Original scene prompt",
            actual_duration_seconds=21.0,
            max_duration_seconds=20.0,
            max_scenes=2,
        )

    assert len(result) == 1
    _, kwargs = mock_client.chat.completions.create.await_args
    assert kwargs["max_completion_tokens"] == 2000
    assert "max_tokens" not in kwargs
    assert "temperature" not in kwargs


@pytest.mark.asyncio
async def test_generate_scene_prompts_retries_with_larger_budget_after_length_truncation():
    truncated_choice = MagicMock()
    truncated_choice.message.content = ""
    truncated_choice.message.refusal = None
    truncated_choice.finish_reason = "length"
    truncated_response = MagicMock()
    truncated_response.choices = [truncated_choice]

    scenes = [{"narration_text": "重試後旁白", "video_prompt": "A recovered scene"}]
    valid_choice = MagicMock()
    valid_choice.message.content = json.dumps({"scenes": scenes})
    valid_choice.message.refusal = None
    valid_choice.finish_reason = "stop"
    valid_response = MagicMock()
    valid_response.choices = [valid_choice]

    with (
        patch("app.services.prompt_generator.get_openai_client") as mock_get_client,
        patch.object(prompt_generator_module.asyncio, "sleep", new=AsyncMock()) as mock_sleep,
    ):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=[truncated_response, valid_response]
        )
        mock_get_client.return_value = mock_client

        result = await generate_scene_prompts("Summary")

    assert len(result) == 1
    assert result[0]["narration_text"] == "重試後旁白"
    assert mock_client.chat.completions.create.await_count == 2
    first_call = mock_client.chat.completions.create.await_args_list[0].kwargs
    second_call = mock_client.chat.completions.create.await_args_list[1].kwargs
    assert first_call["max_completion_tokens"] == 4000
    assert second_call["max_completion_tokens"] == 8000
    mock_sleep.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_scene_prompts_retries_invalid_json_with_larger_budget_after_length_finish_reason():
    truncated_choice = MagicMock()
    truncated_choice.message.content = '{"scenes": ['
    truncated_choice.message.refusal = None
    truncated_choice.finish_reason = "length"
    truncated_response = MagicMock()
    truncated_response.choices = [truncated_choice]

    scenes = [{"narration_text": "修復後旁白", "video_prompt": "A repaired scene"}]
    valid_choice = MagicMock()
    valid_choice.message.content = json.dumps({"scenes": scenes})
    valid_choice.message.refusal = None
    valid_choice.finish_reason = "stop"
    valid_response = MagicMock()
    valid_response.choices = [valid_choice]

    with (
        patch("app.services.prompt_generator.get_openai_client") as mock_get_client,
        patch.object(prompt_generator_module.asyncio, "sleep", new=AsyncMock()) as mock_sleep,
    ):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=[truncated_response, valid_response]
        )
        mock_get_client.return_value = mock_client

        result = await generate_scene_prompts("Summary")

    assert len(result) == 1
    assert result[0]["narration_text"] == "修復後旁白"
    second_call = mock_client.chat.completions.create.await_args_list[1].kwargs
    assert second_call["max_completion_tokens"] == 8000
    mock_sleep.assert_awaited_once()


def test_increase_completion_budget_uses_configured_cap(monkeypatch):
    monkeypatch.setattr(settings, "scene_prompt_max_completion_token_cap", 6000)
    request_kwargs = {"max_completion_tokens": 4000}

    next_budget = prompt_generator_module._increase_completion_budget(request_kwargs)

    assert next_budget == 6000
    assert request_kwargs["max_completion_tokens"] == 6000


@pytest.mark.asyncio
async def test_rewrite_or_split_scene_infers_chinese_prompt_language_from_current_prompt():
    scenes = [{"narration_text": "拆分後旁白", "video_prompt": "清晨市場，低角度鏡頭"}]
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps({"scenes": scenes})
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]

    with patch("app.services.prompt_generator.get_openai_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_get_client.return_value = mock_client

        await rewrite_or_split_scene(
            "原始過長旁白",
            "清晨市場，低角度鏡頭",
            actual_duration_seconds=21.0,
            max_duration_seconds=20.0,
            max_scenes=2,
        )

    _, kwargs = mock_client.chat.completions.create.await_args
    assert "Traditional Chinese (繁體中文) prompt for the Sora 2 video model" in kwargs["messages"][0]["content"]
    assert "Current video prompt (Traditional Chinese):" in kwargs["messages"][1]["content"]


@pytest.mark.asyncio
async def test_generate_scene_prompts_keeps_temperature_for_non_gpt5(monkeypatch):
    scenes = [{"narration_text": "旁白", "video_prompt": "A scene"}]
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps({"scenes": scenes})
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]

    monkeypatch.setattr(settings, "summarizer_model", "gpt-4o-mini")

    with patch("app.services.prompt_generator.get_openai_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_get_client.return_value = mock_client

        await generate_scene_prompts("Summary")

    _, kwargs = mock_client.chat.completions.create.await_args
    assert kwargs["temperature"] == 0.7


@pytest.mark.asyncio
async def test_generate_scene_prompts_retries_after_empty_response():
    empty_choice = MagicMock()
    empty_choice.message.content = ""
    empty_choice.message.refusal = None
    empty_choice.finish_reason = None
    empty_response = MagicMock()
    empty_response.choices = [empty_choice]

    scenes = [{"narration_text": "重試後旁白", "video_prompt": "A recovered scene"}]
    valid_choice = MagicMock()
    valid_choice.message.content = json.dumps({"scenes": scenes})
    valid_choice.message.refusal = None
    valid_choice.finish_reason = "stop"
    valid_response = MagicMock()
    valid_response.choices = [valid_choice]

    with (
        patch("app.services.prompt_generator.get_openai_client") as mock_get_client,
        patch.object(prompt_generator_module.asyncio, "sleep", new=AsyncMock()) as mock_sleep,
    ):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=[empty_response, valid_response]
        )
        mock_get_client.return_value = mock_client

        result = await generate_scene_prompts("Summary")

    assert len(result) == 1
    assert result[0]["narration_text"] == "重試後旁白"
    assert mock_client.chat.completions.create.await_count == 2
    mock_sleep.assert_awaited_once()
