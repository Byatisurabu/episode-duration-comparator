import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


class TestHomePage:
    def test_get_home(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Сравнитель" in resp.text


class TestCompareEndpoint:
    def test_empty_urls(self, client):
        # FastAPI возвращает 422 для пустых обязательных Form-полей (Pydantic validation)
        resp = client.post("/compare", data={"baseline_url": "", "compared_url": ""})
        assert resp.status_code in (200, 422)

    def test_invalid_urls(self, client):
        resp = client.post(
            "/compare",
            data={"baseline_url": "not-a-url", "compared_url": "also-not-url"},
        )
        assert resp.status_code == 200
        assert "некорректно" in resp.text.lower() or "ошибка" in resp.text.lower()

    def test_unsupported_service(self, client):
        resp = client.post(
            "/compare",
            data={
                "baseline_url": "https://example.com/show",
                "compared_url": "https://example.com/show2",
            },
        )
        assert resp.status_code == 200
        assert "не поддерживается" in resp.text


class TestGetSeasonsEndpoint:
    def test_invalid_urls(self, client):
        # FastAPI возвращает 422 для пустых обязательных Form-полей
        resp = client.post(
            "/get-seasons",
            data={"baseline_url": "", "compared_url": ""},
        )
        assert resp.status_code in (400, 422)

    def test_unsupported_baseline(self, client):
        resp = client.post(
            "/get-seasons",
            data={
                "baseline_url": "https://example.com/show",
                "compared_url": "https://www.imdb.com/title/tt0903747/",
            },
        )
        assert resp.status_code == 400


class TestUnifiedSearchEndpoint:
    def test_short_query(self, client):
        resp = client.get("/api/search/unified?q=a")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["imdb"] == []
        assert data["amediateka"] == []

    def test_response_shape(self, client):
        """Проверяем структуру ответа (без реальных HTTP-запросов результаты пустые)."""
        resp = client.get("/api/search/unified?q=nonexistent_show_xyz")
        assert resp.status_code == 200
        data = resp.json()
        assert "ok" in data
        assert "imdb" in data
        assert "amediateka" in data
        assert isinstance(data["imdb"], list)
        assert isinstance(data["amediateka"], list)


class TestImdbDatasetNotReady:
    """Пока локальная база IMDb не собрана — пользователь видит понятное сообщение, а не пустую таблицу."""

    IMDB_URL = "https://www.imdb.com/title/tt0903747/episodes?season=1"
    NOT_READY = "База IMDb ещё загружается"

    @pytest.fixture(autouse=True)
    def missing_dataset(self, monkeypatch, tmp_path):
        import app.scrapers.imdb as imdb_module
        from app.services.imdb_dataset import ImdbDataset
        monkeypatch.setattr(imdb_module, "ImdbDataset", lambda: ImdbDataset(tmp_path / "missing.db"))

    def test_get_seasons(self, client):
        resp = client.post("/get-seasons", data={"baseline_url": self.IMDB_URL, "compared_url": self.IMDB_URL})
        assert resp.status_code == 503
        data = resp.json()
        assert data["ok"] is False
        assert self.NOT_READY in data["errors"][0]

    def test_compare(self, client, monkeypatch, tmp_path):
        # /compare передаёт force_refresh — это умеет только CachedScraper, в проде кеш есть всегда
        import asyncio

        import app.main as main_module
        from app.cache.sqlite_cache import EpisodeCache
        cache = EpisodeCache(tmp_path / "cache.db")
        asyncio.run(cache.init_db())
        monkeypatch.setattr(main_module, "_cache", cache)

        resp = client.post("/compare", data={"baseline_url": self.IMDB_URL, "compared_url": self.IMDB_URL})
        assert resp.status_code == 200
        assert self.NOT_READY in resp.text
