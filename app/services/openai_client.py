import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider
from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

TokenProvider = Callable[[], Awaitable[str]]

_client: AsyncOpenAI | None = None
_credential: DefaultAzureCredential | None = None


def prepare_chat_completion_kwargs(model: str, **kwargs: Any) -> dict[str, Any]:
    """針對不同模型的聊天完成 API 差異，統一整理請求參數。"""
    request_kwargs = {"model": model, **kwargs}

    # 目前 GPT-5 聊天模型只接受預設 temperature，
    # 因此移除自訂值，避免送出會被 API 拒絕的參數。
    if model.lower().startswith("gpt-5"):
        request_kwargs.pop("temperature", None)

    return request_kwargs


def _resolve_api_key() -> str | TokenProvider:
    if settings.use_azure_openai:
        if settings.azure_openai_use_entra_id:
            global _credential
            if _credential is None:
                # 重用同一個 credential 實例，避免背景任務建立重複的驗證鏈。
                _credential = DefaultAzureCredential()
            return get_bearer_token_provider(_credential, settings.azure_openai_token_scope)

        if settings.azure_openai_api_key:
            return settings.azure_openai_api_key

        if settings.openai_api_key:
            return settings.openai_api_key

        raise ValueError(
            "Azure OpenAI is configured but no credential was provided. "
            "Set AZURE_OPENAI_USE_ENTRA_ID=true or configure AZURE_OPENAI_API_KEY/OPENAI_API_KEY."
        )

    if settings.openai_api_key:
        return settings.openai_api_key

    raise ValueError("OPENAI_API_KEY is required when Azure OpenAI is not configured.")


def get_openai_client() -> AsyncOpenAI:
    global _client

    if _client is not None:
        return _client

    # 在整個行程中共用同一個非同步 client，因為 pipeline 會啟動背景任務。
    client_kwargs: dict[str, Any] = {"api_key": _resolve_api_key()}

    if settings.use_azure_openai:
        client_kwargs["base_url"] = settings.resolved_azure_openai_base_url
        logger.info(
            "Initializing AsyncOpenAI for Azure OpenAI endpoint %s using %s",
            settings.azure_openai_endpoint,
            "Microsoft Entra ID" if settings.azure_openai_use_entra_id else "API key",
        )
    elif settings.openai_base_url:
        client_kwargs["base_url"] = f"{settings.openai_base_url.rstrip('/')}/"

    _client = AsyncOpenAI(**client_kwargs)
    return _client


async def _close_resource(resource: Any) -> None:
    if resource is None:
        return

    close = getattr(resource, "close", None)
    if close is None:
        return

    result = close()
    if inspect.isawaitable(result):
        await result


async def close_openai_client() -> None:
    global _client, _credential

    await _close_resource(_client)
    _client = None

    await _close_resource(_credential)
    _credential = None