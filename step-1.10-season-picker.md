# Этап 1.10 — Season Picker (выбор сезона без ручного URL)

**Цель:** пользователь вставляет любую страницу сериала → система находит доступные сезоны → пользователь выбирает нужный → сравнение запускается  
**Статус до:** нужен точный URL конкретного сезона  
**Статус после:** достаточно любой страницы сериала

---

## Итоговые изменения файлов

```
app/
├── scrapers/
│   ├── base.py           ← добавить метод get_seasons()
│   ├── imdb.py           ← реализовать get_seasons()
│   └── amediateka.py     ← реализовать get_seasons()
├── main.py               ← добавить эндпоинт POST /get-seasons
└── templates/
    └── index.html        ← новый двухшаговый UX
```

---

## Шаг 1 — `app/scrapers/base.py`

Добавить абстрактный метод `get_seasons()` в `BaseScraper`.

```python
# app/scrapers/base.py
from abc import ABC, abstractmethod
from typing import List, Optional

from app.models import Episode, ScrapeResult


class BaseScraper(ABC):

    @abstractmethod
    async def scrape_episodes(self, url: str) -> ScrapeResult:
        """Вернуть список Episode с заполненными duration_min"""
        pass

    @abstractmethod
    async def get_seasons(self, url: str) -> List[int]:
        """
        Вернуть список доступных номеров сезонов для сериала.
        url — любая страница сериала или сезона на данном сервисе.
        Возвращает отсортированный список, например [1, 2, 3, 4, 5].
        При ошибке возвращает пустой список [].
        """
        pass

    @abstractmethod
    def build_season_url(self, url: str, season: int) -> str:
        """
        Построить URL конкретного сезона из любого URL сериала.
        Например: build_season_url("https://imdb.com/title/tt0903747/", 3)
                  → "https://www.imdb.com/title/tt0903747/episodes?season=3"
        """
        pass


class ScraperFactory:
    _scrapers = {}

    @classmethod
    def register(cls, key: str, scraper_class):
        cls._scrapers[key] = scraper_class

    @classmethod
    def get_scraper(cls, service_key: str) -> Optional[BaseScraper]:
        scraper_class = cls._scrapers.get(service_key)
        if scraper_class:
            return scraper_class()
        return None
```

---

## Шаг 2 — `app/scrapers/imdb.py`

Добавить два новых метода в класс `ImdbScraper`.

```python
# Добавить в класс ImdbScraper (остальной код без изменений)

async def get_seasons(self, url: str) -> List[int]:
    """Получить список сезонов из страницы сериала на IMDb."""
    series_id = self._extract_series_id(url)
    if not series_id:
        return []

    # Страница со всеми сезонами — специальный URL IMDb
    seasons_url = f"https://www.imdb.com/title/{series_id}/episodes/"

    async with httpx.AsyncClient(headers=self.HEADERS, timeout=15.0) as client:
        try:
            resp = await client.get(seasons_url, follow_redirects=True)
            if resp.status_code != 200:
                return []

            soup = BeautifulSoup(resp.text, "html.parser")

            # Вариант 1 — ищем JSON с данными о сезонах (надёжнее всего)
            # IMDb хранит список сезонов в __NEXT_DATA__ или отдельных script-тегах
            scripts = soup.find_all("script", type="application/json")
            for script in scripts:
                try:
                    data = json.loads(script.string or "")
                    # Ищем ключ с сезонами в любом месте структуры
                    seasons = self._find_seasons_in_json(data)
                    if seasons:
                        return sorted(seasons)
                except Exception:
                    continue

            # Вариант 2 — fallback: ищем select или кнопки выбора сезона
            # IMDb рендерит их как <select id="browse-episodes-season">
            select = soup.find("select", {"id": "browse-episodes-season"})
            if select:
                seasons = []
                for option in select.find_all("option"):
                    val = option.get("value", "").strip()
                    if val.isdigit():
                        seasons.append(int(val))
                if seasons:
                    return sorted(seasons)

            # Вариант 3 — ищем по атрибуту aria-label или data-testid
            season_links = soup.find_all(
                "a",
                attrs={"data-testid": lambda v: v and "season-link" in v}
            )
            if season_links:
                seasons = []
                for link in season_links:
                    text = link.get_text(strip=True)
                    if text.isdigit():
                        seasons.append(int(text))
                if seasons:
                    return sorted(seasons)

            return []

        except Exception as e:
            print(f"IMDb get_seasons error: {e}")
            return []

def _find_seasons_in_json(self, data, depth: int = 0) -> List[int]:
    """Рекурсивно ищет список сезонов в произвольной JSON-структуре IMDb."""
    if depth > 8:  # ограничение глубины рекурсии
        return []

    if isinstance(data, dict):
        # IMDb часто хранит сезоны под ключами типа "seasons", "availableSeasons"
        for key in ("seasons", "availableSeasons", "seasonNumbers"):
            if key in data:
                val = data[key]
                if isinstance(val, list):
                    nums = [int(v) for v in val if str(v).isdigit()]
                    if nums:
                        return nums
        # Рекурсивно проверяем вложенные объекты
        for v in data.values():
            result = self._find_seasons_in_json(v, depth + 1)
            if result:
                return result

    elif isinstance(data, list):
        for item in data:
            result = self._find_seasons_in_json(item, depth + 1)
            if result:
                return result

    return []

def build_season_url(self, url: str, season: int) -> str:
    """Строит URL страницы эпизодов конкретного сезона на IMDb."""
    series_id = self._extract_series_id(url)
    if not series_id:
        return url
    return f"https://www.imdb.com/title/{series_id}/episodes?season={season}"
```

---

## Шаг 3 — `app/scrapers/amediateka.py`

Добавить два новых метода в класс `AmediatekaScraper`.

```python
# Добавить в класс AmediatekaScraper (остальной код без изменений)

async def get_seasons(self, url: str) -> List[int]:
    """
    Получить список сезонов из __NEXT_DATA__ на странице Amediateka.
    Работает с любой страницей сезона данного сериала.
    """
    html = await self._fetch_page(url)
    if not html:
        return []

    match = re.search(
        r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html, re.DOTALL
    )
    if not match:
        return []

    try:
        data = json.loads(match.group(1))
        page_props = data.get("props", {}).get("pageProps", {})
        content = page_props.get("content", {})

        # Amediateka хранит все сезоны в поле series.seasons
        series = content.get("series", {})
        seasons_raw = series.get("seasons", [])

        if seasons_raw:
            seasons = []
            for s in seasons_raw:
                num = s.get("seasonNumber") or s.get("number")
                if isinstance(num, int):
                    seasons.append(num)
            if seasons:
                return sorted(seasons)

        # Fallback: если текущая страница — сезон, берём хотя бы его номер
        current_season = content.get("seasonNumber")
        if isinstance(current_season, int):
            return [current_season]

        return []

    except Exception as e:
        print(f"Amediateka get_seasons error: {e}")
        return []

def build_season_url(self, url: str, season: int) -> str:
    """
    Строит URL конкретного сезона на Amediateka.

    Amediateka использует slug-based URLs:
      https://www.amediateka.ru/series/breaking-bad/season-1/
      https://www.amediateka.ru/series/breaking-bad/season-2/

    Берём slug сериала из текущего URL и подставляем нужный сезон.
    """
    # Паттерн: /series/<slug>/season-<N>/
    match = re.search(r'/series/([^/]+)/', url)
    if match:
        slug = match.group(1)
        return f"https://www.amediateka.ru/series/{slug}/season-{season}/"

    # Если не смогли распарсить — возвращаем оригинальный URL как fallback
    print(f"Amediateka build_season_url: не удалось распарсить slug из {url}")
    return url
```

---

## Шаг 4 — `app/main.py`

Добавить новый эндпоинт `/get-seasons` и обновить импорты.

```python
# Добавить импорт в начало файла
from fastapi.responses import HTMLResponse, JSONResponse  # JSONResponse — новый

# ... существующий код без изменений ...

@app.post("/get-seasons")
async def get_seasons(
    baseline_url: str = Form(...),
    compared_url: str = Form(...),
):
    """
    Принимает два URL сериалов, возвращает JSON со списками сезонов.
    Вызывается из JS на главной странице после нажатия «Найти сезоны».
    """
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
        return JSONResponse(
            {"ok": False, "errors": [f"Baseline: {baseline_name}"]},
            status_code=400
        )
    if not compared_service:
        return JSONResponse(
            {"ok": False, "errors": [f"Compared: {compared_name}"]},
            status_code=400
        )

    baseline_scraper = ScraperFactory.get_scraper(baseline_service)
    compared_scraper = ScraperFactory.get_scraper(compared_service)

    # Запрашиваем сезоны параллельно
    baseline_seasons, compared_seasons = await asyncio.gather(
        baseline_scraper.get_seasons(baseline_url),
        compared_scraper.get_seasons(compared_url),
        return_exceptions=True
    )

    # asyncio.gather с return_exceptions=True может вернуть Exception — обрабатываем
    if isinstance(baseline_seasons, Exception):
        print(f"get_seasons baseline error: {baseline_seasons}")
        baseline_seasons = []
    if isinstance(compared_seasons, Exception):
        print(f"get_seasons compared error: {compared_seasons}")
        compared_seasons = []

    # Общие сезоны — те, которые есть на обоих сервисах
    common_seasons = sorted(
        set(baseline_seasons) & set(compared_seasons)
    )

    return JSONResponse({
        "ok": True,
        "baseline": {
            "service": baseline_name,
            "seasons": baseline_seasons,
        },
        "compared": {
            "service": compared_name,
            "seasons": compared_seasons,
        },
        "common_seasons": common_seasons,
    })
```

---

## Шаг 5 — `app/templates/index.html`

Полная замена файла. Двухшаговый UX: сначала поле URL + кнопка «Найти сезоны», затем появляются кнопки выбора сезона и кнопка «Сравнить».

```html
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ title }}</title>
    <style>
        body {
            font-family: system-ui, -apple-system, sans-serif;
            max-width: 800px;
            margin: 40px auto;
            padding: 20px;
            line-height: 1.6;
            color: #1f2937;
        }
        h1 { color: #0066cc; margin-bottom: 0.5rem; }
        .subtitle {
            color: #6b7280;
            font-size: 0.95rem;
            margin-bottom: 2rem;
        }

        /* ─── Шаг 1: поля URL ─────────────────────────────── */
        .step { margin-bottom: 2rem; }
        .step-label {
            font-size: 0.8rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: #6b7280;
            margin-bottom: 1rem;
        }
        .url-row {
            display: flex;
            flex-direction: column;
            gap: 0.75rem;
            margin-bottom: 1rem;
        }
        .url-field label {
            display: block;
            font-size: 0.9rem;
            font-weight: 500;
            margin-bottom: 0.3rem;
            color: #374151;
        }
        .url-field input {
            width: 100%;
            padding: 10px 14px;
            border: 2px solid #d1d5db;
            border-radius: 8px;
            font-size: 15px;
            box-sizing: border-box;
            transition: border-color 0.15s;
        }
        .url-field input:focus {
            outline: none;
            border-color: #3b82f6;
        }
        .url-field input.error { border-color: #dc2626; }

        /* ─── Кнопки ──────────────────────────────────────── */
        .btn {
            padding: 11px 28px;
            border: none;
            border-radius: 8px;
            font-size: 16px;
            font-weight: 500;
            cursor: pointer;
            transition: background 0.15s, opacity 0.15s;
        }
        .btn-primary { background: #2563eb; color: white; }
        .btn-primary:hover { background: #1d4ed8; }
        .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
        .btn-success { background: #16a34a; color: white; }
        .btn-success:hover { background: #15803d; }
        .btn-success:disabled { opacity: 0.5; cursor: not-allowed; }

        /* ─── Шаг 2: выбор сезона ─────────────────────────── */
        #season-picker {
            display: none;
            background: #f8fafc;
            border: 1px solid #e2e8f0;
            border-radius: 12px;
            padding: 1.5rem;
            margin-bottom: 1.5rem;
        }
        #season-picker.visible { display: block; }

        .season-picker-title {
            font-weight: 600;
            margin-bottom: 1rem;
            color: #1f2937;
        }
        .season-info {
            display: flex;
            gap: 2rem;
            margin-bottom: 1.25rem;
            flex-wrap: wrap;
        }
        .season-info-block { font-size: 0.9rem; color: #6b7280; }
        .season-info-block strong { color: #1f2937; }

        .seasons-buttons {
            display: flex;
            flex-wrap: wrap;
            gap: 0.5rem;
            margin-bottom: 0.5rem;
        }
        .season-btn {
            padding: 8px 18px;
            border: 2px solid #d1d5db;
            border-radius: 8px;
            background: white;
            font-size: 15px;
            cursor: pointer;
            transition: all 0.15s;
            color: #374151;
        }
        .season-btn:hover {
            border-color: #2563eb;
            color: #2563eb;
        }
        .season-btn.selected {
            border-color: #2563eb;
            background: #2563eb;
            color: white;
            font-weight: 600;
        }
        .season-btn.only-baseline {
            border-style: dashed;
            border-color: #d97706;
            color: #92400e;
        }
        .season-btn.only-compared {
            border-style: dashed;
            border-color: #7c3aed;
            color: #4c1d95;
        }
        .season-legend {
            font-size: 0.8rem;
            color: #9ca3af;
            margin-top: 0.5rem;
        }

        /* ─── Статус / ошибки ────────────────────────────── */
        #status {
            margin: 1rem 0;
            font-size: 0.9rem;
            min-height: 1.5rem;
        }
        .status-loading { color: #6b7280; }
        .status-error {
            background: #fef2f2;
            color: #dc2626;
            padding: 12px 16px;
            border-radius: 8px;
            border-left: 4px solid #dc2626;
        }

        /* ─── Скрытые поля формы ─────────────────────────── */
        #compare-form { margin-top: 0; }
    </style>
</head>
<body>

<h1>📊 Сравнитель длительности серий</h1>
<p class="subtitle">
    Сравниваем длительность серий на IMDb и Amediateka, чтобы найти купюры и цензуру.
</p>

{% if error_messages %}
<div class="status-error" style="margin-bottom: 1.5rem;">
    <strong>Ошибки:</strong>
    <ul style="margin: 0.5rem 0 0; padding-left: 1.25rem;">
        {% for err in error_messages %}
            <li>{{ err }}</li>
        {% endfor %}
    </ul>
</div>
{% endif %}

<!-- ─── Шаг 1: ввод URL ──────────────────────────────────── -->
<div class="step">
    <div class="step-label">Шаг 1 — Введите URL сериала на каждом сервисе</div>
    <div class="url-row">
        <div class="url-field">
            <label for="baseline_url">Baseline (IMDb)</label>
            <input type="url" id="baseline_url" name="baseline_url"
                   placeholder="https://www.imdb.com/title/tt0903747/"
                   value="{{ prev_baseline or '' }}">
        </div>
        <div class="url-field">
            <label for="compared_url">Compared (Amediateka)</label>
            <input type="url" id="compared_url" name="compared_url"
                   placeholder="https://www.amediateka.ru/series/breaking-bad/season-1/"
                   value="{{ prev_compared or '' }}">
        </div>
    </div>
    <button class="btn btn-primary" id="find-seasons-btn" onclick="findSeasons()">
        🔍 Найти сезоны
    </button>
</div>

<!-- ─── Статус ───────────────────────────────────────────── -->
<div id="status"></div>

<!-- ─── Шаг 2: выбор сезона ─────────────────────────────── -->
<div id="season-picker">
    <div class="season-picker-title">Шаг 2 — Выберите сезон для сравнения</div>
    <div class="season-info">
        <div class="season-info-block">
            <strong>Baseline:</strong> <span id="info-baseline-service"></span><br>
            Сезонов найдено: <span id="info-baseline-count"></span>
        </div>
        <div class="season-info-block">
            <strong>Compared:</strong> <span id="info-compared-service"></span><br>
            Сезонов найдено: <span id="info-compared-count"></span>
        </div>
    </div>
    <div class="seasons-buttons" id="seasons-buttons"></div>
    <div class="season-legend" id="season-legend"></div>
</div>

<!-- ─── Скрытая форма для сравнения ─────────────────────── -->
<form id="compare-form" action="/compare" method="post" style="display:none; margin-top:1.5rem;">
    <input type="hidden" id="form-baseline-url" name="baseline_url">
    <input type="hidden" id="form-compared-url" name="compared_url">
    <button class="btn btn-success" type="submit" id="compare-btn">
        🚀 Сравнить выбранный сезон
    </button>
</form>

<hr style="margin-top: 3rem; border-color: #e5e7eb;">
<small style="color: #9ca3af;">
    Поддерживаемые сервисы: IMDb и Amediateka &nbsp;·&nbsp;
    Небольшие различия (≤1 мин) могут быть вызваны разными заставками и титрами.
</small>

<script>
    // Текущий выбранный сезон
    let selectedSeason = null;

    // Данные о найденных сезонах (придут с сервера)
    let seasonData = null;

    async function findSeasons() {
        const baselineUrl = document.getElementById('baseline_url').value.trim();
        const comparedUrl = document.getElementById('compared_url').value.trim();

        // Сбросить предыдущий выбор
        selectedSeason = null;
        seasonData = null;
        document.getElementById('compare-form').style.display = 'none';
        document.getElementById('season-picker').classList.remove('visible');

        // Простая валидация
        if (!baselineUrl || !comparedUrl) {
            showError('Введите оба URL.');
            return;
        }

        // UI — показываем загрузку
        const btn = document.getElementById('find-seasons-btn');
        btn.disabled = true;
        btn.textContent = '⏳ Ищем сезоны...';
        showStatus('Запрашиваем информацию о сезонах...', 'loading');

        try {
            const formData = new FormData();
            formData.append('baseline_url', baselineUrl);
            formData.append('compared_url', comparedUrl);

            const resp = await fetch('/get-seasons', {
                method: 'POST',
                body: formData,
            });

            const data = await resp.json();

            if (!data.ok) {
                showError(data.errors ? data.errors.join(' ') : 'Неизвестная ошибка.');
                return;
            }

            seasonData = data;
            renderSeasonPicker(data);
            clearStatus();

        } catch (e) {
            showError('Сетевая ошибка. Проверьте, что приложение запущено.');
        } finally {
            btn.disabled = false;
            btn.textContent = '🔍 Найти сезоны';
        }
    }

    function renderSeasonPicker(data) {
        // Заполняем информацию о сервисах
        document.getElementById('info-baseline-service').textContent = data.baseline.service;
        document.getElementById('info-baseline-count').textContent = data.baseline.seasons.length;
        document.getElementById('info-compared-service').textContent = data.compared.service;
        document.getElementById('info-compared-count').textContent = data.compared.seasons.length;

        // Собираем все уникальные сезоны из обоих сервисов
        const allSeasons = [...new Set([
            ...data.baseline.seasons,
            ...data.compared.seasons
        ])].sort((a, b) => a - b);

        const baselineSet = new Set(data.baseline.seasons);
        const comparedSet = new Set(data.compared.seasons);
        const commonSet = new Set(data.common_seasons);

        // Рендерим кнопки
        const container = document.getElementById('seasons-buttons');
        container.innerHTML = '';

        let hasOnlyBaseline = false;
        let hasOnlyCompared = false;

        for (const season of allSeasons) {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.textContent = `Сезон ${season}`;
            btn.className = 'season-btn';
            btn.dataset.season = season;

            if (!baselineSet.has(season)) {
                // Есть только на Compared
                btn.classList.add('only-compared');
                btn.title = `Только на ${data.compared.service}`;
                hasOnlyCompared = true;
            } else if (!comparedSet.has(season)) {
                // Есть только на Baseline
                btn.classList.add('only-baseline');
                btn.title = `Только на ${data.baseline.service}`;
                hasOnlyBaseline = true;
            }

            btn.addEventListener('click', () => selectSeason(season, btn));
            container.appendChild(btn);
        }

        // Легенда — показываем только если есть сезоны только на одном сервисе
        const legend = document.getElementById('season-legend');
        const legendParts = [];
        if (hasOnlyBaseline) legendParts.push(`— — пунктир оранжевый: только на ${data.baseline.service}`);
        if (hasOnlyCompared) legendParts.push(`— — пунктир фиолетовый: только на ${data.compared.service}`);
        legend.textContent = legendParts.join('  ·  ');

        // Показываем блок выбора
        document.getElementById('season-picker').classList.add('visible');

        // Если только один общий сезон — выбираем его автоматически
        if (data.common_seasons.length === 1) {
            const autoBtn = container.querySelector(`[data-season="${data.common_seasons[0]}"]`);
            if (autoBtn) selectSeason(data.common_seasons[0], autoBtn);
        }
    }

    function selectSeason(season, btnEl) {
        // Убираем выделение с предыдущей кнопки
        document.querySelectorAll('.season-btn.selected')
            .forEach(b => b.classList.remove('selected'));

        // Выделяем текущую
        btnEl.classList.add('selected');
        selectedSeason = season;

        // Строим URL сезона через сервер не нужно — используем данные из seasonData
        // Baseline URL строим через JS: заменяем season=N или season-N в URL
        const baselineUrl = buildSeasonUrl(
            document.getElementById('baseline_url').value.trim(),
            'baseline',
            season
        );
        const comparedUrl = buildSeasonUrl(
            document.getElementById('compared_url').value.trim(),
            'compared',
            season
        );

        document.getElementById('form-baseline-url').value = baselineUrl;
        document.getElementById('form-compared-url').value = comparedUrl;

        // Показываем форму сравнения
        document.getElementById('compare-form').style.display = 'block';
    }

    function buildSeasonUrl(url, side, season) {
        // IMDb: https://www.imdb.com/title/tt0903747/episodes?season=3
        if (url.includes('imdb.com')) {
            const match = url.match(/\/title\/(tt\d+)/);
            if (match) {
                return `https://www.imdb.com/title/${match[1]}/episodes?season=${season}`;
            }
        }

        // Amediateka: https://www.amediateka.ru/series/breaking-bad/season-3/
        if (url.includes('amediateka.ru')) {
            const match = url.match(/\/series\/([^/]+)\//);
            if (match) {
                return `https://www.amediateka.ru/series/${match[1]}/season-${season}/`;
            }
        }

        // Fallback — возвращаем оригинальный URL
        return url;
    }

    function showStatus(msg, type) {
        const el = document.getElementById('status');
        el.className = type === 'loading' ? 'status-loading' : '';
        el.textContent = type === 'loading' ? `⏳ ${msg}` : msg;
    }

    function showError(msg) {
        const el = document.getElementById('status');
        el.className = 'status-error';
        el.textContent = msg;
    }

    function clearStatus() {
        const el = document.getElementById('status');
        el.className = '';
        el.textContent = '';
    }
</script>

</body>
</html>
```

---

## Проверочный чеклист

**Базовый флоу:**
- [ ] Вставить URL страницы сериала (не обязательно сезона) в оба поля
- [ ] Нажать «Найти сезоны» → появляются кнопки сезонов
- [ ] Нажать на сезон → кнопка выделяется, появляется «Сравнить выбранный сезон»
- [ ] Нажать «Сравнить» → открывается таблица результатов

**Граничные случаи:**
- [ ] Один сервис возвращает больше сезонов, чем другой → пунктирные кнопки для уникальных
- [ ] Только один общий сезон → выбирается автоматически
- [ ] Некорректный URL → ошибка ещё на шаге «Найти сезоны», до скрейпинга
- [ ] Сервис недоступен → `get_seasons` возвращает `[]`, ошибка в UI

---

## Возможные проблемы и решения

**IMDb изменил структуру HTML — `get_seasons` возвращает `[]`**  
Это наиболее вероятная проблема — IMDb активно меняет разметку.  
Диагностика: добавить `print(resp.text[:3000])` внутри `get_seasons` и посмотреть что реально приходит.  
Решение: добавить ещё один вариант парсинга — регулярку по тексту типа `"seasonNumber":3`.

**Amediateka: `seasons` пустой в `__NEXT_DATA__`**  
Некоторые страницы Amediateka отдают сезоны под другим ключом.  
Диагностика: `print(json.dumps(series.keys()))` внутри `get_seasons`.  
Решение: расширить поиск — проверить ключи `relatedSeasons`, `allSeasons`.

**URL сериала на Amediateka не содержит `/series/` в пути**  
`build_season_url` вернёт оригинальный URL вместо нужного.  
Решение: дополнить regex под другие форматы URL Amediateka по мере появления.

---

## Следующий шаг после 1.10

После реализации Season Picker архитектура готова к **этапу 2.1 (Telegram-бот)**. Бот сможет использовать те же `get_seasons()` и `scrape_episodes()` — никакого дублирования логики.
