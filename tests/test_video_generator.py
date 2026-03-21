from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import settings
from app.services.video_generator import generate_video


@pytest.mark.asyncio
async def test_generate_video_uses_supported_default_size(tmp_path, monkeypatch):
    output_path = tmp_path / "video.mp4"

    mock_video = MagicMock(status="completed", id="video-123")
    mock_content = AsyncMock()
    mock_content.write_to_file = AsyncMock()

    mock_client = MagicMock()
    mock_client.videos.create_and_poll = AsyncMock(return_value=mock_video)
    mock_client.videos.download_content = AsyncMock(return_value=mock_content)

    monkeypatch.setattr(settings, "sora_video_size", "1280x720")
    monkeypatch.setattr(settings, "video_generation_max_attempts", 1)

    with patch("app.services.video_generator.get_openai_client", return_value=mock_client):
        await generate_video("A cinematic prompt", output_path, duration_seconds=8)

    _, kwargs = mock_client.videos.create_and_poll.await_args
    assert kwargs["size"] == "1280x720"
    assert kwargs["seconds"] == 8


@pytest.mark.asyncio
async def test_generate_video_rejects_unsupported_size(tmp_path, monkeypatch):
    output_path = tmp_path / "video.mp4"

    monkeypatch.setattr(settings, "sora_video_size", "1920x1080")

    with patch("app.services.video_generator.get_openai_client") as mock_get_client:
        with pytest.raises(ValueError, match="Unsupported Sora video size"):
            await generate_video("A cinematic prompt", output_path, duration_seconds=8)

    mock_get_client.assert_not_called()


@pytest.mark.asyncio
async def test_generate_video_surfaces_failure_details(tmp_path, monkeypatch):
    output_path = tmp_path / "video.mp4"

    mock_error = MagicMock(code="content_policy", message="Prompt was blocked")
    mock_video = MagicMock(status="failed", error=mock_error)
    mock_client = MagicMock()
    mock_client.videos.create_and_poll = AsyncMock(return_value=mock_video)

    monkeypatch.setattr(settings, "sora_video_size", "1280x720")
    monkeypatch.setattr(settings, "video_generation_max_attempts", 1)

    with patch("app.services.video_generator.get_openai_client", return_value=mock_client):
        with pytest.raises(
            ValueError,
            match=r"Video generation failed \(status: failed, code: content_policy, message: Prompt was blocked\)",
        ):
            await generate_video("A cinematic prompt", output_path, duration_seconds=8)


@pytest.mark.asyncio
async def test_generate_video_retries_without_reference_image_when_reference_attempt_fails(
    tmp_path,
    monkeypatch,
):
    output_path = tmp_path / "video.mp4"
    reference_image_path = tmp_path / "reference.png"
    reference_image_path.write_bytes(b"reference-image")

    failed_error = MagicMock(code="reference_failed", message="Reference image could not be used")
    failed_video = MagicMock(status="failed", error=failed_error)
    completed_video = MagicMock(status="completed", id="video-456")
    mock_content = AsyncMock()
    mock_content.write_to_file = AsyncMock()

    mock_client = MagicMock()
    mock_client.videos.create_and_poll = AsyncMock(side_effect=[failed_video, completed_video])
    mock_client.videos.download_content = AsyncMock(return_value=mock_content)

    monkeypatch.setattr(settings, "sora_video_size", "1280x720")
    monkeypatch.setattr(settings, "video_generation_max_attempts", 1)

    with patch("app.services.video_generator.get_openai_client", return_value=mock_client):
        await generate_video(
            "A cinematic prompt",
            output_path,
            reference_image_path=reference_image_path,
            duration_seconds=8,
        )

    first_call = mock_client.videos.create_and_poll.await_args_list[0]
    second_call = mock_client.videos.create_and_poll.await_args_list[1]

    assert first_call.kwargs["prompt"] == "Continue from the reference image. A cinematic prompt"
    assert "input_reference" in first_call.kwargs
    assert second_call.kwargs["prompt"] == "A cinematic prompt"
    assert "input_reference" not in second_call.kwargs


@pytest.mark.asyncio
async def test_generate_video_retries_failed_job_after_delay(tmp_path, monkeypatch):
    output_path = tmp_path / "video.mp4"

    failed_error = MagicMock(code="server_error", message="Transient backend failure")
    failed_video = MagicMock(status="failed", error=failed_error)
    completed_video = MagicMock(status="completed", id="video-789")
    mock_content = AsyncMock()
    mock_content.write_to_file = AsyncMock()

    mock_client = MagicMock()
    mock_client.videos.create_and_poll = AsyncMock(side_effect=[failed_video, completed_video])
    mock_client.videos.download_content = AsyncMock(return_value=mock_content)

    monkeypatch.setattr(settings, "sora_video_size", "1280x720")
    monkeypatch.setattr(settings, "video_generation_max_attempts", 2)
    monkeypatch.setattr(settings, "video_generation_retry_delay_seconds", 3)

    with patch("app.services.video_generator.get_openai_client", return_value=mock_client):
        with patch("app.services.video_generator.asyncio.sleep", new=AsyncMock()) as sleep_mock:
            await generate_video("A cinematic prompt", output_path, duration_seconds=8)

    assert mock_client.videos.create_and_poll.await_count == 2
    sleep_mock.assert_awaited_once_with(3)


@pytest.mark.asyncio
async def test_generate_video_skips_reference_retry_for_moderation_blocked(
    tmp_path,
    monkeypatch,
):
    output_path = tmp_path / "video.mp4"
    reference_image_path = tmp_path / "reference.png"
    reference_image_path.write_bytes(b"reference-image")

    blocked_error = MagicMock(
        code="moderation_blocked",
        message="The request is blocked by our moderation system when checking inputs.",
    )
    blocked_video = MagicMock(status="failed", error=blocked_error)
    completed_video = MagicMock(status="completed", id="video-999")
    mock_content = AsyncMock()
    mock_content.write_to_file = AsyncMock()

    mock_client = MagicMock()
    mock_client.videos.create_and_poll = AsyncMock(side_effect=[blocked_video, completed_video])
    mock_client.videos.download_content = AsyncMock(return_value=mock_content)

    monkeypatch.setattr(settings, "sora_video_size", "1280x720")
    monkeypatch.setattr(settings, "video_generation_max_attempts", 2)
    monkeypatch.setattr(settings, "video_generation_retry_delay_seconds", 45)

    with patch("app.services.video_generator.get_openai_client", return_value=mock_client):
        with patch("app.services.video_generator.asyncio.sleep", new=AsyncMock()) as sleep_mock:
            await generate_video(
                "A cinematic prompt",
                output_path,
                reference_image_path=reference_image_path,
                duration_seconds=8,
            )

    assert mock_client.videos.create_and_poll.await_count == 2
    sleep_mock.assert_not_awaited()

    first_call = mock_client.videos.create_and_poll.await_args_list[0]
    second_call = mock_client.videos.create_and_poll.await_args_list[1]
    assert "input_reference" in first_call.kwargs
    assert "input_reference" not in second_call.kwargs