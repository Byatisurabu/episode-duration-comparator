# app/scrapers/amediateka.py
import json
import logging
import re
from typing import List, Optional

import httpx

from app.models import Episode, ScrapeResult
from app.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


class AmediatekaScraper(BaseScraper):
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    async def scrape_episodes(self, url: str) -> ScrapeResult:
        html = await self._fetch_page(url)
        if not html:
            return ScrapeResult(series_title=None, episodes=[])

        series_title = await self._extract_series_title(html) or "Amediateka — название не определено"
        episodes = self._parse_next_data(html)

        # Можно добавить асинхронное обогащение, если где-то не хватает данных
        # но в твоём случае всё уже в __NEXT_DATA__

        return ScrapeResult(series_title=series_title, episodes=episodes)

    async def _fetch_page(self, url: str) -> Optional[str]:
        async with httpx.AsyncClient(headers=self.HEADERS, follow_redirects=True, timeout=15.0) as client:
            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    logger.warning("Amediateka → status %d", resp.status_code)
                    return None
                return resp.text
            except Exception as e:
                logger.error("Ошибка запроса Amediateka: %s", e)
                return None

    def _parse_next_data(self, html: str) -> list[Episode]:
        match = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if not match:
            logger.warning("Не найден __NEXT_DATA__ на Amediateka")
            return []

        try:
            data = json.loads(match.group(1))
            props = data.get("props", {})
            page_props = props.get("pageProps", {})
            content = page_props.get("content", {})

            if content.get("type") != "season":
                logger.warning("content.type != 'season' → возможно не страница сезона")
                return []

            episodes_raw = content.get("episodes", [])
            if not episodes_raw:
                logger.warning("episodes пустой массив")
                return []

            episodes = []
            for ep in episodes_raw:
                num = ep.get("number")
                if not isinstance(num, int):
                    continue

                title = ep.get("title", "Без названия").strip()

                # Amediateka хранит длительность в секундах.
                # Поле может быть int (1270) или str ("1270") — обрабатываем оба варианта.
                duration_raw = ep.get("duration")
                try:
                    duration_seconds = int(duration_raw) if duration_raw else 0
                except (ValueError, TypeError):
                    duration_seconds = 0
                duration_min = (duration_seconds // 60) if duration_seconds > 0 else None

                episodes.append(Episode(
                    season=content.get("seasonNumber", 0),  # или брать из url, если нужно
                    episode=num,
                    title=title,
                    duration_min=duration_min,
                    episode_id=str(ep.get("id")) if ep.get("id") else None
                ))

            # Сортируем на всякий случай
            episodes.sort(key=lambda e: e.episode)
            return episodes

        except json.JSONDecodeError as e:
            logger.error("JSON decode error в __NEXT_DATA__: %s", e)
            return []
        except Exception as e:
            logger.error("Ошибка парсинга __NEXT_DATA__: %s", e)
            return []

    async def _extract_series_title(self, html: str) -> Optional[str]:
        # Приоритет 1 — JSON (самое точное)
        match = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(1))
                content = (
                    data.get("props", {})
                    .get("pageProps", {})
                    .get("content", {})
                )
                content_type = content.get("type")

                if content_type == "series":
                    # Головная страница сериала — название прямо в content.title
                    series_title = content.get("title")
                else:
                    # Страница сезона — название в content.series.title
                    series_title = content.get("series", {}).get("title")

                if series_title and isinstance(series_title, str):
                    # print(f"Amediateka: название из JSON ({content_type}) → {series_title}")
                    return series_title.strip()
            except Exception as e:
                logger.error("Amediateka: ошибка в JSON → %s", e)

        # Приоритет 2 — fallback на <title> страницы
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        title_tag = soup.title
        if title_tag:
            text = title_tag.get_text(strip=True)
            # Примеры: "Клиент всегда мертв — Сезон 3 | Amediateka"
            #          "Клиент всегда мертв. Сезон 3 – смотреть онлайн"
            if "—" in text or " | " in text or " – " in text:
                parts = re.split(r'[—–|]', text, maxsplit=1)
                candidate = parts[0].strip()
                # Убираем возможный хвост с сезоном
                candidate = re.sub(r'(Сезон|Season)\s*\d+.*$', '', candidate, flags=re.I).strip()
                if candidate:
                    logger.info("Amediateka: название из <title> → %s", candidate)
                    return candidate

        logger.warning("Amediateka: название сериала не удалось извлечь")
        return None

    async def get_seasons(self, url: str) -> List[int]:
        """Возвращает список номеров сезонов."""
        season_map = await self._get_season_url_map(url)
        return sorted(season_map.keys())


    async def _get_season_url_map(self, url: str) -> dict[int, str]:
        """
        Возвращает маппинг {номер_сезона: полный_url} из longDescription.
        Например: {1: 'https://...season_1_11976', 2: 'https://...season_2_11977'}
        """
        html = await self._fetch_page(url)
        if not html:
            return {}

        match = re.search(
            r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
            html, re.DOTALL
        )
        if not match:
            return {}

        try:
            data = json.loads(match.group(1))
            page_props = data.get("props", {}).get("pageProps", {})
            content = page_props.get("content", {})
            content_type = content.get("type")

            # Хелпер: извлечь season_map из любой строки с HTML-ссылками
            def extract_from_long_desc(long_desc: str) -> dict:
                if not long_desc:
                    return {}
                found = re.findall(
                    r'href="(https://www\.amediateka\.ru)?(/watch/series_\d+_[^/]+/season_(\d+)_\d+)"',
                    long_desc
                )
                return {
                    int(season_num): f"https://www.amediateka.ru{path}"
                    for _, path, season_num in found
                }

            # Источник 1а — головная страница сериала (content.type == "series")
            # longDescription лежит прямо в content
            if content_type == "series":
                season_map = extract_from_long_desc(content.get("longDescription", ""))
                if season_map:
                    return season_map

            # Источник 1б — страница сезона (content.type == "season")
            # longDescription лежит в seriesContent
            series_content = page_props.get("seriesContent", {})
            season_map = extract_from_long_desc(series_content.get("longDescription", ""))
            if season_map:
                return season_map

            # Источник 2 — fallback: только текущий сезон из content
            current_season = content.get("seasonNumber")
            web_url = content.get("webUrl", "")
            if isinstance(current_season, int) and web_url:
                return {current_season: f"https://www.amediateka.ru{web_url}"}

            return {}

        except Exception as e:
            logger.error("Amediateka _get_season_url_map error: %s", e)
            return {}


    def build_season_url(self, url: str, season: int) -> str:
        """
        Синхронная версия не может использовать _get_season_url_map (она async).
        Этот метод используется только как fallback — основной путь через get_season_url().
        """
        match = re.search(r'/watch/(series_\d+_[^/]+)/', url)
        if match:
            series_alias = match.group(1)
            # Без season_id — будет работать только если Amediateka поддерживает редирект
            return f"https://www.amediateka.ru/watch/{series_alias}/season_{season}"
        return url


    async def get_season_url(self, url: str, season: int) -> str:
        """
        Возвращает точный URL нужного сезона включая season_id.
        Использовать вместо build_season_url для Amediateka.
        """
        season_map = await self._get_season_url_map(url)
        if season in season_map:
            return season_map[season]
        # Fallback на синхронный метод
        return self.build_season_url(url, season)

    async def get_series_title(self, url: str) -> Optional[str]:
        html = await self._fetch_page(url)
        if not html:
            return None
        return self._extract_series_title(html)
