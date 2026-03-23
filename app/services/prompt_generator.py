import asyncio
import json
import logging

from app.config import settings
from app.prompt_language import (
    DEFAULT_VIDEO_PROMPT_LANGUAGE,
    infer_video_prompt_language,
    normalize_video_prompt_language,
)
from app.services.openai_client import get_openai_client, prepare_chat_completion_kwargs
from app.video_timing import estimate_max_narration_chars, get_max_scene_duration_seconds

logger = logging.getLogger(__name__)

# 台灣華語旁白以每秒約 3 個字為較自然的語速
CHARS_PER_SECOND = 3
SCENE_DURATION_LIMIT_SECONDS = get_max_scene_duration_seconds()
MAX_NARRATION_CHARS = estimate_max_narration_chars(CHARS_PER_SECOND)
MAX_SCENE_PROMPT_ATTEMPTS = 3
SCENE_PROMPT_RETRY_DELAY_SECONDS = 3
SCENE_PROMPT_INITIAL_MAX_COMPLETION_TOKENS = 4000
SCENE_REWRITE_MAX_COMPLETION_TOKENS = 2000



def _build_video_prompt_instruction(video_prompt_language: str) -> str:
    if video_prompt_language == "en":
        return (
            '2. "video_prompt": An English prompt for the Sora 2 video model describing the '
            "visual scene. Be specific about camera angles, lighting, subjects, and motion. "
            "Keep it cinematic."
        )

    return (
        '2. "video_prompt": A Traditional Chinese (繁體中文) prompt for the Sora 2 video model '
        "describing the visual scene. Be specific about camera angles, lighting, subjects, and "
        "motion. Keep it cinematic."
    )


def _build_scene_generation_system_prompt(video_prompt_language: str) -> str:
    return f"""You are a video scene planner. Given a summary, split it into consecutive scenes
for a narrated video. Each scene is {SCENE_DURATION_LIMIT_SECONDS} seconds long.

For each scene, provide:
1. "narration_text": The narration in Traditional Chinese (繁體中文) with Taiwanese phrasing.
   Must be at most {MAX_NARRATION_CHARS} characters so it can be read
   in {SCENE_DURATION_LIMIT_SECONDS} seconds
   at a natural Taiwanese Mandarin speaking pace.
{_build_video_prompt_instruction(video_prompt_language)}

Return JSON in the shape {{"scenes": [{{"narration_text": "...", "video_prompt": "..."}}]}} only.
Limit to at most {settings.max_scenes_per_project} scenes.
Ensure scenes flow naturally and tell a cohesive story.
"""


def _build_scene_rewrite_system_prompt(video_prompt_language: str) -> str:
    return f"""You rewrite or split a single narrated video scene so that
every resulting scene can be narrated within {SCENE_DURATION_LIMIT_SECONDS} seconds.

Rules:
1. The narration must be in Traditional Chinese (繁體中文) with Taiwanese phrasing.
2. Treat {SCENE_DURATION_LIMIT_SECONDS} seconds as a hard limit for every returned scene.
3. Prefer rewriting into one shorter scene if possible.
4. If one scene cannot fit naturally, split into the fewest consecutive scenes needed.
5. Preserve the original meaning and keep the overall story flow coherent.
6. Each scene must include:
   - "narration_text": concise narration that fits the hard limit.
   - "video_prompt": {_build_video_prompt_instruction(video_prompt_language).split(': ', 1)[1]}
7. Aim for roughly {MAX_NARRATION_CHARS} Chinese characters or fewer per scene, but prioritize
   natural pacing over character counting.
8. Return JSON in the shape {{"scenes": [ ... ]}} only.
"""


def _extract_scenes_payload(data: object) -> list[dict]:
    # 模型可能直接回傳陣列，或回傳 {"scenes": [...]}；兩種格式都接受。
    if isinstance(data, list):
        return [scene for scene in data if isinstance(scene, dict)]

    if isinstance(data, dict):
        scenes = data.get("scenes", [])
        if isinstance(scenes, list):
            return [scene for scene in scenes if isinstance(scene, dict)]

    return []


def _normalize_scene_payloads(
    scenes: list[dict],
    *,
    max_scenes: int,
    strict_scene_count: bool = False,
) -> list[dict]:
    if max_scenes < 1:
        raise ValueError("max_scenes must be at least 1")

    if strict_scene_count and len(scenes) > max_scenes:
        raise ValueError(f"Scene rewrite exceeded the allowed {max_scenes} scene slots")

    normalized: list[dict] = []
    candidate_scenes = scenes if strict_scene_count else scenes[:max_scenes]

    for i, scene in enumerate(candidate_scenes):
        narration = str(scene.get("narration_text", "")).strip()
        video_prompt = str(scene.get("video_prompt", "")).strip()
        if not narration or not video_prompt:
            logger.warning("Skipping scene %d: missing fields", i)
            continue

        normalized.append({
            # 保留完整旁白文字；真正的硬上限會在 TTS 後以實際音訊時長判定。
            "narration_text": narration,
            "video_prompt": video_prompt,
            "duration_estimate": len(narration) / CHARS_PER_SECOND,
        })

    if not normalized:
        raise ValueError("No valid scenes generated")

    return normalized


def _parse_scene_response(
    raw: str,
    *,
    max_scenes: int,
    strict_scene_count: bool = False,
) -> list[dict]:
    data = json.loads(raw)
    scenes = _extract_scenes_payload(data)

    if not scenes:
        raise ValueError("No scenes generated")

    return _normalize_scene_payloads(
        scenes,
        max_scenes=max_scenes,
        strict_scene_count=strict_scene_count,
    )


def _extract_text_from_content_part(part: object) -> str:
    if isinstance(part, str):
        return part

    if isinstance(part, dict):
        text_value = part.get("text")
        if isinstance(text_value, str):
            return text_value
        if isinstance(text_value, dict):
            nested_value = text_value.get("value")
            if isinstance(nested_value, str):
                return nested_value
        return ""

    text_value = getattr(part, "text", None)
    if isinstance(text_value, str):
        return text_value

    nested_value = getattr(text_value, "value", None)
    if isinstance(nested_value, str):
        return nested_value

    return ""


def _extract_message_text(message: object) -> str:
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        text_fragments: list[str] = []
        for part in content:
            text_value = _extract_text_from_content_part(part).strip()
            if text_value:
                text_fragments.append(text_value)
        return "\n".join(text_fragments).strip()

    return ""


def _extract_scene_result(
    response: object,
    *,
    operation_label: str,
) -> tuple[str | None, str, bool, bool]:
    choices = getattr(response, "choices", None) or []
    if not choices:
        return None, f"No {operation_label} choices returned from model", True, False

    choice = choices[0]
    message = getattr(choice, "message", None)
    raw = _extract_message_text(message)
    if raw:
        return raw, "", False, False

    refusal = getattr(message, "refusal", None)
    if isinstance(refusal, str) and refusal.strip():
        return None, f"Model refused during {operation_label}: {refusal.strip()}", False, False

    finish_reason = getattr(choice, "finish_reason", None)
    if finish_reason == "content_filter":
        return None, f"{operation_label.capitalize()} was blocked by the model content filter", False, False

    if finish_reason == "length":
        return None, f"{operation_label.capitalize()} stopped before returning usable JSON", True, True

    return None, f"Empty {operation_label} returned from model", True, False


def _increase_completion_budget(request_kwargs: dict) -> int | None:
    token_key = None
    if "max_completion_tokens" in request_kwargs:
        token_key = "max_completion_tokens"
    elif "max_tokens" in request_kwargs:
        token_key = "max_tokens"

    if token_key is None:
        return None

    current_budget = request_kwargs.get(token_key)
    if not isinstance(current_budget, int) or current_budget <= 0:
        return None

    token_cap = settings.scene_prompt_max_completion_token_cap
    if token_cap <= 0 or current_budget >= token_cap:
        return None

    next_budget = min(token_cap, current_budget * 2)
    request_kwargs[token_key] = next_budget
    return next_budget


async def _request_scene_payload(
    *,
    client,
    request_kwargs: dict,
    operation_label: str,
    max_scenes: int,
    strict_scene_count: bool = False,
) -> list[dict]:
    last_error_message = f"Empty {operation_label} returned from model"

    for attempt_number in range(1, MAX_SCENE_PROMPT_ATTEMPTS + 1):
        response = await client.chat.completions.create(**request_kwargs)
        raw, error_message, retryable, token_budget_exhausted = _extract_scene_result(
            response,
            operation_label=operation_label,
        )

        if raw is not None:
            try:
                return _parse_scene_response(
                    raw,
                    max_scenes=max_scenes,
                    strict_scene_count=strict_scene_count,
                )
            except json.JSONDecodeError as exc:
                error_message = (
                    f"{operation_label.capitalize()} returned invalid JSON: {exc.msg}"
                )
                retryable = True
                finish_reason = getattr(response.choices[0], "finish_reason", None)
                token_budget_exhausted = finish_reason == "length"
            except ValueError as exc:
                error_message = f"{operation_label.capitalize()} returned unusable JSON: {exc}"
                retryable = True

        last_error_message = error_message
        if retryable and attempt_number < MAX_SCENE_PROMPT_ATTEMPTS:
            expanded_budget = None
            if token_budget_exhausted:
                expanded_budget = _increase_completion_budget(request_kwargs)

            logger.warning(
                "%s attempt %d/%d returned no usable content (%s)%s Retrying in %d seconds.",
                operation_label.capitalize(),
                attempt_number,
                MAX_SCENE_PROMPT_ATTEMPTS,
                error_message,
                (
                    f"; increasing completion token budget to {expanded_budget}"
                    if expanded_budget is not None
                    else ""
                ),
                SCENE_PROMPT_RETRY_DELAY_SECONDS,
            )
            await asyncio.sleep(SCENE_PROMPT_RETRY_DELAY_SECONDS)
            continue

        raise ValueError(error_message)

    raise ValueError(last_error_message)


async def generate_scene_prompts(
    summary: str,
    *,
    video_prompt_language: str = DEFAULT_VIDEO_PROMPT_LANGUAGE,
) -> list[dict]:
    """根據摘要產生分鏡提示詞。

    回傳包含 `narration_text` 與 `video_prompt` 欄位的字典清單。
    """
    client = get_openai_client()
    resolved_video_prompt_language = normalize_video_prompt_language(video_prompt_language)

    request_kwargs = prepare_chat_completion_kwargs(
        settings.summarizer_model,
        messages=[
            {
                "role": "system",
                "content": _build_scene_generation_system_prompt(
                    resolved_video_prompt_language
                ),
            },
            {"role": "user", "content": f"請根據以下摘要生成影片場景：\n\n{summary}"},
        ],
        temperature=0.7,
        max_completion_tokens=SCENE_PROMPT_INITIAL_MAX_COMPLETION_TOKENS,
        response_format={"type": "json_object"},
    )

    validated = await _request_scene_payload(
        client=client,
        request_kwargs=request_kwargs,
        operation_label="scene prompt generation",
        max_scenes=settings.max_scenes_per_project,
    )

    logger.info("Generated %d scene prompts", len(validated))
    return validated


async def rewrite_or_split_scene(
    narration_text: str,
    video_prompt: str,
    *,
    actual_duration_seconds: float,
    max_duration_seconds: float,
    max_scenes: int,
    video_prompt_language: str | None = None,
) -> list[dict]:
    """重寫或拆分單一分鏡，直到每段旁白都符合硬性時長上限。"""
    client = get_openai_client()
    resolved_video_prompt_language = normalize_video_prompt_language(
        video_prompt_language or infer_video_prompt_language(video_prompt)
    )
    current_video_prompt_language_label = (
        "English" if resolved_video_prompt_language == "en" else "Traditional Chinese"
    )

    request_kwargs = prepare_chat_completion_kwargs(
        settings.summarizer_model,
        messages=[
            {
                "role": "system",
                "content": _build_scene_rewrite_system_prompt(resolved_video_prompt_language),
            },
            {
                "role": "user",
                "content": (
                    "The current scene narration is too long. Rewrite it shorter if one scene can fit; "
                    "otherwise split it into the fewest consecutive scenes needed.\n\n"
                    f"Measured narration duration: {actual_duration_seconds:.2f} seconds\n"
                    f"Hard limit per scene: {max_duration_seconds:.2f} seconds\n"
                    f"Maximum scenes you may return: {max_scenes}\n\n"
                    "Current narration (Traditional Chinese):\n"
                    f"{narration_text}\n\n"
                    f"Current video prompt ({current_video_prompt_language_label}):\n"
                    f"{video_prompt}"
                ),
            },
        ],
        temperature=0.4,
        max_completion_tokens=SCENE_REWRITE_MAX_COMPLETION_TOKENS,
        response_format={"type": "json_object"},
    )

    # 重寫過長分鏡時，若超過 max_scenes 應視為錯誤，而不是直接裁切。
    rewritten_scenes = await _request_scene_payload(
        client=client,
        request_kwargs=request_kwargs,
        operation_label="scene rewrite",
        max_scenes=max_scenes,
        strict_scene_count=True,
    )

    if len(rewritten_scenes) == 1:
        candidate = rewritten_scenes[0]
        if (
            candidate["narration_text"] == narration_text.strip()
            and candidate["video_prompt"] == video_prompt.strip()
        ):
            raise ValueError("Scene rewrite returned the original overlong scene unchanged")

    logger.info("Rewrote scene into %d scene(s) after %.2f-second narration", len(rewritten_scenes), actual_duration_seconds)
    return rewritten_scenes
