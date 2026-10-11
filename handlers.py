import json
import logging
from datetime import datetime, timedelta

from telebot.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
)

from db import (
    ensure_user, save_training, get_diary_record, update_diary_record,
    delete_diary_record, get_diary, save_plan, get_plan_record,
    update_plan_record, mark_plan_skipped, get_plan_for_period,
    save_pending, get_pending, clear_pending,
    set_last_action, get_last_action,
)
from llm import ask_llm, parse_json_safe
from ai import extract_anchor, find_candidates, decide_action
from voice import transcribe_voice

logger = logging.getLogger(__name__)

# --- URL вашего Space ---
SPACE_URL = "https://skienbear-ai-running-coach.hf.space"


def register_handlers(bot):

    def confirmation_keyboard():
        kb = InlineKeyboardMarkup()
        kb.row(
            InlineKeyboardButton("✅ Записать", callback_data="confirm"),
            InlineKeyboardButton("❌ Отмена", callback_data="cancel"),
        )
        return kb

    def open_app_keyboard():
        """Кнопка для открытия Mini App."""
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton(
            "📊 Открыть план и дневник",
            web_app=WebAppInfo(url=SPACE_URL),
        ))
        return kb

    # --- /start ---
    @bot.message_handler(commands=["start"])
    def send_welcome(message):
        ensure_user(message.from_user.id, message.from_user.first_name)
        bot.reply_to(message,
            f"🏃 Привет, {message.from_user.first_name}!\n\n"
            "Что я умею:\n"
            "• Записать тренировку — «вчера пробежал 10 км»\n"
            "• Поправить запись — «на самом деле 12 км»\n"
            "• Составить план — «составь план на месяц»\n\n"
            "Открой мини-приложение — там план, дневник и графики.",
            reply_markup=open_app_keyboard(),
        )

    # --- /app ---
    @bot.message_handler(commands=["app"])
    def cmd_app(message):
        ensure_user(message.from_user.id, message.from_user.first_name)
        bot.reply_to(message,
            "📊 Открой мини-приложение:",
            reply_markup=open_app_keyboard(),
        )

    # --- /today /week /month /diary (текстовые сводки) ---
    @bot.message_handler(commands=["today"])
    def cmd_today(message):
        ensure_user(message.from_user.id, message.from_user.first_name)
        bot.reply_to(message, get_plan_for_period(message.from_user.id, 1))

    @bot.message_handler(commands=["week"])
    def cmd_week(message):
        ensure_user(message.from_user.id, message.from_user.first_name)
        bot.reply_to(message, get_plan_for_period(message.from_user.id, 7))

    @bot.message_handler(commands=["month"])
    def cmd_month(message):
        ensure_user(message.from_user.id, message.from_user.first_name)
        bot.reply_to(message, get_plan_for_period(message.from_user.id, 30))

    @bot.message_handler(commands=["diary"])
    def cmd_diary(message):
        ensure_user(message.from_user.id, message.from_user.first_name)
        bot.reply_to(message, get_diary(message.from_user.id))

    # --- Голос ---
    @bot.message_handler(content_types=["voice"])
    def handle_voice(message):
        ensure_user(message.from_user.id, message.from_user.first_name)
        bot.reply_to(message, "🎧 Слушаю...")
        text = transcribe_voice(bot, message.voice.file_id, message.message_id)
        if not text:
            bot.reply_to(message, "😔 Ошибка обработки голоса.")
            return
        bot.reply_to(message, f"📝 Распознал: {text}")
        set_last_action(message.from_user.id, "last_voice", 0, text)
        handle_incoming_text(message, text)

    # --- Текст ---
    @bot.message_handler(func=lambda m: True)
    def handle_text(message):
        ensure_user(message.from_user.id, message.from_user.first_name)
        handle_incoming_text(message, message.text)

    # --- Основная логика ---
    def handle_incoming_text(message, text):
        user_id = message.from_user.id
        user_lower = text.lower().strip()

        # Быстрые команды
        if any(w in user_lower for w in [
            "покажи дневник", "мой дневник", "дневник тренировок",
            "покажи записи", "история тренировок",
        ]):
            bot.reply_to(message, get_diary(user_id))
            return

        if any(w in user_lower for w in [
            "покажи план", "мой план", "покажи календарь",
            "расписание тренировок", "что по плану",
        ]):
            bot.reply_to(message, get_plan_for_period(user_id, 14))
            return

        # Служебные фразы
        service_phrases = {
            "запиши в дневник", "запиши это", "запиши",
            "подтверждаю", "подтвердить", "ок", "окей",
            "да", "yes", "отмена", "отмени", "нет",
        }
        if user_lower in service_phrases:
            pending = get_pending(user_id)
            if pending:
                bot.reply_to(message,
                    "🤔 Есть незавершённое действие. Используй кнопки под предыдущим сообщением.")
            else:
                bot.reply_to(message, "🤔 Нечего записывать. Опиши тренировку.")
            return

        # Якорь → кандидаты → решение
        anchor = extract_anchor(text)
        logger.info(f"Anchor: {anchor}")

        candidates = find_candidates(user_id, anchor) if anchor else {"diary": [], "plan": []}
        logger.info(f"Candidates: diary={len(candidates['diary'])}, plan={len(candidates['plan'])}")

        last_action = get_last_action(user_id)
        decision = decide_action(text, anchor, candidates, last_action)
        logger.info(f"Decision: {decision}")

        if not decision:
            bot.reply_to(message, "🤔 Не понял. Переформулируй?")
            return

        action = decision.get("action")
        confidence = decision.get("confidence", 0)

        if decision.get("needs_clarification") or confidence < 0.6:
            question = decision.get("clarification_question") or "Уточни, о какой тренировке речь?"
            all_candidates = candidates["diary"] + candidates["plan"]
            if all_candidates:
                kb = InlineKeyboardMarkup()
                for cand in all_candidates[:5]:
                    if cand["source"] == "diary":
                        label = f"📝 {cand['date']} {cand['raw_text'][:30]}"
                    else:
                        label = f"📅 {cand['date']} {cand.get('type', '')}"
                    kb.add(InlineKeyboardButton(
                        label,
                        callback_data=f"pick_{cand['source']}_{cand['id']}"
                    ))
                bot.reply_to(message, f"🤔 {question}", reply_markup=kb)
            else:
                bot.reply_to(message, f"🤔 {question}")
            return

        if action == "create_diary":
            do_create_diary(message, text)
        elif action == "update_diary":
            do_update_diary(message, text, decision.get("target_id"))
        elif action == "delete_diary":
            do_delete_diary(message, decision.get("target_id"))
        elif action == "mark_skipped":
            do_mark_skipped(message, decision.get("target_id"))
        elif action == "create_plan":
            do_create_plan(message, text)
        elif action == "update_plan":
            do_update_plan(message, text, decision.get("target_id"))
        elif action == "answer_question":
            reply = ask_llm(text, "Ты AI-тренер по бегу, трейлу и ориентированию. Отвечай коротко и по делу.")
            bot.reply_to(message, reply)
        else:
            bot.reply_to(message, "🤔 Не понял. Переформулируй?")

    # --- Действия ---
    def do_create_diary(message, text):
        prompt = f"""
        Извлеки информацию о тренировке, верни ТОЛЬКО JSON:
        {{"date": "YYYY-MM-DD или null", "type": "тип или null", "distance_km": число или null, "duration_min": число или null, "elevation_m": число или null, "feeling": "оценка или null", "notes": "заметки или null"}}
        Сегодня {datetime.now().date().isoformat()}.
        Текст: "{text}"
        """
        structured = parse_json_safe(ask_llm(prompt, "Ты парсер спортивных данных. Только JSON."))
        if not structured:
            bot.reply_to(message, "😔 Не разобрал тренировку.")
            return
        save_pending(message.from_user.id, "log_training",
                     {"structured": structured, "raw_text": text})
        preview = json.dumps(structured, ensure_ascii=False, indent=2)
        bot.reply_to(message, f"📝 Новая тренировка:\n{preview}\n\nЗаписать?",
                     reply_markup=confirmation_keyboard())

    def do_update_diary(message, text, target_id):
        if not target_id:
            bot.reply_to(message, "🤔 Не нашёл, какую запись править.")
            return
        rec = get_diary_record(target_id, message.from_user.id)
        if not rec:
            bot.reply_to(message, "😔 Запись не найдена.")
            return
        original = rec["structured"]
        update_prompt = f"""
        Есть запись о тренировке: {json.dumps(original, ensure_ascii=False)}
        Пользователь правит: "{text}"
        Верни ТОЛЬКО JSON с полями, которые надо обновить:
        {{"date": ..., "type": ..., "distance_km": ..., "duration_min": ..., "elevation_m": ..., "feeling": ..., "notes": ...}}
        """
        updated = parse_json_safe(ask_llm(update_prompt, "Только JSON.")) or {}
        merged = {k: (updated.get(k) if updated.get(k) is not None else v)
                  for k, v in original.items()}
        save_pending(message.from_user.id, "edit_training",
                     {"record_id": target_id, "structured": merged, "raw_text": rec["raw_text"]})
        preview = json.dumps(merged, ensure_ascii=False, indent=2)
        bot.reply_to(message, f"✏️ Обновлю запись от {rec['date']}:\n{preview}\n\nПодтвердить?",
                     reply_markup=confirmation_keyboard())

    def do_delete_diary(message, target_id):
        if not target_id:
            bot.reply_to(message, "🤔 Не нашёл, что удалять.")
            return
        save_pending(message.from_user.id, "delete_training", {"record_id": target_id})
        bot.reply_to(message, f"🗑 Удалить запись (id={target_id})?",
                     reply_markup=confirmation_keyboard())

    def do_mark_skipped(message, target_id):
        if not target_id:
            bot.reply_to(message, "🤔 Не нашёл тренировку в плане.")
            return
        save_pending(message.from_user.id, "mark_plan_skipped", {"record_id": target_id})
        bot.reply_to(message, "❌ Отметить тренировку как пропущенную?",
                     reply_markup=confirmation_keyboard())

    def do_create_plan(message, text):
        bot.reply_to(message, "🤔 Составляю план...")
        prompt = f"""
        Ты тренер по бегу, трейлу и ориентированию.
        Верни ТОЛЬКО JSON-массив:
        [{{"date": "YYYY-MM-DD", "type": "тип", "distance_km": число, "duration_min": число, "elevation_m": число, "description": "описание"}}]
        Сегодня {datetime.now().date().isoformat()}.
        Запрос: "{text}"
        """
        raw = ask_llm(prompt, "Ты тренер. Только JSON-массив.")
        items = parse_json_safe(raw)
        if not items:
            bot.reply_to(message, f"😔 Не разобрал план:\n{raw}")
            return
        save_plan(message.from_user.id, items)
        summary = "📋 План сохранён:\n\n"
        for item in items:
            summary += f"📅 {item.get('date')}: {item.get('type')} — {item.get('description')}\n"
        bot.reply_to(message, summary,
                     reply_markup=open_app_keyboard())

    def do_update_plan(message, text, target_id):
        if not target_id:
            bot.reply_to(message, "🤔 Не нашёл тренировку в плане.")
            return
        update_prompt = f"""
        Пользователь правит тренировку в плане: "{text}"
        Верни ТОЛЬКО JSON с полями для обновления:
        {{"date": "...", "type": "...", "description": "...", "distance_km": ..., "elevation_m": ...}}
        """
        fields = parse_json_safe(ask_llm(update_prompt, "Только JSON.")) or {}
        fields = {k: v for k, v in fields.items() if v is not None}
        if not fields:
            bot.reply_to(message, "😔 Не разобрал правку.")
            return
        save_pending(message.from_user.id, "edit_plan",
                     {"record_id": target_id, "fields": fields})
        bot.reply_to(message,
                     f"✏️ Обновлю план:\n{json.dumps(fields, ensure_ascii=False, indent=2)}\n\nПодтвердить?",
                     reply_markup=confirmation_keyboard())

    # --- Callback ---
    @bot.callback_query_handler(func=lambda call: True)
    def handle_callback(call):
        user_id = call.from_user.id
        ensure_user(user_id, call.from_user.first_name)

        if call.data == "show_plan":
            bot.answer_callback_query(call.id)
            bot.send_message(call.message.chat.id, get_plan_for_period(user_id, 14))
            return

        if call.data == "show_diary":
            bot.answer_callback_query(call.id)
            bot.send_message(call.message.chat.id, get_diary(user_id))
            return

        if call.data == "confirm":
            pending = get_pending(user_id)
            if not pending:
                bot.answer_callback_query(call.id, "Нечего подтверждать")
                return
            action = pending["action_type"]
            payload = pending["payload"]
            try:
                if action == "log_training":
                    rec_id = save_training(user_id, payload["structured"], payload["raw_text"])
                    set_last_action(user_id, "log_training", rec_id, payload["raw_text"])
                    msg = "✅ Записал в дневник."
                elif action == "edit_training":
                    update_diary_record(payload["record_id"], user_id,
                                        payload["structured"], payload["raw_text"])
                    set_last_action(user_id, "log_training",
                                    payload["record_id"], payload["raw_text"])
                    msg = "✏️ Обновил запись."
                elif action == "delete_training":
                    delete_diary_record(payload["record_id"], user_id)
                    msg = "🗑 Удалил запись."
                elif action == "mark_plan_skipped":
                    mark_plan_skipped(payload["record_id"], user_id)
                    msg = "❌ Отметил как пропущенную."
                elif action == "edit_plan":
                    update_plan_record(payload["record_id"], user_id, payload["fields"])
                    msg = "✏️ Обновил план."
                else:
                    msg = "🤔 Неизвестное действие."
                clear_pending(user_id)
                bot.edit_message_text(msg, call.message.chat.id, call.message.message_id)
            except Exception as e:
                logger.error(f"Confirm error: {e}")
                bot.edit_message_text(f"😔 Ошибка: {e}",
                                      call.message.chat.id, call.message.message_id)

        elif call.data == "cancel":
            clear_pending(user_id)
            bot.edit_message_text("❌ Отменено.",
                                  call.message.chat.id, call.message.message_id)

        elif call.data.startswith("pick_"):
            bot.answer_callback_query(call.id)
            bot.send_message(call.message.chat.id,
                "Напиши, что сделать с этой тренировкой: «удали», «поправь на 12 км» и т.д.")