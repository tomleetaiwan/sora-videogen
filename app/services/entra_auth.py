import asyncio
import logging
from dataclasses import dataclass, field

from azure.identity import DefaultAzureCredential

from app.config import settings

logger = logging.getLogger(__name__)

COGNITIVE_SERVICES_SCOPE = "https://cognitiveservices.azure.com/.default"


@dataclass(slots=True)
class EntraTokenCheck:
    service_name: str
    success: bool
    detail: str


@dataclass(slots=True)
class EntraAuthStatus:
    enabled: bool = False
    ready: bool = True
    warning_message: str | None = None
    checks: list[EntraTokenCheck] = field(default_factory=list)

    @property
    def failed_checks(self) -> list[EntraTokenCheck]:
        return [check for check in self.checks if not check.success]


def create_default_entra_auth_status() -> EntraAuthStatus:
    return EntraAuthStatus()


def _summarize_auth_error(error: Exception) -> str:
    message = str(error).strip()
    if not message:
        return error.__class__.__name__
    return message.splitlines()[0]


async def _close_credential(credential: DefaultAzureCredential | None) -> None:
    if credential is None:
        return

    close = getattr(credential, "close", None)
    if callable(close):
        await asyncio.to_thread(close)


async def evaluate_startup_entra_auth_status() -> EntraAuthStatus:
    checks: list[EntraTokenCheck] = []
    pending_scopes: list[tuple[str, str]] = []

    if settings.use_azure_openai and settings.azure_openai_use_entra_id:
        pending_scopes.append(("Azure OpenAI", settings.azure_openai_token_scope))

    if settings.azure_speech_use_entra_id:
        if not settings.azure_speech_resource_id or not settings.azure_speech_region:
            checks.append(
                EntraTokenCheck(
                    service_name="Azure Speech",
                    success=False,
                    detail=(
                        "AZURE_SPEECH_RESOURCE_ID 與 AZURE_SPEECH_REGION 缺少設定，"
                        "無法建立 Microsoft Entra ID 驗證。"
                    ),
                )
            )
        else:
            pending_scopes.append(("Azure Speech", COGNITIVE_SERVICES_SCOPE))

    status = EntraAuthStatus(enabled=bool(pending_scopes or checks))
    if not status.enabled:
        return status

    credential: DefaultAzureCredential | None = None
    try:
        credential = DefaultAzureCredential()
        for service_name, scope in pending_scopes:
            try:
                await asyncio.to_thread(credential.get_token, scope)
            except Exception as error:
                logger.warning("Microsoft Entra token check failed for %s", service_name, exc_info=True)
                checks.append(
                    EntraTokenCheck(
                        service_name=service_name,
                        success=False,
                        detail=_summarize_auth_error(error),
                    )
                )
            else:
                checks.append(
                    EntraTokenCheck(
                        service_name=service_name,
                        success=True,
                        detail="已成功取得 Microsoft Entra ID token。",
                    )
                )
    finally:
        await _close_credential(credential)

    status.checks = checks
    status.ready = all(check.success for check in checks)
    if not status.ready:
        failed_service_names = "、".join(check.service_name for check in status.failed_checks)
        status.warning_message = (
            f"啟動時無法透過 Microsoft Entra ID 取得 {failed_service_names} 所需 token。"
            "請先確認已完成 Azure CLI 或 VS Code Azure 登入，或已設定可用的服務主體 / Managed Identity。"
        )

    return status