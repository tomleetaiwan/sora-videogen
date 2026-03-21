import logging

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# 通常包含主要內容的標籤
CONTENT_TAGS = ["article", "main", "section", "div"]
STRIP_TAGS = ["script", "style", "nav", "footer", "header", "aside", "form"]


async def scrape_url(url: str, *, max_chars: int = 50_000) -> str:
    """從 URL 擷取文字內容。

    回傳適合進一步做摘要的乾淨文字。
    失敗時會拋出 httpx.HTTPError 或 ValueError。
    """
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        response = await client.get(url, headers={"User-Agent": "SoraVideoGen/1.0"})
        response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    # 移除非正文元素
    for tag in soup.find_all(STRIP_TAGS):
        tag.decompose()

    # 嘗試找出主要內容區塊
    content = None
    for tag_name in ["article", "main", "[role='main']"]:
        content = soup.find(tag_name)
        if content:
            break

    if content is None:
        content = soup.body or soup

    text = content.get_text(separator="\n", strip=True)

    if not text:
        raise ValueError(f"No text content found at {url}")

    # 截斷內容以避免超出 token 上限
    if len(text) > max_chars:
        text = text[:max_chars] + "\n...[truncated]"

    logger.info("Scraped %d characters from %s", len(text), url)
    return text
