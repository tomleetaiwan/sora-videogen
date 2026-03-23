import logging
import math
import shutil
import subprocess
import wave
from fractions import Fraction
from pathlib import Path

import ffmpeg

from app.config import settings
from app.video_timing import resolve_video_duration_seconds

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
GSTREAMER_AAC_ENCODER_FRAME_OFFSETS = {
    "avenc_aac": -1024,
    "voaacenc": 512,
}


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


def _get_first_available_gstreamer_element(
    candidates: tuple[str, ...],
    *,
    element_kind: str,
) -> str:
    for element_name in candidates:
        if inspect_gstreamer_element(element_name):
            return element_name

    candidate_list = ", ".join(candidates)
    raise RuntimeError(
        f"No supported GStreamer {element_kind} is available. Tried: {candidate_list}"
    )


def get_available_gstreamer_aac_encoder() -> str:
    return _get_first_available_gstreamer_element(
        GSTREAMER_AAC_ENCODER_CANDIDATES,
        element_kind="AAC encoder",
    )


def get_gstreamer_aac_encoder_frame_offset(encoder_name: str) -> int:
    return GSTREAMER_AAC_ENCODER_FRAME_OFFSETS.get(encoder_name, 0)


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
    *,
    scene_durations_seconds: list[float | None] | None = None,
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
    gstreamer_aac_encoder = get_available_gstreamer_aac_encoder() if backend == "gstreamer" else None
    aligned_audio_paths, temp_audio_paths = _prepare_aligned_audio_paths(
        audio_paths,
        output_dir=output_path.parent,
        scene_durations_seconds=scene_durations_seconds,
        frame_count_offset=(
            get_gstreamer_aac_encoder_frame_offset(gstreamer_aac_encoder)
            if gstreamer_aac_encoder is not None
            else 0
        ),
    )

    try:
        if backend == "gstreamer":
            return _stitch_with_gstreamer(
                video_paths,
                aligned_audio_paths,
                output_path,
                aac_encoder=gstreamer_aac_encoder,
            )
        return _stitch_with_ffmpeg(video_paths, aligned_audio_paths, output_path)
    finally:
        _cleanup_paths(temp_audio_paths)


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


def _prepare_aligned_audio_paths(
    audio_paths: list[Path],
    *,
    output_dir: Path,
    scene_durations_seconds: list[float | None] | None,
    frame_count_offset: int = 0,
) -> tuple[list[Path], list[Path]]:
    if scene_durations_seconds is not None and len(scene_durations_seconds) != len(audio_paths):
        raise ValueError(
            "Mismatched counts: "
            f"{len(audio_paths)} audio files, {len(scene_durations_seconds)} scene durations"
        )

    aligned_audio_paths: list[Path] = []
    temp_audio_paths: list[Path] = []

    for index, audio_path in enumerate(audio_paths):
        audio_duration_seconds = _get_wav_duration_seconds(audio_path)
        target_duration_seconds = _resolve_target_scene_duration_seconds(
            audio_duration_seconds,
            scene_durations_seconds[index] if scene_durations_seconds is not None else None,
        )

        if target_duration_seconds is None or math.isclose(
            audio_duration_seconds,
            target_duration_seconds,
            abs_tol=0.01,
        ):
            aligned_audio_paths.append(audio_path)
            continue

        aligned_audio_path = output_dir / f"_aligned_audio_{index}.wav"
        _write_aligned_wav(
            source_path=audio_path,
            output_path=aligned_audio_path,
            target_duration_seconds=target_duration_seconds,
            frame_count_offset=frame_count_offset,
        )
        aligned_audio_paths.append(aligned_audio_path)
        temp_audio_paths.append(aligned_audio_path)

    return aligned_audio_paths, temp_audio_paths


def _resolve_target_scene_duration_seconds(
    audio_duration_seconds: float,
    scene_duration_seconds: float | None,
) -> float | None:
    if scene_duration_seconds is not None:
        return scene_duration_seconds

    return resolve_video_duration_seconds(audio_duration_seconds)


def _get_wav_duration_seconds(audio_path: Path) -> float:
    with wave.open(str(audio_path), "rb") as wav_file:
        frame_count = wav_file.getnframes()
        frame_rate = wav_file.getframerate()

    if frame_rate <= 0:
        raise ValueError(f"Invalid WAV frame rate for audio file: {audio_path}")

    return frame_count / float(frame_rate)


def _write_aligned_wav(
    *,
    source_path: Path,
    output_path: Path,
    target_duration_seconds: float,
    frame_count_offset: int = 0,
) -> None:
    with wave.open(str(source_path), "rb") as source_wav:
        params = source_wav.getparams()
        audio_frames = source_wav.readframes(source_wav.getnframes())

    frame_rate = params.framerate
    if frame_rate <= 0:
        raise ValueError(f"Invalid WAV frame rate for audio file: {source_path}")

    target_frame_count = max(0, round(target_duration_seconds * frame_rate) + frame_count_offset)
    frame_width = params.sampwidth * params.nchannels
    target_byte_count = target_frame_count * frame_width

    if len(audio_frames) < target_byte_count:
        audio_frames += b"\x00" * (target_byte_count - len(audio_frames))
    else:
        audio_frames = audio_frames[:target_byte_count]

    with wave.open(str(output_path), "wb") as output_wav:
        output_wav.setparams(params)
        output_wav.writeframes(audio_frames)


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
    *,
    aac_encoder: str | None = None,
) -> Path:
    _validate_stitch_inputs(video_paths, audio_paths, output_path)
    _ensure_command_available(settings.gstreamer_launch_binary)
    segments: list[Path] = []
    try:
        segments = _create_gstreamer_muxed_segments(
            video_paths,
            audio_paths,
            output_path.parent,
            aac_encoder=aac_encoder,
        )
        try:
            _concat_segments_with_gstreamer(segments, output_path)
        except RuntimeError:
            if resolve_command_path("ffmpeg") is None:
                raise

            logger.warning(
                "GStreamer concat failed for %s. Falling back to ffmpeg concat.",
                output_path,
                exc_info=True,
            )
            output_path.unlink(missing_ok=True)
            _concat_segments_with_ffmpeg(segments, output_path)
            logger.info("Stitched %d segments into %s via ffmpeg fallback", len(segments), output_path)
            return output_path
    finally:
        _cleanup_paths(segments)

    logger.info("Stitched %d segments into %s via gstreamer", len(segments), output_path)
    return output_path


def _create_gstreamer_muxed_segments(
    video_paths: list[Path],
    audio_paths: list[Path],
    output_dir: Path,
    *,
    aac_encoder: str | None = None,
) -> list[Path]:
    aac_encoder = aac_encoder or get_available_gstreamer_aac_encoder()
    segments: list[Path] = []

    for i, (video_path, audio_path) in enumerate(zip(video_paths, audio_paths)):
        segment_path = output_dir / f"_segment_{i}.mp4"
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

    return segments


def _concat_segments_with_gstreamer(segments: list[Path], output_path: Path) -> None:
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

    _run_command(concat_command, tool_name="gstreamer concat", timeout_seconds=30)


def _concat_segments_with_ffmpeg(segments: list[Path], output_path: Path) -> None:
    concat_list_path = output_path.parent / "_gstreamer_fallback_concat_list.txt"
    with open(concat_list_path, "w", encoding="utf-8") as file:
        for segment_path in segments:
            file.write(f"file '{segment_path.resolve()}'\n")

    try:
        (
            ffmpeg.input(str(concat_list_path), format="concat", safe=0)
            .output(str(output_path), c="copy")
            .overwrite_output()
            .run(quiet=True)
        )
    finally:
        concat_list_path.unlink(missing_ok=True)


def _cleanup_paths(paths: list[Path]) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except PermissionError:
            logger.warning("Could not remove temporary file because it is still locked: %s", path)


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


def _run_command(command: list[str], *, tool_name: str, timeout_seconds: int | None = None) -> None:
    resolved_executable = resolve_command_path(command[0]) if command else None
    if resolved_executable is None:
        raise RuntimeError(f"Required command not found in PATH: {command[0]}")

    try:
        subprocess.run(
            [resolved_executable, *command[1:]],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        cmd_name = command[0] if command else tool_name
        cmd_str = " ".join(command) if command else cmd_name
        raise RuntimeError(
            f"{tool_name} timed out while running '{cmd_name}'. Command: {cmd_str}"
        ) from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        detail = stderr or stdout or "unknown error"
        cmd_name = command[0] if command else tool_name
        cmd_str = " ".join(command) if command else cmd_name
        raise RuntimeError(
            f"{tool_name} failed while running '{cmd_name}': {detail}. Command: {cmd_str}"
        ) from exc
