import math

from app.config import settings


def get_supported_video_durations_seconds() -> tuple[int, ...]:
    supported_durations = tuple(sorted(settings.sora_supported_durations_seconds))
    allowed_durations = tuple(
        duration
        for duration in supported_durations
        if duration <= settings.scene_duration_seconds
    )
    if allowed_durations:
        return allowed_durations

    minimum_supported_duration = supported_durations[0]
    raise ValueError(
        "scene_duration_seconds must be at least "
        f"{minimum_supported_duration} for the configured Sora durations"
    )


def get_max_scene_duration_seconds() -> int:
    return get_supported_video_durations_seconds()[-1]


def get_supported_video_sizes() -> tuple[str, ...]:
    return tuple(settings.sora_supported_sizes)


def resolve_video_size(video_size: str | None = None) -> str:
    candidate_size = video_size or settings.sora_video_size
    if candidate_size in get_supported_video_sizes():
        return candidate_size

    supported_sizes = ", ".join(get_supported_video_sizes())
    raise ValueError(
        f"Unsupported Sora video size: {candidate_size}. Supported sizes: {supported_sizes}"
    )


def resolve_video_duration_seconds(audio_duration_seconds: float | None) -> int | None:
    if audio_duration_seconds is None:
        return None

    if audio_duration_seconds <= 0:
        return get_supported_video_durations_seconds()[0]

    for duration in get_supported_video_durations_seconds():
        if audio_duration_seconds <= duration:
            return duration

    raise ValueError(
        "Audio duration exceeds the maximum supported Sora clip length: "
        f"{audio_duration_seconds:.2f}s > {get_max_scene_duration_seconds()}s"
    )


def estimate_max_narration_chars(chars_per_second: int) -> int:
    return math.floor(get_max_scene_duration_seconds() * chars_per_second)