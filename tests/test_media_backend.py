import subprocess
from unittest.mock import Mock

import pytest

from app.config import settings
from app.services import media_backend


def test_stitch_videos_dispatches_to_ffmpeg(monkeypatch, tmp_path):
    video_paths = [tmp_path / "scene.mp4"]
    audio_paths = [tmp_path / "scene.wav"]
    output_path = tmp_path / "final.mp4"

    ffmpeg_mock = Mock(return_value=output_path)
    gstreamer_mock = Mock(return_value=output_path)

    monkeypatch.setattr(settings, "media_backend", "ffmpeg")
    monkeypatch.setattr(media_backend, "_stitch_with_ffmpeg", ffmpeg_mock)
    monkeypatch.setattr(media_backend, "_stitch_with_gstreamer", gstreamer_mock)

    result = media_backend.stitch_videos(video_paths, audio_paths, output_path)

    assert result == output_path
    ffmpeg_mock.assert_called_once_with(video_paths, audio_paths, output_path)
    gstreamer_mock.assert_not_called()


def test_stitch_videos_dispatches_to_gstreamer(monkeypatch, tmp_path):
    video_paths = [tmp_path / "scene.mp4"]
    audio_paths = [tmp_path / "scene.wav"]
    output_path = tmp_path / "final.mp4"

    ffmpeg_mock = Mock(return_value=output_path)
    gstreamer_mock = Mock(return_value=output_path)

    monkeypatch.setattr(settings, "media_backend", "gstreamer")
    monkeypatch.setattr(media_backend, "_stitch_with_ffmpeg", ffmpeg_mock)
    monkeypatch.setattr(media_backend, "_stitch_with_gstreamer", gstreamer_mock)

    result = media_backend.stitch_videos(video_paths, audio_paths, output_path)

    assert result == output_path
    gstreamer_mock.assert_called_once_with(video_paths, audio_paths, output_path)
    ffmpeg_mock.assert_not_called()


def test_extract_last_frame_with_gstreamer_uses_sampled_frames(monkeypatch, tmp_path):
    video_path = tmp_path / "scene.mp4"
    output_path = tmp_path / "last_frame.png"
    video_path.write_bytes(b"video")

    recorded_commands: list[list[str]] = []

    def fake_run_command(command: list[str], *, tool_name: str):
        recorded_commands.append(command)
        frame_dir = output_path.parent / f"_{output_path.stem}_frames"
        frame_dir.mkdir(parents=True, exist_ok=True)
        (frame_dir / "frame_00001.png").write_bytes(b"old")
        (frame_dir / "frame_00002.png").write_bytes(b"new")

    monkeypatch.setattr(settings, "media_backend", "gstreamer")
    monkeypatch.setattr(settings, "gstreamer_frame_sample_fps", 2)
    monkeypatch.setattr(media_backend, "_ensure_command_available", Mock())
    monkeypatch.setattr(media_backend, "_run_command", fake_run_command)

    result = media_backend.extract_last_frame(
        video_path,
        output_path,
        effective_duration_seconds=3.0,
    )

    assert result == output_path
    assert output_path.read_bytes() == b"new"
    assert recorded_commands
    command = recorded_commands[0]
    assert "identity" in command
    assert "eos-after=6" in command
    assert any(part.startswith("location=") and "frame_%05d.png" in part for part in command)


def test_run_command_wraps_called_process_error(monkeypatch):
    def fake_subprocess_run(*args, **kwargs):
        raise subprocess.CalledProcessError(
            1,
            args[0],
            output="",
            stderr="boom",
        )

    monkeypatch.setattr(media_backend.subprocess, "run", fake_subprocess_run)

    with pytest.raises(RuntimeError, match="gstreamer concat failed: boom"):
        media_backend._run_command(["gst-launch-1.0"], tool_name="gstreamer concat")


def test_invalid_media_backend_raises(monkeypatch):
    monkeypatch.setattr(settings, "media_backend", "invalid")

    with pytest.raises(ValueError, match="Unsupported media backend"):
        media_backend.get_media_backend()