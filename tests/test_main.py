from unittest.mock import patch

import pytest

from app.config import settings
from app.main import run


@pytest.mark.asyncio
async def test_root_redirects_to_projects(client):
    response = await client.get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/projects/"


@pytest.mark.asyncio
async def test_favicon_is_served(client):
    response = await client.get("/favicon.ico")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/vnd.microsoft.icon")
    assert len(response.content) > 0


def test_run_uses_settings_host_and_port(monkeypatch):
    monkeypatch.setattr(settings, "app_host", "127.0.0.1")
    monkeypatch.setattr(settings, "app_port", 8765)

    with patch("app.main.uvicorn.run") as mock_run:
        run()

    mock_run.assert_called_once_with(
        "app.main:app",
        host="127.0.0.1",
        port=8765,
        reload=True,
    )