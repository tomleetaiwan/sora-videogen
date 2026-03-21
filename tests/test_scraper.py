from unittest.mock import AsyncMock, patch

import pytest

from app.services.scraper import scrape_url


@pytest.mark.asyncio
async def test_scrape_url_extracts_text():
    html = """
    <html><body>
        <nav>Navigation</nav>
        <article><p>Main article content here.</p></article>
        <footer>Footer</footer>
    </body></html>
    """
    mock_response = AsyncMock()
    mock_response.text = html
    mock_response.raise_for_status = lambda: None

    with patch("app.services.scraper.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        result = await scrape_url("https://example.com")

    assert "Main article content" in result
    assert "Navigation" not in result
    assert "Footer" not in result


@pytest.mark.asyncio
async def test_scrape_url_truncates_long_content():
    html = "<html><body><article>" + "x" * 60_000 + "</article></body></html>"
    mock_response = AsyncMock()
    mock_response.text = html
    mock_response.raise_for_status = lambda: None

    with patch("app.services.scraper.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        result = await scrape_url("https://example.com", max_chars=100)

    assert len(result) <= 120  # 100 個字元加上截斷提示
    assert "truncated" in result
