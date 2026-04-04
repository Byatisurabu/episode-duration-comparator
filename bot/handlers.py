# bot/handlers.py
#
# Вся логика диалога Telegram-бота.
# Состояние хранится в context.user_data — изолировано на уровне пользователя.

import asyncio
import logging
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from app.cache.sqlite_cache import EpisodeCache
from app.scrapers.base import ScraperFactory
from app.scrapers.cached import CachedScraper
from app.services.comparison import create_comparison_rows
from app.services.detector import detect_service
from app.services.search import search_series
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


def _get_scraper(service_key: str):
    scraper = ScraperFactory.get_scraper(service_key)
    if scraper:
        return CachedScraper(scraper, _cache, service_key)
    return scraper


# ─── Команды ────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет\\! Я сравниваю длительность серий на IMDb и Amediateka, "
        "чтобы находить купюры и цензуру\\.\n\n"
        "Отправь /compare чтобы начать\\.",
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


# ─── Шаги диалога ───────────────────────────────────────────────────────────

async def received_baseline_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Шаг 1 — получили URL или название сериала для baseline."""
    text = update.message.text.strip()

    # Если это URL — обрабатываем напрямую
    if re.match(r"https?://", text, re.IGNORECASE):
        return await _handle_baseline_url(update, context, text)

    # Иначе — ищем по названию
    await update.message.reply_text("🔍 Ищу сериалы по названию...", parse_mode=None)

    results = await search_series(text)
    if not results:
        await update.message.reply_text(
            "❌ Ничего не найдено по запросу\\. Попробуй другое название или вставь URL\\.",
            parse_mode="MarkdownV2",
        )
        return WAIT_BASELINE_URL

    # Сохраняем результаты поиска для выбора
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
        f"`https://www\\.amediateka\\.ru/watch/series\\_11353\\_klient\\-vsegda\\-mertv/`\n\n"
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

    # Поиск по названию (только IMDb — Amediateka без публичного API)
    await update.message.reply_text("🔍 Ищу сериалы по названию...", parse_mode=None)

    results = await search_series(text)
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
    await reply_target.reply_text(
        "⏳ Ищу доступные сезоны\\.\\.\\.",
        parse_mode="MarkdownV2",
    )

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
        await reply_target.reply_text(
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
        extra_lines.append(
            f"⚠️ Только на {escape(name)}: сезоны {escape(nums)}"
        )

    extra = ("\n" + "\n".join(extra_lines)) if extra_lines else ""

    # Клавиатура с общими сезонами
    keyboard = [[f"Сезон {s}" for s in common_seasons]]
    markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)

    await reply_target.reply_text(
        f"✅ Найдено общих сезонов: *{len(common_seasons)}*{extra}\n\n"
        f"🎬 *Шаг 3 из 3* — Выбери сезон для сравнения:",
        parse_mode="MarkdownV2",
        reply_markup=markup,
    )
    return WAIT_SEASON


async def received_season(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Шаг 3 — получили выбор сезона, запускаем сравнение."""
    text = update.message.text.strip()

    if update.message.text == "Новое сравнение":
        await update.message.reply_text(
            "🔄 Выбери базовый сериал для нового сравнения:",
            reply_markup=ReplyKeyboardRemove(),
        )
        return WAIT_BASELINE_URL

    match = re.search(r"\d+", text)
    if not match:
        await update.message.reply_text(
            "❌ Не понял выбор\\. Нажми одну из кнопок выше\\.",
            parse_mode="MarkdownV2",
        )
        return WAIT_SEASON

    season = int(match.group())
    season_urls = context.user_data.get("season_urls", {})

    if season not in season_urls:
        await update.message.reply_text(
            "❌ Такой сезон не найден\\. Попробуй /compare заново\\.",
            parse_mode="MarkdownV2",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ConversationHandler.END

    await update.message.reply_text(
        f"⏳ Скрейпинг сезона {season}...",
        parse_mode=None,
        reply_markup=ReplyKeyboardRemove(),
    )

    urls = season_urls[season]
    baseline_scraper = _get_scraper(context.user_data["baseline_service"])
    compared_scraper = _get_scraper(context.user_data["compared_service"])

    await update.message.reply_text("📥 Загружаем baseline...", parse_mode=None)

    baseline_result = await baseline_scraper.scrape_episodes(urls["baseline"])

    await update.message.reply_text(
        f"✅ Baseline готов ({len(baseline_result.episodes) if baseline_result.episodes else 0} эп.). Загружаем compared...",
        parse_mode=None,
    )

    compared_result = await compared_scraper.scrape_episodes(urls["compared"])

    await update.message.reply_text("⚙️ Сравниваем и форматируем...", parse_mode=None)

    rows = create_comparison_rows(baseline_result.episodes, compared_result.episodes)

    if not rows:
        await update.message.reply_text(
            "❌ Не удалось получить данные об эпизодах\\.\n"
            "Возможно, сервис временно недоступен\\.",
            parse_mode="MarkdownV2",
        )
        return ConversationHandler.END

    text = format_comparison(
        baseline_name=context.user_data["baseline_name"],
        baseline_title=baseline_result.series_title or "—",
        compared_name=context.user_data["compared_name"],
        compared_title=compared_result.series_title or "—",
        rows=rows,
        season=season,
    )

    for part in split_message(text):
        await update.message.reply_text(part, parse_mode="MarkdownV2")

    # Предлагаем сравнить другой сезон если есть что
    common_seasons = context.user_data.get("common_seasons", [])
    other_seasons = [s for s in common_seasons if s != season]

    if other_seasons:
        keyboard = [
            [f"Сезон {s}" for s in other_seasons],
            ["Новое сравнение"],
        ]
        markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
        await update.message.reply_text(
            "🔄 Сравнить другой сезон этого же сериала?",
            reply_markup=markup,
        )
        return WAIT_SEASON

    await update.message.reply_text(
        "🔄 Отправь /compare чтобы сравнить другой сериал\\.",
        parse_mode="MarkdownV2",
    )
    return ConversationHandler.END


# ─── Fallback ────────────────────────────────────────────────────────────────

async def fallback_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ответ на любое сообщение вне активного диалога."""
    await update.message.reply_text(
        "Отправь /compare чтобы начать сравнение\\.",
        parse_mode="MarkdownV2",
    )


# ─── Сборка ConversationHandler ──────────────────────────────────────────────

def build_conversation_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("compare", cmd_compare)],
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
                MessageHandler(filters.TEXT & ~filters.COMMAND, received_season),
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )
