# app/scrapers/amediateka.py
import re
import json
import asyncio
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from app.scrapers.base import BaseScraper
from app.models import Episode, ScrapeResult
from app.services.normalizer import normalize_duration


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
                    print(f"Amediateka → {resp.status_code}")
                    return None
                return resp.text
            except Exception as e:
                print(f"Ошибка запроса Amediateka: {e}")
                return None

    def _parse_next_data(self, html: str) -> list[Episode]:
        match = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if not match:
            print("Не найден __NEXT_DATA__ на Amediateka")
            return []

        try:
            data = json.loads(match.group(1))
            props = data.get("props", {})
            page_props = props.get("pageProps", {})
            content = page_props.get("content", {})

            if content.get("type") != "season":
                print("content.type != 'season' → возможно не страница сезона")
                return []

            episodes_raw = content.get("episodes", [])
            if not episodes_raw:
                print("episodes пустой массив")
                return []

            episodes = []
            for ep in episodes_raw:
                num = ep.get("number")
                if not isinstance(num, int):
                    continue

                title = ep.get("title", "Без названия").strip()
                duration_raw = ep.get("duration")
                duration_min = normalize_duration(duration_raw)

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
            print(f"JSON decode error в __NEXT_DATA__: {e}")
            return []
        except Exception as e:
            print(f"Ошибка парсинга __NEXT_DATA__: {e}")
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
                series_title = content.get("series", {}).get("title")
                if series_title and isinstance(series_title, str):
                    print(f"Amediateka: название из JSON → {series_title}")
                    return series_title.strip()
            except Exception as e:
                print(f"Amediateka: ошибка в JSON → {e}")

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
                    print(f"Amediateka: название из <title> → {candidate}")
                    return candidate

        print("Amediateka: название сериала не удалось извлечь")
        return None