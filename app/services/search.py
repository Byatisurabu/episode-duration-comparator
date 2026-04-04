# app/services/search.py
#
# Поиск сериалов по названию.
# IMDb: публичный Suggestion API.
# Amediateka: публичный API api.amediateka.tech.

import logging
import uuid
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

# ── IMDb ──────────────────────────────────────────────────────────────────────

IMDB_SUGGESTION_URL = "https://v3.sg.media-imdb.com/suggestion/{first}/{query}.json"

IMDB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
}

TV_TYPES = {"tvSeries", "tvMiniSeries"}

# ── Amediateka ────────────────────────────────────────────────────────────────

AMEDIATEKA_SEARCH_URL = "https://api.amediateka.tech/gateway/v1/cms/v2/search/"

AMEDIATEKA_HEADERS = {
    "Origin": "https://www.amediateka.ru",
    "Referer": "https://www.amediateka.ru/",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
}

# Фиксированные device-параметры — отслеживающие, не авторизационные
_AMEDIATEKA_DEVICE_PARAMS = {
    "deviceId": "f035bd81cb59771944f8f28c2da0969c",
    "deviceModel": "Chrome-144",
    "deviceType": "desktopWeb",
    "deviceVendor": "none",
    "os": "Windows",
    "osVersion": "10",
    "browserVersion": "144",
    "browserType": "Chrome",
    "platform": "amediaWeb",
    "supportLive": "true",
    "supportFlexibleTrial": "true",
}

AMEDIATEKA_BASE_URL = "https://www.amediateka.ru"
AMEDIATEKA_POSTER_SIZE = "80x118"


@dataclass
class SearchResult:
    series_id: str          # tt0903747 или "28661"
    title: str
    year: Optional[str]     # "2008-2013" или "2021"
    poster_url: Optional[str]
    url: str                # полный URL сериала


async def search_series(query: str) -> list[SearchResult]:
    """Ищет сериалы по названию через IMDb Suggestion API."""
    query = query.strip()
    if len(query) < 2:
        return []

    first = query[0].lower()
    encoded = quote(query.lower())
    url = IMDB_SUGGESTION_URL.format(first=first, query=encoded)

    async with httpx.AsyncClient(headers=IMDB_HEADERS, timeout=8.0) as client:
        try:
            resp = await client.get(url)
            if resp.status_code != 200:
                logger.warning("IMDb Suggestion API → status %d", resp.status_code)
                return []
            data = resp.json()
        except Exception as e:
            logger.error("IMDb Suggestion API error: %s", e)
            return []

    results = []
    for item in data.get("d", []):
        if item.get("qid") not in TV_TYPES:
            continue

        series_id = item.get("id", "")
        if not series_id.startswith("tt"):
            continue

        title = item.get("l", "")
        year = item.get("yr") or (str(item["y"]) if item.get("y") else None)
        poster_url = (item.get("i") or {}).get("imageUrl")

        if poster_url and "._V1_." in poster_url:
            poster_url = poster_url.replace("._V1_.", "._V1_UX80_CR0,0,80,118_.")

        results.append(SearchResult(
            series_id=series_id,
            title=title,
            year=year,
            poster_url=poster_url,
            url=f"https://www.imdb.com/title/{series_id}/",
        ))

    return results


async def search_amediateka(query: str) -> list[SearchResult]:
    """Ищет сериалы по названию через Amediateka API. Возвращает только type=series."""
    query = query.strip()
    if len(query) < 2:
        return []

    params = {
        **_AMEDIATEKA_DEVICE_PARAMS,
        "browserTabId": str(uuid.uuid4()),
        "search": query,
    }

    async with httpx.AsyncClient(headers=AMEDIATEKA_HEADERS, timeout=8.0) as client:
        try:
            resp = await client.get(AMEDIATEKA_SEARCH_URL, params=params)
            if resp.status_code != 200:
                logger.warning("Amediateka Search API → status %d", resp.status_code)
                return []
            data = resp.json()
        except Exception as e:
            logger.error("Amediateka Search API error: %s", e)
            return []

    # Ответ — массив объектов с type="contents"
    results = []
    for block in data:
        if block.get("type") != "contents":
            continue
        for item in block.get("content", {}).get("results", []):
            if item.get("type") != "series":
                continue

            series_id = str(item["id"])
            title = item.get("title", "")
            year = str(item["premiereYear"]) if item.get("premiereYear") else None
            web_url = item.get("webUrl", "")

            poster_url = None
            poster_raw = (item.get("assets") or {}).get("productPoster")
            if poster_raw:
                poster_url = poster_raw.replace("{SIZE}", AMEDIATEKA_POSTER_SIZE)

            results.append(SearchResult(
                series_id=series_id,
                title=title,
                year=year,
                poster_url=poster_url,
                url=f"{AMEDIATEKA_BASE_URL}{web_url}",
            ))

    return results
