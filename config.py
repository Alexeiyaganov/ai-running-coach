import os
from openai import OpenAI
from groq import Groq

# --- Ключи ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

# --- База данных (Neon PostgreSQL) ---
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL не задан. Добавь переменную окружения "
        "со строкой подключения к Neon PostgreSQL."
    )

# --- Модели ---
LLM_MODEL = "openrouter/free"
WHISPER_MODEL = "whisper-large-v3"

# --- Клиенты ---
openrouter_client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)
groq_client = Groq(api_key=GROQ_API_KEY)

# --- Strava ---
STRAVA_CLIENT_ID = os.environ.get("STRAVA_CLIENT_ID")
STRAVA_CLIENT_SECRET = os.environ.get("STRAVA_CLIENT_SECRET")

# URL приложения. Лучше задать в Render Environment.
APP_URL = os.environ.get(
    "APP_URL",
    "https://ai-running-coach-ksyj.onrender.com"
)