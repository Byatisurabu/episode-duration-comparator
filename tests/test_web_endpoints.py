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
