import gzip

import pytest

from app.scrapers.base import ServiceUnavailableError
from app.scrapers.imdb import ImdbScraper
from app.services.imdb_dataset import ImdbDataset, build_from_files, is_fresh

EPISODE_TSV = """tconst\tparentTconst\tseasonNumber\tepisodeNumber
tt0959621\ttt0903747\t1\t1
tt1054724\ttt0903747\t1\t2
tt1054725\ttt0903747\t1\t3
tt1232244\ttt0903747\t2\t1
tt9999999\ttt0903747\t\\N\t\\N
tt12345678\ttt0903747\t3\t1
"""

# tt1054725 — без runtime, tt9999999 — без номера сезона: оба не должны попасть в базу
BASICS_TSV = """tconst\ttitleType\tprimaryTitle\toriginalTitle\tisAdult\tstartYear\tendYear\truntimeMinutes\tgenres
tt0903747\ttvSeries\tBreaking Bad\tBreaking Bad\t0\t2008\t2013\t45\tDrama
tt0959621\ttvEpisode\tPilot\tPilot\t0\t2008\t\\N\t58\tDrama
tt1054724\ttvEpisode\tCat's in the Bag...\tCat's in the Bag...\t0\t2008\t\\N\t48\tDrama
tt1054725\ttvEpisode\tNo Runtime\tNo Runtime\t0\t2008\t\\N\t\\N\tDrama
tt1232244\ttvEpisode\tSeven Thirty-Seven\tSeven Thirty-Seven\t0\t2009\t\\N\t47\tDrama
tt9999999\ttvEpisode\tNo Season\tNo Season\t0\t2009\t\\N\t40\tDrama
tt12345678\ttvEpisode\tEight Digit Id\tEight Digit Id\t0\t2010\t\\N\t50\tDrama
tt0000001\tmovie\tSome Movie\tSome Movie\t0\t1900\t\\N\t10\tShort
"""


def _write_gz(path, text):
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(text)


@pytest.fixture
def db_path(tmp_path):
    episode = tmp_path / "title.episode.tsv.gz"
    basics = tmp_path / "title.basics.tsv.gz"
    _write_gz(episode, EPISODE_TSV)
    _write_gz(basics, BASICS_TSV)
    path = tmp_path / "imdb.db"
    build_from_files(episode, basics, path)
    return path


@pytest.fixture
def scraper(db_path):
    return ImdbScraper(ImdbDataset(db_path))


@pytest.fixture
def scraper_not_ready(tmp_path):
    return ImdbScraper(ImdbDataset(tmp_path / "missing.db"))


class TestExtractSeriesId:
    def test_standard_url(self, scraper):
        assert scraper._extract_series_id("https://www.imdb.com/title/tt0903747/") == "tt0903747"

    def test_no_match(self, scraper):
        assert scraper._extract_series_id("https://example.com/") is None


class TestExtractSeason:
    def test_standard(self, scraper):
        assert scraper._extract_season("https://www.imdb.com/title/tt0903747/episodes?season=3") == 3

    def test_no_season(self, scraper):
        assert scraper._extract_season("https://www.imdb.com/title/tt0903747/") is None


class TestBuildDatabase:
    def test_tmp_file_removed(self, db_path):
        assert db_path.exists()
        assert not db_path.with_suffix(".db.tmp").exists()

    def test_is_fresh(self, db_path, tmp_path):
        assert is_fresh(db_path)
        assert not is_fresh(tmp_path / "missing.db")


class TestScrapeEpisodes:
    async def test_success(self, scraper):
        result = await scraper.scrape_episodes("https://www.imdb.com/title/tt0903747/episodes?season=1")
        assert result.series_title == "Breaking Bad"
        assert [e.episode for e in result.episodes] == [1, 2]
        assert result.episodes[0].title == "Pilot"
        assert result.episodes[0].duration_min == 58
        assert result.episodes[0].episode_id == "tt0959621"
        assert result.episodes[1].duration_min == 48

    async def test_episode_without_runtime_skipped(self, scraper):
        result = await scraper.scrape_episodes("https://www.imdb.com/title/tt0903747/episodes?season=1")
        assert "No Runtime" not in [e.title for e in result.episodes]

    async def test_eight_digit_id(self, scraper):
        result = await scraper.scrape_episodes("https://www.imdb.com/title/tt0903747/episodes?season=3")
        assert result.episodes[0].episode_id == "tt12345678"

    async def test_unknown_season(self, scraper):
        result = await scraper.scrape_episodes("https://www.imdb.com/title/tt0903747/episodes?season=9")
        assert result.episodes == []

    async def test_invalid_url(self, scraper):
        result = await scraper.scrape_episodes("https://example.com/no-id")
        assert result.episodes == []

    async def test_no_season_in_url(self, scraper):
        result = await scraper.scrape_episodes("https://www.imdb.com/title/tt0903747/")
        assert result.episodes == []

    async def test_not_ready(self, scraper_not_ready):
        with pytest.raises(ServiceUnavailableError):
            await scraper_not_ready.scrape_episodes("https://www.imdb.com/title/tt0903747/episodes?season=1")


class TestGetSeasons:
    async def test_success(self, scraper):
        assert await scraper.get_seasons("https://www.imdb.com/title/tt0903747/") == [1, 2, 3]

    async def test_unknown_series(self, scraper):
        assert await scraper.get_seasons("https://www.imdb.com/title/tt0000002/") == []

    async def test_invalid_url(self, scraper):
        assert await scraper.get_seasons("https://example.com/no-id") == []

    async def test_not_ready(self, scraper_not_ready):
        with pytest.raises(ServiceUnavailableError):
            await scraper_not_ready.get_seasons("https://www.imdb.com/title/tt0903747/")


class TestGetSeriesTitle:
    async def test_success(self, scraper):
        assert await scraper.get_series_title("https://www.imdb.com/title/tt0903747/") == "Breaking Bad"

    async def test_invalid_url(self, scraper):
        assert await scraper.get_series_title("https://example.com/no-id") is None

    async def test_not_ready(self, scraper_not_ready):
        with pytest.raises(ServiceUnavailableError):
            await scraper_not_ready.get_series_title("https://www.imdb.com/title/tt0903747/")


class TestBuildSeasonUrl:
    def test_standard(self, scraper):
        url = scraper.build_season_url("https://www.imdb.com/title/tt0903747/", 3)
        assert url == "https://www.imdb.com/title/tt0903747/episodes?season=3"

    def test_invalid_url(self, scraper):
        url = scraper.build_season_url("https://example.com/no-id", 1)
        assert url == "https://example.com/no-id"
