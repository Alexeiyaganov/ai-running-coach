import json
import sqlite3
from datetime import datetime, timedelta
from config import DB_PATH


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

    c.execute("""
        CREATE TABLE IF NOT EXISTS last_action (
            user_id INTEGER PRIMARY KEY,
            action_type TEXT,
            record_id INTEGER,
            context TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


# --- Пользователи ---
def ensure_user(user_id, name=None):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT id FROM users WHERE id = ?", (user_id,))
    if c.fetchone() is None:
        c.execute("INSERT INTO users (id, name) VALUES (?, ?)",
                  (user_id, name or f"user_{user_id}"))
        conn.commit()
    conn.close()


# --- Дневник ---
def save_training(user_id, structured, raw_text):
    conn = get_conn()
    c = conn.cursor()
    date = structured.get("date") or datetime.now().date().isoformat()
    c.execute("""
        INSERT INTO diary (user_id, date, raw_text, structured_data)
        VALUES (?, ?, ?, ?)
    """, (user_id, date, raw_text, json.dumps(structured, ensure_ascii=False)))
    conn.commit()
    rec_id = c.lastrowid
    conn.close()
    return rec_id


def get_diary_record(record_id, user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT id, date, raw_text, structured_data FROM diary WHERE id = ? AND user_id = ?",
              (record_id, user_id))
    row = c.fetchone()
    conn.close()
    if row:
        return {"id": row[0], "date": row[1], "raw_text": row[2],
                "structured": json.loads(row[3]) if row[3] else {}}
    return None


def update_diary_record(record_id, user_id, structured, raw_text):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        UPDATE diary
        SET raw_text = ?, structured_data = ?, date = ?
        WHERE id = ? AND user_id = ?
    """, (raw_text, json.dumps(structured, ensure_ascii=False),
          structured.get("date") or datetime.now().date().isoformat(),
          record_id, user_id))
    conn.commit()
    conn.close()


def delete_diary_record(record_id, user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM diary WHERE id = ? AND user_id = ?", (record_id, user_id))
    conn.commit()
    conn.close()


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


def get_plan_record(record_id, user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT id, date, type, description, status FROM plan WHERE id = ? AND user_id = ?",
              (record_id, user_id))
    row = c.fetchone()
    conn.close()
    if row:
        return {"id": row[0], "date": row[1], "type": row[2],
                "description": row[3], "status": row[4]}
    return None


def update_plan_record(record_id, user_id, fields):
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields.keys())
    values = list(fields.values()) + [record_id, user_id]
    conn = get_conn()
    c = conn.cursor()
    c.execute(f"UPDATE plan SET {set_clause} WHERE id = ? AND user_id = ?", values)
    conn.commit()
    conn.close()


def mark_plan_skipped(record_id, user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE plan SET status = 'skipped' WHERE id = ? AND user_id = ?",
              (record_id, user_id))
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
        emoji = "✅" if status == "completed" else ("❌" if status == "skipped" else "⏳")
        result += f"{emoji} {date}: {typ}\n"
        if dist: result += f"   {dist} км\n"
        if elev: result += f"   +{elev} м\n"
        if desc: result += f"   {desc}\n"
        result += "\n"
    return result


# --- Pending ---
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


# --- Last action ---
def set_last_action(user_id, action_type, record_id, context=None):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO last_action (user_id, action_type, record_id, context)
        VALUES (?, ?, ?, ?)
    """, (user_id, action_type, record_id, context))
    conn.commit()
    conn.close()


def get_last_action(user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT action_type, record_id, context FROM last_action WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return {"action_type": row[0], "record_id": row[1], "context": row[2]}
    return None