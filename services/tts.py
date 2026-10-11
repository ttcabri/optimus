"""Ответ голосом: OpenAI TTS, формат opus (как голосовые в Telegram)."""
import logging
import re

from openai import AsyncOpenAI

from config import OPENAI_API_KEY

logger = logging.getLogger(__name__)
client = AsyncOpenAI(api_key=OPENAI_API_KEY)


async def speak(text: str) -> bytes | None:
    clean = re.sub(r"<[^>]+>|https?://\S+|[*_#`•]", "", text).strip()[:1800]
    if not clean:
        return None
    try:
        r = await client.audio.speech.create(model="tts-1", voice="onyx", input=clean, response_format="opus")
        return r.content
    except Exception as e:
        logger.error(f"TTS error: {e}")
        return None
