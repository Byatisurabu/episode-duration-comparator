from app.services.detector import detect_service, get_supported_services_list


class TestDetectService:
    def test_imdb(self):
        key, name = detect_service("https://www.imdb.com/title/tt0903747/episodes?season=1")
        assert key == "imdb"
        assert name == "IMDb"

    def test_imdb_no_www(self):
        key, name = detect_service("https://imdb.com/title/tt0903747/")
        assert key == "imdb"

    def test_amediateka(self):
        key, name = detect_service("https://www.amediateka.ru/watch/series_123/season_1")
        assert key == "amediateka"
        assert name == "Amediateka"

    def test_kinopoisk(self):
        key, name = detect_service("https://www.kinopoisk.ru/series/123/seasons/")
        assert key == "kinopoisk"
        assert name == "Кинопоиск"

    def test_okko(self):
        key, name = detect_service("https://okko.tv/serial/some-show")
        assert key == "okko"

    def test_unknown_domain(self):
        key, error = detect_service("https://example.com/show")
        assert key is None
        assert "не поддерживается" in error

    def test_empty_url(self):
        key, error = detect_service("")
        assert key is None
        assert "пустой" in error

    def test_no_domain(self):
        key, error = detect_service("not-a-url")
        assert key is None
        assert "Некорректный" in error


class TestSupportedServicesList:
    def test_returns_list(self):
        services = get_supported_services_list()
        assert isinstance(services, list)
        assert len(services) >= 2

    def test_has_required_keys(self):
        services = get_supported_services_list()
        for s in services:
            assert "key" in s
            assert "name" in s
            assert "example" in s
