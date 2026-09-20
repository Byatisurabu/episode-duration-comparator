# Сравнитель длительности серий

Инструмент для сравнения длительности серий одного и того же сериала на разных
стриминговых платформах — помогает находить купюры, цензуру и разные монтажные
версии. Есть веб-интерфейс (FastAPI) и Telegram-бот.

**Статус:** MVP, core-функционал завершён. Подробный план и история этапов — в
[`HLE_v0.2.1.md`](HLE_v0.2.1.md) (актуальная версия) и [`HLE_v0.1.md`](HLE_v0.1.md).

## Возможности

- Сравнение длительности эпизодов по двум URL (baseline vs compared)
- Автоопределение сервиса по домену ссылки
- Подбор сезона: можно указать ссылку на сериал целиком — сезоны найдутся
  автоматически, покажутся общие для обеих платформ
- Таблица сравнения с цветовой индикацией отличий (проценты + абсолютная
  разница в минутах)
- Тот же функционал в Telegram-боте (диалог: ссылка 1 → ссылка 2 → выбор
  сезона → результат)

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
  main.py              # FastAPI-приложение: /, /compare, /get-seasons
  config.py             # Пороги и цвета для индикации различий (DiffThresholds, DiffColors)
  models.py              # Episode, ScrapeResult (dataclasses)
  scrapers/
    base.py               # BaseScraper (abstract), ScraperFactory
    factory.py             # Регистрация скрейперов в ScraperFactory
    imdb.py                 # Скрейпер IMDb
    amediateka.py           # Скрейпер Amediateka
  services/
    detector.py            # Определение сервиса по домену URL
    normalizer.py           # Приведение длительности к минутам (PT1H23M, "1h 23m", секунды и т.д.)
    comparison.py           # Сборка строк таблицы сравнения + классификация отличий
  templates/, static/     # Jinja2-шаблоны и CSS веб-интерфейса

bot/
  main.py               # Точка входа Telegram-бота (polling)
  handlers.py            # ConversationHandler: диалог сравнения
  formatter.py            # Форматирование результата в MarkdownV2 для Telegram
```

Оба интерфейса (веб и бот) используют один и тот же слой `app/` — скрейперы,
нормализацию и сравнение.

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
