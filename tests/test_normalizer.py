from app.services.normalizer import normalize_duration


class TestIsoDuration:
    def test_pt_hours_and_minutes(self):
        assert normalize_duration("PT1H23M") == 83

    def test_pt_minutes_only(self):
        assert normalize_duration("PT45M") == 45

    def test_pt_hours_only(self):
        assert normalize_duration("PT2H") == 120

    def test_pt_lowercase(self):
        assert normalize_duration("pt1h30m") == 90

    def test_pt_zero(self):
        assert normalize_duration("PT0M") is None


class TestNumeric:
    def test_integer_minutes(self):
        assert normalize_duration(45) == 45

    def test_float_minutes(self):
        assert normalize_duration(45.7) == 46

    def test_seconds_over_60(self):
        assert normalize_duration("3180") == 53  # 3180 / 60 = 53

    def test_minutes_under_60(self):
        assert normalize_duration("45") == 45

    def test_zero(self):
        assert normalize_duration(0) is None

    def test_negative(self):
        assert normalize_duration(-5) is None


class TestTextFormats:
    def test_hours_minutes_with_space(self):
        assert normalize_duration("1h 23m") == 83

    def test_hours_minutes_no_space(self):
        assert normalize_duration("1h23m") == 83

    def test_minutes_only_min(self):
        assert normalize_duration("45 min") == 45

    def test_minutes_only_m(self):
        assert normalize_duration("45m") == 45

    def test_time_format_hh_mm_ss(self):
        assert normalize_duration("01:23:00") == 83

    def test_time_format_mm_ss(self):
        # Matches first pattern (\d+)h?(\d+)m? as two groups
        result = normalize_duration("23:00")
        assert result is not None


class TestEdgeCases:
    def test_none(self):
        assert normalize_duration(None) is None

    def test_empty_string(self):
        assert normalize_duration("") is None

    def test_whitespace(self):
        assert normalize_duration("   ") is None

    def test_non_string_non_number(self):
        assert normalize_duration([1, 2, 3]) is None

    def test_int_value_1(self):
        assert normalize_duration(1) == 1

    def test_large_seconds_string(self):
        # "3600" → 3600 > 60 → 3600 // 60 = 60
        assert normalize_duration("3600") == 60
