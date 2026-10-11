import os
import logging
import sqlite3
from datetime import datetime
from threading import Thread

import spaces
import gradio as gr
import telebot
from groq import Groq
from openai import OpenAI

# --- Заглушка для ZeroGPU: не вызывается, нужна только чтобы Space запустился ---
@spaces.GPU
def dummy_gpu_check():
    return "ok"
# ------------------------------------------------------------------------------

# --- Логирование ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Ключи из секретов ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

# --- Клиенты ---
bot = telebot.TeleBot(TELEGRAM_TOKEN, threaded=False)
groq_client = Groq(api_key=GROQ_API_KEY)
openrouter_client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)

# --- База данных ---
def init_db():
    conn = sqlite3.connect("training.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS diary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            raw_text TEXT,
            structured_data TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

init_db()

# --- LLM ---
def ask_llm(prompt, system_prompt=None):
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    try:
        response = openrouter_client.chat.completions.create(
            model="openrouter/free",
            messages=messages,
            temperature=0.7,
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"LLM error: {e}")
        return f"😔 Ошибка модели: {e}"

def parse_training_text(text):
    prompt = f"""
    Извлеки информацию о тренировке, верни ТОЛЬКО JSON:
    {{"date": "YYYY-MM-DD", "type": "тип", "distance_km": число, "duration_min": число, "elevation_m": число, "feeling": "оценка", "notes": "заметки"}}
    Текст: "{text}"
    """
    return ask_llm(prompt, "Ты парсер спортивных данных, отвечай только JSON.")

# --- Telegram ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message,
        "🏃 Привет! Я твой AI-тренер.\n\n"
        "Напиши или наговори о тренировке. Например:\n"
        "«Пробежал 10 км за 55 минут, набор 300 м, было тяжело»"
    )

@bot.message_handler(content_types=['voice'])
def handle_voice(message):
    try:
        bot.reply_to(message, "🎧 Слушаю...")
        file_info = bot.get_file(message.voice.file_id)
        downloaded = bot.download_file(file_info.file_path)
        temp_path = f"voice_{message.message_id}.ogg"
        with open(temp_path, 'wb') as f:
            f.write(downloaded)

        with open(temp_path, 'rb') as audio_file:
            transcription = groq_client.audio.transcriptions.create(
                file=("voice.ogg", audio_file),
                model="whisper-large-v3",
            )
        text = transcription.text
        bot.reply_to(message, f"📝 Распознал: {text}")

        structured = parse_training_text(text)
        conn = sqlite3.connect("training.db")
        c = conn.cursor()
        c.execute("INSERT INTO diary (date, raw_text, structured_data) VALUES (?, ?, ?)",
                  (datetime.now().date().isoformat(), text, structured))
        conn.commit()
        conn.close()

        bot.reply_to(message, f"✅ Записал:\n{structured}")
        os.remove(temp_path)
    except Exception as e:
        logger.error(f"Voice error: {e}")
        bot.reply_to(message, "😔 Ошибка обработки голоса.")

@bot.message_handler(func=lambda m: True)
def handle_text(message):
    text = message.text
    if any(w in text.lower() for w in ["план", "составь"]):
        bot.reply_to(message, "🤔 Составляю план...")
        plan = ask_llm(
            f"Ты тренер по бегу, трейлу и ориентированию. Составь план на неделю: {text}",
            "Давай конкретные планы с дистанцией, интенсивностью и набором высоты."
        )
        bot.reply_to(message, f"📋 План:\n\n{plan}")
    else:
        bot.reply_to(message, "📝 Записываю...")
        structured = parse_training_text(text)
        conn = sqlite3.connect("training.db")
        c = conn.cursor()
        c.execute("INSERT INTO diary (date, raw_text, structured_data) VALUES (?, ?, ?)",
                  (datetime.now().date().isoformat(), text, structured))
        conn.commit()
        conn.close()
        bot.reply_to(message, f"✅ Записал:\n{structured}")

# --- Фоновый запуск бота ---
def run_bot():
    logger.info("Telegram бот запущен...")
    bot.infinity_polling(timeout=10, long_polling_timeout=5)

# --- Gradio-заглушка (обязательна для HF) ---
def hello(name):
    return f"AI Running Coach работает. Привет, {name}!"

iface = gr.Interface(
    fn=hello,
    inputs="text",
    outputs="text",
    title="AI Running Coach",
    description="Telegram-бот работает в фоне. Общайся с ним в Telegram."
)

# --- Запуск ---
if __name__ == "__main__":
    t = Thread(target=run_bot)
    t.start()
    iface.launch(server_name="0.0.0.0", server_port=7860)