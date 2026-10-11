import json
import logging
from config import openrouter_client, LLM_MODEL

logger = logging.getLogger(__name__)


def ask_llm(prompt, system_prompt=None):
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    try:
        response = openrouter_client.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            temperature=0.7,
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"LLM error: {e}")
        return f"😔 Ошибка модели: {e}"


def parse_json_safe(raw):
    if not raw:
        return None
    raw = raw.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(raw)
    except Exception as e:
        logger.error(f"JSON parse error: {e}; raw={raw[:500]}")
        return None