from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import settings
from app.services import openai_client


@pytest.fixture(autouse=True)
def reset_client_state(monkeypatch):
    monkeypatch.setattr(openai_client, "_client", None)
    monkeypatch.setattr(openai_client, "_credential", None)


def test_get_openai_client_uses_default_openai_api_key(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(settings, "openai_base_url", "")
    monkeypatch.setattr(settings, "azure_openai_endpoint", "")
    monkeypatch.setattr(settings, "azure_openai_api_key", "")
    monkeypatch.setattr(settings, "azure_openai_use_entra_id", False)

    with patch("app.services.openai_client.AsyncOpenAI") as mock_cls:
        client = MagicMock()
        mock_cls.return_value = client

        result = openai_client.get_openai_client()

    assert result is client
    mock_cls.assert_called_once_with(api_key="test-key")


def test_get_openai_client_uses_azure_entra_id(monkeypatch):
    token_provider = AsyncMock(return_value="token")

    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(settings, "azure_openai_endpoint", "https://example.openai.azure.com")
    monkeypatch.setattr(settings, "azure_openai_api_key", "")
    monkeypatch.setattr(settings, "azure_openai_use_entra_id", True)
    monkeypatch.setattr(
        settings,
        "azure_openai_token_scope",
        "https://cognitiveservices.azure.com/.default",
    )

    with patch("app.services.openai_client.AsyncOpenAI") as mock_cls:
        with patch("app.services.openai_client.DefaultAzureCredential") as mock_credential_cls:
            with patch(
                "app.services.openai_client.get_bearer_token_provider",
                return_value=token_provider,
            ) as mock_token_provider:
                client = MagicMock()
                mock_cls.return_value = client

                result = openai_client.get_openai_client()

    assert result is client
    mock_credential_cls.assert_called_once_with()
    mock_token_provider.assert_called_once()
    mock_cls.assert_called_once_with(
        api_key=token_provider,
        base_url="https://example.openai.azure.com/openai/v1/",
    )


@pytest.mark.asyncio
async def test_close_openai_client_closes_cached_resources(monkeypatch):
    mock_client = AsyncMock()
    mock_credential = AsyncMock()

    monkeypatch.setattr(openai_client, "_client", mock_client)
    monkeypatch.setattr(openai_client, "_credential", mock_credential)

    await openai_client.close_openai_client()

    mock_client.close.assert_awaited_once()
    mock_credential.close.assert_awaited_once()
    assert openai_client._client is None
    assert openai_client._credential is None