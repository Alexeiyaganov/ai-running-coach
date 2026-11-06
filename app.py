import json
import logging
import os

from datetime import datetime, timedelta
from pathlib import Path
from threading import Thread

import telebot
import uvicorn

from fastapi import (
    FastAPI,
    Body,
    Depends,
    Header,
    HTTPException,
    Query,
)
from fastapi.responses import HTMLResponse, JSONResponse

from config import TELEGRAM_TOKEN
from db import init_db, get_conn
from handlers import register_handlers
from auth import (
    validate_telegram_init_data,
    create_session_token,
    verify_session_token,
)


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="AI Running Coach API",
    version="1.0.0",
)

MINI_APP_PATH = Path(__file__).parent / "mini_app.html"


@app.on_event("startup")
def startup():
    init_db()
    logger.info("Database initialized")


@app.get("/", response_class=HTMLResponse)
def mini_app():
    if not MINI_APP_PATH.exists():
        raise HTTPException(status_code=404, detail="Mini App not found")

    return HTMLResponse(
        content=MINI_APP_PATH.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store"},
    )


@app.get("/health")
def health():
    return {"status": "ok"}


def get_current_user(
    authorization: str | None = Header(default=None),
) -> int:
    """Извлекает и проверяет пользователя из Bearer-токена."""

    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Authorization required",
        )

    scheme, _, token = authorization.partition(" ")

    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=401,
            detail="Invalid authorization header",
        )

    try:
        return verify_session_token(token)
    except ValueError:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired session",
        )


def check_user_access(requested_user_id: int, authenticated_user_id: int):
    if requested_user_id != authenticated_user_id:
        raise HTTPException(
            status_code=403,
            detail="Access denied",
        )


@app.post("/api/auth/telegram")
def telegram_auth(payload: dict = Body(...)):
    """
    Обменивает проверенный Telegram initData
    на серверный токен сессии.
    """

    init_data = payload.get("init_data")

    if not isinstance(init_data, str):
        raise HTTPException(
            status_code=400,
            detail="init_data is required",
        )

    try:
        user_id = validate_telegram_init_data(init_data)
        token = create_session_token(user_id)

        return {
            "access_token": token,
            "token_type": "bearer",
            "expires_in": 12 * 60 * 60,
            "user_id": user_id,
        }

    except ValueError:
        logger.warning("Telegram authentication failed")

        raise HTTPException(
            status_code=401,
            detail="Telegram authentication failed",
        )


@app.get("/api/plan/{user_id}")
def api_plan(
    user_id: int,
    days: int = Query(default=14, ge=1, le=90),
    authenticated_user_id: int = Depends(get_current_user),
):
    check_user_access(user_id, authenticated_user_id)

    today = datetime.now().date()
    end = today + timedelta(days=days)

    conn = get_conn()

    try:
        c = conn.cursor()
        c.execute("""
            SELECT date, type, distance_km, duration_min,
                   elevation_m, description, status
            FROM plan
            WHERE user_id = ? AND date >= ? AND date <= ?
            ORDER BY date
        """, (
            authenticated_user_id,
            today.isoformat(),
            end.isoformat(),
        ))

        rows = c.fetchall()

        return JSONResponse([
            {
                "date": r[0],
                "type": r[1],
                "distance_km": r[2],
                "duration_min": r[3],
                "elevation_m": r[4],
                "description": r[5],
                "status": r[6],
            }
            for r in rows
        ])

    finally:
        conn.close()


@app.get("/api/diary/{user_id}")
def api_diary(
    user_id: int,
    limit: int = Query(default=30, ge=1, le=100),
    authenticated_user_id: int = Depends(get_current_user),
):
    check_user_access(user_id, authenticated_user_id)

    conn = get_conn()

    try:
        c = conn.cursor()
        c.execute("""
            SELECT id, date, raw_text, structured_data
            FROM diary
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
        """, (authenticated_user_id, limit))

        rows = c.fetchall()

        result = []

        for r in rows:
            try:
                structured = json.loads(r[3]) if r[3] else {}
            except (ValueError, TypeError):
                structured = {}

            result.append({
                "id": r[0],
                "date": r[1],
                "raw_text": r[2],
                "structured": structured,
            })

        return JSONResponse(result)

    finally:
        conn.close()


@app.get("/api/stats/{user_id}")
def api_stats(
    user_id: int,
    weeks: int = Query(default=8, ge=1, le=52),
    authenticated_user_id: int = Depends(get_current_user),
):
    check_user_access(user_id, authenticated_user_id)

    today = datetime.now().date()
    start = today - timedelta(weeks=weeks)

    conn = get_conn()

    try:
        c = conn.cursor()
        c.execute("""
            SELECT date, structured_data
            FROM diary
            WHERE user_id = ? AND date >= ?
            ORDER BY date
        """, (
            authenticated_user_id,
            start.isoformat(),
        ))

        rows = c.fetchall()

    finally:
        conn.close()

    buckets = {}

    for date_str, struct in rows:
        try:
            d = datetime.fromisoformat(date_str).date()
            s = json.loads(struct) if struct else {}
        except (ValueError, TypeError):
            continue

        delta = (d - start).days

        if delta < 0:
            continue

        week_idx = delta // 7

        try:
            dist = float(s.get("distance_km") or 0)
            elev = float(s.get("elevation_m") or 0)
        except (ValueError, TypeError):
            continue

        if week_idx not in buckets:
            buckets[week_idx] = {
                "volume": 0,
                "elevation": 0,
            }

        buckets[week_idx]["volume"] += dist
        buckets[week_idx]["elevation"] += elev

    labels = []
    volume = []
    elevation = []

    for i in range(weeks):
        week_start = start + timedelta(weeks=i)

        labels.append(week_start.strftime("%d.%m"))

        bucket = buckets.get(
            i,
            {"volume": 0, "elevation": 0},
        )

        volume.append(round(bucket["volume"], 1))
        elevation.append(int(bucket["elevation"]))

    return JSONResponse({
        "weeks": labels,
        "volume": volume,
        "elevation": elevation,
    })


def run_bot():
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN is not configured")
        return

    bot = telebot.TeleBot(
        TELEGRAM_TOKEN,
        threaded=False,
    )

    register_handlers(bot)

    logger.info("Telegram bot started")

    bot.infinity_polling(
        timeout=10,
        long_polling_timeout=5,
    )


if __name__ == "__main__":
    init_db()

    bot_thread = Thread(
        target=run_bot,
        daemon=True,
    )
    bot_thread.start()

    port = int(os.environ.get("PORT", 8000))

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
    )