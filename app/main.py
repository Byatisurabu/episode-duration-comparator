import asyncio
import json as _json
import logging
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import app.scrapers.factory  # регистрирует скрейперы в ScraperFactory
from app.cache.sqlite_cache import EpisodeCache
from app.config import DiffColors, DiffThresholds
from app.scrapers.base import ScraperFactory
from app.scrapers.cached import CachedScraper
from app.services.comparison import create_comparison_rows
from app.services.detector import detect_service
from app.services.search import search_amediateka, search_both, search_series

logger = logging.getLogger(__name__)

# Глобальный экземпляр кеша — инициализируется в lifespan
_cache: EpisodeCache | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _cache
    _cache = EpisodeCache()
    await _cache.init_db()
    yield
    # teardown при необходимости


app = FastAPI(
    title="Сравнитель длительности серий",
    description="MVP — сравнение длительности серий",
    version="0.2.1",
    lifespan=lifespan,
)

templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")


def is_valid_url(url_str: str) -> bool:
    try:
        result = urlparse(url_str)
        return all([result.scheme in ("http", "https"), result.netloc])
    except ValueError:
        return False


def _get_scraper(service_key: str, force_refresh: bool = False):
    """Возвращает CachedScraper, если кеш инициализирован, иначе голый скрейпер."""
    scraper = ScraperFactory.get_scraper(service_key)
    if scraper and _cache:
        return CachedScraper(scraper, _cache, service_key)
    return scraper


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {"title": "Сравнитель длительности серий — MVP"},
    )


@app.post("/compare", response_class=HTMLResponse)
async def compare(
    request: Request,
    baseline_url: str = Form(...),
    compared_url: str = Form(...),
    season_urls_json: str = Form(default=""),
    force_refresh: str = Form(default=""),
):
    errors = []
    refresh = force_refresh == "1"

    if not baseline_url.strip():
        errors.append("Не указан baseline URL")
    if not compared_url.strip():
        errors.append("Не указан compared URL")

    if baseline_url and not is_valid_url(baseline_url):
        errors.append("Baseline URL выглядит некорректно")
    if compared_url and not is_valid_url(compared_url):
        errors.append("Compared URL выглядит некорректно")

    if errors:
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "title": "Ошибка ввода",
                "error_messages": errors,
                "prev_baseline": baseline_url,
                "prev_compared": compared_url,
            },
        )

    baseline_service, baseline_name = detect_service(baseline_url)
    compared_service, compared_name = detect_service(compared_url)

    if not baseline_service:
        errors.append(f"Baseline: {baseline_name}")
    if not compared_service:
        errors.append(f"Compared: {compared_name}")

    if errors:
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "title": "Неподдерживаемый сервис",
                "error_messages": errors,
                "prev_baseline": baseline_url,
                "prev_compared": compared_url,
            },
        )

    baseline_scraper = _get_scraper(baseline_service)
    compared_scraper = _get_scraper(compared_service)
    if not baseline_scraper or not compared_scraper:
        errors.append("Один из сервисов пока не реализован")
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "title": "Сервис не реализован",
                "error_messages": errors,
                "prev_baseline": baseline_url,
                "prev_compared": compared_url,
            },
        )

    baseline_result, compared_result = await asyncio.gather(
        baseline_scraper.scrape_episodes(baseline_url, force_refresh=refresh),
        compared_scraper.scrape_episodes(compared_url, force_refresh=refresh),
    )

    comparison_rows = create_comparison_rows(
        baseline_result.episodes,
        compared_result.episodes,
    )

    try:
        season_urls = _json.loads(season_urls_json) if season_urls_json else {}
        season_urls = {int(k): v for k, v in season_urls.items()}
    except Exception:
        season_urls = {}

    context = {
        "title": "Сравнение длительности серий",
        "baseline_name": baseline_name,
        "baseline_series_title": baseline_result.series_title or "—",
        "baseline_url": baseline_url,
        "compared_name": compared_name,
        "compared_series_title": compared_result.series_title or "—",
        "compared_url": compared_url,
        "comparison_rows": comparison_rows,
        "total_episodes": len(comparison_rows),
        "colors": DiffColors,
        "thresholds": DiffThresholds,
        "season_urls": season_urls,
    }

    return templates.TemplateResponse(request, "result.html", context)


@app.post("/get-seasons")
async def get_seasons(
    baseline_url: str = Form(...),
    compared_url: str = Form(...),
):
    errors = []

    if not baseline_url.strip() or not is_valid_url(baseline_url):
        errors.append("Некорректный Baseline URL")
    if not compared_url.strip() or not is_valid_url(compared_url):
        errors.append("Некорректный Compared URL")

    if errors:
        return JSONResponse({"ok": False, "errors": errors}, status_code=400)

    baseline_service, baseline_name = detect_service(baseline_url)
    compared_service, compared_name = detect_service(compared_url)

    if not baseline_service:
        return JSONResponse({"ok": False, "errors": [f"Baseline: {baseline_name}"]}, status_code=400)
    if not compared_service:
        return JSONResponse({"ok": False, "errors": [f"Compared: {compared_name}"]}, status_code=400)

    baseline_scraper = _get_scraper(baseline_service)
    compared_scraper = _get_scraper(compared_service)

    baseline_seasons, compared_seasons = await asyncio.gather(
        baseline_scraper.get_seasons(baseline_url),
        compared_scraper.get_seasons(compared_url),
        return_exceptions=True,
    )

    if isinstance(baseline_seasons, Exception):
        baseline_seasons = []
    if isinstance(compared_seasons, Exception):
        compared_seasons = []

    common_seasons = sorted(set(baseline_seasons) & set(compared_seasons))
    all_seasons = sorted(set(baseline_seasons) | set(compared_seasons))

    baseline_urls, compared_urls = await asyncio.gather(
        asyncio.gather(*[baseline_scraper.get_season_url(baseline_url, s) for s in all_seasons]),
        asyncio.gather(*[compared_scraper.get_season_url(compared_url, s) for s in all_seasons]),
    )

    season_urls = {
        s: {"baseline": baseline_urls[i], "compared": compared_urls[i]}
        for i, s in enumerate(all_seasons)
    }

    return JSONResponse({
        "ok": True,
        "baseline": {"service": baseline_name, "seasons": baseline_seasons},
        "compared": {"service": compared_name, "seasons": compared_seasons},
        "common_seasons": common_seasons,
        "season_urls": season_urls,
    })


@app.get("/api/search")
async def api_search(q: str = "", service: str = "imdb"):
    """Поиск сериалов по названию. service=imdb|amediateka."""
    if len(q.strip()) < 2:
        return JSONResponse({"ok": True, "results": []})
    if service == "amediateka":
        results = await search_amediateka(q)
    else:
        results = await search_series(q)
    return JSONResponse({
        "ok": True,
        "results": _serialize_results(results),
    })


def _serialize_results(results):
    return [
        {
            "series_id": r.series_id,
            "title": r.title,
            "year": r.year,
            "poster_url": r.poster_url,
            "url": r.url,
        }
        for r in results
    ]


@app.get("/api/search/unified")
async def api_search_unified(q: str = ""):
    """Единый поиск по IMDb и Amediateka одновременно."""
    if len(q.strip()) < 2:
        return JSONResponse({"ok": True, "imdb": [], "amediateka": []})
    result = await search_both(q)
    return JSONResponse({
        "ok": True,
        "imdb": _serialize_results(result.imdb),
        "amediateka": _serialize_results(result.amediateka),
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
