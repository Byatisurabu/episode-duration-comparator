# app/scrapers/imdb.py

import re
import json
import logging
from typing import List, Optional
from math import ceil

import httpx

from app.scrapers.base import BaseScraper
from app.models import Episode, ScrapeResult

logger = logging.getLogger(__name__)

GRAPHQL_URL = "https://graphql.imdb.com/"

GRAPHQL_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    "Content-Type": "application/json",
}

EPISODES_QUERY = """
query Episodes($id: ID!, $season: String!) {
  title(id: $id) {
    titleText {
      text
    }
    episodes {
      episodes(first: 250, filter: {includeSeasons: [$season]}) {
        edges {
          node {
            id
            titleText {
              text
            }
            series {
              episodeNumber {
                seasonNumber
                episodeNumber
              }
            }
            runtime {
              seconds
            }
          }
        }
      }
    }
  }
}
"""

SEASONS_QUERY = """
query Seasons($id: ID!) {
  title(id: $id) {
    titleText {
      text
    }
    episodes {
      seasons {
        number
      }
    }
  }
}
"""

TITLE_QUERY = """
query Title($id: ID!) {
  title(id: $id) {
    titleText {
      text
    }
  }
}
"""


class ImdbScraper(BaseScraper):

    async def _graphql(self, query: str, variables: dict) -> Optional[dict]:
        """Выполняет GraphQL-запрос к IMDb API."""
        async with httpx.AsyncClient(headers=GRAPHQL_HEADERS, timeout=15.0) as client:
            try:
                resp = await client.post(
                    GRAPHQL_URL,
                    json={"query": query, "variables": variables},
                )
                if resp.status_code != 200:
                    logger.warning("IMDb GraphQL → status %d", resp.status_code)
                    return None
                data = resp.json()
                if "errors" in data:
                    logger.warning("IMDb GraphQL errors: %s", data["errors"])
                return data.get("data")
            except Exception as e:
                logger.error("IMDb GraphQL request failed: %s", e)
                return None

    async def scrape_episodes(self, url: str) -> ScrapeResult:
        series_id = self._extract_series_id(url)
        if not series_id:
            return ScrapeResult(series_title=None, episodes=[])

        season = self._extract_season(url)
        if season is None:
            logger.warning("Не удалось определить сезон из URL: %s", url)
            return ScrapeResult(series_title=None, episodes=[])

        data = await self._graphql(
            EPISODES_QUERY,
            {"id": series_id, "season": str(season)},
        )
        if not data or not data.get("title"):
            return ScrapeResult(series_title=None, episodes=[])

        title_data = data["title"]
        series_title = (title_data.get("titleText") or {}).get("text")

        edges = (
            title_data
            .get("episodes", {})
            .get("episodes", {})
            .get("edges", [])
        )

        episodes = []
        for edge in edges:
            node = edge.get("node", {})
            ep_number_data = (
                node.get("series", {})
                .get("episodeNumber", {})
            )
            ep_season = ep_number_data.get("seasonNumber")
            ep_num = ep_number_data.get("episodeNumber")
            ep_title = (node.get("titleText") or {}).get("text", "")
            ep_id = node.get("id")

            runtime_seconds = (node.get("runtime") or {}).get("seconds")
            duration_min = ceil(runtime_seconds / 60) if runtime_seconds else None

            if ep_season is not None and ep_num is not None:
                episodes.append(Episode(
                    season=int(ep_season),
                    episode=int(ep_num),
                    title=ep_title,
                    duration_min=duration_min,
                    episode_id=ep_id,
                ))

        # Логируем отфильтрованные эпизоды без длительности
        no_duration = [ep for ep in episodes if ep.duration_min is None]
        if no_duration:
            logger.warning(
                "IMDb: %d эпизод(ов) без длительности (сезон %d): %s",
                len(no_duration), season,
                [f"S{ep.season}E{ep.episode}" for ep in no_duration],
            )

        return ScrapeResult(
            series_title=series_title,
            episodes=[ep for ep in episodes if ep.duration_min is not None],
        )

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

        data = await self._graphql(SEASONS_QUERY, {"id": series_id})
        if not data or not data.get("title"):
            return []

        seasons_data = (
            data["title"]
            .get("episodes", {})
            .get("seasons", [])
        )
        return sorted(s["number"] for s in seasons_data if "number" in s)

    def build_season_url(self, url: str, season: int) -> str:
        series_id = self._extract_series_id(url)
        if not series_id:
            return url
        return f"https://www.imdb.com/title/{series_id}/episodes?season={season}"

    async def get_series_title(self, url: str) -> Optional[str]:
        series_id = self._extract_series_id(url)
        if not series_id:
            return None

        data = await self._graphql(TITLE_QUERY, {"id": series_id})
        if not data or not data.get("title"):
            return None

        return (data["title"].get("titleText") or {}).get("text")
