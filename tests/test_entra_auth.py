import pytest

from app.config import settings
from app.services import entra_auth


@pytest.mark.asyncio
async def test_evaluate_startup_entra_auth_status_returns_default_when_entra_disabled(monkeypatch):
    monkeypatch.setattr(settings, "azure_openai_endpoint", "")
    monkeypatch.setattr(settings, "azure_openai_use_entra_id", False)
    monkeypatch.setattr(settings, "azure_speech_use_entra_id", False)

    status = await entra_auth.evaluate_startup_entra_auth_status()

    assert status.enabled is False
    assert status.ready is True
    assert status.checks == []
    assert status.warning_message is None


@pytest.mark.asyncio
async def test_evaluate_startup_entra_auth_status_reports_token_failure(monkeypatch):
    monkeypatch.setattr(settings, "azure_openai_endpoint", "https://example.openai.azure.com")
    monkeypatch.setattr(settings, "azure_openai_use_entra_id", True)
    monkeypatch.setattr(settings, "azure_speech_use_entra_id", False)

    class FakeCredential:
        def get_token(self, scope):
            raise RuntimeError("login required")

        def close(self):
            return None

    monkeypatch.setattr(entra_auth, "DefaultAzureCredential", lambda: FakeCredential())

    status = await entra_auth.evaluate_startup_entra_auth_status()

    assert status.enabled is True
    assert status.ready is False
    assert status.warning_message is not None
    assert len(status.failed_checks) == 1
    assert status.failed_checks[0].service_name == "Azure OpenAI"
    assert status.failed_checks[0].detail == "login required"