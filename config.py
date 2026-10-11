import os
from openai import OpenAI
from groq import Groq

# --- Ключи ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

# --- Модели ---
LLM_MODEL = "openrouter/free"
WHISPER_MODEL = "whisper-large-v3"

# --- БД ---
DB_PATH = "training.db"

# --- Клиенты ---
openrouter_client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)
groq_client = Groq(api_key=GROQ_API_KEY)