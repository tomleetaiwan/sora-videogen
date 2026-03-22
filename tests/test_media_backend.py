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


def test_stitch_with_gstreamer_uses_first_available_aac_encoder(monkeypatch, tmp_path):
    video_paths = [tmp_path / "scene.mp4"]
    audio_paths = [tmp_path / "scene.wav"]
    output_path = tmp_path / "final.mp4"
    recorded_commands: list[list[str]] = []

    monkeypatch.setattr(media_backend, "_ensure_command_available", Mock())
    monkeypatch.setattr(media_backend, "get_available_gstreamer_aac_encoder", lambda: "voaacenc")

    def fake_run_command(command: list[str], *, tool_name: str):
        recorded_commands.append(command)

    monkeypatch.setattr(media_backend, "_run_command", fake_run_command)

    media_backend._stitch_with_gstreamer(video_paths, audio_paths, output_path)

    assert recorded_commands
    assert "voaacenc" in recorded_commands[0]


def test_get_available_gstreamer_aac_encoder_prefers_first_available(monkeypatch):
    availability = {
        "avenc_aac": False,
        "fdkaacenc": False,
        "voaacenc": True,
        "faac": True,
    }

    monkeypatch.setattr(
        media_backend,
        "inspect_gstreamer_element",
        lambda element_name: availability[element_name],
    )

    assert media_backend.get_available_gstreamer_aac_encoder() == "voaacenc"


def test_get_available_gstreamer_aac_encoder_raises_when_none_found(monkeypatch):
    monkeypatch.setattr(media_backend, "inspect_gstreamer_element", lambda element_name: False)

    with pytest.raises(RuntimeError, match="No supported GStreamer AAC encoder is available"):
        media_backend.get_available_gstreamer_aac_encoder()


def test_run_command_wraps_called_process_error(monkeypatch):
    def fake_subprocess_run(*args, **kwargs):
        raise subprocess.CalledProcessError(
            1,
            args[0],
            output="",
            stderr="boom",
        )

    monkeypatch.setattr(media_backend, "resolve_command_path", lambda command_name: command_name)
    monkeypatch.setattr(media_backend.subprocess, "run", fake_subprocess_run)

    with pytest.raises(RuntimeError, match="gstreamer concat failed while running 'gst-launch-1.0': boom"):
        media_backend._run_command(["gst-launch-1.0"], tool_name="gstreamer concat")


def test_run_command_uses_resolved_executable_path(monkeypatch):
    recorded_command = None

    def fake_subprocess_run(command, **kwargs):
        nonlocal recorded_command
        recorded_command = command

    monkeypatch.setattr(
        media_backend,
        "resolve_command_path",
        lambda command_name: r"C:\gstreamer\bin\gst-launch-1.0.exe",
    )
    monkeypatch.setattr(media_backend.subprocess, "run", fake_subprocess_run)

    media_backend._run_command(["gst-launch-1.0", "--version"], tool_name="gstreamer concat")

    assert recorded_command == [r"C:\gstreamer\bin\gst-launch-1.0.exe", "--version"]


def test_format_gstreamer_path_uses_forward_slashes(tmp_path):
    windows_like_path = tmp_path / "nested dir" / "file.wav"

    formatted_path = media_backend._format_gstreamer_path(windows_like_path)

    assert "\\" not in formatted_path
    assert "/" in formatted_path


def test_invalid_media_backend_raises(monkeypatch):
    monkeypatch.setattr(settings, "media_backend", "invalid")

    with pytest.raises(ValueError, match="Unsupported media backend"):
        media_backend.get_media_backend()