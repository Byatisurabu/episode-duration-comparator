# app/scrapers/imdb.py

import re
import asyncio
import json
from typing import List, Optional

import httpx
from bs4 import BeautifulSoup
import asyncio

from app.scrapers.base import BaseScraper
from app.models import Episode, ScrapeResult
from app.services.normalizer import normalize_duration


class ImdbScraper(BaseScraper):
    # Семафор — не более 3 одновременных запросов к IMDb
    # Создаём на уровне класса, чтобы лимит был общим для всех вызовов
    _semaphore = asyncio.Semaphore(3)

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    async def scrape_episodes(self, url: str) -> ScrapeResult:
        series_id = self._extract_series_id(url)
        if not series_id:
            return ScrapeResult(series_title=None, episodes=[])

        series_title = await self._get_series_title(series_id)
        episodes = await self._extract_episode_list_from_page(url)
        if not episodes:
            return ScrapeResult(series_title=series_title, episodes=[])

        # gather оставляем — параллелизм нужен, но теперь он ограничен семафором
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

        async with self._semaphore:  # ← ключевое изменение
            url = f"https://www.imdb.com/title/{episode.episode_id}/"
            async with httpx.AsyncClient(headers=self.HEADERS, timeout=12.0) as client:
                try:
                    resp = await client.get(url)
                    if resp.status_code == 429:
                        # IMDb явно говорит "слишком много запросов" — ждём и пробуем ещё раз
                        print(f"Rate limit на {episode.episode_id}, пауза 5 сек")
                        await asyncio.sleep(5)
                        resp = await client.get(url)
                    if resp.status_code != 200:
                        return
                    minutes = await self._extract_duration_from_episode_page(resp.text)
                    if minutes is not None:
                        episode.duration_min = minutes
                except Exception as e:
                    print(f"Ошибка длительности {episode.episode_id}: {e}")
            
            # Пауза после каждого запроса, пока семафор ещё захвачен
            # Это гарантирует минимум 0.4 сек между запросами в одном "слоте"
            await asyncio.sleep(0.4)
                
    async def _extract_duration_from_episode_page(self, html: str) -> Optional[int]:
        # ld+json
        soup = BeautifulSoup(html, "html.parser")
        script = soup.find("script", type="application/ld+json")
        if script:
            try:
                data = json.loads(script.string)
                dur = data.get("duration")
                if dur:
                    return normalize_duration(dur)
            except:
                pass

        # текстовый поиск
        candidates = re.findall(r'(\d{1,2}h?\s*\d{0,2}m?|PT\d+[HM])', html.lower())
        for cand in candidates:
            min_val = normalize_duration(cand)
            if min_val and 5 < min_val < 300:
                return min_val

        return None
    
    async def get_seasons(self, url: str) -> List[int]:
        series_id = self._extract_series_id(url)
        if not series_id:
            return []

        # Идём на ГЛАВНУЮ страницу сериала — там есть полный список сезонов
        main_url = f"https://www.imdb.com/title/{series_id}/"

        async with httpx.AsyncClient(headers=self.HEADERS, timeout=15.0) as client:
            try:
                resp = await client.get(main_url, follow_redirects=True)
                if resp.status_code != 200:
                    return []

                html = resp.text

                # Вариант 1 — ищем seasonNumber в __NEXT_DATA__ или script-блоках
                # IMDb пишет что-то вроде: "numberOfSeasons":5
                match = re.search(r'"numberOfSeasons"\s*:\s*(\d+)', html)
                if match:
                    total = int(match.group(1))
                    return list(range(1, total + 1))

                # Вариант 2 — ищем в тексте все упоминания "season":N
                matches = re.findall(r'"seasonNumber"\s*:\s*(\d+)', html)
                if matches:
                    seasons = sorted(set(int(m) for m in matches))
                    return seasons

                # Вариант 3 — ищем select с выбором сезона (старая разметка)
                soup = BeautifulSoup(html, "html.parser")
                select = soup.find("select", {"id": "browse-episodes-season"})
                if select:
                    seasons = []
                    for option in select.find_all("option"):
                        val = option.get("value", "").strip()
                        if val.isdigit():
                            seasons.append(int(val))
                    if seasons:
                        return sorted(seasons)

                # Вариант 4 — ищем ссылки вида /episodes?season=N на главной
                season_nums = re.findall(r'episodes\?season=(\d+)', html)
                if season_nums:
                    return sorted(set(int(n) for n in season_nums))

                return []

            except Exception as e:
                print(f"IMDb get_seasons error: {e}")
                return []
            
    def _find_seasons_in_json(self, data, depth: int = 0) -> List[int]:
        """Рекурсивно ищет список сезонов в произвольной JSON-структуре IMDb."""
        if depth > 8:  # ограничение глубины рекурсии
            return []

        if isinstance(data, dict):
            # IMDb часто хранит сезоны под ключами типа "seasons", "availableSeasons"
            for key in ("seasons", "availableSeasons", "seasonNumbers"):
                if key in data:
                    val = data[key]
                    if isinstance(val, list):
                        nums = [int(v) for v in val if str(v).isdigit()]
                        if nums:
                            return nums
            # Рекурсивно проверяем вложенные объекты
            for v in data.values():
                result = self._find_seasons_in_json(v, depth + 1)
                if result:
                    return result

        elif isinstance(data, list):
            for item in data:
                result = self._find_seasons_in_json(item, depth + 1)
                if result:
                    return result

        return []

    def build_season_url(self, url: str, season: int) -> str:
        """Строит URL страницы эпизодов конкретного сезона на IMDb."""
        series_id = self._extract_series_id(url)
        if not series_id:
            return url
        return f"https://www.imdb.com/title/{series_id}/episodes?season={season}"

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