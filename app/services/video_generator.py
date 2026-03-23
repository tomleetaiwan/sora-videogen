import asyncio
import inspect
import logging
from pathlib import Path

from app.config import settings
from app.services.media_backend import extract_last_frame
from app.services.openai_client import get_openai_client
from app.video_timing import get_max_scene_duration_seconds, resolve_video_size

logger = logging.getLogger(__name__)
NON_RETRYABLE_VIDEO_ERROR_CODES = {
    "content_policy",
    "invalid_value",
    "invalid_prompt",
    "moderation_blocked",
}


async def _maybe_await(result):
    # 視 client 版本而定，SDK helper 可能回傳 None 或 awaitable。
    if inspect.isawaitable(result):
        return await result
    return result


def _format_video_failure(video) -> str:
    details = [f"status: {video.status}"]

    error = getattr(video, "error", None)
    if error is not None:
        error_code = getattr(error, "code", None)
        error_message = getattr(error, "message", None)
        if error_code:
            details.append(f"code: {error_code}")
        if error_message:
            details.append(f"message: {error_message}")

    return ", ".join(details)


def _should_retry_video_failure(video) -> bool:
    error = getattr(video, "error", None)
    error_code = getattr(error, "code", None)
    return error_code not in NON_RETRYABLE_VIDEO_ERROR_CODES


async def _request_video_generation(
    client,
    request_kwargs: dict,
    *,
    reference_image_path: Path | None,
):
    if reference_image_path is None:
        return await client.videos.create_and_poll(**request_kwargs)

    with open(reference_image_path, "rb") as reference_file:
        return await client.videos.create_and_poll(
            **request_kwargs,
            input_reference=reference_file,
        )


async def _create_video_with_retries(
    client,
    request_kwargs: dict,
    *,
    reference_image_path: Path | None,
):
    max_attempts = max(1, settings.video_generation_max_attempts)
    retry_delay_seconds = max(0, settings.video_generation_retry_delay_seconds)

    for attempt_number in range(1, max_attempts + 1):
        try:
            video = await _request_video_generation(
                client,
                request_kwargs,
                reference_image_path=reference_image_path,
            )
        except Exception:
            if attempt_number >= max_attempts:
                raise

            logger.warning(
                "Video generation request failed on attempt %d/%d. Retrying in %d seconds.",
                attempt_number,
                max_attempts,
                retry_delay_seconds,
                exc_info=True,
            )
            await asyncio.sleep(retry_delay_seconds)
            continue

        if video.status == "completed":
            return video

        if attempt_number < max_attempts and _should_retry_video_failure(video):
            logger.warning(
                "Video generation returned a failed job on attempt %d/%d (%s). Retrying in %d seconds.",
                attempt_number,
                max_attempts,
                _format_video_failure(video),
                retry_delay_seconds,
            )
            await asyncio.sleep(retry_delay_seconds)
            continue

        return video


async def generate_video(
    prompt: str,
    output_path: Path,
    *,
    reference_image_path: Path | None = None,
    duration_seconds: int | None = None,
) -> Path:
    """使用 OpenAI Sora 2 生成影片。

    參數：
        prompt：影片生成提示詞。
        output_path：生成影片的儲存位置。
        reference_image_path：可選的前一段影片最後影格，用於維持畫面連續性。
        duration_seconds：影片時長；預設為 Sora 支援的最大片段時長。

    回傳生成後的影片檔案路徑。
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    video_size = resolve_video_size()
    client = get_openai_client()

    base_request_kwargs: dict = {
        "model": settings.sora_model,
        "size": video_size,
        "seconds": duration_seconds
        if duration_seconds is not None
        else get_max_scene_duration_seconds(),
    }

    generation_attempts: list[dict[str, object]] = []
    if reference_image_path and reference_image_path.exists():
        generation_attempts.append(
            {
                "prompt": f"Continue from the reference image. {prompt}",
                "reference_image_path": reference_image_path,
            }
        )
    generation_attempts.append({"prompt": prompt, "reference_image_path": None})

    video = None
    for attempt_index, attempt in enumerate(generation_attempts):
        request_kwargs = {**base_request_kwargs, "prompt": attempt["prompt"]}
        attempt_reference_image_path = attempt["reference_image_path"]

        video = await _create_video_with_retries(
            client,
            request_kwargs,
            reference_image_path=attempt_reference_image_path,
        )

        if video.status == "completed":
            break

        if attempt_reference_image_path is not None and attempt_index < len(generation_attempts) - 1:
            logger.warning(
                "Reference-guided video generation failed (%s). Retrying without a reference image.",
                _format_video_failure(video),
            )
            continue

        raise ValueError(f"Video generation failed ({_format_video_failure(video)})")

    content = await client.videos.download_content(video.id, variant="video")
    await _maybe_await(content.write_to_file(str(output_path)))

    logger.info("Generated video: %s", output_path)
    return output_path
