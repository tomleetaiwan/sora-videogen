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

Return a JSON array of objects with keys "narration_text" and "video_prompt".
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

    response = await client.chat.completions.create(
        **prepare_chat_completion_kwargs(
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
            max_completion_tokens=4000,
            response_format={"type": "json_object"},
        )
    )

    raw = response.choices[0].message.content
    if not raw:
        raise ValueError("Empty response from prompt generator")

    validated = _parse_scene_response(raw, max_scenes=settings.max_scenes_per_project)

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

    response = await client.chat.completions.create(
        **prepare_chat_completion_kwargs(
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
            max_completion_tokens=2000,
            response_format={"type": "json_object"},
        )
    )

    raw = response.choices[0].message.content
    if not raw:
        raise ValueError("Empty response from scene rewrite")

    # 重寫過長分鏡時，若超過 max_scenes 應視為錯誤，而不是直接裁切。
    rewritten_scenes = _parse_scene_response(
        raw,
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
