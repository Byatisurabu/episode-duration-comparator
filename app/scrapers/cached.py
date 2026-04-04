# app/scrapers/cached.py
#
# Декоратор над BaseScraper, добавляющий кеширование через EpisodeCache.
# Не меняет сами скрейперы — просто оборачивает их.

import logging
import re
from typing import List, Optional

from app.cache.sqlite_cache import EpisodeCache
from app.models import ScrapeResult
from app.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


class CachedScraper(BaseScraper):
    def __init__(self, scraper: BaseScraper, cache: EpisodeCache, service: str):
        self._scraper = scraper
        self._cache = cache
        self._service = service

    async def scrape_episodes(self, url: str, force_refresh: bool = False) -> ScrapeResult:
        series_id = self._extract_id(url)
        season = self._extract_season(url)

        if series_id and season is not None and not force_refresh:
            cached = await self._cache.get(self._service, series_id, season)
            if cached is not None:
                return cached

        result = await self._scraper.scrape_episodes(url)

        if series_id and season is not None and result.episodes:
            await self._cache.put(self._service, series_id, season, result)

        return result

    # ── Делегируем остальные методы напрямую к оригинальному скрейперу ────────

    async def get_seasons(self, url: str) -> List[int]:
        return await self._scraper.get_seasons(url)

    def build_season_url(self, url: str, season: int) -> str:
        return self._scraper.build_season_url(url, season)

    async def get_season_url(self, url: str, season: int) -> str:
        return await self._scraper.get_season_url(url, season)

    async def get_series_title(self, url: str) -> Optional[str]:
        return await self._scraper.get_series_title(url)

    # ── Вспомогательные методы ─────────────────────────────────────────────────

    def _extract_id(self, url: str) -> Optional[str]:
        """Извлекает идентификатор сериала из URL для любого сервиса."""
        # IMDb: /title/tt0903747/
        m = re.search(r'/title/(tt\d+)', url)
        if m:
            return m.group(1)
        # Amediateka: /watch/series_123_someshow/
        m = re.search(r'/watch/(series_\d+_[^/]+)', url)
        if m:
            return m.group(1)
        return None

    def _extract_season(self, url: str) -> Optional[int]:
        """Извлекает номер сезона из URL."""
        # IMDb: ?season=1
        m = re.search(r'[?&]season=(\d+)', url)
        if m:
            return int(m.group(1))
        # Amediateka: /season_3_11978
        m = re.search(r'/season_(\d+)_\d+', url)
        if m:
            return int(m.group(1))
        return None
