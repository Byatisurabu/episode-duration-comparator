import pytest
from app.services.comparison import create_comparison_rows
from app.models import Episode


def _ep(season, episode, title, duration_min):
    return Episode(season=season, episode=episode, title=title, duration_min=duration_min)


class TestBasicComparison:
    def test_identical_durations(self):
        baseline = [_ep(1, 1, "Pilot", 48)]
        compared = [_ep(1, 1, "Pilot", 48)]
        rows = create_comparison_rows(baseline, compared)
        assert len(rows) == 1
        assert rows[0]["diff_min"] == 0
        assert rows[0]["color_class"] == "small-diff"

    def test_small_diff_within_threshold(self):
        """Разница ≤2 мин → small-diff."""
        baseline = [_ep(1, 1, "Pilot", 50)]
        compared = [_ep(1, 1, "Pilot", 52)]
        rows = create_comparison_rows(baseline, compared)
        assert rows[0]["diff_min"] == 2
        assert rows[0]["color_class"] == "small-diff"

    def test_medium_diff(self):
        """Разница 3-5% → medium-diff."""
        baseline = [_ep(1, 1, "Pilot", 100)]
        compared = [_ep(1, 1, "Pilot", 96)]  # -4%
        rows = create_comparison_rows(baseline, compared)
        assert rows[0]["color_class"] == "medium-diff"

    def test_large_red_cut(self):
        """Разница >5%, compared короче → large-red."""
        baseline = [_ep(1, 1, "Pilot", 60)]
        compared = [_ep(1, 1, "Pilot", 50)]  # -16.7%
        rows = create_comparison_rows(baseline, compared)
        assert rows[0]["diff_min"] == -10
        assert rows[0]["color_class"] == "large-red"
        assert rows[0]["row_class"] == "highlight-strong"

    def test_large_green_addition(self):
        """Разница >5%, compared длиннее → large-green."""
        baseline = [_ep(1, 1, "Pilot", 50)]
        compared = [_ep(1, 1, "Pilot", 60)]  # +20%
        rows = create_comparison_rows(baseline, compared)
        assert rows[0]["diff_min"] == 10
        assert rows[0]["color_class"] == "large-green"
        assert rows[0]["row_class"] == "highlight-weak"


class TestMissingEpisodes:
    def test_missing_in_compared(self):
        baseline = [_ep(1, 1, "Pilot", 48)]
        compared = []
        rows = create_comparison_rows(baseline, compared)
        assert len(rows) == 1
        assert rows[0]["missing_in_compared"] is True
        assert rows[0]["row_class"] == "missing-compared"

    def test_missing_in_baseline(self):
        baseline = []
        compared = [_ep(1, 1, "Pilot", 48)]
        rows = create_comparison_rows(baseline, compared)
        assert len(rows) == 1
        assert rows[0]["missing_in_baseline"] is True
        assert rows[0]["row_class"] == "missing-baseline"


class TestSorting:
    def test_sorted_by_season_and_episode(self):
        baseline = [_ep(1, 3, "Ep3", 40), _ep(1, 1, "Ep1", 40), _ep(2, 1, "S2E1", 40)]
        compared = [_ep(1, 3, "Ep3", 40), _ep(1, 1, "Ep1", 40), _ep(2, 1, "S2E1", 40)]
        rows = create_comparison_rows(baseline, compared)
        keys = [(r["season"], r["episode"]) for r in rows]
        assert keys == [(1, 1), (1, 3), (2, 1)]


class TestEmptyInput:
    def test_both_empty(self):
        assert create_comparison_rows([], []) == []

    def test_multiple_episodes(self):
        baseline = [_ep(1, i, f"Ep{i}", 40 + i) for i in range(1, 4)]
        compared = [_ep(1, i, f"Ep{i}", 40 + i) for i in range(1, 4)]
        rows = create_comparison_rows(baseline, compared)
        assert len(rows) == 3
