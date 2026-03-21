import asyncio
import logging

from app.config import settings
from app.services.openai_client import get_openai_client, prepare_chat_completion_kwargs

logger = logging.getLogger(__name__)
MAX_SUMMARY_ATTEMPTS = 2
SUMMARY_RETRY_DELAY_SECONDS = 3

SYSTEM_PROMPT = """You are a content summarizer. Given the text content from a web page,
produce a clear, structured summary that can be used to create a narrated video.

Requirements:
- Write in Traditional Chinese (繁體中文)
- Use a narrative style suitable for video narration
- Organize the summary into logical sections/themes
- Keep the total summary between 500-2000 characters
- Focus on the most important and interesting information
"""


def _extract_summary_result(response) -> tuple[str | None, str, bool]:
    choices = getattr(response, "choices", None) or []
    if not choices:
        return None, "No summary choices returned from model", True

    choice = choices[0]
    message = getattr(choice, "message", None)

    content = getattr(message, "content", None)
    if isinstance(content, str):
        summary = content.strip()
        if summary:
            return summary, "", False

    refusal = getattr(message, "refusal", None)
    if isinstance(refusal, str) and refusal.strip():
        return None, f"Model refused to summarize content: {refusal.strip()}", False

    finish_reason = getattr(choice, "finish_reason", None)
    if finish_reason == "content_filter":
        return None, "Summary generation was blocked by the model content filter", False

    if finish_reason == "length":
        return None, "Summary generation stopped before returning usable content", False

    return None, "Empty summary returned from model", True


async def summarize_content(content: str) -> str:
    """使用 GPT-5-mini 摘要抓取到的網頁內容。

    回傳正體中文的結構化摘要。
    """
    client = get_openai_client()

    request_kwargs = prepare_chat_completion_kwargs(
        settings.summarizer_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"請摘要以下網頁內容：\n\n{content}"},
        ],
        temperature=0.7,
        max_completion_tokens=2000,
    )

    for attempt_number in range(1, MAX_SUMMARY_ATTEMPTS + 1):
        response = await client.chat.completions.create(**request_kwargs)
        summary, error_message, retryable = _extract_summary_result(response)

        if summary is not None:
            logger.info("Generated summary: %d characters", len(summary))
            return summary

        if retryable and attempt_number < MAX_SUMMARY_ATTEMPTS:
            logger.warning(
                "Summary model returned no usable content on attempt %d/%d (%s). Retrying in %d seconds.",
                attempt_number,
                MAX_SUMMARY_ATTEMPTS,
                error_message,
                SUMMARY_RETRY_DELAY_SECONDS,
            )
            await asyncio.sleep(SUMMARY_RETRY_DELAY_SECONDS)
            continue

        raise ValueError(error_message)

    raise ValueError("Empty summary returned from model")
