# tests/test_search.py
#
# Тесты для app/services/search.py

from unittest.mock import AsyncMock, MagicMock, patch

from app.services.search import search_amediateka, search_both, search_series

# ── Фикстуры ─────────────────────────────────────────────────────────────────

IMDB_RESPONSE_OK = {
    "d": [
        {
            "id": "tt0903747",
            "l": "Breaking Bad",
            "qid": "tvSeries",
            "y": 2008,
            "yr": "2008-2013",
            "i": {"imageUrl": "https://m.media-amazon.com/images/M/abc._V1_.jpg"},
        },
        {
            "id": "tt10234158",
            "l": "Better Call Saul",
            "qid": "tvSeries",
            "y": 2015,
            "yr": "2015-2022",
            "i": None,
        },
        {
            "id": "tt0903748",
            "l": "Breaking Bad: The Movie",
            "qid": "movie",          # ← должен быть отфильтрован
            "y": 2020,
        },
        {
            "id": "tt1234567",
            "l": "Breaking Point",
            "qid": "tvMiniSeries",   # ← должен включаться
            "y": 2010,
        },
        {
            # Не tt-идентификатор — должен быть пропущен
            "id": "nm0012345",
            "l": "Some Person",
            "qid": "tvSeries",
            "y": 2005,
        },
    ]
}


def _make_mock_response(data: dict, status: int = 200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = data
    return resp


# ── Тесты ────────────────────────────────────────────────────────────────────

class TestSearchSeries:

    async def test_returns_tv_series_only(self):
        """Фильтрует movie, оставляет tvSeries и tvMiniSeries."""
        mock_resp = _make_mock_response(IMDB_RESPONSE_OK)

        with patch("app.services.search.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            results = await search_series("breaking")

        titles = [r.title for r in results]
        assert "Breaking Bad" in titles
        assert "Better Call Saul" in titles
        assert "Breaking Point" in titles
        # Фильм отфильтрован
        assert "Breaking Bad: The Movie" not in titles

    async def test_skips_non_tt_ids(self):
        """Пропускает элементы, где id не начинается с 'tt'."""
        mock_resp = _make_mock_response(IMDB_RESPONSE_OK)

        with patch("app.services.search.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            results = await search_series("breaking")

        ids = [r.series_id for r in results]
        assert "nm0012345" not in ids

    async def test_result_fields(self):
        """Проверяет поля SearchResult."""
        mock_resp = _make_mock_response(IMDB_RESPONSE_OK)

        with patch("app.services.search.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            results = await search_series("breaking")

        bb = next(r for r in results if r.series_id == "tt0903747")
        assert bb.title == "Breaking Bad"
        assert bb.year == "2008-2013"
        assert bb.url == "https://www.imdb.com/title/tt0903747/"
        # Постер уменьшен
        assert "_V1_UX80_CR0,0,80,118_" in bb.poster_url

    async def test_no_poster_when_image_none(self):
        """poster_url равен None если поле i == None."""
        mock_resp = _make_mock_response(IMDB_RESPONSE_OK)

        with patch("app.services.search.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            results = await search_series("breaking")

        bcs = next(r for r in results if r.series_id == "tt10234158")
        assert bcs.poster_url is None

    async def test_empty_query_returns_empty(self):
        """Запрос < 2 символов возвращает пустой список без HTTP запроса."""
        with patch("app.services.search.httpx.AsyncClient") as mock_client_cls:
            results = await search_series("a")

        mock_client_cls.assert_not_called()
        assert results == []

    async def test_blank_query_returns_empty(self):
        """Пустая строка возвращает пустой список."""
        with patch("app.services.search.httpx.AsyncClient") as mock_client_cls:
            results = await search_series("   ")

        mock_client_cls.assert_not_called()
        assert results == []

    async def test_http_error_returns_empty(self):
        """При не-200 статусе возвращает пустой список."""
        mock_resp = _make_mock_response({}, status=503)

        with patch("app.services.search.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            results = await search_series("breaking")

        assert results == []

    async def test_network_exception_returns_empty(self):
        """При сетевой ошибке возвращает пустой список."""
        with patch("app.services.search.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=Exception("timeout"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            results = await search_series("breaking")

        assert results == []

    async def test_empty_results_from_api(self):
        """API вернул пустой список d=[]."""
        mock_resp = _make_mock_response({"d": []})

        with patch("app.services.search.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            results = await search_series("zzzzz_no_match")

        assert results == []

    async def test_year_fallback_to_y_field(self):
        """Если yr отсутствует — используется поле y."""
        data = {
            "d": [
                {
                    "id": "tt9999999",
                    "l": "Test Show",
                    "qid": "tvSeries",
                    "y": 2020,
                    # yr отсутствует
                }
            ]
        }
        mock_resp = _make_mock_response(data)

        with patch("app.services.search.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            results = await search_series("test")

        assert len(results) == 1
        assert results[0].year == "2020"

    async def test_url_uses_first_char_lowercase(self):
        """Первый символ запроса используется строчным в URL."""
        mock_resp = _make_mock_response({"d": []})
        captured_urls = []

        async def mock_get(url, **kwargs):
            captured_urls.append(url)
            return mock_resp

        with patch("app.services.search.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await search_series("Breaking Bad")

        assert len(captured_urls) == 1
        assert "/b/" in captured_urls[0]


# ── Тесты search_amediateka ───────────────────────────────────────────────────

AMEDIATEKA_RESPONSE_OK = [
    {
        "type": "contents",
        "content": {
            "count": 3,
            "next": None,
            "previous": None,
            "results": [
                {
                    "id": 28661,
                    "title": "Призраки",
                    "webUrl": "/watch/series_28661_prizraki",
                    "type": "series",
                    "premiereYear": 2021,
                    "assets": {
                        "productPoster": "https://i.amediateka.tech/resize/{SIZE}/_stor_/poster.jpg",
                    },
                },
                {
                    "id": 9446,
                    "title": "Призраки Абу-Грэйб",
                    "webUrl": "/watch/movies_9446_prizraki-abu-greyb",
                    "type": "movie",   # ← должен быть отфильтрован
                    "premiereYear": 2007,
                    "assets": {},
                },
                {
                    "id": 11223,
                    "title": "Игра престолов",
                    "webUrl": "/watch/series_11223_igra-prestolov",
                    "type": "series",
                    "premiereYear": 2011,
                    "assets": {},      # нет постера
                },
            ],
        },
    }
]


class TestSearchAmediateka:

    async def test_returns_series_only(self):
        """Фильтрует movie, оставляет только series."""
        mock_resp = _make_mock_response(AMEDIATEKA_RESPONSE_OK)

        with patch("app.services.search.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            results = await search_amediateka("ghosts")

        titles = [r.title for r in results]
        assert "Призраки" in titles
        assert "Игра престолов" in titles
        assert "Призраки Абу-Грэйб" not in titles

    async def test_result_fields(self):
        """Проверяет поля SearchResult для Amediateka."""
        mock_resp = _make_mock_response(AMEDIATEKA_RESPONSE_OK)

        with patch("app.services.search.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            results = await search_amediateka("ghosts")

        ghosts = next(r for r in results if r.series_id == "28661")
        assert ghosts.title == "Призраки"
        assert ghosts.year == "2021"
        assert ghosts.url == "https://www.amediateka.ru/watch/series_28661_prizraki"
        assert "80x118" in ghosts.poster_url
        assert "{SIZE}" not in ghosts.poster_url

    async def test_no_poster_when_assets_empty(self):
        """poster_url равен None если assets пустой."""
        mock_resp = _make_mock_response(AMEDIATEKA_RESPONSE_OK)

        with patch("app.services.search.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            results = await search_amediateka("ghosts")

        got = next(r for r in results if r.series_id == "11223")
        assert got.poster_url is None

    async def test_empty_query_returns_empty(self):
        """Запрос < 2 символов возвращает пустой список без HTTP запроса."""
        with patch("app.services.search.httpx.AsyncClient") as mock_client_cls:
            results = await search_amediateka("a")

        mock_client_cls.assert_not_called()
        assert results == []

    async def test_http_error_returns_empty(self):
        """При не-200 статусе возвращает пустой список."""
        mock_resp = _make_mock_response({}, status=503)

        with patch("app.services.search.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            results = await search_amediateka("ghosts")

        assert results == []

    async def test_network_exception_returns_empty(self):
        """При сетевой ошибке возвращает пустой список."""
        with patch("app.services.search.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=Exception("timeout"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            results = await search_amediateka("ghosts")

        assert results == []

    async def test_ignores_non_contents_blocks(self):
        """Блоки с type != 'contents' игнорируются."""
        data = [
            {"type": "banner", "content": {}},
            {"type": "contents", "content": {"results": [
                {"id": 1, "title": "Сериал", "webUrl": "/watch/series_1_serial",
                 "type": "series", "premiereYear": 2020, "assets": {}},
            ]}},
        ]
        mock_resp = _make_mock_response(data)

        with patch("app.services.search.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            results = await search_amediateka("сериал")

        assert len(results) == 1
        assert results[0].title == "Сериал"


# ── Тесты search_both ───────────────────────────────────────────────────────

class TestSearchBoth:

    async def test_both_services_succeed(self):
        """Оба сервиса возвращают результаты."""
        with (
            patch("app.services.search.search_series", new_callable=AsyncMock) as mock_imdb,
            patch("app.services.search.search_amediateka", new_callable=AsyncMock) as mock_amd,
        ):
            mock_imdb.return_value = [
                MagicMock(series_id="tt123", title="Test Show"),
            ]
            mock_amd.return_value = [
                MagicMock(series_id="456", title="Тест Шоу"),
            ]

            result = await search_both("test")

        assert len(result.imdb) == 1
        assert len(result.amediateka) == 1
        assert result.imdb[0].title == "Test Show"
        assert result.amediateka[0].title == "Тест Шоу"

    async def test_imdb_fails_amediateka_ok(self):
        """IMDb падает — amediateka всё равно возвращается."""
        with (
            patch("app.services.search.search_series", new_callable=AsyncMock) as mock_imdb,
            patch("app.services.search.search_amediateka", new_callable=AsyncMock) as mock_amd,
        ):
            mock_imdb.side_effect = Exception("IMDb down")
            mock_amd.return_value = [MagicMock(title="Сериал")]

            result = await search_both("test")

        assert result.imdb == []
        assert len(result.amediateka) == 1

    async def test_amediateka_fails_imdb_ok(self):
        """Amediateka падает — IMDb всё равно возвращается."""
        with (
            patch("app.services.search.search_series", new_callable=AsyncMock) as mock_imdb,
            patch("app.services.search.search_amediateka", new_callable=AsyncMock) as mock_amd,
        ):
            mock_imdb.return_value = [MagicMock(title="Show")]
            mock_amd.side_effect = Exception("Amediateka down")

            result = await search_both("test")

        assert len(result.imdb) == 1
        assert result.amediateka == []

    async def test_both_fail(self):
        """Оба сервиса падают — пустые списки."""
        with (
            patch("app.services.search.search_series", new_callable=AsyncMock) as mock_imdb,
            patch("app.services.search.search_amediateka", new_callable=AsyncMock) as mock_amd,
        ):
            mock_imdb.side_effect = Exception("fail")
            mock_amd.side_effect = Exception("fail")

            result = await search_both("test")

        assert result.imdb == []
        assert result.amediateka == []

    async def test_short_query(self):
        """Короткий запрос — оба сервиса вернут пустые списки (внутренняя проверка)."""
        result = await search_both("a")
        assert result.imdb == []
        assert result.amediateka == []
