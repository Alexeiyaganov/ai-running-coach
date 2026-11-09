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
        # --- Подписки и тарифы ---
    c.execute("""
        CREATE TABLE IF NOT EXISTS subscription_plans (
            id SERIAL PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            display_name TEXT,
            price_monthly_cents INTEGER DEFAULT 0,
            price_yearly_cents INTEGER DEFAULT 0,
            max_trainings_per_month INTEGER DEFAULT -1,
            max_llm_calls_per_day INTEGER DEFAULT -1,
            max_sources_connected INTEGER DEFAULT 0,
            auto_sync_enabled BOOLEAN DEFAULT FALSE,
            features JSONB DEFAULT '{}',
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            plan_id INTEGER NOT NULL REFERENCES subscription_plans(id),
            started_at TIMESTAMP NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            status TEXT DEFAULT 'active',
            auto_renew BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            subscription_id INTEGER,
            amount_cents INTEGER NOT NULL,
            currency TEXT DEFAULT 'RUB',
            payment_method TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            external_id TEXT,
            metadata JSONB DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (subscription_id) REFERENCES subscriptions(id) ON DELETE SET NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS usage_log (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            action_type TEXT NOT NULL,
            metadata JSONB DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    # Индексы для производительности
    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_subscriptions_user_status 
        ON subscriptions (user_id, status)
    """)
    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_usage_log_user_action 
        ON usage_log (user_id, action_type, created_at)
    """)
    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_payments_user 
        ON payments (user_id, created_at)
    """)
        # --- Расширяем diary для внешних источников и видов спорта ---
    c.execute("""
        ALTER TABLE diary
        ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'manual'
    """)

    c.execute("""
        ALTER TABLE diary
        ADD COLUMN IF NOT EXISTS external_id TEXT
    """)

    c.execute("""
        ALTER TABLE diary
        ADD COLUMN IF NOT EXISTS sport TEXT DEFAULT 'running'
    """)

    c.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_diary_user_source_external
        ON diary (user_id, source, external_id)
        WHERE external_id IS NOT NULL
    """)

    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_diary_user_sport_date
        ON diary (user_id, sport, date)
    """)

    # --- Справочник видов спорта ---
    c.execute("""
        CREATE TABLE IF NOT EXISTS sports (
            code TEXT PRIMARY KEY,
            name_ru TEXT NOT NULL,
            name_en TEXT NOT NULL,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # --- Выбранные пользователем виды спорта ---
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_sports (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            sport_code TEXT NOT NULL REFERENCES sports(code),
            is_primary BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, sport_code)
        )
    """)

    # --- OAuth-токены источников: Strava, Garmin, Coros и т.д. ---
    c.execute("""
        CREATE TABLE IF NOT EXISTS oauth_tokens (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            provider TEXT NOT NULL,
            access_token TEXT NOT NULL,
            refresh_token TEXT,
            expires_at TIMESTAMP,
            athlete_id TEXT,
            scope TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, provider)
        )
    """)

    # --- OAuth state для защиты авторизации ---
    c.execute("""
        CREATE TABLE IF NOT EXISTS oauth_states (
            state TEXT PRIMARY KEY,
            user_id BIGINT NOT NULL,
            provider TEXT NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_oauth_tokens_user_provider
        ON oauth_tokens (user_id, provider)
    """)

    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_oauth_states_expires
        ON oauth_states (expires_at)
    """)

    # Инициализируем тарифы по умолчанию
    init_default_plans()
        # --- Наполняем справочник видов спорта ---
    default_sports = [
        ("running", "Бег", "Running"),
        ("trail_running", "Трейлраннинг", "Trail Running"),
        ("cycling", "Велосипед", "Cycling"),
        ("swimming", "Плавание", "Swimming"),
        ("strength", "Силовые", "Strength Training"),
        ("hiking", "Хайкинг", "Hiking"),
        ("walking", "Ходьба", "Walking"),
        ("skiing", "Лыжи", "Skiing"),
        ("orienteering", "Ориентирование", "Orienteering"),
        ("fitness", "Фитнес", "Fitness"),
    ]

    for code, name_ru, name_en in default_sports:
        c.execute("""
            INSERT INTO sports (code, name_ru, name_en)
            VALUES (%s, %s, %s)
            ON CONFLICT (code) DO NOTHING
        """, (code, name_ru, name_en))
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

# --- Тарифы ---
def init_default_plans():
    """Инициализирует тарифы по умолчанию (вызывается один раз при старте)."""
    conn = get_conn()
    c = conn.cursor()
    try:
        # Проверяем, есть ли уже тарифы
        c.execute("SELECT COUNT(*) FROM subscription_plans")
        if c.fetchone()[0] > 0:
            return
        
        plans = [
            {
                "name": "free",
                "display_name": "Бесплатный",
                "price_monthly_cents": 0,
                "price_yearly_cents": 0,
                "max_trainings_per_month": 10,
                "max_llm_calls_per_day": 5,
                "max_sources_connected": 0,
                "auto_sync_enabled": False,
                "features": json.dumps({
                    "manual_entry": True,
                    "voice_input": True,
                    "diary": True,
                    "plan": True,
                    "sync": False,
                    "analytics": False,
                }),
            },
            {
                "name": "basic",
                "display_name": "Базовый",
                "price_monthly_cents": 29900,  # 299 руб
                "price_yearly_cents": 299000,  # 2990 руб/год
                "max_trainings_per_month": -1,  # неограниченно
                "max_llm_calls_per_day": 100,
                "max_sources_connected": 1,
                "auto_sync_enabled": False,
                "features": json.dumps({
                    "manual_entry": True,
                    "voice_input": True,
                    "diary": True,
                    "plan": True,
                    "sync": True,
                    "analytics": False,
                }),
            },
            {
                "name": "pro",
                "display_name": "Про",
                "price_monthly_cents": 59900,  # 599 руб
                "price_yearly_cents": 599000,  # 5990 руб/год
                "max_trainings_per_month": -1,
                "max_llm_calls_per_day": -1,  # неограниченно
                "max_sources_connected": 5,
                "auto_sync_enabled": True,
                "features": json.dumps({
                    "manual_entry": True,
                    "voice_input": True,
                    "diary": True,
                    "plan": True,
                    "sync": True,
                    "auto_sync": True,
                    "analytics": True,
                    "rag": True,
                    "priority_support": True,
                }),
            },
        ]
        
        for plan in plans:
            c.execute("""
                INSERT INTO subscription_plans 
                    (name, display_name, price_monthly_cents, price_yearly_cents,
                     max_trainings_per_month, max_llm_calls_per_day, 
                     max_sources_connected, auto_sync_enabled, features)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                plan["name"], plan["display_name"], 
                plan["price_monthly_cents"], plan["price_yearly_cents"],
                plan["max_trainings_per_month"], plan["max_llm_calls_per_day"],
                plan["max_sources_connected"], plan["auto_sync_enabled"],
                plan["features"],
            ))
        
        conn.commit()
        logger.info("Default subscription plans initialized")
    finally:
        conn.close()


def get_plan_by_name(plan_name):
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("""
            SELECT id, name, display_name, price_monthly_cents, price_yearly_cents,
                   max_trainings_per_month, max_llm_calls_per_day, 
                   max_sources_connected, auto_sync_enabled, features
            FROM subscription_plans
            WHERE name = %s AND is_active = TRUE
        """, (plan_name,))
        row = c.fetchone()
        if row:
            return {
                "id": row[0],
                "name": row[1],
                "display_name": row[2],
                "price_monthly_cents": row[3],
                "price_yearly_cents": row[4],
                "max_trainings_per_month": row[5],
                "max_llm_calls_per_day": row[6],
                "max_sources_connected": row[7],
                "auto_sync_enabled": row[8],
                "features": row[9],
            }
        return None
    finally:
        conn.close()


def get_all_plans():
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("""
            SELECT id, name, display_name, price_monthly_cents, price_yearly_cents,
                   max_trainings_per_month, max_llm_calls_per_day, 
                   max_sources_connected, auto_sync_enabled, features
            FROM subscription_plans
            WHERE is_active = TRUE
            ORDER BY price_monthly_cents
        """)
        rows = c.fetchall()
        return [
            {
                "id": r[0],
                "name": r[1],
                "display_name": r[2],
                "price_monthly_cents": r[3],
                "price_yearly_cents": r[4],
                "max_trainings_per_month": r[5],
                "max_llm_calls_per_day": r[6],
                "max_sources_connected": r[7],
                "auto_sync_enabled": r[8],
                "features": r[9],
            }
            for r in rows
        ]
    finally:
        conn.close()


# --- Подписки ---
def get_user_subscription(user_id):
    """Получает активную подписку пользователя."""
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("""
            SELECT s.id, s.plan_id, s.started_at, s.expires_at, s.status, s.auto_renew,
                   p.name, p.display_name, p.max_trainings_per_month, 
                   p.max_llm_calls_per_day, p.max_sources_connected, 
                   p.auto_sync_enabled, p.features
            FROM subscriptions s
            JOIN subscription_plans p ON s.plan_id = p.id
            WHERE s.user_id = %s AND s.status = 'active' 
                  AND s.expires_at > CURRENT_TIMESTAMP
            ORDER BY s.expires_at DESC
            LIMIT 1
        """, (user_id,))
        row = c.fetchone()
        if row:
            return {
                "subscription_id": row[0],
                "plan_id": row[1],
                "started_at": row[2],
                "expires_at": row[3],
                "status": row[4],
                "auto_renew": row[5],
                "plan_name": row[6],
                "plan_display_name": row[7],
                "max_trainings_per_month": row[8],
                "max_llm_calls_per_day": row[9],
                "max_sources_connected": row[10],
                "auto_sync_enabled": row[11],
                "features": row[12],
            }
        return None
    finally:
        conn.close()


def create_subscription(user_id, plan_id, duration_days=30):
    """Создаёт новую подписку."""
    conn = get_conn()
    c = conn.cursor()
    try:
        started_at = datetime.now()
        expires_at = started_at + timedelta(days=duration_days)
        
        c.execute("""
            INSERT INTO subscriptions 
                (user_id, plan_id, started_at, expires_at, status)
            VALUES (%s, %s, %s, %s, 'active')
            RETURNING id
        """, (user_id, plan_id, started_at, expires_at))
        
        sub_id = c.fetchone()[0]
        conn.commit()
        return sub_id
    finally:
        conn.close()


def cancel_subscription(user_id):
    """Отменяет активную подписку."""
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("""
            UPDATE subscriptions 
            SET status = 'cancelled', auto_renew = FALSE
            WHERE user_id = %s AND status = 'active'
        """, (user_id,))
        conn.commit()
    finally:
        conn.close()


# --- Платежи ---
def create_payment(user_id, subscription_id, amount_cents, currency, 
                   payment_method, external_id=None, metadata=None):
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("""
            INSERT INTO payments 
                (user_id, subscription_id, amount_cents, currency, 
                 payment_method, status, external_id, metadata)
            VALUES (%s, %s, %s, %s, %s, 'pending', %s, %s)
            RETURNING id
        """, (
            user_id, subscription_id, amount_cents, currency,
            payment_method, external_id,
            json.dumps(metadata or {}, ensure_ascii=False),
        ))
        payment_id = c.fetchone()[0]
        conn.commit()
        return payment_id
    finally:
        conn.close()


def update_payment_status(payment_id, status, metadata=None):
    conn = get_conn()
    c = conn.cursor()
    try:
        if metadata:
            c.execute("""
                UPDATE payments 
                SET status = %s, metadata = metadata || %s::jsonb
                WHERE id = %s
            """, (status, json.dumps(metadata, ensure_ascii=False), payment_id))
        else:
            c.execute("""
                UPDATE payments SET status = %s WHERE id = %s
            """, (status, payment_id))
        conn.commit()
    finally:
        conn.close()


# --- Usage tracking ---
def log_usage(user_id, action_type, metadata=None):
    """Логирует использование для отслеживания квот."""
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("""
            INSERT INTO usage_log (user_id, action_type, metadata)
            VALUES (%s, %s, %s)
        """, (
            user_id, 
            action_type, 
            json.dumps(metadata or {}, ensure_ascii=False),
        ))
        conn.commit()
    finally:
        conn.close()


def get_usage_count(user_id, action_type, since=None):
    """Получает количество действий за период."""
    conn = get_conn()
    c = conn.cursor()
    try:
        if since:
            c.execute("""
                SELECT COUNT(*) FROM usage_log
                WHERE user_id = %s AND action_type = %s AND created_at >= %s
            """, (user_id, action_type, since))
        else:
            c.execute("""
                SELECT COUNT(*) FROM usage_log
                WHERE user_id = %s AND action_type = %s
            """, (user_id, action_type))
        return c.fetchone()[0]
    finally:
        conn.close()

# --- Виды спорта ---
def get_sports():
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("""
            SELECT code, name_ru, name_en
            FROM sports
            WHERE is_active = TRUE
            ORDER BY name_ru
        """)
        rows = c.fetchall()
        return [
            {
                "code": r[0],
                "name_ru": r[1],
                "name_en": r[2],
            }
            for r in rows
        ]
    finally:
        conn.close()


def set_user_sports(user_id, sport_codes, primary_sport=None):
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("DELETE FROM user_sports WHERE user_id = %s", (user_id,))

        for code in sport_codes:
            c.execute("""
                INSERT INTO user_sports (user_id, sport_code, is_primary)
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id, sport_code) DO UPDATE SET
                    is_primary = EXCLUDED.is_primary
            """, (
                user_id,
                code,
                code == primary_sport,
            ))

        conn.commit()
    finally:
        conn.close()


def get_user_sports(user_id):
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("""
            SELECT s.code, s.name_ru, s.name_en, us.is_primary
            FROM user_sports us
            JOIN sports s ON s.code = us.sport_code
            WHERE us.user_id = %s
            ORDER BY us.is_primary DESC, s.name_ru
        """, (user_id,))
        rows = c.fetchall()
        return [
            {
                "code": r[0],
                "name_ru": r[1],
                "name_en": r[2],
                "is_primary": r[3],
            }
            for r in rows
        ]
    finally:
        conn.close()


# --- OAuth state ---
def create_oauth_state(user_id, provider, ttl_minutes=15):
    import secrets

    state = secrets.token_urlsafe(32)
    expires_at = datetime.now() + timedelta(minutes=ttl_minutes)

    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("""
            INSERT INTO oauth_states (state, user_id, provider, expires_at)
            VALUES (%s, %s, %s, %s)
        """, (state, user_id, provider, expires_at))
        conn.commit()
        return state
    finally:
        conn.close()


def pop_oauth_state(state, provider):
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("""
            SELECT user_id
            FROM oauth_states
            WHERE state = %s
              AND provider = %s
              AND expires_at > CURRENT_TIMESTAMP
        """, (state, provider))
        row = c.fetchone()

        c.execute("DELETE FROM oauth_states WHERE state = %s", (state,))
        conn.commit()

        return row[0] if row else None
    finally:
        conn.close()


# --- OAuth tokens ---
def save_oauth_token(
    user_id,
    provider,
    access_token,
    refresh_token=None,
    expires_at=None,
    athlete_id=None,
    scope=None,
):
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("""
            INSERT INTO oauth_tokens
                (user_id, provider, access_token, refresh_token,
                 expires_at, athlete_id, scope)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id, provider) DO UPDATE SET
                access_token = EXCLUDED.access_token,
                refresh_token = EXCLUDED.refresh_token,
                expires_at = EXCLUDED.expires_at,
                athlete_id = EXCLUDED.athlete_id,
                scope = EXCLUDED.scope,
                updated_at = CURRENT_TIMESTAMP
        """, (
            user_id,
            provider,
            access_token,
            refresh_token,
            expires_at,
            athlete_id,
            scope,
        ))
        conn.commit()
    finally:
        conn.close()


def get_oauth_token(user_id, provider):
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("""
            SELECT access_token, refresh_token, expires_at, athlete_id, scope
            FROM oauth_tokens
            WHERE user_id = %s AND provider = %s
        """, (user_id, provider))
        row = c.fetchone()

        if not row:
            return None

        return {
            "access_token": row[0],
            "refresh_token": row[1],
            "expires_at": row[2],
            "athlete_id": row[3],
            "scope": row[4],
        }
    finally:
        conn.close()


def delete_oauth_token(user_id, provider):
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("""
            DELETE FROM oauth_tokens
            WHERE user_id = %s AND provider = %s
        """, (user_id, provider))
        conn.commit()
    finally:
        conn.close()


# --- Импорт тренировок ---
def save_imported_activity(user_id, provider, external_id, sport, structured, raw_text):
    """
    Сохраняет импортную тренировку в diary.
    Если такая тренировка уже есть — обновляет её.
    Возвращает: created / updated.
    """
    conn = get_conn()
    c = conn.cursor()
    try:
        date = structured.get("date") or datetime.now().date().isoformat()

        c.execute("""
            SELECT id
            FROM diary
            WHERE user_id = %s
              AND source = %s
              AND external_id = %s
        """, (user_id, provider, external_id))

        existing = c.fetchone()

        if existing:
            record_id = existing[0]
            c.execute("""
                UPDATE diary
                SET date = %s,
                    raw_text = %s,
                    structured_data = %s,
                    sport = %s
                WHERE id = %s AND user_id = %s
            """, (
                date,
                raw_text,
                json.dumps(structured, ensure_ascii=False),
                sport,
                record_id,
                user_id,
            ))
            conn.commit()
            return "updated"

        c.execute("""
            INSERT INTO diary
                (user_id, date, raw_text, structured_data, source, external_id, sport)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            user_id,
            date,
            raw_text,
            json.dumps(structured, ensure_ascii=False),
            provider,
            external_id,
            sport,
        ))
        conn.commit()
        return "created"
    finally:
        conn.close()