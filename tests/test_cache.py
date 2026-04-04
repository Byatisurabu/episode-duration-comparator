from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.cache.sqlite_cache import EpisodeCache, _deserialize, _serialize
from app.models import Episode, ScrapeResult


def _make_result(title="Test", n=2):
    episodes = [
        Episode(season=1, episode=i, title=f"Ep{i}", duration_min=40 + i, episode_id=f"tt{i:07d}")
        for i in range(1, n + 1)
    ]
    return ScrapeResult(series_title=title, episodes=episodes)


@pytest.fixture
async def cache(tmp_path):
    c = EpisodeCache(db_path=tmp_path / "test_cache.db", ttl_days=7)
    await c.init_db()
    return c


class TestSerialization:
    def test_roundtrip(self):
        result = _make_result("Breaking Bad", n=3)
        restored = _deserialize(_serialize(result))
        assert restored.series_title == "Breaking Bad"
        assert len(restored.episodes) == 3
        assert restored.episodes[0].title == "Ep1"
        assert restored.episodes[0].duration_min == 41
        assert restored.episodes[0].episode_id == "tt0000001"

    def test_empty_episodes(self):
        result = ScrapeResult(series_title="Empty", episodes=[])
        restored = _deserialize(_serialize(result))
        assert restored.episodes == []

    def test_none_fields(self):
        result = ScrapeResult(series_title=None, episodes=[
            Episode(season=1, episode=1, title="Ep", duration_min=None, episode_id=None)
        ])
        restored = _deserialize(_serialize(result))
        assert restored.series_title is None
        assert restored.episodes[0].duration_min is None


class TestEpisodeCachePutGet:
    @pytest.mark.asyncio
    async def test_put_and_get(self, cache):
        result = _make_result()
        await cache.put("imdb", "tt0903747", 1, result)
        got = await cache.get("imdb", "tt0903747", 1)
        assert got is not None
        assert got.series_title == "Test"
        assert len(got.episodes) == 2

    @pytest.mark.asyncio
    async def test_miss(self, cache):
        got = await cache.get("imdb", "tt9999999", 1)
        assert got is None

    @pytest.mark.asyncio
    async def test_different_keys(self, cache):
        r1 = _make_result("Show A")
        r2 = _make_result("Show B")
        await cache.put("imdb", "tt0000001", 1, r1)
        await cache.put("imdb", "tt0000002", 1, r2)
        assert (await cache.get("imdb", "tt0000001", 1)).series_title == "Show A"
        assert (await cache.get("imdb", "tt0000002", 1)).series_title == "Show B"

    @pytest.mark.asyncio
    async def test_overwrite(self, cache):
        await cache.put("imdb", "tt0903747", 1, _make_result("Old"))
        await cache.put("imdb", "tt0903747", 1, _make_result("New"))
        got = await cache.get("imdb", "tt0903747", 1)
        assert got.series_title == "New"


class TestEpisodeCacheExpiry:
    @pytest.mark.asyncio
    async def test_expired_returns_none(self, cache):
        result = _make_result()
        await cache.put("imdb", "tt0903747", 1, result)

        # Подменяем "сейчас" на дату после истечения TTL
        future = datetime.now(timezone.utc) + timedelta(days=8)
        with patch("app.cache.sqlite_cache.datetime") as mock_dt:
            mock_dt.now.return_value = future
            mock_dt.fromisoformat = datetime.fromisoformat
            got = await cache.get("imdb", "tt0903747", 1)

        assert got is None

    @pytest.mark.asyncio
    async def test_not_expired(self, cache):
        result = _make_result()
        await cache.put("imdb", "tt0903747", 1, result)

        # Через 6 дней — ещё не истёк
        soon = datetime.now(timezone.utc) + timedelta(days=6)
        with patch("app.cache.sqlite_cache.datetime") as mock_dt:
            mock_dt.now.return_value = soon
            mock_dt.fromisoformat = datetime.fromisoformat
            got = await cache.get("imdb", "tt0903747", 1)

        assert got is not None


class TestEpisodeCacheInvalidate:
    @pytest.mark.asyncio
    async def test_invalidate_removes_entry(self, cache):
        await cache.put("imdb", "tt0903747", 1, _make_result())
        await cache.invalidate("imdb", "tt0903747", 1)
        assert await cache.get("imdb", "tt0903747", 1) is None

    @pytest.mark.asyncio
    async def test_invalidate_nonexistent_ok(self, cache):
        # Не должно падать
        await cache.invalidate("imdb", "tt9999999", 99)
