import json
import logging
from datetime import datetime, timedelta
from threading import Thread
from pathlib import Path

import spaces
import telebot
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from config import TELEGRAM_TOKEN
from db import init_db, get_conn
from handlers import register_handlers


@spaces.GPU
def dummy_gpu_check():
    return "ok"


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

MINI_APP_PATH = Path(__file__).parent / "mini_app.html"


@app.get("/", response_class=HTMLResponse)
def mini_app():
    return MINI_APP_PATH.read_text(encoding="utf-8")


@app.get("/api/plan/{user_id}")
def api_plan(user_id: int, days: int = 14):
    today = datetime.now().date()
    end = today + timedelta(days=days)
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT date, type, distance_km, duration_min, elevation_m, description, status
        FROM plan WHERE user_id = ? AND date >= ? AND date <= ?
        ORDER BY date
    """, (user_id, today.isoformat(), end.isoformat()))
    rows = c.fetchall()
    conn.close()
    return JSONResponse([
        {
            "date": r[0], "type": r[1], "distance_km": r[2],
            "duration_min": r[3], "elevation_m": r[4],
            "description": r[5], "status": r[6],
        } for r in rows
    ])


@app.get("/api/diary/{user_id}")
def api_diary(user_id: int, limit: int = 30):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT id, date, raw_text, structured_data FROM diary
        WHERE user_id = ? ORDER BY id DESC LIMIT ?
    """, (user_id, limit))
    rows = c.fetchall()
    conn.close()
    return JSONResponse([
        {
            "id": r[0], "date": r[1], "raw_text": r[2],
            "structured": json.loads(r[3]) if r[3] else {},
        } for r in rows
    ])


@app.get("/api/stats/{user_id}")
def api_stats(user_id: int, weeks: int = 8):
    """Агрегирует дневник по неделям: объём и набор высоты."""
    today = datetime.now().date()
    start = today - timedelta(weeks=weeks)

    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT date, structured_data FROM diary
        WHERE user_id = ? AND date >= ?
        ORDER BY date
    """, (user_id, start.isoformat()))
    rows = c.fetchall()
    conn.close()

    # Собираем данные по неделям
    buckets = {}
    for date_str, struct in rows:
        try:
            d = datetime.fromisoformat(date_str).date()
        except Exception:
            continue
        # Номер недели от start
        delta = (d - start).days
        if delta < 0:
            continue
        week_idx = delta // 7

        s = json.loads(struct) if struct else {}
        dist = s.get("distance_km") or 0
        elev = s.get("elevation_m") or 0

        if week_idx not in buckets:
            buckets[week_idx] = {"volume": 0, "elevation": 0}
        buckets[week_idx]["volume"] += dist
        buckets[week_idx]["elevation"] += elev

    labels = []
    volume = []
    elevation = []
    for i in range(weeks):
        week_start = start + timedelta(weeks=i)
        labels.append(week_start.strftime("%d.%m"))
        b = buckets.get(i, {"volume": 0, "elevation": 0})
        volume.append(round(b["volume"], 1))
        elevation.append(int(b["elevation"]))

    return JSONResponse({
        "weeks": labels,
        "volume": volume,
        "elevation": elevation,
    })


def run_bot():
    bot = telebot.TeleBot(TELEGRAM_TOKEN, threaded=False)
    register_handlers(bot)
    logger.info("Telegram бот запущен...")
    bot.infinity_polling(timeout=10, long_polling_timeout=5)


if __name__ == "__main__":
    init_db()
    t = Thread(target=run_bot)
    t.start()
    uvicorn.run(app, host="0.0.0.0", port=7860)