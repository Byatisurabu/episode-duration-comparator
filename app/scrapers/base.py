# app/scrapers/base.py
from abc import ABC, abstractmethod
from typing import List, Optional

from app.models import Episode


class BaseScraper(ABC):
    @abstractmethod
    async def scrape_episodes(self, url: str) -> List[Episode]:
        """Вернуть список Episode с заполненными duration_min"""
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