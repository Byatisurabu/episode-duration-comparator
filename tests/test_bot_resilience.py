import time
from unittest.mock import AsyncMock, MagicMock

from telegram import Update
from telegram.error import NetworkError
from telegram.ext import ApplicationBuilder, CommandHandler, ConversationHandler

from bot.handlers import (
    callback_noop,
    callback_stale,
    cmd_cancel,
    cmd_restart,
    cmd_start,
    error_handler,
    register_handlers,
)
from bot.main import build_application
from bot.watchdog import Watchdog


def _update(user_id: int = 1):
    update = MagicMock(spec=Update)
    update.effective_user.id = user_id
    update.effective_chat.id = user_id
    update.effective_message.reply_text = AsyncMock()
    update.callback_query = None
    return update


def _context(**user_data):
    context = MagicMock()
    context.user_data = dict(user_data)
    context.bot.send_message = AsyncMock()
    context.application.bot_data = {}
    return context


class TestHandlerOrder:
    """Глобальные /start и /cancel раньше стояли перед ConversationHandler и перехватывали
    команду: пользователь видел "отменено", а диалог оставался в прежнем шаге."""

    def test_conversation_handler_goes_first(self):
        app = ApplicationBuilder().token("123:TEST").build()
        register_handlers(app)
        assert isinstance(app.handlers[0][0], ConversationHandler)

    def test_reset_commands_are_fallbacks(self):
        app = ApplicationBuilder().token("123:TEST").build()
        register_handlers(app)
        conv = app.handlers[0][0]
        commands = {c for h in conv.fallbacks if isinstance(h, CommandHandler) for c in h.commands}
        assert {"start", "menu", "cancel", "compare"} <= commands

    def test_error_handler_registered(self):
        app = ApplicationBuilder().token("123:TEST").build()
        register_handlers(app)
        assert error_handler in app.error_handlers


class TestSequentialUpdates:
    def test_updates_not_concurrent(self):
        """С concurrent_updates долгий обработчик возвращает своё состояние поверх END
        от /cancel того же пользователя — диалог "воскресает" в старом шаге."""
        assert build_application("123:TEST").concurrent_updates == 1


class TestResetCommands:
    async def test_start_ends_conversation(self):
        context = _context(baseline_url="x")
        assert await cmd_start(_update(), context) == ConversationHandler.END
        assert context.user_data == {}

    async def test_cancel_ends_conversation(self):
        context = _context(baseline_url="x")
        assert await cmd_cancel(_update(), context) == ConversationHandler.END
        assert context.user_data == {}


class TestCallbacks:
    async def test_stale_button_answers_and_shows_menu(self):
        update = _update()
        update.callback_query = MagicMock()
        update.callback_query.answer = AsyncMock()
        update.callback_query.message.reply_text = AsyncMock()
        context = _context(season_urls={1: {}})

        await callback_stale(update, context)

        update.callback_query.answer.assert_awaited_once()
        assert "reply_markup" in update.callback_query.message.reply_text.call_args.kwargs
        assert context.user_data == {}

    async def test_noop_answers(self):
        update = _update()
        update.callback_query = MagicMock()
        update.callback_query.answer = AsyncMock()
        await callback_noop(update, _context())
        update.callback_query.answer.assert_awaited_once()


class TestErrorHandler:
    async def test_notifies_user_and_resets(self):
        context = _context(baseline_url="x")
        context.error = RuntimeError("boom")
        await error_handler(_update(), context)
        context.bot.send_message.assert_awaited_once()
        assert context.user_data == {}

    async def test_polling_network_error_is_silent(self):
        context = _context()
        context.error = NetworkError("timeout")
        await error_handler(None, context)
        context.bot.send_message.assert_not_awaited()


class TestRestart:
    async def test_ignored_for_non_admin(self, monkeypatch):
        monkeypatch.setenv("ADMIN_IDS", "42")
        context = _context()
        await cmd_restart(_update(user_id=1), context)
        context.application.stop_running.assert_not_called()
        assert "restart_requested" not in context.application.bot_data

    async def test_admin_stops_polling(self, monkeypatch):
        monkeypatch.setenv("ADMIN_IDS", "7, 42")
        context = _context()
        watchdog = MagicMock()
        context.application.bot_data["watchdog"] = watchdog
        await cmd_restart(_update(user_id=42), context)
        context.application.stop_running.assert_called_once()
        watchdog.stop.assert_called_once()
        assert context.application.bot_data["restart_requested"] is True


class TestWatchdog:
    def _app(self, running=True, polling=True):
        app = MagicMock()
        app.running = running
        app.updater.running = polling
        return app

    def test_healthy(self):
        assert Watchdog(self._app()).check() is None

    def test_stalled_event_loop(self):
        wd = Watchdog(self._app(), stall_timeout=60)
        wd._last_beat = time.monotonic() - 61
        assert "event loop" in wd.check()

    def test_polling_stopped(self):
        assert "polling" in Watchdog(self._app(polling=False)).check()

    def test_not_started_yet(self):
        assert Watchdog(self._app(running=False, polling=False)).check() is None
