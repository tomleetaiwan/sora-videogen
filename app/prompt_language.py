import re

DEFAULT_VIDEO_PROMPT_LANGUAGE = "zh-TW"
SUPPORTED_VIDEO_PROMPT_LANGUAGES = ("zh-TW", "en")


def normalize_video_prompt_language(value: str | None) -> str:
    if value in SUPPORTED_VIDEO_PROMPT_LANGUAGES:
        return value
    return DEFAULT_VIDEO_PROMPT_LANGUAGE


def infer_video_prompt_language(video_prompt: str | None) -> str:
    if video_prompt and re.search(r"[\u3400-\u9fff]", video_prompt):
        return "zh-TW"
    return "en"


def get_video_prompt_language_label(value: str | None) -> str:
    language = normalize_video_prompt_language(value)
    if language == "en":
        return "English"
    return "中文"
