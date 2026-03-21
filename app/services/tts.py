import asyncio
import logging
from pathlib import Path
import wave

import azure.cognitiveservices.speech as speechsdk
from azure.identity import DefaultAzureCredential

from app.config import settings

logger = logging.getLogger(__name__)

AZURE_SPEECH_TOKEN_SCOPE = "https://cognitiveservices.azure.com/.default"


def _build_speech_config() -> tuple[speechsdk.SpeechConfig, DefaultAzureCredential | None]:
    credential: DefaultAzureCredential | None = None

    if settings.azure_speech_use_entra_id:
        if not settings.azure_speech_resource_id or not settings.azure_speech_region:
            raise ValueError(
                "AZURE_SPEECH_RESOURCE_ID and AZURE_SPEECH_REGION are required when "
                "AZURE_SPEECH_USE_ENTRA_ID=true."
            )

        credential = DefaultAzureCredential()
        token = credential.get_token(AZURE_SPEECH_TOKEN_SCOPE)
        # Azure Speech 要求 Entra token 採用 aad#<resource-id>#<token> 格式。
        authorization_token = f"aad#{settings.azure_speech_resource_id}#{token.token}"
        speech_config = speechsdk.SpeechConfig(
            auth_token=authorization_token,
            region=settings.azure_speech_region,
        )
    else:
        if not settings.azure_speech_key:
            raise ValueError("AZURE_SPEECH_KEY is required when Azure Speech Entra ID is disabled.")

        if settings.azure_speech_endpoint:
            speech_config = speechsdk.SpeechConfig(
                subscription=settings.azure_speech_key,
                endpoint=settings.azure_speech_endpoint,
            )
        elif settings.azure_speech_region:
            speech_config = speechsdk.SpeechConfig(
                subscription=settings.azure_speech_key,
                region=settings.azure_speech_region,
            )
        else:
            raise ValueError(
                "AZURE_SPEECH_REGION or AZURE_SPEECH_ENDPOINT is required when using Azure Speech key authentication."
            )

    speech_config.speech_synthesis_voice_name = settings.tts_voice
    speech_config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Riff24Khz16BitMonoPcm
    )
    return speech_config, credential


def _close_credential(credential: DefaultAzureCredential | None) -> None:
    if credential is None:
        return

    close = getattr(credential, "close", None)
    if callable(close):
        close()


def _synthesize_to_file(text: str, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    speech_config, credential = _build_speech_config()
    try:
        # 直接輸出到磁碟，讓後續 pipeline 可以把檔案路徑交給 ffmpeg 使用。
        audio_config = speechsdk.audio.AudioOutputConfig(filename=str(output_path))
        synthesizer = speechsdk.SpeechSynthesizer(
            speech_config=speech_config,
            audio_config=audio_config,
        )
        result = synthesizer.speak_text_async(text).get()

        if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
            logger.info("Generated narration audio with Azure Speech: %s", output_path)
            return output_path

        if result.reason == speechsdk.ResultReason.Canceled:
            cancellation = result.cancellation_details
            details = getattr(cancellation, "error_details", "")
            raise ValueError(
                f"Azure speech synthesis canceled: {cancellation.reason}. {details}".strip()
            )

        raise ValueError(f"Azure speech synthesis failed with reason: {result.reason}")
    finally:
        _close_credential(credential)


async def generate_narration(
    text: str,
    output_path: Path,
) -> Path:
    """為旁白文字產生 TTS 音訊。

    使用 Azure Speech 文字轉語音，並回傳產生出的音訊檔路徑。
    """
    return await asyncio.to_thread(_synthesize_to_file, text, output_path)


def get_audio_duration_seconds(audio_path: Path) -> float:
    """量測產生出的 WAV 旁白檔案時長。"""
    # 讀取 WAV 標頭已足夠，並可避免為時長檢查再額外加入 ffprobe 依賴。
    with wave.open(str(audio_path), "rb") as wav_file:
        frame_count = wav_file.getnframes()
        frame_rate = wav_file.getframerate()

    if frame_rate <= 0:
        raise ValueError(f"Invalid WAV frame rate for audio file: {audio_path}")

    return frame_count / float(frame_rate)
