# bot/handlers.py
#
# Вся логика диалога Telegram-бота.
# Состояние хранится в context.user_data — изолировано на уровне пользователя.

import asyncio
import logging
import re

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from app.services.detector import detect_service
from app.scrapers.base import ScraperFactory
from app.services.comparison import create_comparison_rows
from bot.formatter import escape, format_comparison, split_message
import app.scrapers.factory  # регистрирует скрейперы в ScraperFactory

logger = logging.getLogger(__name__)

# Состояния диалога
WAIT_BASELINE_URL = 1
WAIT_COMPARED_URL = 2
WAIT_SEASON = 3


# ─── Команды ────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет\\! Я сравниваю длительность серий на IMDb и Amediateka, "
        "чтобы находить купюры и цензуру\\.\n\n"
        "Отправь /compare чтобы начать\\.",
        parse_mode="MarkdownV2",
    )


async def cmd_compare(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало диалога — запрашиваем IMDb URL."""
    context.user_data.clear()

    await update.message.reply_text(
        "🔗 *Шаг 1 из 3* — Отправь IMDb URL сериала\\.\n\n"
        "Подойдёт любая страница сериала или сезона, например:\n"
        "`https://www\\.imdb\\.com/title/tt0248654/`\n\n"
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
    """Шаг 1 — получили IMDb URL."""
    url = update.message.text.strip()

    service, name = detect_service(url)
    if not service:
        await update.message.reply_text(
            "❌ Не удалось определить сервис по этому URL\\.\n"
            "Поддерживаются: *IMDb*, *Amediateka*\\.\n\n"
            "Попробуй ещё раз или /cancel\\.",
            parse_mode="MarkdownV2",
        )
        return WAIT_BASELINE_URL

    context.user_data["baseline_url"] = url
    context.user_data["baseline_service"] = service
    context.user_data["baseline_name"] = name

    await update.message.reply_text(
        f"✅ Baseline: *{escape(name)}*\n\n"
        f"🔗 *Шаг 2 из 3* — Отправь Amediateka URL того же сериала\\.\n\n"
        f"Подойдёт ссылка на сериал или на любой его сезон, например:\n"
        f"`https://www\\.amediateka\\.ru/watch/series\\_11353\\_klient\\-vsegda\\-mertv/`\n\n"
        f"/cancel — отменить",
        parse_mode="MarkdownV2",
    )
    return WAIT_COMPARED_URL


async def received_compared_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Шаг 2 — получили Amediateka URL, ищем общие сезоны."""
    url = update.message.text.strip()

    service, name = detect_service(url)
    if not service:
        await update.message.reply_text(
            "❌ Не удалось определить сервис по этому URL\\.\n"
            "Попробуй ещё раз или /cancel\\.",
            parse_mode="MarkdownV2",
        )
        return WAIT_COMPARED_URL

    context.user_data["compared_url"] = url
    context.user_data["compared_service"] = service
    context.user_data["compared_name"] = name

    await update.message.reply_text(
        "⏳ Ищу доступные сезоны\\.\\.\\.",
        parse_mode="MarkdownV2",
    )

    baseline_scraper = ScraperFactory.get_scraper(context.user_data["baseline_service"])
    compared_scraper = ScraperFactory.get_scraper(service)

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
        await update.message.reply_text(
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

    await update.message.reply_text(
        f"✅ Найдено общих сезонов: *{len(common_seasons)}*{extra}\n\n"
        f"🎬 *Шаг 3 из 3* — Выбери сезон для сравнения:",
        parse_mode="MarkdownV2",
        reply_markup=markup,
    )
    return WAIT_SEASON


async def received_season(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Шаг 3 — получили выбор сезона, запускаем сравнение."""
    text = update.message.text.strip()

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
        f"⏳ Собираю данные по сезону {season}\\.\\.\\.",
        parse_mode="MarkdownV2",
        reply_markup=ReplyKeyboardRemove(),
    )

    urls = season_urls[season]
    baseline_scraper = ScraperFactory.get_scraper(context.user_data["baseline_service"])
    compared_scraper = ScraperFactory.get_scraper(context.user_data["compared_service"])

    baseline_result, compared_result = await asyncio.gather(
        baseline_scraper.scrape_episodes(urls["baseline"]),
        compared_scraper.scrape_episodes(urls["compared"]),
    )

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
        keyboard = [[f"Сезон {s}" for s in other_seasons]]
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
                MessageHandler(filters.TEXT & ~filters.COMMAND, received_baseline_url)
            ],
            WAIT_COMPARED_URL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, received_compared_url)
            ],
            WAIT_SEASON: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, received_season)
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )