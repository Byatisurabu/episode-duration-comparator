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
COPY bot/ ./bot/

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