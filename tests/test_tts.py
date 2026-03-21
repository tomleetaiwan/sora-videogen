from pathlib import Path
from unittest.mock import MagicMock, patch
import wave

import pytest

from app.config import settings
from app.services.tts import _synthesize_to_file, generate_narration, get_audio_duration_seconds


@pytest.fixture(autouse=True)
def reset_tts_settings(monkeypatch):
    monkeypatch.setattr(settings, "azure_speech_key", "speech-key")
    monkeypatch.setattr(settings, "azure_speech_region", "eastus")
    monkeypatch.setattr(settings, "azure_speech_endpoint", "")
    monkeypatch.setattr(settings, "azure_speech_use_entra_id", False)
    monkeypatch.setattr(settings, "azure_speech_resource_id", "")
    monkeypatch.setattr(settings, "tts_voice", "zh-TW-HsiaoYuNeural")


def test_synthesize_to_file_uses_azure_speech_key_auth(tmp_path):
    output_path = tmp_path / "narration.wav"

    speechsdk = MagicMock()
    speechsdk.ResultReason.SynthesizingAudioCompleted = "completed"
    speechsdk.ResultReason.Canceled = "canceled"
    speechsdk.SpeechSynthesisOutputFormat.Riff24Khz16BitMonoPcm = "riff"

    speech_config = MagicMock()
    speechsdk.SpeechConfig.return_value = speech_config
    audio_config = MagicMock()
    speechsdk.audio.AudioOutputConfig.return_value = audio_config

    result = MagicMock()
    result.reason = "completed"
    synthesizer = MagicMock()
    synthesizer.speak_text_async.return_value.get.return_value = result
    speechsdk.SpeechSynthesizer.return_value = synthesizer

    with patch("app.services.tts.speechsdk", speechsdk):
        returned_path = _synthesize_to_file("旁白內容", output_path)

    assert returned_path == output_path
    speechsdk.SpeechConfig.assert_called_once_with(subscription="speech-key", region="eastus")
    assert speech_config.speech_synthesis_voice_name == "zh-TW-HsiaoYuNeural"
    speech_config.set_speech_synthesis_output_format.assert_called_once_with("riff")
    speechsdk.audio.AudioOutputConfig.assert_called_once_with(filename=str(output_path))
    speechsdk.SpeechSynthesizer.assert_called_once_with(
        speech_config=speech_config,
        audio_config=audio_config,
    )


def test_synthesize_to_file_uses_azure_speech_entra_id(monkeypatch, tmp_path):
    output_path = tmp_path / "narration.wav"

    monkeypatch.setattr(settings, "azure_speech_key", "")
    monkeypatch.setattr(settings, "azure_speech_use_entra_id", True)
    monkeypatch.setattr(settings, "azure_speech_resource_id", "/subscriptions/test/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/speech")

    speechsdk = MagicMock()
    speechsdk.ResultReason.SynthesizingAudioCompleted = "completed"
    speechsdk.ResultReason.Canceled = "canceled"
    speechsdk.SpeechSynthesisOutputFormat.Riff24Khz16BitMonoPcm = "riff"

    speech_config = MagicMock()
    speechsdk.SpeechConfig.return_value = speech_config
    speechsdk.audio.AudioOutputConfig.return_value = MagicMock()

    result = MagicMock()
    result.reason = "completed"
    speechsdk.SpeechSynthesizer.return_value.speak_text_async.return_value.get.return_value = result

    credential = MagicMock()
    credential.get_token.return_value.token = "aad-token"

    with patch("app.services.tts.speechsdk", speechsdk):
        with patch("app.services.tts.DefaultAzureCredential", return_value=credential):
            returned_path = _synthesize_to_file("旁白內容", output_path)

    assert returned_path == output_path
    speechsdk.SpeechConfig.assert_called_once_with(
        auth_token="aad#/subscriptions/test/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/speech#aad-token",
        region="eastus",
    )
    credential.get_token.assert_called_once()
    credential.close.assert_called_once()


def test_synthesize_to_file_raises_on_canceled_result(tmp_path):
    output_path = tmp_path / "narration.wav"

    speechsdk = MagicMock()
    speechsdk.ResultReason.SynthesizingAudioCompleted = "completed"
    speechsdk.ResultReason.Canceled = "canceled"
    speechsdk.SpeechSynthesisOutputFormat.Riff24Khz16BitMonoPcm = "riff"
    speechsdk.SpeechConfig.return_value = MagicMock()
    speechsdk.audio.AudioOutputConfig.return_value = MagicMock()

    result = MagicMock()
    result.reason = "canceled"
    result.cancellation_details.reason = "Error"
    result.cancellation_details.error_details = "bad request"
    speechsdk.SpeechSynthesizer.return_value.speak_text_async.return_value.get.return_value = result

    with patch("app.services.tts.speechsdk", speechsdk):
        with pytest.raises(ValueError, match="Azure speech synthesis canceled"):
            _synthesize_to_file("旁白內容", output_path)


@pytest.mark.asyncio
async def test_generate_narration_uses_thread_wrapper(tmp_path):
    output_path = tmp_path / "narration.wav"

    with patch("app.services.tts.asyncio.to_thread") as mock_to_thread:
        mock_to_thread.return_value = output_path

        result = await generate_narration("旁白內容", output_path)

    assert result == output_path
    mock_to_thread.assert_awaited_once()


def test_get_audio_duration_seconds_reads_wav_length(tmp_path):
    output_path = tmp_path / "narration.wav"
    frame_rate = 8000
    duration_seconds = 2.5

    with wave.open(str(output_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(frame_rate)
        wav_file.writeframes(b"\x00\x00" * int(frame_rate * duration_seconds))

    assert get_audio_duration_seconds(output_path) == pytest.approx(duration_seconds, rel=0.01)