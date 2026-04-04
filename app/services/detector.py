# app/services/detector.py
from typing import Optional, Tuple
from urllib.parse import urlparse

# Список поддерживаемых сервисов (расширяется в будущем)
SUPPORTED_SERVICES = {
    "imdb": {
        "names": ["imdb.com", "www.imdb.com"],
        "display_name": "IMDb",
        "example": "https://www.imdb.com/title/tt0944947/episodes?season=1"
    },
    "kinopoisk": {
        "names": ["kinopoisk.ru", "www.kinopoisk.ru", "hd.kinopoisk.ru"],
        "display_name": "Кинопоиск",
        "example": "https://www.kinopoisk.ru/series/1234567/seasons/"
    },
    "amediateka": {
        "names": ["amediateka.ru", "www.amediateka.ru"],
        "display_name": "Amediateka",
        "example": "https://www.amediateka.ru/series/..."
    },
    "okko": {
        "names": ["okko.tv", "www.okko.tv"],
        "display_name": "Okko",
        "example": "https://okko.tv/serial/..."
    },
    # Добавляй сюда другие сервисы по мере реализации
    # "ivi":       {"names": ["ivi.tv", "www.ivi.tv"], ...},
    # "premier":   {"names": ["premier.one", ...], ...},
}


def detect_service(url: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Возвращает:
      (service_key, display_name)   — если удалось определить
      (None, error_message)         — если не удалось
    """
    if not url:
        return None, "URL пустой"

    parsed = urlparse(url)
    if not parsed.netloc:
        return None, "Некорректный URL (нет домена)"

    domain = parsed.netloc.lower()

    for service_key, data in SUPPORTED_SERVICES.items():
        if any(domain == name.lower() or domain.endswith("." + name.lower())
               for name in data["names"]):
            return service_key, data["display_name"]

    return None, f"Сервис не поддерживается (домен: {domain})"


def get_supported_services_list() -> list[dict]:
    """Для отображения в интерфейсе / отладки"""
    return [
        {"key": k, "name": v["display_name"], "example": v["example"]}
        for k, v in SUPPORTED_SERVICES.items()
    ]
