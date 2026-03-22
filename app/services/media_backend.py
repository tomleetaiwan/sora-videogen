import logging
import math
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

import ffmpeg

from app.config import settings

logger = logging.getLogger(__name__)

SUPPORTED_MEDIA_BACKENDS = {"ffmpeg", "gstreamer"}
GSTREAMER_REQUIRED_ELEMENTS = (
    "filesrc",
    "filesink",
    "qtdemux",
    "h264parse",
    "wavparse",
    "audioconvert",
    "audioresample",
    "aacparse",
    "mp4mux",
    "concat",
    "queue",
    "uridecodebin",
    "videoconvert",
    "videorate",
    "pngenc",
    "multifilesink",
    "identity",
)
GSTREAMER_AAC_ENCODER_CANDIDATES = (
    "avenc_aac",
    "fdkaacenc",
    "voaacenc",
    "faac",
)


def get_media_backend() -> str:
    backend = settings.media_backend.strip().lower()
    if backend not in SUPPORTED_MEDIA_BACKENDS:
        raise ValueError(
            "Unsupported media backend: "
            f"{settings.media_backend}. Supported backends: ffmpeg, gstreamer"
        )
    return backend


def resolve_command_path(command_name: str) -> str | None:
    return shutil.which(command_name)


def _format_gstreamer_path(path: Path) -> str:
    return path.resolve().as_posix()


def get_available_gstreamer_aac_encoder() -> str:
    for encoder_name in GSTREAMER_AAC_ENCODER_CANDIDATES:
        if inspect_gstreamer_element(encoder_name):
            return encoder_name

    candidate_list = ", ".join(GSTREAMER_AAC_ENCODER_CANDIDATES)
    raise RuntimeError(
        "No supported GStreamer AAC encoder is available. "
        f"Tried: {candidate_list}"
    )


def inspect_gstreamer_element(element_name: str) -> bool:
    resolved_command = resolve_command_path(settings.gstreamer_inspect_binary)
    if resolved_command is None:
        return False

    try:
        subprocess.run(
            [resolved_command, element_name],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False

    return True


def stitch_videos(
    video_paths: list[Path],
    audio_paths: list[Path],
    output_path: Path,
) -> Path:
    """將影片與對應音軌串接成單一影片。

    每支影片都會配上相對應的旁白音軌，並依目前設定的媒體後端
    依序輸出為單一連續影片檔。

    參數：
        video_paths：依順序排列的影片分段檔案清單。
        audio_paths：依相同順序排列的旁白音訊檔案清單。
        output_path：最終串接影片的儲存位置。

    回傳最終影片的路徑。
    """
    backend = get_media_backend()
    if backend == "gstreamer":
        return _stitch_with_gstreamer(video_paths, audio_paths, output_path)
    return _stitch_with_ffmpeg(video_paths, audio_paths, output_path)


def extract_last_frame(
    video_path: Path,
    output_path: Path,
    *,
    effective_duration_seconds: float | None = None,
) -> Path:
    """擷取影片最後可見的影格。"""
    backend = get_media_backend()
    if backend == "gstreamer":
        return _extract_last_frame_with_gstreamer(
            video_path,
            output_path,
            effective_duration_seconds=effective_duration_seconds,
        )
    return _extract_last_frame_with_ffmpeg(
        video_path,
        output_path,
        effective_duration_seconds=effective_duration_seconds,
    )


def _validate_stitch_inputs(
    video_paths: list[Path],
    audio_paths: list[Path],
    output_path: Path,
) -> None:
    if len(video_paths) != len(audio_paths):
        raise ValueError(
            f"Mismatched counts: {len(video_paths)} videos, {len(audio_paths)} audio files"
        )

    if not video_paths:
        raise ValueError("No videos to stitch")

    output_path.parent.mkdir(parents=True, exist_ok=True)


def _stitch_with_ffmpeg(
    video_paths: list[Path],
    audio_paths: list[Path],
    output_path: Path,
) -> Path:
    _validate_stitch_inputs(video_paths, audio_paths, output_path)

    segments = []
    for i, (vpath, apath) in enumerate(zip(video_paths, audio_paths)):
        video_input = ffmpeg.input(str(vpath))
        audio_input = ffmpeg.input(str(apath))
        segment = ffmpeg.output(
            video_input.video,
            audio_input.audio,
            str(output_path.parent / f"_segment_{i}.mp4"),
            vcodec="copy",
            acodec="aac",
            shortest=None,
        ).overwrite_output()
        segment.run(quiet=True)
        segments.append(output_path.parent / f"_segment_{i}.mp4")

    concat_list_path = output_path.parent / "_concat_list.txt"
    with open(concat_list_path, "w", encoding="utf-8") as file:
        for seg in segments:
            file.write(f"file '{seg.resolve()}'\n")

    (
        ffmpeg.input(str(concat_list_path), format="concat", safe=0)
        .output(str(output_path), c="copy")
        .overwrite_output()
        .run(quiet=True)
    )

    for seg in segments:
        seg.unlink(missing_ok=True)
    concat_list_path.unlink(missing_ok=True)

    logger.info("Stitched %d segments into %s via ffmpeg", len(segments), output_path)
    return output_path


def _stitch_with_gstreamer(
    video_paths: list[Path],
    audio_paths: list[Path],
    output_path: Path,
) -> Path:
    _validate_stitch_inputs(video_paths, audio_paths, output_path)
    _ensure_command_available(settings.gstreamer_launch_binary)
    aac_encoder = get_available_gstreamer_aac_encoder()

    segments: list[Path] = []
    try:
        for i, (video_path, audio_path) in enumerate(zip(video_paths, audio_paths)):
            segment_path = output_path.parent / f"_segment_{i}.mp4"
            _run_command(
                [
                    settings.gstreamer_launch_binary,
                    "-q",
                    "-e",
                    "filesrc",
                    f"location={_format_gstreamer_path(video_path)}",
                    "!",
                    "qtdemux",
                    "name=demux",
                    "demux.video_0",
                    "!",
                    "queue",
                    "!",
                    "h264parse",
                    "!",
                    "mux.",
                    "filesrc",
                    f"location={_format_gstreamer_path(audio_path)}",
                    "!",
                    "wavparse",
                    "!",
                    "audioconvert",
                    "!",
                    "audioresample",
                    "!",
                    aac_encoder,
                    "!",
                    "aacparse",
                    "!",
                    "mux.",
                    "mp4mux",
                    "name=mux",
                    "faststart=true",
                    "!",
                    "filesink",
                    f"location={_format_gstreamer_path(segment_path)}",
                ],
                tool_name="gstreamer segment mux",
            )
            segments.append(segment_path)

        concat_command = [
            settings.gstreamer_launch_binary,
            "-q",
            "-e",
            "concat",
            "name=vcat",
            "!",
            "queue",
            "!",
            "h264parse",
            "!",
            "mux.",
            "concat",
            "name=acat",
            "!",
            "queue",
            "!",
            "aacparse",
            "!",
            "mux.",
            "mp4mux",
            "name=mux",
            "faststart=true",
            "!",
            "filesink",
            f"location={_format_gstreamer_path(output_path)}",
        ]

        for index, segment_path in enumerate(segments):
            demux_name = f"demux{index}"
            concat_command.extend(
                [
                    "filesrc",
                    f"location={_format_gstreamer_path(segment_path)}",
                    "!",
                    "qtdemux",
                    f"name={demux_name}",
                    f"{demux_name}.video_0",
                    "!",
                    "queue",
                    "!",
                    "h264parse",
                    "!",
                    "vcat.",
                    f"{demux_name}.audio_0",
                    "!",
                    "queue",
                    "!",
                    "aacparse",
                    "!",
                    "acat.",
                ]
            )

        _run_command(concat_command, tool_name="gstreamer concat")
    finally:
        for segment_path in segments:
            segment_path.unlink(missing_ok=True)

    logger.info("Stitched %d segments into %s via gstreamer", len(segments), output_path)
    return output_path


def _extract_last_frame_with_ffmpeg(
    video_path: Path,
    output_path: Path,
    *,
    effective_duration_seconds: float | None = None,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    probe = ffmpeg.probe(str(video_path))
    duration = float(probe["format"]["duration"])
    if effective_duration_seconds is not None:
        duration = min(duration, effective_duration_seconds)

    (
        ffmpeg.input(str(video_path), ss=max(0, duration - 0.1))
        .output(str(output_path), vframes=1, format="image2")
        .overwrite_output()
        .run(quiet=True)
    )

    logger.info("Extracted last frame via ffmpeg: %s", output_path)
    return output_path


def _extract_last_frame_with_gstreamer(
    video_path: Path,
    output_path: Path,
    *,
    effective_duration_seconds: float | None = None,
) -> Path:
    _ensure_command_available(settings.gstreamer_launch_binary)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    temp_dir = output_path.parent / f"_{output_path.stem}_frames"
    temp_dir.mkdir(parents=True, exist_ok=True)
    frame_pattern = temp_dir / "frame_%05d.png"
    fps_fraction = Fraction(settings.gstreamer_frame_sample_fps).limit_denominator()

    command = [
        settings.gstreamer_launch_binary,
        "-q",
        "-e",
        "uridecodebin",
        f"uri={video_path.resolve().as_uri()}",
        "!",
        "videoconvert",
        "!",
        "videorate",
        "!",
        f"video/x-raw,framerate={fps_fraction.numerator}/{fps_fraction.denominator}",
    ]

    if effective_duration_seconds is not None:
        frame_count = max(
            1,
            math.ceil(effective_duration_seconds * settings.gstreamer_frame_sample_fps),
        )
        command.extend(["!", "identity", f"eos-after={frame_count}"])

    command.extend(
        [
            "!",
            "pngenc",
            "!",
            "multifilesink",
            f"location={_format_gstreamer_path(frame_pattern)}",
        ]
    )

    try:
        _run_command(command, tool_name="gstreamer frame extraction")
        frame_files = sorted(temp_dir.glob("frame_*.png"))
        if not frame_files:
            raise ValueError("GStreamer did not produce any extracted frames")

        frame_files[-1].replace(output_path)
    finally:
        for frame_file in temp_dir.glob("frame_*.png"):
            frame_file.unlink(missing_ok=True)
        temp_dir.rmdir()

    logger.info("Extracted last frame via gstreamer: %s", output_path)
    return output_path


def _ensure_command_available(command_name: str) -> None:
    if resolve_command_path(command_name):
        return
    raise RuntimeError(f"Required command not found in PATH: {command_name}")


def _run_command(command: list[str], *, tool_name: str) -> None:
    resolved_executable = resolve_command_path(command[0]) if command else None
    if resolved_executable is None:
        raise RuntimeError(f"Required command not found in PATH: {command[0]}")

    try:
        subprocess.run(
            [resolved_executable, *command[1:]],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        detail = stderr or stdout or "unknown error"
        cmd_name = command[0] if command else tool_name
        cmd_str = " ".join(command) if command else cmd_name
        raise RuntimeError(
            f"{tool_name} failed while running '{cmd_name}': {detail}. Command: {cmd_str}"
        ) from exc
