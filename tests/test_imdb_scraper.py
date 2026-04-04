import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.scrapers.imdb import ImdbScraper


@pytest.fixture
def scraper():
    return ImdbScraper()


# Мок-ответ GraphQL для эпизодов
EPISODES_GRAPHQL_RESPONSE = {
    "data": {
        "title": {
            "titleText": {"text": "Breaking Bad"},
            "episodes": {
                "episodes": {
                    "edges": [
                        {
                            "node": {
                                "id": "tt0959621",
                                "titleText": {"text": "Pilot"},
                                "series": {"episodeNumber": {"seasonNumber": 1, "episodeNumber": 1}},
                                "runtime": {"seconds": 3480},
                            }
                        },
                        {
                            "node": {
                                "id": "tt1054724",
                                "titleText": {"text": "Cat's in the Bag..."},
                                "series": {"episodeNumber": {"seasonNumber": 1, "episodeNumber": 2}},
                                "runtime": {"seconds": 2880},
                            }
                        },
                    ]
                }
            },
        }
    }
}

SEASONS_GRAPHQL_RESPONSE = {
    "data": {
        "title": {
            "titleText": {"text": "Breaking Bad"},
            "episodes": {
                "seasons": [{"number": 1}, {"number": 2}, {"number": 3}]
            },
        }
    }
}

TITLE_GRAPHQL_RESPONSE = {
    "data": {
        "title": {
            "titleText": {"text": "Breaking Bad"}
        }
    }
}


def _mock_httpx_response(json_data, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    return resp


class TestExtractSeriesId:
    def test_standard_url(self, scraper):
        assert scraper._extract_series_id("https://www.imdb.com/title/tt0903747/episodes?season=1") == "tt0903747"

    def test_no_match(self, scraper):
        assert scraper._extract_series_id("https://www.imdb.com/chart/top") is None


class TestExtractSeason:
    def test_standard(self, scraper):
        assert scraper._extract_season("https://www.imdb.com/title/tt0903747/episodes?season=3") == 3

    def test_no_season(self, scraper):
        assert scraper._extract_season("https://www.imdb.com/title/tt0903747/") is None


class TestScrapeEpisodes:
    @pytest.mark.asyncio
    async def test_success(self, scraper):
        with patch.object(scraper, "_graphql", new_callable=AsyncMock) as mock_gql:
            mock_gql.return_value = EPISODES_GRAPHQL_RESPONSE["data"]
            result = await scraper.scrape_episodes(
                "https://www.imdb.com/title/tt0903747/episodes?season=1"
            )
            assert result.series_title == "Breaking Bad"
            assert len(result.episodes) == 2
            assert result.episodes[0].title == "Pilot"
            assert result.episodes[0].duration_min == 58  # ceil(3480/60)
            assert result.episodes[0].episode_id == "tt0959621"
            assert result.episodes[1].duration_min == 48  # ceil(2880/60)

    @pytest.mark.asyncio
    async def test_invalid_url(self, scraper):
        result = await scraper.scrape_episodes("https://example.com/no-id")
        assert result.episodes == []

    @pytest.mark.asyncio
    async def test_no_season_in_url(self, scraper):
        result = await scraper.scrape_episodes("https://www.imdb.com/title/tt0903747/")
        assert result.episodes == []

    @pytest.mark.asyncio
    async def test_episode_without_runtime(self, scraper):
        """Эпизоды без runtime фильтруются из результата."""
        data = {
            "title": {
                "titleText": {"text": "Test"},
                "episodes": {
                    "episodes": {
                        "edges": [
                            {
                                "node": {
                                    "id": "tt123",
                                    "titleText": {"text": "No Runtime"},
                                    "series": {"episodeNumber": {"seasonNumber": 1, "episodeNumber": 1}},
                                    "runtime": None,
                                }
                            }
                        ]
                    }
                },
            }
        }
        with patch.object(scraper, "_graphql", new_callable=AsyncMock) as mock_gql:
            mock_gql.return_value = data
            result = await scraper.scrape_episodes(
                "https://www.imdb.com/title/tt0903747/episodes?season=1"
            )
            assert result.episodes == []


class TestGetSeasons:
    @pytest.mark.asyncio
    async def test_success(self, scraper):
        with patch.object(scraper, "_graphql", new_callable=AsyncMock) as mock_gql:
            mock_gql.return_value = SEASONS_GRAPHQL_RESPONSE["data"]
            seasons = await scraper.get_seasons("https://www.imdb.com/title/tt0903747/")
            assert seasons == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_invalid_url(self, scraper):
        seasons = await scraper.get_seasons("https://example.com/no-id")
        assert seasons == []

    @pytest.mark.asyncio
    async def test_graphql_failure(self, scraper):
        with patch.object(scraper, "_graphql", new_callable=AsyncMock) as mock_gql:
            mock_gql.return_value = None
            seasons = await scraper.get_seasons("https://www.imdb.com/title/tt0903747/")
            assert seasons == []


class TestGetSeriesTitle:
    @pytest.mark.asyncio
    async def test_success(self, scraper):
        with patch.object(scraper, "_graphql", new_callable=AsyncMock) as mock_gql:
            mock_gql.return_value = TITLE_GRAPHQL_RESPONSE["data"]
            title = await scraper.get_series_title("https://www.imdb.com/title/tt0903747/")
            assert title == "Breaking Bad"

    @pytest.mark.asyncio
    async def test_invalid_url(self, scraper):
        title = await scraper.get_series_title("https://example.com/no-id")
        assert title is None


class TestBuildSeasonUrl:
    def test_standard(self, scraper):
        url = scraper.build_season_url("https://www.imdb.com/title/tt0903747/", 3)
        assert url == "https://www.imdb.com/title/tt0903747/episodes?season=3"

    def test_invalid_url(self, scraper):
        url = scraper.build_season_url("https://example.com/no-id", 1)
        assert url == "https://example.com/no-id"
