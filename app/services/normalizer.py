import re
from typing import Optional, Union


def normalize_duration(raw: Union[str, int, float, None]) -> Optional[int]:
    """
    Приводит любое представление длительности к минутам (int) или None.
    Поддерживает форматы:
    - PT1H23M, PT45M (ISO duration)
    - 1h 23m, 1h23m, 83 min, 45m
    - 01:23:00, 1:23 (но только если нет часов → считаем минуты)
    """
    if raw is None:
        return None

    # Если уже число — просто приводим
    if isinstance(raw, (int, float)):
        return int(round(raw)) if raw > 0 else None

    if not isinstance(raw, str):
        return None

    s = raw.strip().lower().replace(" ", "")

    if not s:
        return None

    # ─── ISO PT-формат (самый надёжный для IMDb ld+json) ──────────────
    if s.startswith("pt"):
        minutes = 0
        h = re.search(r"(\d+)h", s)
        m = re.search(r"(\d+)m", s)
        if h:
            minutes += int(h.group(1)) * 60
        if m:
            minutes += int(m.group(1))
        return minutes if minutes > 0 else None

    # ─── Числовые строки ───────────────────────────────────────────────
    if s.isdigit():
        val = int(s)
        # Предполагаем секунды, если значение > 60
        return val // 60 if val > 60 else val

    # ─── Текстовые форматы ─────────────────────────────────────────────
    patterns = [
        # 1h 23m, 1h23m
        r"(?:(\d+)h)?(\d+)m?",
        # 83 min, 45m
        r"(\d+)\s*min?",
        # 01:23:00 → берём только минуты (часы редко в сериалах)
        r"(\d+):(\d{2})(?::\d{2})?",
    ]

    for pat in patterns:
        m = re.match(pat + "$", s)
        if m:
            groups = [g for g in m.groups() if g is not None]
            if len(groups) == 1:
                return int(groups[0])
            elif len(groups) == 2:
                h, m = map(int, groups)
                return h * 60 + m
            elif len(groups) == 3:
                # если есть часы — считаем только минуты (для сериалов)
                _, m, _ = map(int, groups)
                return m

    return None
