import json
import logging
from datetime import datetime, timedelta
from llm import ask_llm, parse_json_safe
from db import get_conn

logger = logging.getLogger(__name__)


def extract_anchor(text):
    today = datetime.now().date()
    prompt = f"""
    Сегодня {today.isoformat()} ({today.strftime('%A')}).

    Извлеки из сообщения параметры, по которым можно найти тренировку.
    Верни ТОЛЬКО JSON:
    {{
        "date": "YYYY-MM-DD или null",
        "date_range": "day|week|month|null",
        "type": "тип или null",
        "distance_km": число или null,
        "elevation_m": число или null,
        "feeling": "тяжело|легко|нормально|null",
        "is_skip": true|false,
        "is_edit": true|false,
        "is_new": true|false,
        "keywords": ["ключевые слова"]
    }}

    Примеры:
    - "вчера пробежал 10 км" → date=вчера, distance_km=10, is_new=true
    - "интервалы во вторник были тяжело" → date=вторник, type=интервалы, feeling=тяжело
    - "на самом деле 12, не 10" → distance_km=12, is_edit=true
    - "пропустил вчерашнюю" → date=вчера, is_skip=true

    Сообщение: "{text}"
    """
    return parse_json_safe(ask_llm(prompt, "Ты извлекатель параметров. Отвечай только JSON."))


def find_candidates(user_id, anchor):
    if not anchor:
        return {"diary": [], "plan": []}

    conn = get_conn()
    c = conn.cursor()
    candidates = {"diary": [], "plan": []}

    # --- Дневник ---
    query = "SELECT id, date, raw_text, structured_data FROM diary WHERE user_id = ?"
    params = [user_id]

    if anchor.get("date"):
        query += " AND date = ?"
        params.append(anchor["date"])
    elif anchor.get("date_range") == "week":
        start = (datetime.now().date() - timedelta(days=7)).isoformat()
        query += " AND date >= ?"
        params.append(start)

    query += " ORDER BY date DESC LIMIT 10"
    c.execute(query, params)
    for row in c.fetchall():
        rec_id, date, raw, struct = row
        structured = json.loads(struct) if struct else {}
        if anchor.get("type") and anchor["type"].lower() not in (structured.get("type") or "").lower():
            continue
        candidates["diary"].append({
            "id": rec_id, "date": date, "raw_text": raw,
            "structured": structured, "source": "diary",
        })

    # --- План ---
    query = "SELECT id, date, type, description, status FROM plan WHERE user_id = ?"
    params = [user_id]

    if anchor.get("date"):
        query += " AND date = ?"
        params.append(anchor["date"])
    elif anchor.get("date_range") == "week":
        today = datetime.now().date()
        start = (today - timedelta(days=7)).isoformat()
        end = (today + timedelta(days=7)).isoformat()
        query += " AND date >= ? AND date <= ?"
        params.extend([start, end])

    query += " ORDER BY date DESC LIMIT 10"
    c.execute(query, params)
    for row in c.fetchall():
        rec_id, date, typ, desc, status = row
        if anchor.get("type") and anchor["type"].lower() not in (typ or "").lower():
            continue
        candidates["plan"].append({
            "id": rec_id, "date": date, "type": typ,
            "description": desc, "status": status, "source": "plan",
        })

    conn.close()
    return candidates


def decide_action(text, anchor, candidates, last_action):
    diary_short = [{"id": x["id"], "date": x["date"], "raw": x["raw_text"][:80]} for x in candidates["diary"]]
    plan_short = [{"id": x["id"], "date": x["date"], "type": x["type"], "desc": x["description"]}
                  for x in candidates["plan"]]

    prompt = f"""
    Пользователь написал: "{text}"

    Якорь: {json.dumps(anchor, ensure_ascii=False)}

    Кандидаты в дневнике: {json.dumps(diary_short, ensure_ascii=False)}
    Кандидаты в плане: {json.dumps(plan_short, ensure_ascii=False)}

    Последнее действие: {json.dumps(last_action, ensure_ascii=False) if last_action else 'нет'}

    Реши, что хочет пользователь. Верни ТОЛЬКО JSON:
    {{
        "action": "create_diary | update_diary | delete_diary | mark_skipped | create_plan | update_plan | delete_plan | answer_question",
        "target_id": ID или null,
        "target_source": "diary | plan | null",
        "confidence": 0.0-1.0,
        "reasoning": "кратко",
        "needs_clarification": true|false,
        "clarification_question": "вопрос или null"
    }}

    Правила:
    - Новая тренировка → create_diary
    - Правка ("на самом деле", "не 10 а 12") → update_diary
    - Пропуск ("не сделал", "пропустил") → mark_skipped
    - Работа с планом → update_plan / create_plan
    - Вопрос → answer_question
    - Если кандидатов несколько и непонятно → needs_clarification: true
    - Если уверенность < 0.6 → needs_clarification: true
    """
    return parse_json_safe(ask_llm(prompt, "Ты решатель действий. Отвечай только JSON."))