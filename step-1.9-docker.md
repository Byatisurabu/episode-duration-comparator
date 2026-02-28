# Этап 1.9 — Контейнеризация (Docker)

**Цель:** запуск приложения одной командой `docker compose up`  
**Статус до:** работает только локально через `uvicorn`  
**Статус после:** воспроизводимый запуск в контейнере, готовый к деплою на VPS

---

## Итоговая структура файлов

```
episode-duration-comparator/
├── app/
│   ├── ...               (без изменений)
├── Dockerfile            ← новый
├── docker-compose.yml    ← новый
├── .dockerignore         ← новый
├── requirements.txt      ← новый
└── HLE_v0.2.md
```

---

## Шаг 1 — `requirements.txt`

Создать в корне проекта рядом с `Dockerfile`.

```
fastapi==0.115.6
uvicorn[standard]==0.34.0
httpx==0.28.1
beautifulsoup4==4.12.3
jinja2==3.1.4
python-multipart==0.0.20
```

**Почему зафиксированы версии:**  
Docker-образ должен собираться одинаково сейчас и через полгода. Плавающие версии (`fastapi>=0.100`) — источник сюрпризов при пересборке.

**Проверить актуальные версии** (если проект уже работал локально):
```bash
pip freeze | grep -E "fastapi|uvicorn|httpx|beautifulsoup4|jinja2|python-multipart"
```

---

## Шаг 2 — `Dockerfile`

```dockerfile
# Этап сборки зависимостей отдельно от финального образа
FROM python:3.12-slim AS builder

WORKDIR /build

# Копируем только requirements — слой кешируется, пока файл не меняется
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# Финальный образ — чистый, без pip и кеша
FROM python:3.12-slim

WORKDIR /app

# Берём установленные пакеты из builder-слоя
COPY --from=builder /install /usr/local

# Копируем код приложения
COPY app/ ./app/

# Не запускаем от root
RUN useradd -m appuser
USER appuser

# Порт, который слушает uvicorn
EXPOSE 8000

# --workers 1 достаточно для личного/семейного использования
# --no-access-log убирает мусор в логах (запросы к /static)
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--no-access-log"]
```

**Почему multi-stage build:**  
Финальный образ не содержит pip, кеш pip не попадает в слои. Размер образа ~180 MB вместо ~400 MB.

**Почему `python:3.12-slim`, а не `alpine`:**  
`beautifulsoup4` и `httpx` требуют компиляции нативных зависимостей на alpine — это боль. `slim` даёт разумный баланс размера и простоты.

---

## Шаг 3 — `docker-compose.yml`

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
```

**`restart: unless-stopped`** — контейнер поднимается автоматически после перезагрузки машины, но не перезапускается если его остановить вручную (`docker compose stop`). Нужно для деплоя на домашнем ПК или VPS.

**`PYTHONUNBUFFERED=1`** — логи (`print(...)`) сразу видны в `docker compose logs`, без буферизации.

---

## Шаг 4 — `.dockerignore`

```
# Python артефакты
__pycache__/
*.pyc
*.pyo
*.pyd
.Python
*.egg-info/

# Виртуальные окружения
.venv/
venv/
env/

# Документация и прочее
HLE_v0.1.md
HLE_v0.2.md
*.md

# Git
.git/
.gitignore

# IDE
.idea/
.vscode/
*.DS_Store

# Тесты (если появятся)
tests/
.pytest_cache/
```

**Зачем:** Docker COPY копирует всё что есть, если не указать исключения. `.dockerignore` работает как `.gitignore` — уменьшает build context и не тащит мусор в образ.

---

## Шаг 5 — Сборка и первый запуск

```bash
# Перейти в корень проекта
cd episode-duration-comparator

# Собрать образ (первый раз ~2-3 минуты)
docker compose build

# Запустить
docker compose up

# Или в фоне:
docker compose up -d
```

Открыть в браузере: `http://localhost:8000`

---

## Шаг 6 — Полезные команды для работы

```bash
# Посмотреть логи (в реальном времени)
docker compose logs -f

# Остановить
docker compose stop

# Остановить и удалить контейнер
docker compose down

# Пересобрать после изменений в коде
docker compose build && docker compose up -d

# Зайти внутрь контейнера для отладки
docker compose exec app bash

# Посмотреть размер образа
docker images | grep episode-comparator
```

---

## Шаг 7 — Проверочный чеклист

- [ ] `docker compose build` завершается без ошибок
- [ ] `docker compose up` запускается, в логах нет `ERROR`
- [ ] `http://localhost:8000` открывается в браузере
- [ ] Форма принимает два URL и возвращает таблицу
- [ ] `docker compose stop && docker compose up` — приложение поднимается повторно
- [ ] Перезагрузить машину → `docker compose up -d` → работает (`restart: unless-stopped` в действии)

---

## Возможные проблемы и решения

**`ModuleNotFoundError: No module named 'app'`**  
Uvicorn запущен не из корня `/app`. Убедиться, что `WORKDIR /app` и `COPY app/ ./app/` стоят именно так.

**`Permission denied` при старте**  
Если `static/` или `templates/` не читаются от `appuser` — временно убрать `USER appuser` для диагностики, потом вернуть.

**Порт 8000 занят**  
Поменять в `docker-compose.yml`: `"8080:8000"` → открывать `http://localhost:8080`.

**Медленная сборка при каждом изменении кода**  
Слои Docker кешируются снизу вверх. `COPY requirements.txt` и `pip install` идут до `COPY app/` — именно поэтому переустановка зависимостей происходит только при изменении `requirements.txt`, а не при каждом изменении кода.

---

## Следующий шаг после 1.9

После успешной контейнеризации — **этап 2.1: Telegram-бот**.

Бот будет работать в том же `docker-compose.yml` как второй сервис:

```yaml
services:
  app:       # веб-интерфейс (уже есть)
    ...
  bot:       # Telegram-бот (будет добавлен на этапе 2.1)
    build: .
    command: ["python", "-m", "bot.main"]
    environment:
      - BOT_TOKEN=${BOT_TOKEN}
      - APP_URL=http://app:8000
    depends_on:
      - app
    restart: unless-stopped
```

Оба сервиса в одной сети Docker — бот сможет обращаться к FastAPI-приложению по внутреннему адресу `http://app:8000`, без проброса наружу.
