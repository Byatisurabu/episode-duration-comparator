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
from app.services.search import search_both
from bot.formatter import escape, format_comparison, split_message

# Общий кеш для бота (тот же data/cache.db что и у веб-приложения)
_cache = EpisodeCache()

logger = logging.getLogger(__name__)

# Состояния диалога
WAIT_SEARCH = 1          # пользователь вводит название (единый поиск)
WAIT_UNIFIED_PICK = 2    # выбирает из результатов (IMDb + Amediateka в одном KB)
WAIT_COMPARED_URL = 3    # fallback: поиск дал результаты только для одного сервиса
WAIT_COMPARED_PICK = 4   # fallback: выбор из результатов второго сервиса
WAIT_SEASON = 5          # выбор сезона

# Префиксы для callback_data
CB_UNIFIED_BASELINE = "ub:"   # unified pick — IMDb
CB_UNIFIED_COMPARED = "uc:"   # unified pick — Amediateka
CB_COMPARED = "cp:"           # fallback: compared pick
CB_SEASON = "sn:"
CB_ALL_SEASONS = "sn:all"
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
        "1\\. Введи название сериала — бот найдёт его сразу на IMDb и Amediateka\n"
        "2\\. Выбери нужный сериал из каждого сервиса\n"
        "3\\. Выбери сезон для сравнения\n\n"
        "Поддерживаемые сервисы: *IMDb*, *Amediateka*\\.",
        parse_mode="MarkdownV2",
    )


async def cmd_compare(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало диалога — запрашиваем название сериала."""
    context.user_data.clear()

    await update.message.reply_text(
        "🔍 *Шаг 1 из 2* — Введи название сериала\\.\n\n"
        "Я найду его сразу на IMDb и Amediateka\\.\n\n"
        "Пример: `Breaking Bad`\n\n"
        "_Или вставь URL напрямую \\(IMDb или Amediateka\\) — пропущу поиск\\._\n\n"
        "/cancel — отменить",
        parse_mode="MarkdownV2",
        reply_markup=ReplyKeyboardRemove(),
    )
    return WAIT_SEARCH


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
        "🔍 *Шаг 1 из 2* — Введи название сериала\\.\n\n"
        "Я найду его сразу на IMDb и Amediateka\\.\n\n"
        "Пример: `Breaking Bad`\n\n"
        "_Или вставь URL напрямую \\(IMDb или Amediateka\\) — пропущу поиск\\._\n\n"
        "/cancel — отменить",
        parse_mode="MarkdownV2",
    )
    return WAIT_SEARCH


# ─── Шаг 1: единый поиск ────────────────────────────────────────────────────

async def received_search_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Шаг 1 — получили название или URL."""
    text = update.message.text.strip()

    # Если это URL — определяем сервис и обрабатываем напрямую
    if re.match(r"https?://", text, re.IGNORECASE):
        return await _handle_direct_url(update, context, text)

    # Иначе — единый поиск по обоим сервисам
    status_msg = await update.message.reply_text("🔍 Ищу на IMDb и Amediateka...")

    unified = await search_both(text)

    if not unified.imdb and not unified.amediateka:
        await status_msg.edit_text(
            "❌ Ничего не найдено по запросу\\. Попробуй другое название или вставь URL\\.",
            parse_mode="MarkdownV2",
        )
        return WAIT_SEARCH

    # Сохраняем результаты поиска для дальнейшего выбора
    context.user_data["unified_imdb"] = {r.series_id: r for r in unified.imdb}
    context.user_data["unified_amediateka"] = {r.series_id: r for r in unified.amediateka}
    context.user_data["baseline_picked"] = False
    context.user_data["compared_picked"] = False

    await status_msg.delete()
    await _send_unified_picker(update.message, context, unified)
    return WAIT_UNIFIED_PICK


async def _handle_direct_url(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str) -> int:
    """Пользователь вставил URL напрямую — определяем сервис и переходим к шагу 2."""
    service, name = detect_service(url)
    if not service:
        await update.message.reply_text(
            "❌ Не удалось определить сервис по этому URL\\.\n"
            "Поддерживаются: *IMDb*, *Amediateka*\\.\n\n"
            "Попробуй ещё раз или /cancel\\.",
            parse_mode="MarkdownV2",
        )
        return WAIT_SEARCH

    scraper = _get_scraper(service)
    title = await scraper.get_series_title(url)
    seasons = await scraper.get_seasons(url)
    seasons_str = str(len(seasons)) if seasons else "сезоны не найдены"
    preview = f"✅ *{escape(title or name)}*, найдено сезонов: {seasons_str}"

    if service == "imdb":
        context.user_data["baseline_url"] = url
        context.user_data["baseline_service"] = service
        context.user_data["baseline_name"] = name
        await update.message.reply_text(
            f"{preview}\n\n"
            f"🔍 Теперь введи название или URL сериала на Amediateka\\.\n\n"
            f"/cancel — отменить",
            parse_mode="MarkdownV2",
        )
        return WAIT_COMPARED_URL
    else:
        # Amediateka URL без baseline — попросить IMDb
        context.user_data["compared_url"] = url
        context.user_data["compared_service"] = service
        context.user_data["compared_name"] = name
        await update.message.reply_text(
            f"{preview}\n\n"
            f"🔍 Теперь введи название или URL сериала на IMDb\\.\n\n"
            f"/cancel — отменить",
            parse_mode="MarkdownV2",
        )
        # Переходим в специальный режим: ждём baseline
        context.user_data["need_baseline"] = True
        return WAIT_COMPARED_URL


async def _send_unified_picker(message, context, unified, edit=False):
    """Формирует InlineKeyboard с результатами обоих сервисов."""
    rows = []

    imdb_results = list(context.user_data.get("unified_imdb", {}).values())
    amd_results = list(context.user_data.get("unified_amediateka", {}).values())

    picked_baseline_id = context.user_data.get("picked_baseline_id")
    picked_compared_id = context.user_data.get("picked_compared_id")

    if imdb_results:
        rows.append([InlineKeyboardButton("── 🎬 IMDb ──", callback_data="noop")])
        for r in imdb_results[:6]:
            label = f"{r.title}{' (' + r.year + ')' if r.year else ''}"
            if picked_baseline_id == r.series_id:
                label = f"✅ {label}"
            rows.append([InlineKeyboardButton(label, callback_data=f"{CB_UNIFIED_BASELINE}{r.series_id}")])

    if amd_results:
        rows.append([InlineKeyboardButton("── 📺 Amediateka ──", callback_data="noop")])
        for r in amd_results[:6]:
            label = f"{r.title}{' (' + r.year + ')' if r.year else ''}"
            if picked_compared_id == r.series_id:
                label = f"✅ {label}"
            rows.append([InlineKeyboardButton(label, callback_data=f"{CB_UNIFIED_COMPARED}{r.series_id}")])

    baseline_done = "✅" if picked_baseline_id else "⬜"
    compared_done = "✅" if picked_compared_id else "⬜"

    header = (
        f"📋 *Выбери сериал из каждого сервиса:*\n\n"
        f"{baseline_done} IMDb: {'выбрано' if picked_baseline_id else 'не выбрано'}\n"
        f"{compared_done} Amediateka: {'выбрано' if picked_compared_id else 'не выбрано'}"
    )

    if edit:
        await message.edit_text(
            header,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(rows),
        )
    else:
        await message.reply_text(
            header,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(rows),
        )


# ─── Шаг 2: выбор из unified picker ─────────────────────────────────────────

async def callback_unified_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Пользователь нажал на результат в unified picker."""
    query = update.callback_query
    await query.answer()

    data = query.data
    if data.startswith(CB_UNIFIED_BASELINE):
        series_id = data[len(CB_UNIFIED_BASELINE):]
        result = context.user_data.get("unified_imdb", {}).get(series_id)
        if result:
            context.user_data["baseline_url"] = result.url
            context.user_data["baseline_service"] = "imdb"
            context.user_data["baseline_name"] = "IMDb"
            context.user_data["picked_baseline_id"] = series_id

    elif data.startswith(CB_UNIFIED_COMPARED):
        series_id = data[len(CB_UNIFIED_COMPARED):]
        result = context.user_data.get("unified_amediateka", {}).get(series_id)
        if result:
            context.user_data["compared_url"] = result.url
            context.user_data["compared_service"] = "amediateka"
            context.user_data["compared_name"] = "Amediateka"
            context.user_data["picked_compared_id"] = series_id

    # Оба выбраны — идём к сезонам
    if context.user_data.get("baseline_url") and context.user_data.get("compared_url"):
        await query.edit_message_text(
            "✅ IMDb: выбрано\n✅ Amediateka: выбрано\n\n⏳ Ищу сезоны\\.\\.\\.",
            parse_mode="MarkdownV2",
        )
        return await _fetch_seasons_and_show(query.message, context)

    # Иначе обновляем клавиатуру с отметкой выбора
    await _send_unified_picker(query.message, context, unified=None, edit=True)
    return WAIT_UNIFIED_PICK


# ─── Fallback: шаг 2 через отдельный URL ────────────────────────────────────

async def received_compared_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fallback: пользователь вводит URL или название второго сервиса."""
    text = update.message.text.strip()

    need_baseline = context.user_data.get("need_baseline", False)

    if re.match(r"https?://", text, re.IGNORECASE):
        url = text
        service, name = detect_service(url)
        if not service:
            await update.message.reply_text(
                "❌ Не удалось определить сервис\\. Попробуй ещё раз или /cancel\\.",
                parse_mode="MarkdownV2",
            )
            return WAIT_COMPARED_URL

        if need_baseline:
            context.user_data["baseline_url"] = url
            context.user_data["baseline_service"] = service
            context.user_data["baseline_name"] = name
        else:
            context.user_data["compared_url"] = url
            context.user_data["compared_service"] = service
            context.user_data["compared_name"] = name

        status_msg = await update.message.reply_text("⏳ Ищу сезоны...")
        return await _fetch_seasons_and_show(status_msg, context)

    # Поиск по названию
    await update.message.reply_text("🔍 Ищу...")
    from app.services.search import search_amediateka, search_series

    if need_baseline:
        results = await search_series(text)
        prefix = CB_UNIFIED_BASELINE
        context.user_data["fallback_imdb"] = {r.series_id: r for r in results}
    else:
        results = await search_amediateka(text)
        prefix = CB_COMPARED
        context.user_data["fallback_compared"] = {r.series_id: r for r in results}

    if not results:
        await update.message.reply_text(
            "❌ Ничего не найдено\\. Попробуй другое название или вставь URL\\.",
            parse_mode="MarkdownV2",
        )
        return WAIT_COMPARED_URL

    keyboard = [
        [InlineKeyboardButton(
            f"{r.title}{' (' + r.year + ')' if r.year else ''}",
            callback_data=f"{prefix}{r.series_id}",
        )]
        for r in results[:8]
    ]
    await update.message.reply_text(
        "📋 Выбери сериал:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return WAIT_COMPARED_PICK


async def callback_compared_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fallback: выбор из результатов второго сервиса."""
    query = update.callback_query
    await query.answer()

    data = query.data
    need_baseline = context.user_data.get("need_baseline", False)

    if need_baseline and data.startswith(CB_UNIFIED_BASELINE):
        series_id = data[len(CB_UNIFIED_BASELINE):]
        result = context.user_data.get("fallback_imdb", {}).get(series_id)
        if result:
            context.user_data["baseline_url"] = result.url
            context.user_data["baseline_service"] = "imdb"
            context.user_data["baseline_name"] = "IMDb"
    elif data.startswith(CB_COMPARED):
        series_id = data[len(CB_COMPARED):]
        result = context.user_data.get("fallback_compared", {}).get(series_id)
        if result:
            context.user_data["compared_url"] = result.url
            context.user_data["compared_service"] = "amediateka"
            context.user_data["compared_name"] = "Amediateka"

    await query.edit_message_text("✅ Выбрано\\. ⏳ Ищу сезоны\\.\\.\\.", parse_mode="MarkdownV2")
    return await _fetch_seasons_and_show(query.message, context)


# ─── Общая логика: поиск сезонов и отображение ───────────────────────────────

async def _fetch_seasons_and_show(message, context) -> int:
    """Запрашивает сезоны у обоих сервисов и показывает picker."""
    baseline_scraper = _get_scraper(context.user_data["baseline_service"])
    compared_scraper = _get_scraper(context.user_data["compared_service"])

    baseline_seasons, compared_seasons = await asyncio.gather(
        baseline_scraper.get_seasons(context.user_data["baseline_url"]),
        compared_scraper.get_seasons(context.user_data["compared_url"]),
        return_exceptions=True,
    )

    if isinstance(baseline_seasons, Exception):
        logger.error("get_seasons baseline: %s", baseline_seasons)
        baseline_seasons = []
    if isinstance(compared_seasons, Exception):
        logger.error("get_seasons compared: %s", compared_seasons)
        compared_seasons = []

    common_seasons = sorted(set(baseline_seasons) & set(compared_seasons))

    if not common_seasons:
        await message.reply_text(
            "❌ Не удалось найти общие сезоны на обоих сервисах\\.\n"
            "Проверь URL и попробуй /compare заново\\.",
            parse_mode="MarkdownV2",
        )
        return ConversationHandler.END

    all_seasons = sorted(set(baseline_seasons) | set(compared_seasons))
    baseline_season_urls, compared_season_urls = await asyncio.gather(
        asyncio.gather(*[
            baseline_scraper.get_season_url(context.user_data["baseline_url"], s)
            for s in all_seasons
        ]),
        asyncio.gather(*[
            compared_scraper.get_season_url(context.user_data["compared_url"], s)
            for s in all_seasons
        ]),
    )

    season_urls = {
        s: {"baseline": baseline_season_urls[i], "compared": compared_season_urls[i]}
        for i, s in enumerate(all_seasons)
    }

    context.user_data["season_urls"] = season_urls
    context.user_data["common_seasons"] = common_seasons

    only_baseline = sorted(set(baseline_seasons) - set(compared_seasons))
    only_compared = sorted(set(compared_seasons) - set(baseline_seasons))

    extra_lines = []
    baseline_name = context.user_data.get("baseline_name", "IMDb")
    compared_name = context.user_data.get("compared_name", "Amediateka")
    if only_baseline:
        extra_lines.append(f"⚠️ Только на {escape(baseline_name)}: сезоны {escape(', '.join(str(s) for s in only_baseline))}")
    if only_compared:
        extra_lines.append(f"⚠️ Только на {escape(compared_name)}: сезоны {escape(', '.join(str(s) for s in only_compared))}")

    extra = ("\n" + "\n".join(extra_lines)) if extra_lines else ""

    season_buttons = [
        InlineKeyboardButton(f"Сезон {s}", callback_data=f"{CB_SEASON}{s}")
        for s in common_seasons
    ]
    rows = [season_buttons[i:i + 4] for i in range(0, len(season_buttons), 4)]
    if len(common_seasons) > 1:
        rows.append([InlineKeyboardButton("📊 Все сезоны", callback_data=CB_ALL_SEASONS)])

    await message.reply_text(
        f"✅ Найдено общих сезонов: *{len(common_seasons)}*{extra}\n\n"
        f"🎬 *Шаг 2 из 2* — Выбери сезон для сравнения:",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(rows),
    )
    return WAIT_SEASON


# ─── Вспомогательные функции ─────────────────────────────────────────────────

async def _scrape_and_compare_season(message, context, season: int) -> bool:
    """Скрейпит оба сервиса и отправляет результат сравнения для одного сезона."""
    season_urls = context.user_data.get("season_urls", {})
    urls = season_urls.get(season)
    if not urls:
        await message.reply_text(f"❌ Сезон {season} не найден\\.", parse_mode="MarkdownV2")
        return False

    baseline_name = escape(context.user_data.get("baseline_name", "IMDb"))
    compared_name = escape(context.user_data.get("compared_name", "Amediateka"))

    baseline_scraper = _get_scraper(context.user_data["baseline_service"])
    compared_scraper = _get_scraper(context.user_data["compared_service"])

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

        await status_msg.edit_text(f"✅ Сезон {season} — готово\\!", parse_mode="MarkdownV2")

        text = format_comparison(
            baseline_name=context.user_data.get("baseline_name", "IMDb"),
            baseline_title=baseline_result.series_title or "—",
            compared_name=context.user_data.get("compared_name", "Amediateka"),
            compared_title=compared_result.series_title or "—",
            rows=rows,
            season=season,
        )

        for part in split_message(text):
            await message.reply_text(part, parse_mode="MarkdownV2")

        return True

    except Exception as e:
        logger.error("_scrape_and_compare_season season=%d: %s", season, e)
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
    query = update.callback_query
    await query.answer()

    season = int(query.data[len(CB_SEASON):])
    await query.edit_message_reply_markup(reply_markup=None)

    await _scrape_and_compare_season(query.message, context, season)
    return await _offer_next_action(query.message, context, exclude_season=season)


async def callback_all_seasons(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    query = update.callback_query
    await query.answer()
    context.user_data.clear()

    await query.edit_message_text(
        "🔍 *Шаг 1 из 2* — Введи название сериала\\.\n\n"
        "Я найду его сразу на IMDb и Amediateka\\.\n\n"
        "Пример: `Breaking Bad`\n\n"
        "_Или вставь URL напрямую \\(IMDb или Amediateka\\)\\._\n\n"
        "/cancel — отменить",
        parse_mode="MarkdownV2",
    )
    return WAIT_SEARCH


# ─── Fallback ────────────────────────────────────────────────────────────────

async def fallback_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            WAIT_SEARCH: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, received_search_input),
            ],
            WAIT_UNIFIED_PICK: [
                CallbackQueryHandler(callback_unified_pick, pattern=r"^u[bc]:"),
                CallbackQueryHandler(lambda u, c: None, pattern=r"^noop$"),
            ],
            WAIT_COMPARED_URL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, received_compared_url),
            ],
            WAIT_COMPARED_PICK: [
                CallbackQueryHandler(callback_compared_pick, pattern=rf"^({CB_COMPARED}|{CB_UNIFIED_BASELINE})"),
            ],
            WAIT_SEASON: [
                CallbackQueryHandler(callback_season_pick, pattern=r"^sn:\d+$"),
                CallbackQueryHandler(callback_all_seasons, pattern=r"^sn:all$"),
                CallbackQueryHandler(callback_new_compare, pattern=r"^action:new_compare$"),
            ],
        },
        fallbacks=[
            CommandHandler("compare", cmd_compare),
            CommandHandler("cancel", cmd_cancel),
        ],
    )
