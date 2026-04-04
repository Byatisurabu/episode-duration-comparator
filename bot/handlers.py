# bot/handlers.py
#
# Вся логика диалога Telegram-бота.
# Состояние хранится в context.user_data — изолировано на уровне пользователя.

import asyncio
import logging
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import app.scrapers.factory  # noqa: F401 — side-effect: регистрирует скрейперы в ScraperFactory
from app.cache.sqlite_cache import EpisodeCache
from app.scrapers.base import ScraperFactory
from app.scrapers.cached import CachedScraper
from app.services.comparison import create_comparison_rows
from app.services.detector import detect_service
from app.services.search import search_amediateka, search_series
from bot.formatter import escape, format_comparison, split_message

# Общий кеш для бота (тот же data/cache.db что и у веб-приложения)
_cache = EpisodeCache()

logger = logging.getLogger(__name__)

# Состояния диалога
WAIT_BASELINE_URL = 1
WAIT_COMPARED_URL = 2
WAIT_SEASON = 3
WAIT_BASELINE_PICK = 4
WAIT_COMPARED_PICK = 5

# Префиксы для callback_data
CB_BASELINE = "bl:"
CB_COMPARED = "cp:"
CB_SEASON = "sn:"
CB_ALL_SEASONS = "sn:all"
CB_ACTION = "action:"
CB_NEW_COMPARE = "action:new_compare"


def _get_scraper(service_key: str):
    scraper = ScraperFactory.get_scraper(service_key)
    if scraper:
        return CachedScraper(scraper, _cache, service_key)
    return scraper


# ─── Команды ────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("🔍 Сравнить сериалы", callback_data="action:compare")]]
    await update.message.reply_text(
        "👋 Привет\\! Я сравниваю длительность серий на IMDb и Amediateka, "
        "чтобы находить купюры и цензуру\\.\n\n"
        "Нажми кнопку ниже или отправь /compare чтобы начать\\.",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ *Как пользоваться ботом*\n\n"
        "Этот бот сравнивает длительность серий одного и того же сериала "
        "на двух сервисах \\(IMDb и Amediateka\\), "
        "чтобы находить купюры и цензуру\\.\n\n"
        "*Команды:*\n"
        "/compare — начать сравнение\n"
        "/cancel — отменить текущее сравнение\n"
        "/help — эта справка\n\n"
        "*Процесс:*\n"
        "1\\. Введи название или URL сериала на первом сервисе \\(IMDb\\)\n"
        "2\\. Введи название или URL того же сериала на втором сервисе \\(Amediateka\\)\n"
        "3\\. Выбери сезон для сравнения\n\n"
        "Поддерживаемые сервисы: *IMDb*, *Amediateka*\\.",
        parse_mode="MarkdownV2",
    )


async def cmd_compare(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало диалога — запрашиваем IMDb URL или название."""
    context.user_data.clear()

    await update.message.reply_text(
        "🔍 *Шаг 1 из 3* — Введи название сериала для поиска или отправь URL напрямую\\.\n\n"
        "Примеры:\n"
        "`Breaking Bad` — поиск по названию\n"
        "`https://www\\.imdb\\.com/title/tt0903747/` — прямой URL\n\n"
        "/cancel — отменить",
        parse_mode="MarkdownV2",
        reply_markup=ReplyKeyboardRemove(),
    )
    return WAIT_BASELINE_URL


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Прерывает текущий диалог в любой момент."""
    context.user_data.clear()
    await update.message.reply_text(
        "❌ Сравнение отменено\\. Отправь /compare чтобы начать заново\\.",
        parse_mode="MarkdownV2",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


# ─── Callback: запуск из /start ──────────────────────────────────────────────

async def callback_action_compare(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Кнопка '🔍 Сравнить сериалы' из /start."""
    query = update.callback_query
    await query.answer()
    context.user_data.clear()

    await query.edit_message_text(
        "🔍 *Шаг 1 из 3* — Введи название сериала для поиска или отправь URL напрямую\\.\n\n"
        "Примеры:\n"
        "`Breaking Bad` — поиск по названию\n"
        "`https://www\\.imdb\\.com/title/tt0903747/` — прямой URL\n\n"
        "/cancel — отменить",
        parse_mode="MarkdownV2",
    )
    return WAIT_BASELINE_URL


# ─── Шаги диалога ───────────────────────────────────────────────────────────

async def received_baseline_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Шаг 1 — получили URL или название сериала для baseline."""
    text = update.message.text.strip()

    # Если это URL — обрабатываем напрямую
    if re.match(r"https?://", text, re.IGNORECASE):
        return await _handle_baseline_url(update, context, text)

    # Иначе — ищем по названию
    await update.message.reply_text("🔍 Ищу сериалы по названию...")

    results = await search_series(text)
    if not results:
        await update.message.reply_text(
            "❌ Ничего не найдено по запросу\\. Попробуй другое название или вставь URL\\.",
            parse_mode="MarkdownV2",
        )
        return WAIT_BASELINE_URL

    context.user_data["search_results"] = {r.series_id: r for r in results}

    keyboard = [
        [InlineKeyboardButton(
            text=f"{r.title}{' (' + r.year + ')' if r.year else ''}",
            callback_data=f"{CB_BASELINE}{r.series_id}",
        )]
        for r in results[:8]
    ]
    await update.message.reply_text(
        "📋 Выбери сериал из списка:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return WAIT_BASELINE_PICK


async def callback_baseline_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Пользователь выбрал сериал из инлайн-клавиатуры (baseline)."""
    query = update.callback_query
    await query.answer()

    series_id = query.data[len(CB_BASELINE):]
    results = context.user_data.get("search_results", {})
    result = results.get(series_id)

    if not result:
        await query.edit_message_text("❌ Ошибка выбора. Попробуй /compare заново.")
        return ConversationHandler.END

    await query.edit_message_text(f"✅ Выбрано: {result.title}{' (' + result.year + ')' if result.year else ''}")
    return await _handle_baseline_url(update, context, result.url, use_message=False)


async def _handle_baseline_url(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    url: str,
    use_message: bool = True,
) -> int:
    """Общая логика после получения URL для baseline."""
    service, name = detect_service(url)
    if not service:
        msg = (
            "❌ Не удалось определить сервис по этому URL\\.\n"
            "Поддерживаются: *IMDb*, *Amediateka*\\.\n\n"
            "Попробуй ещё раз или /cancel\\."
        )
        if use_message:
            await update.message.reply_text(msg, parse_mode="MarkdownV2")
        else:
            await update.callback_query.message.reply_text(msg, parse_mode="MarkdownV2")
        return WAIT_BASELINE_URL

    context.user_data["baseline_url"] = url
    context.user_data["baseline_service"] = service
    context.user_data["baseline_name"] = name

    # Preview: название + сезоны
    scraper = _get_scraper(service)
    title = await scraper.get_series_title(url)
    seasons = await scraper.get_seasons(url)
    seasons_str = f"{len(seasons)}" if seasons else "сезоны не найдены"
    if isinstance(title, str):
        preview = f"✅ Сериал *{escape(title)}*, найдено сезонов: {seasons_str}"
    else:
        preview = f"✅ Сериал *{escape(name)}*, найдено сезонов: {seasons_str}"

    reply_target = update.message if use_message else update.callback_query.message
    await reply_target.reply_text(
        f"{preview}\n\n"
        f"🔍 *Шаг 2 из 3* — Введи название или URL того же сериала на другом сервисе\\.\n\n"
        f"Например:\n"
        f"`Клиент всегда мёртв` — поиск по названию на Amediateka\n"
        f"`https://www\\.amediateka\\.ru/watch/series\\_11353\\_/` — прямой URL\n\n"
        f"/cancel — отменить",
        parse_mode="MarkdownV2",
    )
    return WAIT_COMPARED_URL


async def received_compared_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Шаг 2 — получили URL или название для compared."""
    text = update.message.text.strip()

    # Если это URL — обрабатываем напрямую
    if re.match(r"https?://", text, re.IGNORECASE):
        return await _handle_compared_url(update, context, text)

    # Поиск по названию через Amediateka API
    await update.message.reply_text("🔍 Ищу сериалы по названию...")

    results = await search_amediateka(text)
    if not results:
        await update.message.reply_text(
            "❌ Ничего не найдено\\. Попробуй другое название или вставь URL напрямую\\.",
            parse_mode="MarkdownV2",
        )
        return WAIT_COMPARED_URL

    context.user_data["search_results"] = {r.series_id: r for r in results}

    keyboard = [
        [InlineKeyboardButton(
            text=f"{r.title}{' (' + r.year + ')' if r.year else ''}",
            callback_data=f"{CB_COMPARED}{r.series_id}",
        )]
        for r in results[:8]
    ]
    await update.message.reply_text(
        "📋 Выбери сериал из списка:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return WAIT_COMPARED_PICK


async def callback_compared_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Пользователь выбрал сериал из инлайн-клавиатуры (compared)."""
    query = update.callback_query
    await query.answer()

    series_id = query.data[len(CB_COMPARED):]
    results = context.user_data.get("search_results", {})
    result = results.get(series_id)

    if not result:
        await query.edit_message_text("❌ Ошибка выбора. Попробуй /compare заново.")
        return ConversationHandler.END

    await query.edit_message_text(f"✅ Выбрано: {result.title}{' (' + result.year + ')' if result.year else ''}")
    return await _handle_compared_url(update, context, result.url, use_message=False)


async def _handle_compared_url(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    url: str,
    use_message: bool = True,
) -> int:
    """Общая логика после получения URL для compared."""
    service, name = detect_service(url)
    if not service:
        msg = (
            "❌ Не удалось определить сервис по этому URL\\.\n"
            "Попробуй ещё раз или /cancel\\."
        )
        if use_message:
            await update.message.reply_text(msg, parse_mode="MarkdownV2")
        else:
            await update.callback_query.message.reply_text(msg, parse_mode="MarkdownV2")
        return WAIT_COMPARED_URL

    context.user_data["compared_url"] = url
    context.user_data["compared_service"] = service
    context.user_data["compared_name"] = name

    reply_target = update.message if use_message else update.callback_query.message
    status_msg = await reply_target.reply_text("⏳ Ищу доступные сезоны...")

    baseline_scraper = _get_scraper(context.user_data["baseline_service"])
    compared_scraper = _get_scraper(service)

    baseline_seasons, compared_seasons = await asyncio.gather(
        baseline_scraper.get_seasons(context.user_data["baseline_url"]),
        compared_scraper.get_seasons(url),
        return_exceptions=True,
    )

    if isinstance(baseline_seasons, Exception):
        logger.error(f"get_seasons baseline error: {baseline_seasons}")
        baseline_seasons = []
    if isinstance(compared_seasons, Exception):
        logger.error(f"get_seasons compared error: {compared_seasons}")
        compared_seasons = []

    common_seasons = sorted(set(baseline_seasons) & set(compared_seasons))

    if not common_seasons:
        await status_msg.edit_text(
            "❌ Не удалось найти общие сезоны на обоих сервисах\\.\n"
            "Проверь URL и попробуй /compare заново\\.",
            parse_mode="MarkdownV2",
        )
        return ConversationHandler.END

    # Строим season_urls для всех сезонов
    all_seasons = sorted(set(baseline_seasons) | set(compared_seasons))
    baseline_season_urls, compared_season_urls = await asyncio.gather(
        asyncio.gather(*[
            baseline_scraper.get_season_url(context.user_data["baseline_url"], s)
            for s in all_seasons
        ]),
        asyncio.gather(*[
            compared_scraper.get_season_url(url, s)
            for s in all_seasons
        ]),
    )

    season_urls = {
        s: {
            "baseline": baseline_season_urls[i],
            "compared": compared_season_urls[i],
        }
        for i, s in enumerate(all_seasons)
    }

    context.user_data["season_urls"] = season_urls
    context.user_data["common_seasons"] = common_seasons

    # Формируем информацию об эксклюзивных сезонах
    only_baseline = sorted(set(baseline_seasons) - set(compared_seasons))
    only_compared = sorted(set(compared_seasons) - set(baseline_seasons))

    extra_lines = []
    if only_baseline:
        nums = ", ".join(str(s) for s in only_baseline)
        extra_lines.append(
            f"⚠️ Только на {escape(context.user_data['baseline_name'])}: сезоны {escape(nums)}"
        )
    if only_compared:
        nums = ", ".join(str(s) for s in only_compared)
        extra_lines.append(f"⚠️ Только на {escape(name)}: сезоны {escape(nums)}")

    extra = ("\n" + "\n".join(extra_lines)) if extra_lines else ""

    # InlineKeyboard с сезонами — максимум 4 в ряд
    season_buttons = [
        InlineKeyboardButton(f"Сезон {s}", callback_data=f"{CB_SEASON}{s}")
        for s in common_seasons
    ]
    rows = [season_buttons[i:i + 4] for i in range(0, len(season_buttons), 4)]

    if len(common_seasons) > 1:
        rows.append([InlineKeyboardButton("📊 Все сезоны", callback_data=CB_ALL_SEASONS)])

    await status_msg.edit_text(
        f"✅ Найдено общих сезонов: *{len(common_seasons)}*{extra}\n\n"
        f"🎬 *Шаг 3 из 3* — Выбери сезон для сравнения:",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(rows),
    )
    return WAIT_SEASON


# ─── Вспомогательные функции ─────────────────────────────────────────────────

async def _scrape_and_compare_season(message, context, season: int) -> bool:
    """Скрейпит оба сервиса и отправляет результат сравнения для одного сезона.
    Прогресс показывается через редактирование одного сообщения.
    Возвращает True при успехе."""
    season_urls = context.user_data.get("season_urls", {})
    urls = season_urls.get(season)
    if not urls:
        await message.reply_text(f"❌ Сезон {season} не найден\\.", parse_mode="MarkdownV2")
        return False

    baseline_name = escape(context.user_data["baseline_name"])
    compared_name = escape(context.user_data["compared_name"])

    baseline_scraper = _get_scraper(context.user_data["baseline_service"])
    compared_scraper = _get_scraper(context.user_data["compared_service"])

    # Одно сообщение с прогрессом
    status_msg = await message.reply_text(
        f"⏳ Сезон {season} — загружаю {baseline_name}\\.\\.\\.",
        parse_mode="MarkdownV2",
    )

    try:
        baseline_result = await baseline_scraper.scrape_episodes(urls["baseline"])
        ep_b = len(baseline_result.episodes) if baseline_result.episodes else 0

        await status_msg.edit_text(
            f"⏳ Сезон {season} — {baseline_name}: {ep_b} эп\\. ✅\n"
            f"Загружаю {compared_name}\\.\\.\\.",
            parse_mode="MarkdownV2",
        )

        compared_result = await compared_scraper.scrape_episodes(urls["compared"])
        ep_c = len(compared_result.episodes) if compared_result.episodes else 0

        await status_msg.edit_text(
            f"⏳ Сезон {season} — {baseline_name}: {ep_b} эп\\. ✅\n"
            f"{compared_name}: {ep_c} эп\\. ✅\n"
            f"Сравниваю и форматирую\\.\\.\\.",
            parse_mode="MarkdownV2",
        )

        rows = create_comparison_rows(baseline_result.episodes, compared_result.episodes)

        if not rows:
            await status_msg.edit_text(
                f"❌ Сезон {season} — не удалось получить данные об эпизодах\\.",
                parse_mode="MarkdownV2",
            )
            return False

        await status_msg.edit_text(
            f"✅ Сезон {season} — готово\\!",
            parse_mode="MarkdownV2",
        )

        text = format_comparison(
            baseline_name=context.user_data["baseline_name"],
            baseline_title=baseline_result.series_title or "—",
            compared_name=context.user_data["compared_name"],
            compared_title=compared_result.series_title or "—",
            rows=rows,
            season=season,
        )

        for part in split_message(text):
            await message.reply_text(part, parse_mode="MarkdownV2")

        return True

    except Exception as e:
        logger.error(f"_scrape_and_compare_season season={season}: {e}")
        await status_msg.edit_text(
            f"❌ Сезон {season} — ошибка при загрузке данных\\.",
            parse_mode="MarkdownV2",
        )
        return False


async def _offer_next_action(message, context, exclude_season: int | None = None) -> int:
    """Показывает кнопки: оставшиеся сезоны + 'Новое сравнение'."""
    common_seasons = context.user_data.get("common_seasons", [])
    other_seasons = [s for s in common_seasons if s != exclude_season]

    keyboard_rows = []

    if other_seasons:
        season_buttons = [
            InlineKeyboardButton(f"Сезон {s}", callback_data=f"{CB_SEASON}{s}")
            for s in other_seasons
        ]
        keyboard_rows = [season_buttons[i:i + 4] for i in range(0, len(season_buttons), 4)]
        if len(other_seasons) > 1:
            keyboard_rows.append([InlineKeyboardButton("📊 Все сезоны", callback_data=CB_ALL_SEASONS)])

    keyboard_rows.append([InlineKeyboardButton("🔄 Новое сравнение", callback_data=CB_NEW_COMPARE)])

    if other_seasons:
        await message.reply_text(
            "🔄 Сравнить другой сезон?",
            reply_markup=InlineKeyboardMarkup(keyboard_rows),
        )
    else:
        await message.reply_text(
            "✅ Все доступные сезоны сравнены\\!",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard_rows),
        )

    return WAIT_SEASON


# ─── Callback: выбор сезона ──────────────────────────────────────────────────

async def callback_season_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Пользователь выбрал конкретный сезон."""
    query = update.callback_query
    await query.answer()

    season = int(query.data[len(CB_SEASON):])
    await query.edit_message_reply_markup(reply_markup=None)  # убираем клавиатуру

    await _scrape_and_compare_season(query.message, context, season)
    return await _offer_next_action(query.message, context, exclude_season=season)


async def callback_all_seasons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Пользователь выбрал 'Все сезоны'."""
    query = update.callback_query
    await query.answer()

    common_seasons = context.user_data.get("common_seasons", [])
    await query.edit_message_text(
        f"📊 Сравниваю все {len(common_seasons)} сезона\\(ов\\) по очереди\\.\\.\\.",
        parse_mode="MarkdownV2",
    )

    for season in common_seasons:
        await _scrape_and_compare_season(query.message, context, season)

    keyboard = [[InlineKeyboardButton("🔄 Новое сравнение", callback_data=CB_NEW_COMPARE)]]
    await query.message.reply_text(
        "✅ Все сезоны сравнены\\!",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return WAIT_SEASON


async def callback_new_compare(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Пользователь хочет начать новое сравнение."""
    query = update.callback_query
    await query.answer()
    context.user_data.clear()

    await query.edit_message_text(
        "🔍 *Шаг 1 из 3* — Введи название сериала для поиска или отправь URL напрямую\\.\n\n"
        "Примеры:\n"
        "`Breaking Bad` — поиск по названию\n"
        "`https://www\\.imdb\\.com/title/tt0903747/` — прямой URL\n\n"
        "/cancel — отменить",
        parse_mode="MarkdownV2",
    )
    return WAIT_BASELINE_URL


# ─── Fallback ────────────────────────────────────────────────────────────────

async def fallback_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ответ на любое сообщение вне активного диалога."""
    keyboard = [[InlineKeyboardButton("🔍 Сравнить сериалы", callback_data="action:compare")]]
    await update.message.reply_text(
        "Отправь /compare чтобы начать сравнение\\.",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ─── Сборка ConversationHandler ──────────────────────────────────────────────

def build_conversation_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("compare", cmd_compare),
            CallbackQueryHandler(callback_action_compare, pattern=r"^action:compare$"),
        ],
        states={
            WAIT_BASELINE_URL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, received_baseline_url),
            ],
            WAIT_BASELINE_PICK: [
                CallbackQueryHandler(callback_baseline_pick, pattern=f"^{CB_BASELINE}"),
            ],
            WAIT_COMPARED_URL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, received_compared_url),
            ],
            WAIT_COMPARED_PICK: [
                CallbackQueryHandler(callback_compared_pick, pattern=f"^{CB_COMPARED}"),
            ],
            WAIT_SEASON: [
                CallbackQueryHandler(callback_season_pick, pattern=r"^sn:\d+$"),
                CallbackQueryHandler(callback_all_seasons, pattern=r"^sn:all$"),
                CallbackQueryHandler(callback_new_compare, pattern=r"^action:new_compare$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )
