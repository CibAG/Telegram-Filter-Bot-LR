from __future__ import annotations

from app.services.matching import find_matching_filter, matches, validate_pattern


def test_word_match_is_case_insensitive() -> None:
    assert matches("спам", "Это СПАМ в чате")
    assert matches("Спам", "это спам")


def test_word_match_respects_boundaries() -> None:
    assert matches("мат", "вот мат")
    assert not matches("мат", "математика")


def test_phrase_match() -> None:
    assert matches("купи слона", "Ну купи слона, пожалуйста")
    assert not matches("купи слона", "купите слона")


def test_substring_match() -> None:
    assert matches("спам", "антиспамный фильтр", match_type="substring")
    assert not matches("спам", "СПАМ", match_type="substring", case_sensitive=True)


def test_regex_match() -> None:
    assert matches(r"\b\d{3}-\d{3}\b", "телефон 123-456", match_type="regex")
    assert not matches(r"\b\d{3}-\d{3}\b", "телефон 12-34", match_type="regex")


def test_zero_width_characters_do_not_bypass_filter() -> None:
    assert matches("спам", "сп\u200bам")


def test_validate_pattern() -> None:
    assert validate_pattern("слово", "word") is None
    assert validate_pattern("(", "regex") is not None
    assert validate_pattern("", "word") is not None


def test_find_matching_filter_checks_caption() -> None:
    class FakeFilter:
        id = 1
        pattern = "запрет"
        match_type = "word"
        case_sensitive = False

    result = find_matching_filter([FakeFilter()], "", "подпись с запрет")  # type: ignore[list-item]
    assert result is not None
    assert result.id == 1
