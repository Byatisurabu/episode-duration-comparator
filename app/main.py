from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import HttpUrl, ValidationError
from urllib.parse import urlparse
from app.services.detector import detect_service
from app.scrapers.base import ScraperFactory
import asyncio
import app.scrapers.factory

app = FastAPI(
    title="Сравнитель длительности серий",
    description="MVP — сравнение длительности серий",
    version="0.1.0"
)

templates = Jinja2Templates(directory="app/templates")


def is_valid_url(url_str: str) -> bool:
    """Простая проверка, что строка похожа на http/https URL"""
    try:
        result = urlparse(url_str)
        return all([result.scheme in ("http", "https"), result.netloc])
    except ValueError:
        return False


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "title": "Сравнитель длительности серий — MVP"}
    )


@app.post("/compare", response_class=HTMLResponse)
async def compare(
    request: Request,
    baseline_url: str = Form(...),
    compared_url: str = Form(...)
):
    errors = []

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
            "index.html",
            {
                "request": request,
                "title": "Ошибка ввода",
                "error_messages": errors,
                "prev_baseline": baseline_url,
                "prev_compared": compared_url,
            }
        )

    # ─── здесь определяем сервисы ───────────────────────────────────────
    baseline_service, baseline_name = detect_service(baseline_url)
    compared_service, compared_name = detect_service(compared_url)

    if not baseline_service:
        errors.append(f"Baseline: {baseline_name}")
    if not compared_service:
        errors.append(f"Compared: {compared_name}")

    if errors:
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "title": "Неподдерживаемый сервис",
                "error_messages": errors,
                "prev_baseline": baseline_url,
                "prev_compared": compared_url,
            }
        )

    baseline_scraper = ScraperFactory.get_scraper(baseline_service)
    compared_scraper = ScraperFactory.get_scraper(compared_service)
    if not baseline_scraper or not compared_scraper:
        errors.append("Один из сервисов пока не реализован")
        # возврат с ошибкой    

    # Запускаем скрейпинг (асинхронно)
    # baseline_eps, compared_eps = await asyncio.gather(
    #     baseline_scraper.scrape_episodes(baseline_url),
    #     compared_scraper.scrape_episodes(compared_url)
    # )

    baseline_result = await baseline_scraper.scrape_episodes(baseline_url)
    compared_result = await compared_scraper.scrape_episodes(compared_url)

    # Пока просто показываем, что определили
    context = {
        "request": request,
        "title": "Результаты анализа длительности",
        "baseline_url": baseline_url,
        "baseline_name": baseline_name,
        "baseline_series_title": baseline_result.series_title or "Название не удалось определить",
        "baseline_episodes": baseline_result.episodes,
        "compared_url": compared_url,
        "compared_name": compared_name,
        "compared_series_title": compared_result.series_title or "—",
        "compared_episodes": compared_result.episodes,  # если реализовано
        "message": "Данные с IMDB получены",
    }        

    return templates.TemplateResponse("result.html", context)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)