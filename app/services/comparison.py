# app/services/comparison.py

from app.config import DiffThresholds


def create_comparison_rows(baseline_eps, compared_eps):
    """Создаёт плоский список для таблицы сравнения"""

    base_dict = {}
    comp_dict = {}
    all_keys = set()

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
        abs_p = 0

        if dur_b is not None and dur_c is not None and dur_b > 0:
            diff_min = dur_c - dur_b
            diff_percent = round((diff_min / dur_b) * 100, 1)
            abs_p = abs(diff_percent)
            abs_m = abs(diff_min)

            if abs_m <= DiffThresholds.MINUTES_INSIGNIFICANT or abs_p < DiffThresholds.PERCENT_SMALL:
                color_class = "small-diff"
            elif abs_p < DiffThresholds.PERCENT_MEDIUM:
                color_class = "medium-diff"
            else:
                color_class = "large-red" if diff_min < 0 else "large-green"

        row_class = ""
        if b is None:
            row_class = "missing-baseline"
        elif c is None:
            row_class = "missing-compared"
        elif abs_p >= DiffThresholds.PERCENT_MEDIUM:
            row_class = "highlight-strong" if diff_min < 0 else "highlight-weak"

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
            "row_class": row_class,
            "missing_in_baseline": b is None,
            "missing_in_compared": c is None,
        })

    return rows