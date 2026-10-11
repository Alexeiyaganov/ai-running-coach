import os
import json
import logging
import sqlite3
from datetime import datetime, timedelta
from threading import Thread

import spaces
import gradio as gr
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from groq import Groq
from openai import OpenAI

# --- Заглушка для ZeroGPU ---
@spaces.GPU
def dummy_gpu_check():
    return "ok"

# --- Логирование ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Ключи ---
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

# --- БД ---
DB_PATH = "training.db"


def get_conn():
    return sqlite3.connect(DB_PATH)


def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            name TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            name TEXT,
            date TEXT,
            distance_km REAL,
            elevation_m INTEGER,
            target_time TEXT,
            priority TEXT,
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS plan (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            date TEXT,
            type TEXT,
            distance_km REAL,
            duration_min INTEGER,
            elevation_m INTEGER,
            description TEXT,
            status TEXT DEFAULT 'planned',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS diary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            date TEXT,
            raw_text TEXT,
            structured_data TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS pending (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action_type TEXT,
            payload TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


init_db()


# --- Пользователи ---
def ensure_user(user_id, name=None):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT id, name FROM users WHERE id = ?", (user_id,))
    row = c.fetchone()
    if row is None:
        c.execute("INSERT INTO users (id, name) VALUES (?, ?)", (user_id, name or f"user_{user_id}"))
        conn.commit()
    conn.close()


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


def parse_json_safe(raw):
    raw = raw.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(raw)
    except Exception as e:
        logger.error(f"JSON parse error: {e}; raw={raw[:500]}")
        return None


def parse_training_text(text):
    prompt = f"""
    Извлеки информацию о тренировке, верни ТОЛЬКО JSON:
    {{"date": "YYYY-MM-DD или null", "type": "тип или null", "distance_km": число или null, "duration_min": число или null, "elevation_m": число или null, "feeling": "оценка или null", "notes": "заметки или null"}}
    Сегодня {datetime.now().date().isoformat()}.
    Текст: "{text}"
    """
    return parse_json_safe(ask_llm(prompt, "Ты парсер спортивных данных, отвечай только JSON."))


def is_training_description(text):
    prompt = f'Это описание тренировки? Ответь одним словом "да" или "нет". Текст: "{text}"'
    answer = ask_llm(prompt, "Отвечай только 'да' или 'нет'.").strip().lower()
    return "да" in answer


# --- Pending (подтверждения) ---
def save_pending(user_id, action_type, payload):
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM pending WHERE user_id = ?", (user_id,))
    c.execute("INSERT INTO pending (user_id, action_type, payload) VALUES (?, ?, ?)",
              (user_id, action_type, json.dumps(payload, ensure_ascii=False)))
    conn.commit()
    conn.close()


def get_pending(user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT id, action_type, payload FROM pending WHERE user_id = ? ORDER BY id DESC LIMIT 1",
              (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return {"id": row[0], "action_type": row[1], "payload": json.loads(row[2])}
    return None


def clear_pending(user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM pending WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def confirmation_keyboard():
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("✅ Записать", callback_data="confirm"),
        InlineKeyboardButton("❌ Отмена", callback_data="cancel"),
    )
    return kb


# --- Запись тренировки ---
def save_training(user_id, structured, raw_text):
    conn = get_conn()
    c = conn.cursor()
    date = structured.get("date") or datetime.now().date().isoformat()
    c.execute("""
        INSERT INTO diary (user_id, date, raw_text, structured_data)
        VALUES (?, ?, ?, ?)
    """, (user_id, date, raw_text, json.dumps(structured, ensure_ascii=False)))
    conn.commit()
    conn.close()


# --- План ---
def save_plan(user_id, items):
    conn = get_conn()
    c = conn.cursor()
    for item in items:
        c.execute("""
            INSERT INTO plan (user_id, date, type, distance_km, duration_min, elevation_m, description)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            item.get("date"),
            item.get("type"),
            item.get("distance_km"),
            item.get("duration_min"),
            item.get("elevation_m"),
            item.get("description"),
        ))
    conn.commit()
    conn.close()


def get_plan_for_period(user_id, days):
    today = datetime.now().date()
    end = today + timedelta(days=days)
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT date, type, distance_km, duration_min, elevation_m, description, status
        FROM plan
        WHERE user_id = ? AND date >= ? AND date <= ?
        ORDER BY date
    """, (user_id, today.isoformat(), end.isoformat()))
    rows = c.fetchall()
    conn.close()

    if not rows:
        return f"📭 На ближайшие {days} дней плана нет."

    result = f"📅 План на {days} дней:\n\n"
    for date, typ, dist, dur, elev, desc, status in rows:
        emoji = "✅" if status == "completed" else "⏳"
        result += f"{emoji} {date}: {typ}\n"
        if dist: result += f"   {dist} км\n"
        if elev: result += f"   +{elev} м\n"
        if desc: result += f"   {desc}\n"
        result += "\n"
    return result


def get_diary(user_id, limit=10):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT date, raw_text FROM diary WHERE user_id = ? ORDER BY id DESC LIMIT ?",
              (user_id, limit))
    rows = c.fetchall()
    conn.close()

    if not rows:
        return "📭 Дневник пуст."

    result = "📖 Последние записи:\n\n"
    for date, raw in rows:
        result += f"📅 {date}\n{raw}\n\n"
    return result


# --- Telegram ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    ensure_user(message.from_user.id, message.from_user.first_name)
    bot.reply_to(message,
        f"🏃 Привет, {message.from_user.first_name}!\n\n"
        "Что я умею:\n"
        "• Записать тренировку — «пробежал 10 км за час»\n"
        "• Составить план — «составь план на месяц»\n"
        "• Показать дневник — «что записал?»\n\n"
        "Команды: /today /week /month /goals /diary"
    )


@bot.message_handler(commands=['today'])
def cmd_today(message):
    ensure_user(message.from_user.id, message.from_user.first_name)
    bot.reply_to(message, get_plan_for_period(message.from_user.id, 1))


@bot.message_handler(commands=['week'])
def cmd_week(message):
    ensure_user(message.from_user.id, message.from_user.first_name)
    bot.reply_to(message, get_plan_for_period(message.from_user.id, 7))


@bot.message_handler(commands=['month'])
def cmd_month(message):
    ensure_user(message.from_user.id, message.from_user.first_name)
    bot.reply_to(message, get_plan_for_period(message.from_user.id, 30))


@bot.message_handler(commands=['diary'])
def cmd_diary(message):
    ensure_user(message.from_user.id, message.from_user.first_name)
    bot.reply_to(message, get_diary(message.from_user.id))


@bot.message_handler(commands=['goals'])
def cmd_goals(message):
    ensure_user(message.from_user.id, message.from_user.first_name)
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT name, date, distance_km, elevation_m, priority FROM goals WHERE user_id = ? AND status = 'active'",
              (message.from_user.id,))
    rows = c.fetchall()
    conn.close()
    if not rows:
        bot.reply_to(message, "🎯 Активных целей нет. Напиши: «Хочу трейл 30 км 15 июня»")
        return
    result = "🎯 Активные цели:\n\n"
    for name, date, dist, elev, prio in rows:
        result += f"• {name} ({date}) — {dist} км, +{elev} м [приоритет {prio}]\n"
    bot.reply_to(message, result)


@bot.message_handler(content_types=['voice'])
def handle_voice(message):
    ensure_user(message.from_user.id, message.from_user.first_name)
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
        os.remove(temp_path)
        bot.reply_to(message, f"📝 Распознал: {text}")

        handle_incoming_text(message, text)
    except Exception as e:
        logger.error(f"Voice error: {e}")
        bot.reply_to(message, "😔 Ошибка обработки голоса.")


@bot.message_handler(func=lambda m: True)
def handle_text(message):
    ensure_user(message.from_user.id, message.from_user.first_name)
    handle_incoming_text(message, message.text)


def handle_incoming_text(message, text):
    user_id = message.from_user.id
    user_lower = text.lower()

    # 0. Если есть pending и пользователь написал что-то кроме /start
    # (кнопки обрабатываются отдельно)

    # 1. План
    if any(w in user_lower for w in ["план", "составь", "подготовь", "расписание", "программа"]):
        bot.reply_to(message, "🤔 Составляю план...")
        prompt = f"""
        Ты тренер по бегу, трейлу и ориентированию.
        Составь план тренировок. Верни ТОЛЬКО JSON-массив:
        [{{"date": "YYYY-MM-DD", "type": "тип", "distance_km": число, "duration_min": число, "elevation_m": число, "description": "описание"}}]
        Сегодня {datetime.now().date().isoformat()}.
        Запрос: "{text}"
        """
        raw = ask_llm(prompt, "Ты тренер. Отвечай только JSON-массивом.")
        items = parse_json_safe(raw)
        if not items:
            bot.reply_to(message, f"😔 Не разобрал план. Сырой ответ:\n{raw}")
            return
        save_plan(user_id, items)
        summary = "📋 План сохранён:\n\n"
        for item in items:
            summary += f"📅 {item.get('date')}: {item.get('type')} — {item.get('description')}\n"
        bot.reply_to(message, summary)
        return

    # 2. Дневник
    if any(w in user_lower for w in ["что записал", "дневник", "история", "мои тренировки"]):
        bot.reply_to(message, get_diary(user_id))
        return

    # 3. Приветствие
    if any(w in user_lower for w in ["привет", "как дела", "помощь", "help", "что умеешь"]):
        bot.reply_to(message,
            "🏃 Могу:\n"
            "• Записать тренировку\n"
            "• Составить план\n"
            "• Показать дневник\n\n"
            "Команды: /today /week /month /goals /diary"
        )
        return

    # 4. Проверяем, тренировка ли это
    if is_training_description(text):
        structured = parse_training_text(text)
        if not structured:
            bot.reply_to(message, "😔 Не разобрал описание тренировки.")
            return
        save_pending(user_id, "log_training", {"structured": structured, "raw_text": text})
        preview = json.dumps(structured, ensure_ascii=False, indent=2)
        bot.reply_to(
            message,
            f"📝 Разобрал так:\n{preview}\n\nЗаписать?",
            reply_markup=confirmation_keyboard(),
        )
        return

    # 5. Всё остальное — отвечаем как тренер
    reply = ask_llm(text, "Ты AI-тренер по бегу, трейлу и ориентированию. Отвечай коротко и по делу.")
    bot.reply_to(message, reply)


@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    user_id = call.from_user.id
    ensure_user(user_id, call.from_user.first_name)

    if call.data == "confirm":
        pending = get_pending(user_id)
        if not pending:
            bot.answer_callback_query(call.id, "Нечего подтверждать")
            return
        if pending["action_type"] == "log_training":
            payload = pending["payload"]
            save_training(user_id, payload["structured"], payload["raw_text"])
            clear_pending(user_id)
            bot.edit_message_text("✅ Записал в дневник.", call.message.chat.id, call.message.message_id)
        else:
            bot.answer_callback_query(call.id, "Неизвестное действие")

    elif call.data == "cancel":
        clear_pending(user_id)
        bot.edit_message_text("❌ Отменено.", call.message.chat.id, call.message.message_id)


# --- Фоновый запуск бота ---
def run_bot():
    logger.info("Telegram бот запущен...")
    bot.infinity_polling(timeout=10, long_polling_timeout=5)


# --- Gradio ---
def hello(name):
    return f"AI Running Coach работает. Привет, {name}!"


iface = gr.Interface(
    fn=hello,
    inputs="text",
    outputs="text",
    title="AI Running Coach",
    description="Telegram-бот работает в фоне."
)


if __name__ == "__main__":
    t = Thread(target=run_bot)
    t.start()
    iface.launch(server_name="0.0.0.0", server_port=7860)