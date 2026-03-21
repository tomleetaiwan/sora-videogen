from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import settings
from app.services.summarizer import summarize_content


@pytest.mark.asyncio
async def test_summarize_content_returns_summary():
    mock_choice = MagicMock()
    mock_choice.message.content = "這是一篇關於人工智慧的摘要"
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]

    with patch("app.services.summarizer.get_openai_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_get_client.return_value = mock_client

        result = await summarize_content("Some article content about AI")

    assert "人工智慧" in result
    _, kwargs = mock_client.chat.completions.create.await_args
    assert "max_completion_tokens" in kwargs
    assert kwargs["max_completion_tokens"] == 2000
    assert "max_tokens" not in kwargs
    assert "temperature" not in kwargs


@pytest.mark.asyncio
async def test_summarize_content_keeps_temperature_for_non_gpt5(monkeypatch):
    mock_choice = MagicMock()
    mock_choice.message.content = "摘要"
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]

    monkeypatch.setattr(settings, "summarizer_model", "gpt-4o-mini")

    with patch("app.services.summarizer.get_openai_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_get_client.return_value = mock_client

        await summarize_content("Some article content")

    _, kwargs = mock_client.chat.completions.create.await_args
    assert kwargs["temperature"] == 0.7


@pytest.mark.asyncio
async def test_summarize_content_raises_on_empty():
    mock_choice = MagicMock()
    mock_choice.message.content = ""
    mock_choice.message.refusal = None
    mock_choice.finish_reason = "stop"
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]

    with patch("app.services.summarizer.get_openai_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_get_client.return_value = mock_client

        with pytest.raises(ValueError, match="Empty summary"):
            await summarize_content("content")


@pytest.mark.asyncio
async def test_summarize_content_retries_after_empty_response():
    empty_choice = MagicMock()
    empty_choice.message.content = ""
    empty_choice.message.refusal = None
    empty_choice.finish_reason = "stop"
    empty_response = MagicMock()
    empty_response.choices = [empty_choice]

    summary_choice = MagicMock()
    summary_choice.message.content = "這是重試後的摘要"
    summary_response = MagicMock()
    summary_response.choices = [summary_choice]

    with patch("app.services.summarizer.get_openai_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=[empty_response, summary_response]
        )
        mock_get_client.return_value = mock_client

        with patch("app.services.summarizer.asyncio.sleep", new=AsyncMock()) as sleep_mock:
            result = await summarize_content("content")

    assert result == "這是重試後的摘要"
    assert mock_client.chat.completions.create.await_count == 2
    sleep_mock.assert_awaited_once_with(3)


@pytest.mark.asyncio
async def test_summarize_content_surfaces_refusal_reason():
    refusal_choice = MagicMock()
    refusal_choice.message.content = None
    refusal_choice.message.refusal = "I can't help with that request."
    refusal_choice.finish_reason = "stop"
    refusal_response = MagicMock()
    refusal_response.choices = [refusal_choice]

    with patch("app.services.summarizer.get_openai_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=refusal_response)
        mock_get_client.return_value = mock_client

        with pytest.raises(ValueError, match="Model refused to summarize content"):
            await summarize_content("content")
