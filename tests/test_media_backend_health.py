import subprocess

import pytest

from app.config import settings
from app.services import media_backend_health
from app.services.media_backend_health import evaluate_startup_media_backend_status


@pytest.mark.asyncio
async def test_media_backend_startup_check_is_disabled_for_ffmpeg(monkeypatch):
    monkeypatch.setattr(settings, "media_backend", "ffmpeg")

    status = await evaluate_startup_media_backend_status()

    assert status.enabled is False
    assert status.ready is True
    assert status.failed_checks == []


@pytest.mark.asyncio
async def test_media_backend_startup_check_reports_missing_commands(monkeypatch):
    monkeypatch.setattr(settings, "media_backend", "gstreamer")
    monkeypatch.setattr(
        "app.services.media_backend_health._command_exists",
        lambda command_name: False,
    )

    status = await evaluate_startup_media_backend_status()

    assert status.enabled is True
    assert status.ready is False
    assert status.warning_message is not None
    assert any(check.component_name == settings.gstreamer_launch_binary for check in status.failed_checks)
    assert any(check.component_name == settings.gstreamer_inspect_binary for check in status.failed_checks)


@pytest.mark.asyncio
async def test_media_backend_startup_check_reports_missing_plugin(monkeypatch):
    monkeypatch.setattr(settings, "media_backend", "gstreamer")
    monkeypatch.setattr(
        "app.services.media_backend_health._command_exists",
        lambda command_name: True,
    )

    def fake_inspect(element_name: str):
        if element_name == "mp4mux":
            return False, "No such element or plugin 'mp4mux'"
        return True, "GStreamer 插件可用。"

    monkeypatch.setattr(
        "app.services.media_backend_health._inspect_gstreamer_aac_encoder",
        lambda: (True, "GStreamer AAC encoder 可用：voaacenc。"),
    )

    monkeypatch.setattr(
        "app.services.media_backend_health._inspect_gstreamer_element",
        fake_inspect,
    )

    status = await evaluate_startup_media_backend_status()

    assert status.enabled is True
    assert status.ready is False
    assert any(check.component_name == "mp4mux" for check in status.failed_checks)


@pytest.mark.asyncio
async def test_media_backend_startup_check_accepts_aac_encoder_fallback(monkeypatch):
    monkeypatch.setattr(settings, "media_backend", "gstreamer")
    monkeypatch.setattr(
        "app.services.media_backend_health._command_exists",
        lambda command_name: True,
    )
    monkeypatch.setattr(
        "app.services.media_backend_health._inspect_gstreamer_element",
        lambda element_name: (True, "GStreamer 插件可用。"),
    )
    monkeypatch.setattr(
        "app.services.media_backend_health._inspect_gstreamer_aac_encoder",
        lambda: (True, "GStreamer AAC encoder 可用：voaacenc。"),
    )

    status = await evaluate_startup_media_backend_status()

    assert status.enabled is True
    assert status.ready is True
    assert status.failed_checks == []


def test_inspect_gstreamer_aac_encoder_reports_all_candidates_when_missing(monkeypatch):
    monkeypatch.setattr(media_backend_health, "inspect_gstreamer_element", lambda element_name: False)

    success, detail = media_backend_health._inspect_gstreamer_aac_encoder()

    assert success is False
    assert "avenc_aac" in detail
    assert "voaacenc" in detail


def test_media_backend_subprocess_error_summary_uses_first_line():
    from app.services.media_backend_health import _summarize_subprocess_error

    error = subprocess.CalledProcessError(
        1,
        ["gst-inspect-1.0", "avenc_aac"],
        stderr="first line\nsecond line",
        output="",
    )

    assert _summarize_subprocess_error(error) == "first line"


def test_media_backend_subprocess_error_summary_uses_stdout_when_stderr_empty():
    from app.services.media_backend_health import _summarize_subprocess_error

    error = subprocess.CalledProcessError(
        1,
        ["gst-inspect-1.0", "avenc_aac"],
        stderr="",
        output="stdout first line\nstdout second line",
    )

    assert _summarize_subprocess_error(error) == "stdout first line"


def test_media_backend_subprocess_error_summary_handles_empty_output_and_stderr():
    from app.services.media_backend_health import _summarize_subprocess_error

    error = subprocess.CalledProcessError(
        1,
        ["gst-inspect-1.0", "avenc_aac"],
        stderr="",
        output="",
    )

    # When both stderr and stdout are empty, fall back to the string
    # representation of the error to ensure a non-empty summary.
    assert _summarize_subprocess_error(error) == error.__class__.__name__


def test_inspect_gstreamer_element_uses_resolved_executable_path(monkeypatch):
    recorded_command = None

    def fake_subprocess_run(command, **kwargs):
        nonlocal recorded_command
        recorded_command = command

    monkeypatch.setattr(
        media_backend_health,
        "resolve_command_path",
        lambda command_name: r"C:\gstreamer\bin\gst-inspect-1.0.exe",
    )
    monkeypatch.setattr(media_backend_health.subprocess, "run", fake_subprocess_run)

    success, detail = media_backend_health._inspect_gstreamer_element("identity")

    assert success is True
    assert detail == "GStreamer 插件可用。"
    assert recorded_command == [r"C:\gstreamer\bin\gst-inspect-1.0.exe", "identity"]
