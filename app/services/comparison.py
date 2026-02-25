# В конец файла main.py (или создай новый файл services/comparison.py и импортируй)

def create_comparison_rows(baseline_eps, compared_eps):
    """Создаёт плоский список для таблицы сравнения"""
    from collections import defaultdict

    # Собираем все уникальные (season, episode)
    all_keys = set()
    base_dict = {}
    comp_dict = {}

    for ep in baseline_eps:
        key = (ep.season, ep.episode)
        all_keys.add(key)
        base_dict[key] = ep

    for ep in compared_eps:
        key = (ep.season, ep.episode)
        all_keys.add(key)
        comp_dict[key] = ep

    rows = []
    for season, episode in sorted(all_keys):
        b = base_dict.get((season, episode))
        c = comp_dict.get((season, episode))

        title_b = b.title if b else "—"
        title_c = c.title if c else "—"
        dur_b = b.duration_min if b and b.duration_min is not None else None
        dur_c = c.duration_min if c and c.duration_min is not None else None

        diff_min = None
        diff_percent = None
        color_class = "neutral"

        if dur_b is not None and dur_c is not None:
            diff_min = dur_c - dur_b
            if dur_b > 0:
                diff_percent = round((diff_min / dur_b) * 100, 1)

            if abs(diff_percent or 0) < 3:
                color_class = "neutral"
            elif diff_min < 0:
                color_class = "red"      # сокращение
            else:
                color_class = "green"    # длиннее

        rows.append({
            "season": season,
            "episode": episode,
            "title_baseline": title_b,
            "duration_baseline": dur_b,
            "title_compared": title_c,
            "duration_compared": dur_c,
            "diff_min": diff_min,
            "diff_percent": diff_percent,
            "color_class": color_class,
            "missing_in_baseline": b is None,
            "missing_in_compared": c is None,
        })

    return rows