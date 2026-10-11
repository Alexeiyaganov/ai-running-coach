import os
import logging
from config import groq_client, WHISPER_MODEL

logger = logging.getLogger(__name__)


def transcribe_voice(bot, file_id, message_id):
    """Скачивает голосовое из Telegram и распознаёт через Groq Whisper."""
    try:
        file_info = bot.get_file(file_id)
        downloaded = bot.download_file(file_info.file_path)
        temp_path = f"voice_{message_id}.ogg"
        with open(temp_path, "wb") as f:
            f.write(downloaded)

        with open(temp_path, "rb") as audio_file:
            transcription = groq_client.audio.transcriptions.create(
                file=("voice.ogg", audio_file),
                model=WHISPER_MODEL,
            )
        os.remove(temp_path)
        return transcription.text
    except Exception as e:
        logger.error(f"Voice error: {e}")
        return None