import logging
from threading import Thread

import spaces
import gradio as gr
import telebot

from config import TELEGRAM_TOKEN
from db import init_db
from handlers import register_handlers


@spaces.GPU
def dummy_gpu_check():
    return "ok"


logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


def main():
    init_db()

    bot = telebot.TeleBot(TELEGRAM_TOKEN, threaded=False)
    register_handlers(bot)

    def run_bot():
        logger.info("Telegram бот запущен...")
        bot.infinity_polling(timeout=10, long_polling_timeout=5)

    t = Thread(target=run_bot)
    t.start()

    def hello(name):
        return f"AI Running Coach работает. Привет, {name}!"

    iface = gr.Interface(
        fn=hello,
        inputs="text",
        outputs="text",
        title="AI Running Coach",
        description="Telegram-бот работает в фоне.",
    )
    iface.launch(server_name="0.0.0.0", server_port=7860)


if __name__ == "__main__":
    main()