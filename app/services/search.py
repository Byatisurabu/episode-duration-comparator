# app/services/search.py
#
# Поиск сериалов по названию через IMDb Suggestion API.
# Публичный API, не требует ключей.

import logging
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

SUGGESTION_URL = "https://v3.sg.media-imdb.com/suggestion/{first}/{query}.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
}

# Только эти типы считаем сериалами
TV_TYPES = {"tvSeries", "tvMiniSeries"}


@dataclass
class SearchResult:
    series_id: str          # tt0903747
    title: str              # Breaking Bad
    year: Optional[str]     # "2008-2013" или "2008"
    poster_url: Optional[str]
    url: str                # https://www.imdb.com/title/tt0903747/


async def search_series(query: str) -> list[SearchResult]:
    """
    Ищет сериалы по названию через IMDb Suggestion API.
    Возвращает список результатов, отфильтрованных по типу tvSeries/tvMiniSeries.
    """
    query = query.strip()
    if len(query) < 2:
        return []

    # API использует первую букву как часть пути
    first = query[0].lower()
    encoded = quote(query.lower())
    url = SUGGESTION_URL.format(first=first, query=encoded)

    async with httpx.AsyncClient(headers=HEADERS, timeout=8.0) as client:
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

        # Уменьшаем постер до разумного размера через параметр URL Amazon
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
