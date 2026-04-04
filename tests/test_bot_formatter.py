from bot.formatter import escape, format_comparison, split_message


class TestEscape:
    def test_underscores(self):
        assert escape("hello_world") == r"hello\_world"

    def test_asterisks(self):
        assert escape("*bold*") == r"\*bold\*"

    def test_brackets(self):
        assert escape("[link](url)") == r"\[link\]\(url\)"

    def test_dots(self):
        assert escape("end.") == r"end\."

    def test_plain_text(self):
        assert escape("hello") == "hello"

    def test_multiple_specials(self):
        result = escape("a_b*c.d")
        assert result == r"a\_b\*c\.d"


class TestSplitMessage:
    def test_short_message(self):
        text = "short"
        assert split_message(text) == ["short"]

    def test_exact_limit(self):
        text = "x" * 4096
        assert split_message(text) == [text]

    def test_splits_on_newlines(self):
        lines = ["line " + str(i) for i in range(200)]
        text = "\n".join(lines)
        parts = split_message(text, limit=100)
        assert len(parts) > 1
        for part in parts:
            assert len(part) <= 100

    def test_reassembles_correctly(self):
        lines = ["line " + str(i) for i in range(50)]
        text = "\n".join(lines)
        parts = split_message(text, limit=100)
        reassembled = "\n".join(parts)
        assert reassembled == text


class TestFormatComparison:
    def test_basic_output(self):
        rows = [
            {
                "season": 1, "episode": 1,
                "title_baseline": "Pilot", "title_compared": "Pilot",
                "duration_baseline": 48, "duration_compared": 48,
                "diff_min": 0, "diff_percent": 0.0,
                "color_class": "small-diff",
                "row_class": "",
                "missing_in_baseline": False, "missing_in_compared": False,
            }
        ]
        result = format_comparison("IMDb", "Breaking Bad", "Amediateka", "Breaking Bad", rows, 1)
        assert "Сравнение сезона 1" in result
        assert "IMDb" in result
        assert "S01E01" in result

    def test_missing_episode(self):
        rows = [
            {
                "season": 1, "episode": 1,
                "title_baseline": "Pilot", "title_compared": "—",
                "duration_baseline": 48, "duration_compared": None,
                "diff_min": None, "diff_percent": None,
                "color_class": "neutral",
                "row_class": "missing-compared",
                "missing_in_baseline": False, "missing_in_compared": True,
            }
        ]
        result = format_comparison("IMDb", "Test", "Amediateka", "Test", rows, 1)
        assert "❓" in result

    def test_large_red_diff(self):
        rows = [
            {
                "season": 1, "episode": 1,
                "title_baseline": "Ep1", "title_compared": "Ep1",
                "duration_baseline": 60, "duration_compared": 50,
                "diff_min": -10, "diff_percent": -16.7,
                "color_class": "large-red",
                "row_class": "highlight-strong",
                "missing_in_baseline": False, "missing_in_compared": False,
            }
        ]
        result = format_comparison("IMDb", "Test", "Amediateka", "Test", rows, 1)
        assert "🔴" in result
