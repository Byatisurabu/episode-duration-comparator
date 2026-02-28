# Этап 2.1 — Telegram-бот

**Цель:** удобный доступ к сравнению через Telegram без браузера  
**Режим запуска:** Polling (локально), архитектура готова к переключению на Webhook при выносе на хостинг  
**Диалог:** бот задаёт вопросы по одному — пользователь отвечает URL-ами, затем выбирает сезон

---

## Итоговая структура файлов

```
episode-duration-comparator/
├── app/                        (без изменений)
├── bot/
│   ├── __init__.py             ← новый (пустой)
│   ├── main.py                 ← новый — точка входа, запуск polling
│   ├── handlers.py             ← новый — вся логика диалога
│   └── formatter.py            ← новый — форматирование результата в текст
├── Dockerfile                  (без изменений)
├── docker-compose.yml          ← обновить: добавить сервис bot
├── requirements.txt            ← обновить: добавить python-telegram-bot
└── .env                        ← новый — BOT_TOKEN
```

---

## Шаг 1 — Получить токен бота

1. Открыть Telegram, найти `@BotFather`
2. Отправить `/newbot`
3. Придумать имя и username бота
4. Скопировать токен вида `7123456789:AAF...`

---

## Шаг 2 — `.env`

Создать в корне проекта рядом с `docker-compose.yml`:

```
BOT_TOKEN=7123456789:AAF...
```

Добавить `.env` в `.gitignore` — токен не должен попасть в репозиторий:

```
# .gitignore
.env
__pycache__/
*.pyc
```

---

## Шаг 3 — `requirements.txt`

Добавить одну строку:

```
python-telegram-bot==21.9
```

Полный файл после изменения:

```
fastapi==0.115.6
uvicorn[standard]==0.34.0
httpx==0.28.1
beautifulsoup4==4.12.3
jinja2==3.1.4
python-multipart==0.0.20
python-telegram-bot==21.9
```

---

## Шаг 4 — `bot/formatter.py`

Форматирует список `comparison_rows` (тот же что строит `create_comparison_rows`) в читаемый текст для Telegram.

```python
# bot/formatter.py

def format_comparison(
    baseline_name: str,
    baseline_title: str,
    compared_name: str,
    compared_title: str,
    rows: list[dict],
    season: int,
) -> str:
    """
    Форматирует результат сравнения в Markdown-текст для Telegram.
    Возвращает одно или несколько сообщений (Telegram ограничивает 4096 символов).
    """
    lines = []

    # Заголовок
    lines.append(f"📊 *Сравнение сезона {season}*")
    lines.append(f"")
    lines.append(f"▪️ Baseline: *{baseline_name}* — {_escape(baseline_title)}")
    lines.append(f"▪️ Compared: *{compared_name}* — {_escape(compared_title)}")
    lines.append(f"")

    # Таблица эпизодов
    significant = []   # строки с разницей ≥ 5%
    small = []         # строки с малой разницей
    missing = []       # отсутствующие эпизоды

    for row in rows:
        ep_label = f"S{row['season']:02d}E{row['episode']:02d}"

        if row.get('missing_in_baseline') or row.get('missing_in_compared'):
            where = compared_name if row.get('missing_in_baseline') else baseline_name
            missing.append(f"  ❓ {ep_label} — только на {where}")
            continue

        dur_b = row['duration_baseline']
        dur_c = row['duration_compared']
        diff = row['diff_min']
        pct = row['diff_percent']
        color = row['color_class']

        if dur_b is None or dur_c is None:
            continue

        if color in ('large-red', 'large-green'):
            sign = "+" if diff > 0 else ""
            emoji = "🟢" if diff > 0 else "🔴"
            significant.append(
                f"  {emoji} {ep_label}: {dur_b}м → {dur_c}м ({sign}{diff}м, {sign}{pct}%)"
            )
        elif color == 'medium-diff':
            sign = "+" if diff > 0 else ""
            small.append(
                f"  🟡 {ep_label}: {dur_b}м → {dur_c}м ({sign}{diff}м)"
            )
        # small-diff и neutral не выводим — незначительная разница

    # Собираем итог
    total = len(rows)
    sig_count = len(significant)

    if not significant and not small and not missing:
        lines.append("✅ Значимых различий не найдено\\. Все эпизоды совпадают в пределах 1 мин\\.")
    else:
        if significant:
            lines.append(f"*Значимые отличия \\(≥5%\\):*")
            lines.extend(significant)
            lines.append("")

        if small:
            lines.append(f"*Небольшие отличия \\(3–5%\\):*")
            lines.extend(small)
            lines.append("")

        if missing:
            lines.append(f"*Отсутствующие эпизоды:*")
            lines.extend(missing)
            lines.append("")

    lines.append(f"_Всего эпизодов: {total}, с отличиями ≥5%: {sig_count}_")

    return "\n".join(lines)


def _escape(text: str) -> str:
    """Экранирует спецсимволы для Telegram MarkdownV2."""
    special = r'_*[]()~`>#+-=|{}.!'
    for ch in special:
        text = text.replace(ch, f'\\{ch}')
    return text


def split_message(text: str, limit: int = 4096) -> list[str]:
    """
    Разбивает длинный текст на части ≤ limit символов,
    разрезая по переносам строк.
    Нужно для сериалов с большим количеством эпизодов.
    """
    if len(text) <= limit:
        return [text]

    parts = []
    current = []
    current_len = 0

    for line in text.split("\n"):
        line_len = len(line) + 1  # +1 за \n
        if current_len + line_len > limit:
            parts.append("\n".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += line_len

    if current:
        parts.append("\n".join(current))

    return parts
```

---

## Шаг 5 — `bot/handlers.py`

Вся логика диалога. Состояние хранится в памяти — словарь `user_states`.

```python
# bot/handlers.py

import logging
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
from bot.formatter import format_comparison, split_message
import app.scrapers.factory  # регистрирует скрейперы

logger = logging.getLogger(__name__)

# Состояния диалога
WAIT_BASELINE_URL = 1
WAIT_COMPARED_URL = 2
WAIT_SEASON = 3


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start — приветствие и инструкция."""
    await update.message.reply_text(
        "👋 Привет\\! Я сравниваю длительность серий на IMDb и Amediateka\\.\n\n"
        "Отправь /compare чтобы начать сравнение\\.",
        parse_mode="MarkdownV2"
    )


async def cmd_compare(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /compare — начало диалога."""
    context.user_data.clear()
    await update.message.reply_text(
        "🔗 Шаг 1/3 — Отправь *IMDb URL* сериала или сезона\\.\n\n"
        "Например:\n`https://www.imdb.com/title/tt0248654/`\n\n"
        "Или /cancel для отмены\\.",
        parse_mode="MarkdownV2",
        reply_markup=ReplyKeyboardRemove(),
    )
    return WAIT_BASELINE_URL


async def received_baseline_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получили baseline URL — валидируем и просим compared URL."""
    url = update.message.text.strip()

    service, name = detect_service(url)
    if not service:
        await update.message.reply_text(
            f"❌ Не удалось определить сервис по этому URL\\.\n"
            f"Поддерживаются: IMDb, Amediateka\\.\n\n"
            f"Попробуй ещё раз или /cancel\\.",
            parse_mode="MarkdownV2",
        )
        return WAIT_BASELINE_URL  # остаёмся в том же состоянии

    context.user_data['baseline_url'] = url
    context.user_data['baseline_service'] = service
    context.user_data['baseline_name'] = name

    await update.message.reply_text(
        f"✅ Baseline: *{_escape(name)}*\n\n"
        f"🔗 Шаг 2/3 — Теперь отправь *Amediateka URL* того же сериала\\.\n\n"
        f"Например:\n`https://www\\.amediateka\\.ru/watch/series\\_11353\\_klient\\-vsegda\\-mertv/season\\_1\\_11976`\n\n"
        f"Или /cancel для отмены\\.",
        parse_mode="MarkdownV2",
    )
    return WAIT_COMPARED_URL


async def received_compared_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получили compared URL — ищем сезоны и предлагаем выбрать."""
    url = update.message.text.strip()

    service, name = detect_service(url)
    if not service:
        await update.message.reply_text(
            f"❌ Не удалось определить сервис по этому URL\\.\n"
            f"Попробуй ещё раз или /cancel\\.",
            parse_mode="MarkdownV2",
        )
        return WAIT_COMPARED_URL

    context.user_data['compared_url'] = url
    context.user_data['compared_service'] = service
    context.user_data['compared_name'] = name

    # Ищем сезоны
    await update.message.reply_text("⏳ Ищу доступные сезоны\\.\\.\\.", parse_mode="MarkdownV2")

    baseline_scraper = ScraperFactory.get_scraper(context.user_data['baseline_service'])
    compared_scraper = ScraperFactory.get_scraper(service)

    import asyncio
    baseline_seasons, compared_seasons = await asyncio.gather(
        baseline_scraper.get_seasons(context.user_data['baseline_url']),
        compared_scraper.get_seasons(url),
        return_exceptions=True,
    )

    if isinstance(baseline_seasons, Exception):
        baseline_seasons = []
    if isinstance(compared_seasons, Exception):
        compared_seasons = []

    common_seasons = sorted(set(baseline_seasons) & set(compared_seasons))

    if not common_seasons:
        await update.message.reply_text(
            "❌ Не удалось найти общие сезоны на обоих сервисах\\.\n"
            "Проверь URL и попробуй /compare заново\\.",
            parse_mode="MarkdownV2",
        )
        return ConversationHandler.END

    # Сохраняем season_urls
    all_seasons = sorted(set(baseline_seasons) | set(compared_seasons))
    baseline_urls, compared_urls = await asyncio.gather(
        asyncio.gather(*[baseline_scraper.get_season_url(context.user_data['baseline_url'], s) for s in all_seasons]),
        asyncio.gather(*[compared_scraper.get_season_url(url, s) for s in all_seasons]),
    )
    season_urls = {
        s: {"baseline": baseline_urls[i], "compared": compared_urls[i]}
        for i, s in enumerate(all_seasons)
    }
    context.user_data['season_urls'] = season_urls
    context.user_data['common_seasons'] = common_seasons

    # Клавиатура с сезонами
    keyboard = [[f"Сезон {s}" for s in common_seasons]]
    markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)

    only_baseline = sorted(set(baseline_seasons) - set(compared_seasons))
    only_compared = sorted(set(compared_seasons) - set(baseline_seasons))
    extra = ""
    if only_baseline:
        extra += f"\n⚠️ Только на {_escape(context.user_data['baseline_name'])}: {', '.join(str(s) for s in only_baseline)}"
    if only_compared:
        extra += f"\n⚠️ Только на {_escape(name)}: {', '.join(str(s) for s in only_compared)}"

    await update.message.reply_text(
        f"✅ Найдено общих сезонов: *{len(common_seasons)}*{_escape(extra)}\n\n"
        f"🎬 Шаг 3/3 — Выбери сезон для сравнения:",
        parse_mode="MarkdownV2",
        reply_markup=markup,
    )
    return WAIT_SEASON


async def received_season(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получили выбор сезона — запускаем сравнение."""
    text = update.message.text.strip()

    # Парсим номер из текста вида "Сезон 3"
    import re
    match = re.search(r'\d+', text)
    if not match:
        await update.message.reply_text(
            "❌ Не понял выбор\\. Нажми одну из кнопок выше\\.",
            parse_mode="MarkdownV2",
        )
        return WAIT_SEASON

    season = int(match.group())
    season_urls = context.user_data.get('season_urls', {})

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
    baseline_scraper = ScraperFactory.get_scraper(context.user_data['baseline_service'])
    compared_scraper = ScraperFactory.get_scraper(context.user_data['compared_service'])

    import asyncio
    baseline_result, compared_result = await asyncio.gather(
        baseline_scraper.scrape_episodes(urls['baseline']),
        compared_scraper.scrape_episodes(urls['compared']),
    )

    rows = create_comparison_rows(baseline_result.episodes, compared_result.episodes)

    if not rows:
        await update.message.reply_text(
            "❌ Не удалось получить данные об эпизодах\\.\n"
            "Возможно, сервис временно недоступен\\.",
            parse_mode="MarkdownV2",
        )
        return ConversationHandler.END

    # Форматируем и отправляем результат
    text = format_comparison(
        baseline_name=context.user_data['baseline_name'],
        baseline_title=baseline_result.series_title or "—",
        compared_name=context.user_data['compared_name'],
        compared_title=compared_result.series_title or "—",
        rows=rows,
        season=season,
    )

    for part in split_message(text):
        await update.message.reply_text(part, parse_mode="MarkdownV2")

    await update.message.reply_text(
        "🔄 Хочешь сравнить ещё? Отправь /compare\\.",
        parse_mode="MarkdownV2",
    )
    return ConversationHandler.END


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /cancel — прерывает текущий диалог."""
    context.user_data.clear()
    await update.message.reply_text(
        "❌ Сравнение отменено\\. Отправь /compare чтобы начать заново\\.",
        parse_mode="MarkdownV2",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


async def fallback_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ответ на любое неожиданное сообщение вне диалога."""
    await update.message.reply_text(
        "Отправь /compare чтобы начать сравнение\\.",
        parse_mode="MarkdownV2",
    )


def build_conversation_handler() -> ConversationHandler:
    """Собирает ConversationHandler — вызывается из main.py."""
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
        # per_user=True по умолчанию — у каждого пользователя свой context.user_data
    )


def _escape(text: str) -> str:
    """Экранирует спецсимволы для Telegram MarkdownV2."""
    special = r'_*[]()~`>#+-=|{}.!'
    for ch in special:
        text = text.replace(ch, f'\\{ch}')
    return text
```

---

## Шаг 6 — `bot/main.py`

Точка входа. Polling с возможностью переключиться на Webhook одним параметром.

```python
# bot/main.py

import logging
import os
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters
from bot.handlers import cmd_start, cmd_cancel, fallback_unknown, build_conversation_handler

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN не задан. Добавь его в .env или переменные окружения.")

    app = ApplicationBuilder().token(token).build()

    # Глобальные команды — работают в любой момент
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("cancel", cmd_cancel))

    # Диалог сравнения
    app.add_handler(build_conversation_handler())

    # Fallback на любое сообщение вне диалога
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback_unknown))

    logger.info("Бот запущен в режиме polling")
    app.run_polling(
        allowed_updates=["message"],
        drop_pending_updates=True,  # игнорировать сообщения пока бот был выключен
    )

    # ───────────────────────────────────────────────────────────────
    # При переносе на хостинг: заменить run_polling на run_webhook:
    #
    # app.run_webhook(
    #     listen="0.0.0.0",
    #     port=8443,
    #     webhook_url=f"https://{os.environ['DOMAIN']}/webhook",
    # )
    # ───────────────────────────────────────────────────────────────


if __name__ == "__main__":
    main()
```

---

## Шаг 7 — `docker-compose.yml`

Добавить сервис `bot` рядом с существующим `app`.

```yaml
services:
  app:
    build: .
    container_name: episode-comparator
    ports:
      - "8000:8000"
    restart: unless-stopped
    environment:
      - PYTHONUNBUFFERED=1
      - PYTHONDONTWRITEBYTECODE=1

  bot:
    build: .
    container_name: episode-comparator-bot
    command: ["python", "-m", "bot.main"]
    restart: unless-stopped
    environment:
      - PYTHONUNBUFFERED=1
      - PYTHONDONTWRITEBYTECODE=1
      - BOT_TOKEN=${BOT_TOKEN}      # берётся из .env автоматически
    depends_on:
      - app
```

Оба сервиса используют один образ. `bot` запускает `python -m bot.main` вместо `uvicorn`. `BOT_TOKEN` подтягивается из `.env` через синтаксис `${BOT_TOKEN}` — docker compose читает `.env` автоматически если файл лежит рядом с `docker-compose.yml`.

---

## Шаг 8 — Запуск

```bash
# Пересобрать образ (добавили python-telegram-bot в requirements.txt)
docker compose build

# Запустить оба сервиса
docker compose up -d

# Проверить логи бота
docker compose logs -f bot
```

В логах должно появиться:
```
[INFO] bot.main: Бот запущен в режиме polling
```

Открыть Telegram → найти своего бота → отправить `/start`.

---

## Диалог — как это выглядит в Telegram

```
Пользователь: /compare

Бот: 🔗 Шаг 1/3 — Отправь IMDb URL сериала или сезона.
     Или /cancel для отмены.

Пользователь: https://www.imdb.com/title/tt0248654/

Бот: ✅ Baseline: IMDb
     🔗 Шаг 2/3 — Теперь отправь Amediateka URL того же сериала.

Пользователь: https://www.amediateka.ru/watch/series_11353_.../season_3_11978

Бот: ⏳ Ищу доступные сезоны...
     ✅ Найдено общих сезонов: 5
     🎬 Шаг 3/3 — Выбери сезон:
     [ Сезон 1 ] [ Сезон 2 ] [ Сезон 3 ] [ Сезон 4 ] [ Сезон 5 ]

Пользователь: Сезон 3  (нажатие кнопки)

Бот: ⏳ Собираю данные по сезону 3...

Бот: 📊 Сравнение сезона 3
     ▪️ Baseline: IMDb — Клиент всегда мертв
     ▪️ Compared: Amediateka — Клиент всегда мертв

     Значимые отличия (≥5%):
       🔴 S03E01: 55м → 48м (-7м, -12.7%)
       🔴 S03E04: 52м → 45м (-7м, -13.5%)
     ...

     Всего эпизодов: 13, с отличиями ≥5%: 4

Бот: 🔄 Хочешь сравнить ещё? Отправь /compare.
```

---

## Проверочный чеклист

- [ ] `.env` создан, `BOT_TOKEN` заполнен
- [ ] `docker compose build` завершается без ошибок
- [ ] `docker compose logs bot` показывает `Бот запущен в режиме polling`
- [ ] `/start` в Telegram — бот отвечает
- [ ] `/compare` → диалог проходит все три шага
- [ ] Результат сравнения приходит в Telegram
- [ ] `/cancel` в середине диалога — диалог прерывается корректно
- [ ] Некорректный URL на шаге 1 или 2 — бот просит ввести снова, не падает

---

## Возможные проблемы и решения

**`BOT_TOKEN не задан`**
`.env` не найден или не в той папке. Убедиться, что файл лежит рядом с `docker-compose.yml`.

**`Conflict: terminated by other getUpdates request`**
Запущено два экземпляра бота одновременно. Остановить все контейнеры: `docker compose down`, затем `docker compose up -d`.

**Бот не отвечает на сообщения после перезапуска**
`drop_pending_updates=True` в `run_polling` — все сообщения пока бот был выключен игнорируются. Это намеренное поведение, чтобы не обрабатывать устаревшие запросы.

**Telegram возвращает ошибку парсинга MarkdownV2**
Спецсимволы в названии сериала не экранированы. `_escape()` есть в обоих файлах (`handlers.py` и `formatter.py`) — убедиться, что все пользовательские строки прогоняются через неё перед отправкой.

---

## Подготовка к выносу на хостинг (этап 2.2)

Когда придёт время переносить бота на VPS, нужно будет сделать три вещи:

1. В `bot/main.py` заменить `app.run_polling(...)` на `app.run_webhook(...)` (заготовка уже в коде в виде комментария)
2. В `docker-compose.yml` добавить проброс порта для webhook: `"8443:8443"`
3. Добавить переменную окружения `DOMAIN` с адресом VPS

Всё остальное — логика диалога, скрейперы, форматирование — остаётся без изменений.
