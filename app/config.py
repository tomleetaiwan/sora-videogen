from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openai_api_key: str = ""
    openai_base_url: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_use_entra_id: bool = False
    azure_openai_token_scope: str = "https://cognitiveservices.azure.com/.default"
    azure_speech_key: str = ""
    azure_speech_region: str = ""
    azure_speech_endpoint: str = ""
    azure_speech_use_entra_id: bool = False
    azure_speech_resource_id: str = ""
    database_url: str = "sqlite+aiosqlite:///./sora-videogen.db"
    media_dir: Path = Path("./media")
    media_backend: str = "ffmpeg"
    gstreamer_launch_binary: str = "gst-launch-1.0"
    gstreamer_inspect_binary: str = "gst-inspect-1.0"
    gstreamer_frame_sample_fps: int = 2

    # OpenAI 模型
    summarizer_model: str = "gpt-5-mini"
    tts_voice: str = "zh-TW-HsiaoYuNeural"
    sora_model: str = "sora-2"
    sora_video_size: str = "1280x720"

    # 應用程式
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    max_scenes_per_project: int = 300
    scene_duration_seconds: int = 12
    sora_supported_durations_seconds: tuple[int, int, int] = (4, 8, 12)
    sora_supported_sizes: tuple[str, str, str, str] = (
        "720x1280",
        "1280x720",
        "1024x1792",
        "1792x1024",
    )
    video_generation_max_attempts: int = 2
    video_generation_retry_delay_seconds: int = 45

    @property
    def use_azure_openai(self) -> bool:
        return bool(self.azure_openai_endpoint)

    @property
    def resolved_azure_openai_base_url(self) -> str:
        endpoint = self.azure_openai_endpoint.rstrip("/")
        if not endpoint:
            return ""
        if endpoint.endswith("/openai/v1"):
            return f"{endpoint}/"
        return f"{endpoint}/openai/v1/"


settings = Settings()
