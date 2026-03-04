# bot/formatter.py
#
# Форматирует результат сравнения в текст для Telegram (MarkdownV2).
# Использует те же пороги из config.py что и веб-интерфейс.

from app.config import DiffThresholds


def format_comparison(
    baseline_name: str,
    baseline_title: str,
    compared_name: str,
    compared_title: str,
    rows: list[dict],
    season: int,
) -> str:
    lines = []

    lines.append(f"📊 *Сравнение сезона {season}*")
    lines.append("")
    lines.append(f"▪️ Baseline: *{escape(baseline_name)}* — {escape(baseline_title)}")
    lines.append(f"▪️ Compared: *{escape(compared_name)}* — {escape(compared_title)}")
    lines.append("")

    significant = []
    medium = []
    missing = []

    for row in rows:
        ep_label = f"S{row['season']:02d}E{row['episode']:02d}"

        if row.get("missing_in_baseline") or row.get("missing_in_compared"):
            where = compared_name if row.get("missing_in_baseline") else baseline_name
            missing.append(f"  ❓ {ep_label} — только на {escape(where)}")
            continue

        dur_b = row["duration_baseline"]
        dur_c = row["duration_compared"]
        diff = row["diff_min"]
        pct = row["diff_percent"]
        color = row["color_class"]

        if dur_b is None or dur_c is None:
            continue

        sign = "+" if diff > 0 else ""

        if color in ("large-red", "large-green"):
            emoji = "🟢" if diff > 0 else "🔴"
            significant.append(
                f"  {emoji} {ep_label}: {dur_b}м → {dur_c}м "
                f"\\({sign}{diff}м, {sign}{pct}%\\)"
            )
        elif color == "medium-diff":
            medium.append(
                f"  🟡 {ep_label}: {dur_b}м → {dur_c}м \\({sign}{diff}м\\)"
            )

    total = len(rows)
    sig_count = len(significant)

    if not significant and not medium and not missing:
        lines.append(
            "✅ Значимых различий не найдено\\. "
            f"Все эпизоды совпадают в пределах {DiffThresholds.MINUTES_INSIGNIFICANT} мин\\."
        )
    else:
        if significant:
            lines.append(
                f"*Значимые отличия \\(≥{DiffThresholds.PERCENT_MEDIUM}%\\):*"
            )
            lines.extend(significant)
            lines.append("")

        if medium:
            lines.append(
                f"*Небольшие отличия "
                f"\\({DiffThresholds.PERCENT_SMALL}–{DiffThresholds.PERCENT_MEDIUM}%\\):*"
            )
            lines.extend(medium)
            lines.append("")

        if missing:
            lines.append("*Отсутствующие эпизоды:*")
            lines.extend(missing)
            lines.append("")

    content = f"Всего эпизодов: {total}, с отличиями ≥{DiffThresholds.PERCENT_MEDIUM}%: {sig_count}"
    lines.append(f"_{escape(content)}_")

    return "\n".join(lines)


def escape(text: str) -> str:
    """Экранирует спецсимволы для Telegram MarkdownV2."""
    special = r"_*[]()~`>#+-=|{}.!"
    for ch in special:
        text = text.replace(ch, f"\\{ch}")
    return text


def split_message(text: str, limit: int = 4096) -> list[str]:
    """
    Разбивает длинный текст на части ≤ limit символов по переносам строк.
    Нужно для сериалов с большим количеством эпизодов.
    """
    if len(text) <= limit:
        return [text]

    parts = []
    current_lines = []
    current_len = 0

    for line in text.split("\n"):
        line_len = len(line) + 1  # +1 за \n
        if current_len + line_len > limit:
            parts.append("\n".join(current_lines))
            current_lines = []
            current_len = 0
        current_lines.append(line)
        current_len += line_len

    if current_lines:
        parts.append("\n".join(current_lines))

    return parts