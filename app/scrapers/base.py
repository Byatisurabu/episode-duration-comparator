# app/scrapers/base.py
from abc import ABC, abstractmethod
from typing import List, Optional

from app.models import ScrapeResult


class BaseScraper(ABC):

    @abstractmethod
    async def scrape_episodes(self, url: str) -> ScrapeResult:
        pass

    @abstractmethod
    async def get_seasons(self, url: str) -> List[int]:
        pass

    async def get_season_url(self, url: str, season: int) -> str:
        """
        Возвращает URL конкретного сезона. По умолчанию — синхронный build_season_url.
        Скрейперы, которым нужна async-логика (например Amediateka),
        переопределяют этот метод.
        """
        return self.build_season_url(url, season)

    @abstractmethod
    async def get_series_title(self, url: str) -> Optional[str]:
        """
        Извлечь название сериала из главной страницы.
        """
        pass

    @abstractmethod
    def build_season_url(self, url: str, season: int) -> str:
        """
        Построить URL конкретного сезона из любого URL сериала.
        Например: build_season_url("https://imdb.com/title/tt0903747/", 3)
                  → "https://www.imdb.com/title/tt0903747/episodes?season=3"
        """
        pass


class ScraperFactory:
    _scrapers = {}

    @classmethod
    def register(cls, key: str, scraper_class):
        cls._scrapers[key] = scraper_class

    @classmethod
    def get_scraper(cls, service_key: str) -> Optional[BaseScraper]:
        scraper_class = cls._scrapers.get(service_key)
        if scraper_class:
            return scraper_class()
        return None
