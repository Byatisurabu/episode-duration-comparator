import json
from unittest.mock import AsyncMock, patch

import pytest

from app.scrapers.amediateka import AmediatekaScraper


@pytest.fixture
def scraper():
    return AmediatekaScraper()


def _make_next_data_html(next_data: dict) -> str:
    """Оборачивает JSON в HTML со script#__NEXT_DATA__."""
    return f'<html><head><script id="__NEXT_DATA__" type="application/json">{json.dumps(next_data)}</script></head><body></body></html>'


SEASON_NEXT_DATA = {
    "props": {
        "pageProps": {
            "content": {
                "type": "season",
                "seasonNumber": 1,
                "title": "Сезон 1",
                "series": {"title": "Тест Сериал"},
                "episodes": [
                    {"number": 1, "title": "Пилот", "duration": 2880, "id": 101},
                    {"number": 2, "title": "Второй", "duration": 2700, "id": 102},
                    {"number": 3, "title": "Третий", "duration": None, "id": 103},
                ],
            }
        }
    }
}

SERIES_NEXT_DATA = {
    "props": {
        "pageProps": {
            "content": {
                "type": "series",
                "title": "Тест Сериал",
                "longDescription": (
                    '<a href="/watch/series_123_test/season_1_11976">Сезон 1</a>'
                    '<a href="/watch/series_123_test/season_2_11977">Сезон 2</a>'
                    '<a href="/watch/series_123_test/season_3_11978">Сезон 3</a>'
                ),
            }
        }
    }
}


class TestParseNextData:
    def test_parses_episodes(self, scraper):
        html = _make_next_data_html(SEASON_NEXT_DATA)
        episodes = scraper._parse_next_data(html)
        assert len(episodes) == 3
        assert episodes[0].title == "Пилот"
        assert episodes[0].season == 1
        assert episodes[0].episode == 1
        assert episodes[0].duration_min == 48  # 2880/60=48
        assert episodes[0].episode_id == "101"

    def test_episode_without_duration(self, scraper):
        html = _make_next_data_html(SEASON_NEXT_DATA)
        episodes = scraper._parse_next_data(html)
        # Episode 3 has duration=None → normalize_duration returns None
        assert episodes[2].duration_min is None

    def test_no_next_data(self, scraper):
        html = "<html><body>No data</body></html>"
        assert scraper._parse_next_data(html) == []

    def test_wrong_content_type(self, scraper):
        data = {"props": {"pageProps": {"content": {"type": "movie"}}}}
        html = _make_next_data_html(data)
        assert scraper._parse_next_data(html) == []

    def test_empty_episodes(self, scraper):
        data = {"props": {"pageProps": {"content": {"type": "season", "episodes": []}}}}
        html = _make_next_data_html(data)
        assert scraper._parse_next_data(html) == []

    def test_string_duration(self, scraper):
        """duration как строка (напр. '1270') — должна корректно парситься."""
        data = {
            "props": {
                "pageProps": {
                    "content": {
                        "type": "season",
                        "seasonNumber": 1,
                        "episodes": [
                            {"number": 1, "title": "Эпизод", "duration": "1270", "id": 201},
                        ],
                    }
                }
            }
        }
        html = _make_next_data_html(data)
        episodes = scraper._parse_next_data(html)
        assert len(episodes) == 1
        assert episodes[0].duration_min == 21  # 1270 // 60 = 21


class TestExtractSeriesTitle:
    @pytest.mark.asyncio
    async def test_from_season_page(self, scraper):
        html = _make_next_data_html(SEASON_NEXT_DATA)
        title = await scraper._extract_series_title(html)
        assert title == "Тест Сериал"

    @pytest.mark.asyncio
    async def test_from_series_page(self, scraper):
        html = _make_next_data_html(SERIES_NEXT_DATA)
        title = await scraper._extract_series_title(html)
        assert title == "Тест Сериал"

    @pytest.mark.asyncio
    async def test_fallback_to_title_tag(self, scraper):
        html = "<html><head><title>Мой Сериал — Сезон 1 | Amediateka</title></head><body></body></html>"
        title = await scraper._extract_series_title(html)
        assert title == "Мой Сериал"


class TestScrapeEpisodes:
    @pytest.mark.asyncio
    async def test_success(self, scraper):
        html = _make_next_data_html(SEASON_NEXT_DATA)
        with patch.object(scraper, "_fetch_page", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = html
            result = await scraper.scrape_episodes("https://www.amediateka.ru/watch/series_123/season_1")
            assert result.series_title == "Тест Сериал"
            assert len(result.episodes) == 3

    @pytest.mark.asyncio
    async def test_fetch_failure(self, scraper):
        with patch.object(scraper, "_fetch_page", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = None
            result = await scraper.scrape_episodes("https://www.amediateka.ru/watch/series_123/season_1")
            assert result.episodes == []


class TestGetSeasons:
    @pytest.mark.asyncio
    async def test_from_series_page(self, scraper):
        html = _make_next_data_html(SERIES_NEXT_DATA)
        with patch.object(scraper, "_fetch_page", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = html
            seasons = await scraper.get_seasons("https://www.amediateka.ru/watch/series_123_test/")
            assert seasons == [1, 2, 3]


class TestBuildSeasonUrl:
    def test_builds_url(self, scraper):
        url = scraper.build_season_url("https://www.amediateka.ru/watch/series_123_test/season_1_11976", 2)
        assert "season_2" in url
        assert "series_123_test" in url
