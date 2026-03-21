import pytest

from app.video_timing import get_max_scene_duration_seconds, resolve_video_duration_seconds


def test_resolve_video_duration_seconds_uses_supported_sora_lengths():
    assert get_max_scene_duration_seconds() == 12
    assert resolve_video_duration_seconds(4.0) == 4
    assert resolve_video_duration_seconds(6.0) == 8
    assert resolve_video_duration_seconds(8.0) == 8
    assert resolve_video_duration_seconds(8.1) == 12
    assert resolve_video_duration_seconds(11.9) == 12


def test_resolve_video_duration_seconds_rejects_audio_longer_than_supported_max():
    with pytest.raises(ValueError, match="maximum supported Sora clip length"):
        resolve_video_duration_seconds(12.1)