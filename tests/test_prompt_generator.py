import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import settings
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
