# Сравнитель длительности серий

Инструмент для сравнения длительности серий одного и того же сериала на разных
стриминговых платформах — помогает находить купюры, цензуру и разные монтажные
версии. Есть веб-интерфейс (FastAPI) и Telegram-бот.

**Статус:** далеко за пределами исходного MVP — есть поиск по названию, кэш
результатов, CI/CD с автодеплоем и тесты. `HLE_v0.1.md`/`HLE_v0.2.1.md` и
`step-*.md` описывают только раннюю стадию проекта и не отражают текущее
состояние — актуальную картину лучше смотреть в коде и `git log`.

## Возможности

- Поиск сериала по названию сразу на IMDb и Amediateka (единый поиск), либо
  ручной ввод URL
- Подбор сезона: сезоны находятся автоматически, показываются общие для
  обеих платформ
- Таблица сравнения с цветовой индикацией отличий (проценты + абсолютная
  разница в минутах)
- Кэширование результатов скрейпинга (SQLite, TTL 7 дней) — повторное
  сравнение того же сезона не бьёт по источникам заново
- Тот же функционал в Telegram-боте, с той же логикой поиска и кэша

### Поддерживаемые сервисы

| Сервис | Статус |
|---|---|
| IMDb | ✅ реализовано |
| Amediateka | ✅ реализовано |
| Кинопоиск | 🔲 определяется по домену, скрейпер не реализован |
| Okko | 🔲 определяется по домену, скрейпер не реализован |

## Архитектура

```
app/
  main.py              # FastAPI: /, /compare, /get-seasons, /api/search, /api/search/unified
  config.py             # Пороги и цвета для индикации различий (DiffThresholds, DiffColors)
  models.py              # Episode, ScrapeResult (dataclasses)
  cache/
    sqlite_cache.py        # SQLite-кэш результатов скрейпинга (TTL 7 дней)
  scrapers/
    base.py               # BaseScraper (abstract), ScraperFactory
    cached.py              # Декоратор с кэшированием поверх любого скрейпера
    factory.py             # Регистрация скрейперов в ScraperFactory
    imdb.py                 # Скрейпер IMDb (GraphQL API)
    amediateka.py           # Скрейпер Amediateka (HTML + __NEXT_DATA__)
  services/
    detector.py            # Определение сервиса по домену URL
    normalizer.py           # Приведение длительности к минутам (PT1H23M, "1h 23m", секунды и т.д.)
    comparison.py           # Сборка строк таблицы сравнения + классификация отличий
    search.py               # Поиск сериалов по названию (IMDb + Amediateka)
  templates/, static/     # Jinja2-шаблоны и CSS веб-интерфейса

bot/
  main.py               # Точка входа Telegram-бота (polling)
  handlers.py            # ConversationHandler: поиск по названию → выбор сезона
  formatter.py            # Форматирование результата в MarkdownV2 для Telegram

tests/                  # pytest, 127+ тестов
.github/workflows/ci.yml  # линт (ruff) + тесты + docker build + автодеплой по SSH на push в main
```

Оба интерфейса (веб и бот) используют один и тот же слой `app/` — скрейперы,
кэш, нормализацию и сравнение.

## Установка и запуск

### Требования

- Python 3.12
- Токен Telegram-бота (для запуска бота) — от [@BotFather](https://t.me/BotFather)

### Локально

```bash
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

Создать `.env` в корне проекта:

```
BOT_TOKEN=твой_токен_бота
```

Запуск веб-приложения:

```bash
uvicorn app.main:app --reload
# или: python -m app.main
```
Открыть http://localhost:8000

Запуск Telegram-бота:

```bash
python -m bot.main
```

### Docker

```bash
docker compose up
```

Поднимет два контейнера: `app` (веб, порт 8000) и `bot` (polling, читает
`BOT_TOKEN` из переменной окружения хоста).

### Тесты и линт

```bash
pip install -r requirements-dev.txt
pytest tests/ -q
ruff check .
```

## Конфигурация

Пороги и цвета классификации различий — в одном месте, `app/config.py`:

- `DiffThresholds.MINUTES_INSIGNIFICANT` — абсолютная разница в минутах,
  ниже которой отличие считается незначительным (приоритет над процентом)
- `DiffThresholds.PERCENT_SMALL` / `PERCENT_MEDIUM` — границы между
  «незначительным», «небольшим» и «значимым» отличием
- `DiffColors` — цвета для таблицы (веб) и легенды

Изменения применяются после перезапуска приложения.

## Как добавить новый сервис (скрейпер)

1. Добавить домен и метаданные в `SUPPORTED_SERVICES` в `app/services/detector.py`
2. Создать `app/scrapers/<service>.py`, реализовать `BaseScraper`
   (`scrape_episodes`, `get_seasons`, `build_season_url`, `get_series_title`)
3. Зарегистрировать в `app/scrapers/factory.py`:
   `ScraperFactory.register("<service_key>", <ScraperClass>)`
4. Добавить regex-извлечение id/сезона из URL нового сервиса в
   `app/scrapers/cached.py` (`_extract_id`/`_extract_season`) — иначе кэш для
   него просто не будет работать (без ошибки, молча)
