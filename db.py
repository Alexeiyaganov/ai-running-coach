import json
import logging
from datetime import datetime, timedelta

import psycopg2
from config import DATABASE_URL

logger = logging.getLogger(__name__)


def get_conn():
    """Создаёт подключение к PostgreSQL (Neon)."""
    return psycopg2.connect(DATABASE_URL, connect_timeout=10)


def init_db():
    """Создаёт таблицы, если их ещё нет."""
    conn = get_conn()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id BIGINT PRIMARY KEY,
            name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS goals (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            name TEXT,
            date TEXT,
            distance_km REAL,
            elevation_m INTEGER,
            target_time TEXT,
            priority TEXT,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS plan (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            date TEXT,
            type TEXT,
            distance_km REAL,
            duration_min INTEGER,
            elevation_m INTEGER,
            description TEXT,
            status TEXT DEFAULT 'planned',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS diary (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            date TEXT,
            raw_text TEXT,
            structured_data TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS pending (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            action_type TEXT,
            payload TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS last_action (
            user_id BIGINT PRIMARY KEY,
            action_type TEXT,
            record_id INTEGER,
            context TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Индексы для ускорения запросов
    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_diary_user_date
        ON diary (user_id, date)
    """)
    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_plan_user_date
        ON plan (user_id, date)
    """)

    conn.commit()
    conn.close()
    logger.info("Database schema initialized")


# --- Пользователи ---
def ensure_user(user_id, name=None):
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("SELECT id FROM users WHERE id = %s", (user_id,))
        if c.fetchone() is None:
            c.execute(
                "INSERT INTO users (id, name) VALUES (%s, %s)",
                (user_id, name or f"user_{user_id}"),
            )
            conn.commit()
    finally:
        conn.close()


# --- Дневник ---
def save_training(user_id, structured, raw_text):
    conn = get_conn()
    c = conn.cursor()
    try:
        date = structured.get("date") or datetime.now().date().isoformat()
        c.execute("""
            INSERT INTO diary (user_id, date, raw_text, structured_data)
            VALUES (%s, %s, %s, %s)
            RETURNING id
        """, (
            user_id,
            date,
            raw_text,
            json.dumps(structured, ensure_ascii=False),
        ))
        rec_id = c.fetchone()[0]
        conn.commit()
        return rec_id
    finally:
        conn.close()


def get_diary_record(record_id, user_id):
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("""
            SELECT id, date, raw_text, structured_data
            FROM diary WHERE id = %s AND user_id = %s
        """, (record_id, user_id))
        row = c.fetchone()
        if row:
            return {
                "id": row[0],
                "date": row[1],
                "raw_text": row[2],
                "structured": json.loads(row[3]) if row[3] else {},
            }
        return None
    finally:
        conn.close()


def update_diary_record(record_id, user_id, structured, raw_text):
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("""
            UPDATE diary
            SET raw_text = %s, structured_data = %s, date = %s
            WHERE id = %s AND user_id = %s
        """, (
            raw_text,
            json.dumps(structured, ensure_ascii=False),
            structured.get("date") or datetime.now().date().isoformat(),
            record_id,
            user_id,
        ))
        conn.commit()
    finally:
        conn.close()


def delete_diary_record(record_id, user_id):
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute(
            "DELETE FROM diary WHERE id = %s AND user_id = %s",
            (record_id, user_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_diary(user_id, limit=10):
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("""
            SELECT date, raw_text FROM diary
            WHERE user_id = %s
            ORDER BY id DESC LIMIT %s
        """, (user_id, limit))
        rows = c.fetchall()
    finally:
        conn.close()

    if not rows:
        return "📭 Дневник пуст."
    result = "📖 Последние записи:\n\n"
    for date, raw in rows:
        result += f"📅 {date}\n{raw}\n\n"
    return result


# --- План ---
def save_plan(user_id, items):
    conn = get_conn()
    c = conn.cursor()
    try:
        for item in items:
            c.execute("""
                INSERT INTO plan
                    (user_id, date, type, distance_km, duration_min,
                     elevation_m, description)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
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
    finally:
        conn.close()


def get_plan_record(record_id, user_id):
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("""
            SELECT id, date, type, description, status
            FROM plan WHERE id = %s AND user_id = %s
        """, (record_id, user_id))
        row = c.fetchone()
        if row:
            return {
                "id": row[0],
                "date": row[1],
                "type": row[2],
                "description": row[3],
                "status": row[4],
            }
        return None
    finally:
        conn.close()


def update_plan_record(record_id, user_id, fields):
    if not fields:
        return
    # Валидация имён колонок для защиты от SQL-инъекций
    allowed_columns = {
        "date", "type", "distance_km", "duration_min",
        "elevation_m", "description", "status",
    }
    safe_fields = {k: v for k, v in fields.items() if k in allowed_columns}
    if not safe_fields:
        return

    set_clause = ", ".join(f"{k} = %s" for k in safe_fields.keys())
    values = list(safe_fields.values()) + [record_id, user_id]

    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute(
            f"UPDATE plan SET {set_clause} WHERE id = %s AND user_id = %s",
            values,
        )
        conn.commit()
    finally:
        conn.close()


def mark_plan_skipped(record_id, user_id):
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute(
            "UPDATE plan SET status = 'skipped' WHERE id = %s AND user_id = %s",
            (record_id, user_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_plan_for_period(user_id, days):
    today = datetime.now().date()
    end = today + timedelta(days=days)
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("""
            SELECT date, type, distance_km, duration_min,
                   elevation_m, description, status
            FROM plan
            WHERE user_id = %s AND date >= %s AND date <= %s
            ORDER BY date
        """, (user_id, today.isoformat(), end.isoformat()))
        rows = c.fetchall()
    finally:
        conn.close()

    if not rows:
        return f"📭 На ближайшие {days} дней плана нет."
    result = f"📅 План на {days} дней:\n\n"
    for date, typ, dist, dur, elev, desc, status in rows:
        emoji = "✅" if status == "completed" else ("❌" if status == "skipped" else "⏳")
        result += f"{emoji} {date}: {typ}\n"
        if dist:
            result += f"   {dist} км\n"
        if elev:
            result += f"   +{elev} м\n"
        if desc:
            result += f"   {desc}\n"
        result += "\n"
    return result


# --- Pending ---
def save_pending(user_id, action_type, payload):
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("DELETE FROM pending WHERE user_id = %s", (user_id,))
        c.execute("""
            INSERT INTO pending (user_id, action_type, payload)
            VALUES (%s, %s, %s)
        """, (
            user_id,
            action_type,
            json.dumps(payload, ensure_ascii=False),
        ))
        conn.commit()
    finally:
        conn.close()


def get_pending(user_id):
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("""
            SELECT id, action_type, payload FROM pending
            WHERE user_id = %s
            ORDER BY id DESC LIMIT 1
        """, (user_id,))
        row = c.fetchone()
        if row:
            return {
                "id": row[0],
                "action_type": row[1],
                "payload": json.loads(row[2]),
            }
        return None
    finally:
        conn.close()


def clear_pending(user_id):
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("DELETE FROM pending WHERE user_id = %s", (user_id,))
        conn.commit()
    finally:
        conn.close()


# --- Last action ---
def set_last_action(user_id, action_type, record_id, context=None):
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("""
            INSERT INTO last_action (user_id, action_type, record_id, context)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                action_type = EXCLUDED.action_type,
                record_id = EXCLUDED.record_id,
                context = EXCLUDED.context,
                created_at = CURRENT_TIMESTAMP
        """, (user_id, action_type, record_id, context))
        conn.commit()
    finally:
        conn.close()


def get_last_action(user_id):
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("""
            SELECT action_type, record_id, context
            FROM last_action WHERE user_id = %s
        """, (user_id,))
        row = c.fetchone()
        if row:
            return {
                "action_type": row[0],
                "record_id": row[1],
                "context": row[2],
            }
        return None
    finally:
        conn.close()