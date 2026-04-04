# bot/main.py
#
# Точка входа Telegram-бота.
# Запуск: python -m bot.main
#
# Текущий режим: Polling — работает локально без публичного URL.
# При переносе на VPS: заменить run_polling на run_webhook (см. комментарий ниже).

import asyncio
import logging
import os

from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

from app.cache.sqlite_cache import EpisodeCache
from bot.handlers import (
    cmd_start,
    cmd_cancel,
    fallback_unknown,
    build_conversation_handler,
)

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "BOT_TOKEN не задан. Добавь его в .env или в переменные окружения."
        )

    # Инициализируем кеш (создаём таблицу если не существует)
    asyncio.run(EpisodeCache().init_db())

    app = ApplicationBuilder().token(token).build()

    # Глобальные команды — доступны в любой момент, включая середину диалога
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("cancel", cmd_cancel))

    # Диалог сравнения
    app.add_handler(build_conversation_handler())

    # Fallback — любое сообщение вне диалога
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback_unknown))

    logger.info("Бот запущен в режиме polling")

    app.run_polling(
        allowed_updates=["message"],
        drop_pending_updates=True,  # игнорировать сообщения пока бот был выключен
    )

    # ── При переносе на VPS: заменить run_polling на run_webhook ──────────────
    #
    # app.run_webhook(
    #     listen="0.0.0.0",
    #     port=8443,
    #     webhook_url=f"https://{os.environ['DOMAIN']}/webhook/{token}",
    #     secret_token=os.environ.get("WEBHOOK_SECRET", ""),
    # )
    #
    # И добавить в docker-compose.yml для bot-сервиса:
    #   ports:
    #     - "8443:8443"
    #   environment:
    #     - DOMAIN=yourdomain.com
    # ─────────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    main()