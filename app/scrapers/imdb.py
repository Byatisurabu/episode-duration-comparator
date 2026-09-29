# app/scrapers/imdb.py
#
# IMDb — данные из локальной базы, собранной из официальных датасетов IMDb
# (см. app/services/imdb_dataset.py). Сетевых запросов к IMDb здесь нет:
# GraphQL API закрыт для сторонних клиентов.

import logging
import re
from typing import List, Optional

from app.models import Episode, ScrapeResult
from app.scrapers.base import BaseScraper, ServiceUnavailableError
from app.services.imdb_dataset import ImdbDataset, ImdbDatasetNotReady

logger = logging.getLogger(__name__)

NOT_READY_MESSAGE = "База IMDb ещё загружается — попробуйте через пару минут."


class ImdbScraper(BaseScraper):

    def __init__(self, dataset: Optional[ImdbDataset] = None):
        self._dataset = dataset or ImdbDataset()

    async def scrape_episodes(self, url: str) -> ScrapeResult:
        series_id = self._extract_series_id(url)
        if not series_id:
            return ScrapeResult(series_title=None, episodes=[])

        season = self._extract_season(url)
        if season is None:
            logger.warning("Не удалось определить сезон из URL: %s", url)
            return ScrapeResult(series_title=None, episodes=[])

        try:
            series_title = await self._dataset.get_series_title(series_id)
            rows = await self._dataset.get_episodes(series_id, season)
        except ImdbDatasetNotReady:
            raise ServiceUnavailableError(NOT_READY_MESSAGE)

        episodes = [
            Episode(
                season=r.season,
                episode=r.episode,
                title=r.title,
                duration_min=r.runtime_min,
                episode_id=r.episode_id,
            )
            for r in rows
        ]
        return ScrapeResult(series_title=series_title, episodes=episodes)

    def _extract_series_id(self, url: str) -> Optional[str]:
        m = re.search(r'/title/(tt\d+)', url)
        return m.group(1) if m else None

    def _extract_season(self, url: str) -> Optional[int]:
        """Извлекает номер сезона из URL вида ?season=N."""
        m = re.search(r'[?&]season=(\d+)', url)
        return int(m.group(1)) if m else None

    async def get_seasons(self, url: str) -> List[int]:
        series_id = self._extract_series_id(url)
        if not series_id:
            return []

        try:
            return await self._dataset.get_seasons(series_id)
        except ImdbDatasetNotReady:
            raise ServiceUnavailableError(NOT_READY_MESSAGE)

    def build_season_url(self, url: str, season: int) -> str:
        series_id = self._extract_series_id(url)
        if not series_id:
            return url
        return f"https://www.imdb.com/title/{series_id}/episodes?season={season}"

    async def get_series_title(self, url: str) -> Optional[str]:
        series_id = self._extract_series_id(url)
        if not series_id:
            return None

        try:
            return await self._dataset.get_series_title(series_id)
        except ImdbDatasetNotReady:
            raise ServiceUnavailableError(NOT_READY_MESSAGE)
