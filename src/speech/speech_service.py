import os
import asyncio
import aiofiles
from urllib.parse import urlparse
from typing import Optional
from fastapi import HTTPException
from src.config import (
    logger,
    MOCK_MODE,
    AZURE_SPEECH_KEY,
    AZURE_SPEECH_ENDPOINT
)

speechsdk = None
if not MOCK_MODE:
    try:
        import azure.cognitiveservices.speech as speechsdk
        logger.info("Azure Speech SDK imported successfully inside speech module.")
    except ImportError:
        logger.warning("Failed to import azure-cognitiveservices-speech inside speech module.")

async def transcribe_audio_file(file_bytes: bytes) -> str:
    if MOCK_MODE or not speechsdk or not AZURE_SPEECH_KEY:
        # Simulate transcription delay
        await asyncio.sleep(1.0)
        logger.info("[SPEECH] Mocking speech-to-text transcription")
        return "I want a refund for the broken headphones I received in my order ORD-10001."

    # Write to a temporary file
    temp_filename = "temp_audio.wav"
    async with aiofiles.open(temp_filename, "wb") as f:
        await f.write(file_bytes)

    try:
        parsed = urlparse(AZURE_SPEECH_ENDPOINT)
        base_endpoint = f"{parsed.scheme}://{parsed.netloc}"
        speech_config = speechsdk.SpeechConfig(subscription=AZURE_SPEECH_KEY, endpoint=base_endpoint)
        audio_config = speechsdk.audio.AudioConfig(filename=temp_filename)
        recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config, audio_config=audio_config)

        # Run synchronously in executor
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, recognizer.recognize_once)

        if result.reason == speechsdk.ResultReason.RecognizedSpeech:
            logger.info(f"[SPEECH] Azure STT Transcribed: {result.text}")
            return result.text
        elif result.reason == speechsdk.ResultReason.NoMatch:
            logger.warning("[SPEECH] Azure STT: No speech could be recognized")
            return ""
        else:
            logger.error(f"[SPEECH] Azure STT Error: {result.error_details}")
            raise HTTPException(status_code=500, detail=f"Speech recognition failed: {result.error_details}")
    finally:
        if os.path.exists(temp_filename):
            os.remove(temp_filename)

async def synthesize_speech(text: str) -> Optional[bytes]:
    if MOCK_MODE or not speechsdk or not AZURE_SPEECH_KEY:
        logger.info("[SPEECH] Mocking text-to-speech synthesis")
        return None

    try:
        parsed = urlparse(AZURE_SPEECH_ENDPOINT)
        base_endpoint = f"{parsed.scheme}://{parsed.netloc}"
        speech_config = speechsdk.SpeechConfig(subscription=AZURE_SPEECH_KEY, endpoint=base_endpoint)
        speech_config.speech_synthesis_voice_name = "en-US-Ava:DragonHDLatestNeural"
        # Configure output format
        speech_config.set_speech_synthesis_output_format(speechsdk.SpeechSynthesisOutputFormat.Riff16Khz16BitMonoPcm)
        synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=None)

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, synthesizer.speak_text, text)

        if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
            logger.info(f"[SPEECH] Azure TTS Synthesized: {len(result.audio_data)} bytes")
            return result.audio_data
        else:
            if result.reason == speechsdk.ResultReason.Canceled:
                cancellation = speechsdk.SpeechSynthesisCancellationDetails(result)
                logger.error(f"[SPEECH] Azure TTS Canceled: {cancellation.reason}")
                if cancellation.error_details:
                    logger.error(f"[SPEECH] Azure TTS Error: {cancellation.error_details}")
            else:
                logger.error(f"[SPEECH] Azure TTS Failed with reason: {result.reason}")
            return None
    except Exception as e:
        logger.error(f"[SPEECH] TTS Exception: {e}")
        return None
