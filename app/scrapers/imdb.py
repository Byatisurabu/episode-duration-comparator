# app/scrapers/imdb.py

import re
import asyncio
import json
from typing import List, Optional

import httpx
from bs4 import BeautifulSoup

from app.scrapers.base import BaseScraper
from app.models import Episode, ScrapeResult


class ImdbScraper(BaseScraper):
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    async def scrape_episodes(self, url: str) -> ScrapeResult:
        # 1. Получаем ID сериала из URL эпизодов
        series_id = self._extract_series_id(url)
        if not series_id:
            return ScrapeResult(series_title=None, episodes=[])

        # 2. Получаем название сериала (с главной страницы)
        series_title = await self._get_series_title(series_id)

        # 3. Получаем список эпизодов (как раньше)
        episodes = await self._extract_episode_list_from_page(url)
        if not episodes:
            return ScrapeResult(series_title=series_title, episodes=[])

        # 4. Обогащаем длительностями
        tasks = [self._enrich_with_duration(ep) for ep in episodes]
        await asyncio.gather(*tasks, return_exceptions=True)

        return ScrapeResult(
            series_title=series_title,
            episodes=[ep for ep in episodes if ep.duration_min is not None]
        )

    def _extract_series_id(self, url: str) -> Optional[str]:
        m = re.search(r'/title/(tt\d+)', url)
        return m.group(1) if m else None

    async def _get_series_title(self, series_id: str) -> Optional[str]:
        main_url = f"https://www.imdb.com/title/{series_id}/"
        async with httpx.AsyncClient(headers=self.HEADERS, timeout=10.0) as client:
            try:
                resp = await client.get(main_url, follow_redirects=True)
                if resp.status_code != 200:
                    return None

                soup = BeautifulSoup(resp.text, "html.parser")

                # Вариант 1 — JSON-LD (самый надёжный)
                script = soup.find("script", type="application/ld+json")
                if script:
                    try:
                        data = json.loads(script.string)
                        name = data.get("name")
                        if name:
                            return name.strip()
                    except:
                        pass

                # Вариант 2 — h1 заголовок
                h1 = soup.select_one("h1[data-testid='hero-title-block__title']")
                if h1:
                    return h1.get_text(strip=True)

                # Вариант 3 — запасной (очень старый, но иногда работает)
                title_tag = soup.find("title")
                if title_tag:
                    text = title_tag.get_text(strip=True)
                    # часто "One Piece (TV Series 1999– ) - IMDb"
                    if " - IMDb" in text:
                        return text.split(" - IMDb")[0].strip()

                return None

            except Exception as e:
                print(f"Ошибка при получении названия сериала {series_id}: {e}")
                return None

    async def _extract_episode_list_from_page(self, url: str) -> List[Episode]:
        async with httpx.AsyncClient(headers=self.HEADERS, follow_redirects=True, timeout=20.0) as client:
            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    print(f"IMDb → status {resp.status_code}")
                    return []

                html = resp.text
                soup = BeautifulSoup(html, "html.parser")

                # Вариант 1 — ищем application/ld+json (самый надёжный, если есть)
                scripts = soup.find_all("script", type="application/ld+json")
                for script in scripts:
                    try:
                        data = json.loads(script.string)
                        if isinstance(data, dict) and data.get("@type") == "ItemList":
                            items = data.get("itemListElement", [])
                            episodes = []
                            for item in items:
                                ep_data = item.get("item", {})
                                if ep_data.get("@type") == "Episode":
                                    season = ep_data.get("partOfSeason", {}).get("seasonNumber")
                                    ep_num = ep_data.get("episodeNumber")
                                    title = ep_data.get("name")
                                    url = ep_data.get("url")
                                    if season and ep_num and title:
                                        episode_id = re.search(r'tt\d+', url).group(0) if url else None
                                        episodes.append(Episode(
                                            season=int(season),
                                            episode=int(ep_num),
                                            title=title,
                                            episode_id=episode_id
                                        ))
                            if episodes:
                                return episodes
                    except Exception:
                        pass

                # Вариант 2 — fallback на твою регулярку (работала раньше)
                pattern = re.compile(
                    r'\{'
                    r'"id":"(tt\d+)",'
                    r'"type":"tvEpisode",'
                    r'"season":"(\d+)",'
                    r'"episode":"(\d+)",'
                    r'"titleText":"([^"]*)",'
                    r'[^\}]*\}',
                    re.DOTALL
                )

                matches = pattern.findall(html)
                episodes = []
                for match in matches:
                    episode_id, season_str, ep_str, title = match
                    try:
                        episodes.append(Episode(
                            season=int(season_str),
                            episode=int(ep_str),
                            title=title.strip(),
                            episode_id=episode_id
                        ))
                    except ValueError:
                        continue

                if episodes:
                    return sorted(episodes, key=lambda e: (e.season, e.episode))

                print("Не нашли ни ld+json, ни старый JSON-паттерн")
                return []

            except Exception as e:
                print(f"Ошибка при парсинге списка: {e}")
                return []

    async def _enrich_with_duration(self, episode: Episode):
        if not episode.episode_id:
            return

        url = f"https://www.imdb.com/title/{episode.episode_id}/"
        async with httpx.AsyncClient(headers=self.HEADERS, timeout=12.0) as client:
            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    return

                html = resp.text

                # Вариант А — ищем в ld+json на странице эпизода
                soup = BeautifulSoup(html, "html.parser")
                script = soup.find("script", type="application/ld+json")
                if script:
                    try:
                        data = json.loads(script.string)
                        duration = data.get("duration")  # PT45M
                        if duration and duration.startswith("PT"):
                            minutes = 0
                            h = re.search(r'(\d+)H', duration)
                            m = re.search(r'(\d+)M', duration)
                            if h:
                                minutes += int(h.group(1)) * 60
                            if m:
                                minutes += int(m.group(1))
                            if minutes > 0:
                                episode.duration_min = minutes
                                return
                    except:
                        pass

                # Вариант Б — ищем текст "X min" / "Xh Ym" в технических данных
                runtime_texts = re.findall(
                    r'(\d{1,2}h?\s*\d{0,2}m?)',
                    html.lower()
                )
                for txt in runtime_texts:
                    minutes = self._parse_runtime(txt)
                    if minutes and 5 < minutes < 300:  # разумные границы
                        episode.duration_min = minutes
                        return

            except Exception as e:
                print(f"Ошибка при парсинге длительности {episode.episode_id}: {e}")

    @staticmethod
    def _parse_runtime(text: str) -> Optional[int]:
        text = text.replace(" ", "").lower()
        total = 0
        h = re.search(r"(\d+)h", text)
        if h:
            total += int(h.group(1)) * 60
        m = re.search(r"(\d+)m", text)
        if m:
            total += int(m.group(1))
        return total if total > 0 else None