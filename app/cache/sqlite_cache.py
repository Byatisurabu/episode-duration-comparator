# app/cache/sqlite_cache.py
#
# Кеш результатов скрейпинга на базе SQLite (через aiosqlite).
# Ключ: (service, series_id, season). TTL: 7 дней.

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import aiosqlite

from app.models import Episode, ScrapeResult

logger = logging.getLogger(__name__)

DB_PATH = Path("data/cache.db")
TTL_DAYS = 7


class EpisodeCache:
    def __init__(self, db_path: Path = DB_PATH, ttl_days: int = TTL_DAYS):
        self.db_path = db_path
        self.ttl = timedelta(days=ttl_days)

    async def init_db(self) -> None:
        """Создаёт таблицу, если не существует. Вызывать при старте."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS episode_cache (
                    service    TEXT NOT NULL,
                    series_id  TEXT NOT NULL,
                    season     INTEGER NOT NULL,
                    data       TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    PRIMARY KEY (service, series_id, season)
                )
            """)
            await db.commit()
        logger.info("EpisodeCache: БД инициализирована (%s)", self.db_path)

    async def get(self, service: str, series_id: str, season: int) -> Optional[ScrapeResult]:
        """Возвращает закешированный результат или None если нет/устарел."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT data, fetched_at FROM episode_cache WHERE service=? AND series_id=? AND season=?",
                (service, series_id, season),
            ) as cursor:
                row = await cursor.fetchone()

        if row is None:
            return None

        data_json, fetched_at_str = row
        fetched_at = datetime.fromisoformat(fetched_at_str)
        if datetime.now(timezone.utc) - fetched_at > self.ttl:
            logger.info("Cache expired: %s/%s/s%d", service, series_id, season)
            return None

        logger.info("Cache hit: %s/%s/s%d", service, series_id, season)
        return _deserialize(data_json)

    async def put(self, service: str, series_id: str, season: int, result: ScrapeResult) -> None:
        """Сохраняет результат в кеш. Перезаписывает если запись уже есть."""
        data_json = _serialize(result)
        fetched_at = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO episode_cache (service, series_id, season, data, fetched_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(service, series_id, season) DO UPDATE SET
                    data=excluded.data,
                    fetched_at=excluded.fetched_at
                """,
                (service, series_id, season, data_json, fetched_at),
            )
            await db.commit()
        logger.info("Cache put: %s/%s/s%d (%d episodes)", service, series_id, season, len(result.episodes or []))

    async def invalidate(self, service: str, series_id: str, season: int) -> None:
        """Удаляет запись из кеша."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM episode_cache WHERE service=? AND series_id=? AND season=?",
                (service, series_id, season),
            )
            await db.commit()
        logger.info("Cache invalidated: %s/%s/s%d", service, series_id, season)


# ── Сериализация ──────────────────────────────────────────────────────────────

def _serialize(result: ScrapeResult) -> str:
    return json.dumps({
        "series_title": result.series_title,
        "episodes": [
            {
                "season": ep.season,
                "episode": ep.episode,
                "title": ep.title,
                "duration_min": ep.duration_min,
                "episode_id": ep.episode_id,
            }
            for ep in (result.episodes or [])
        ],
    }, ensure_ascii=False)


def _deserialize(data_json: str) -> ScrapeResult:
    data = json.loads(data_json)
    episodes = [
        Episode(
            season=ep["season"],
            episode=ep["episode"],
            title=ep["title"],
            duration_min=ep.get("duration_min"),
            episode_id=ep.get("episode_id"),
        )
        for ep in data.get("episodes", [])
    ]
    return ScrapeResult(series_title=data.get("series_title"), episodes=episodes)
