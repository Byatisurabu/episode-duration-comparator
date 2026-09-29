# bot/main.py
#
# Точка входа Telegram-бота.
# Запуск: python -m bot.main
#
# Текущий режим: Polling — работает локально без публичного URL.
# При переносе на VPS: заменить run_polling на run_webhook (см. комментарий ниже).

import logging
import os
import sys
import time

from telegram import BotCommand, BotCommandScopeChat, BotCommandScopeDefault
from telegram.error import TelegramError
from telegram.ext import ApplicationBuilder

from app.cache.sqlite_cache import EpisodeCache
from bot.handlers import ADMIN_COMMANDS, MENU_COMMANDS, admin_ids, register_handlers
from bot.watchdog import Watchdog

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def _setup_menu(application) -> None:
    """Команды для кнопки "Меню" в Telegram. Ошибка здесь не должна мешать старту бота."""
    bot = application.bot
    public = [BotCommand(name, desc) for name, desc in MENU_COMMANDS]
    try:
        await bot.set_my_commands(public, scope=BotCommandScopeDefault())
    except TelegramError as e:
        logger.warning("Не удалось установить меню команд: %s", e)
    admin = public + [BotCommand(name, desc) for name, desc in ADMIN_COMMANDS]
    for admin_id in admin_ids():
        try:
            await bot.set_my_commands(admin, scope=BotCommandScopeChat(admin_id))
        except TelegramError as e:
            # Например, админ ещё ни разу не писал боту
            logger.warning("Не удалось установить меню администратора %s: %s", admin_id, e)


async def _post_init(application) -> None:
    """Инициализируем кеш и сторожа внутри event loop PTB, а не до его запуска."""
    await EpisodeCache().init_db()
    await _setup_menu(application)
    application.bot_data["started_at"] = time.monotonic()
    watchdog = Watchdog(application)
    watchdog.start()
    application.bot_data["watchdog"] = watchdog


def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "BOT_TOKEN не задан. Добавь его в .env или в переменные окружения."
        )

    app = (
        ApplicationBuilder()
        .token(token)
        # Апдейты разных пользователей обрабатываются параллельно: долгое сравнение
        # "всех сезонов" у одного не блокирует бота для остальных
        .concurrent_updates(True)
        # Явные таймауты к Telegram API — чтобы запрос не висел бесконечно
        .connect_timeout(10)
        .read_timeout(20)
        .write_timeout(20)
        .pool_timeout(10)
        .get_updates_read_timeout(40)
        .post_init(_post_init)
        .build()
    )

    register_handlers(app)

    logger.info("Бот запущен в режиме polling")

    app.run_polling(
        timeout=30,                 # long polling; get_updates_read_timeout выше с запасом
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=True,  # игнорировать сообщения пока бот был выключен
    )

    if app.bot_data.get("restart_requested"):
        # /restart от администратора: заменяем процесс свежим (PID сохраняется,
        # поэтому работает и в Docker, и при локальном запуске)
        logger.info("Перезапуск по команде /restart")
        os.execv(sys.executable, [sys.executable, "-m", "bot.main"])

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
