from unittest.mock import AsyncMock, MagicMock

from bot.handlers import WAIT_COMPARED_URL, WAIT_SEARCH, received_compared_url, received_search_input

KINOPOISK_URL = "https://www.kinopoisk.ru/series/1234567/"


def _update(text: str):
    update = MagicMock()
    update.message.text = text
    update.message.reply_text = AsyncMock()
    return update


def _context(**user_data):
    context = MagicMock()
    context.user_data = dict(user_data)
    return context


class TestServiceWithoutScraper:
    """Кинопоиск/Okko распознаются detect_service, но скрейпера для них нет —
    бот должен ответить сообщением, а не упасть с AttributeError на None."""

    async def test_direct_url(self):
        update = _update(KINOPOISK_URL)
        state = await received_search_input(update, _context())
        assert state == WAIT_SEARCH
        assert "Кинопоиск пока не поддерживается" in update.message.reply_text.call_args.args[0]

    async def test_compared_url(self):
        update = _update(KINOPOISK_URL)
        context = _context(need_baseline=False)
        state = await received_compared_url(update, context)
        assert state == WAIT_COMPARED_URL
        assert "Кинопоиск пока не поддерживается" in update.message.reply_text.call_args.args[0]
        assert "compared_url" not in context.user_data
