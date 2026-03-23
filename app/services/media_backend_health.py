import asyncio
import logging
import subprocess
from dataclasses import dataclass, field

from app.config import settings
from app.services.media_backend import (
    GSTREAMER_AAC_ENCODER_CANDIDATES,
    GSTREAMER_REQUIRED_ELEMENTS,
    get_media_backend,
    inspect_gstreamer_element,
    resolve_command_path,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MediaBackendCheck:
    component_name: str
    success: bool
    detail: str


@dataclass(slots=True)
class MediaBackendStatus:
    enabled: bool = False
    ready: bool = True
    warning_message: str | None = None
    checks: list[MediaBackendCheck] = field(default_factory=list)

    @property
    def failed_checks(self) -> list[MediaBackendCheck]:
        return [check for check in self.checks if not check.success]


def create_default_media_backend_status() -> MediaBackendStatus:
    return MediaBackendStatus()


async def evaluate_startup_media_backend_status() -> MediaBackendStatus:
    try:
        backend = get_media_backend()
    except ValueError as error:
        return MediaBackendStatus(
            enabled=True,
            ready=False,
            warning_message="媒體後端設定無效，請先修正後再啟動影片流程。",
            checks=[
                MediaBackendCheck(
                    component_name="MEDIA_BACKEND",
                    success=False,
                    detail=str(error),
                )
            ],
        )

    status = MediaBackendStatus(enabled=backend == "gstreamer")
    if not status.enabled:
        return status

    checks: list[MediaBackendCheck] = []

    launch_available = _command_exists(settings.gstreamer_launch_binary)
    checks.append(
        MediaBackendCheck(
            component_name=settings.gstreamer_launch_binary,
            success=launch_available,
            detail=(
                "已在 PATH 中找到 GStreamer 啟動指令。"
                if launch_available
                else f"找不到指令 {settings.gstreamer_launch_binary}。"
            ),
        )
    )

    inspect_available = _command_exists(settings.gstreamer_inspect_binary)
    checks.append(
        MediaBackendCheck(
            component_name=settings.gstreamer_inspect_binary,
            success=inspect_available,
            detail=(
                "已在 PATH 中找到 GStreamer 插件檢查指令。"
                if inspect_available
                else f"找不到指令 {settings.gstreamer_inspect_binary}。"
            ),
        )
    )

    if inspect_available:
        for element_name in GSTREAMER_REQUIRED_ELEMENTS:
            success, detail = await asyncio.to_thread(_inspect_gstreamer_element, element_name)
            checks.append(
                MediaBackendCheck(
                    component_name=element_name,
                    success=success,
                    detail=detail,
                )
            )
        encoder_success, encoder_detail = await asyncio.to_thread(_inspect_gstreamer_aac_encoder)
        checks.append(
            MediaBackendCheck(
                component_name="AAC encoder",
                success=encoder_success,
                detail=encoder_detail,
            )
        )

    status.checks = checks
    status.ready = all(check.success for check in checks)
    if not status.ready:
        failed_component_names = "、".join(check.component_name for check in status.failed_checks)
        status.warning_message = (
            "啟動時發現 GStreamer 媒體後端尚未就緒。"
            f"請先補齊以下缺少的指令或插件：{failed_component_names}。"
        )

    return status


def _command_exists(command_name: str) -> bool:
    return resolve_command_path(command_name) is not None


def _inspect_gstreamer_element(element_name: str) -> tuple[bool, str]:
    resolved_command = resolve_command_path(settings.gstreamer_inspect_binary)
    if resolved_command is None:
        return False, f"找不到指令 {settings.gstreamer_inspect_binary}。"

    try:
        subprocess.run(
            [resolved_command, element_name],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as error:
        logger.warning("Missing GStreamer element: %s", element_name, exc_info=True)
        return False, _summarize_subprocess_error(error)
    except FileNotFoundError:
        return False, f"找不到指令 {settings.gstreamer_inspect_binary}。"

    return True, "GStreamer 插件可用。"


def _inspect_gstreamer_aac_encoder() -> tuple[bool, str]:
    for encoder_name in GSTREAMER_AAC_ENCODER_CANDIDATES:
        if inspect_gstreamer_element(encoder_name):
            return True, f"GStreamer AAC encoder 可用：{encoder_name}。"

    candidate_list = "、".join(GSTREAMER_AAC_ENCODER_CANDIDATES)
    return False, f"找不到可用的 AAC encoder。已檢查：{candidate_list}。"


def _summarize_subprocess_error(error: subprocess.CalledProcessError) -> str:
    stderr = (error.stderr or "").strip()
    stdout = (error.stdout or "").strip()
    summary = stderr or stdout or error.__class__.__name__
    lines = summary.splitlines()
    if lines:
        return lines[0]
    # Fallback: in the unlikely event summary is empty, use the class name.
    return error.__class__.__name__
