"""Defensive normalization layer for converter output.

The conversion prompt is explicit about field shapes, but the model
occasionally echoes AD&D source conventions (e.g. single-letter sizes)
instead of the 5.5e form. These tests pin the normalizer.
"""
from __future__ import annotations

import pytest

from app.conversion import _normalize_size


@pytest.mark.parametrize("raw,expected", [
    ("Tiny", "Tiny"),
    ("Small", "Small"),
    ("Medium", "Medium"),
    ("Large", "Large"),
    ("Huge", "Huge"),
    ("Gargantuan", "Gargantuan"),
])
def test_full_word_sizes_pass_through(raw, expected):
    c = {"size": raw}
    _normalize_size(c)
    assert c["size"] == expected


@pytest.mark.parametrize("letter,expected", [
    ("T", "Tiny"),
    ("S", "Small"),
    ("M", "Medium"),
    ("L", "Large"),
    ("H", "Huge"),
    ("G", "Gargantuan"),
])
def test_single_letter_sizes_expanded(letter, expected):
    c = {"size": letter}
    _normalize_size(c)
    assert c["size"] == expected


@pytest.mark.parametrize("raw,expected", [
    ("M (Man-sized)", "Medium"),
    ("L (Large)", "Large"),
    ("H, Huge", "Huge"),
])
def test_parenthetical_size_letters_expanded(raw, expected):
    c = {"size": raw}
    _normalize_size(c)
    assert c["size"] == expected


@pytest.mark.parametrize("raw,expected", [
    ("huge", "Huge"),
    ("MEDIUM", "Medium"),
    ("tiny", "Tiny"),
])
def test_wrong_case_normalised(raw, expected):
    c = {"size": raw}
    _normalize_size(c)
    assert c["size"] == expected


def test_unknown_size_left_untouched_for_ui_to_flag():
    """If we can't recognize it, leave it alone — the form will render a
    "? (raw value) — re-run conversion" sentinel option so the user can see
    something's wrong rather than silently defaulting to Tiny."""
    c = {"size": "Colossal"}
    _normalize_size(c)
    assert c["size"] == "Colossal"


def test_missing_size_is_noop():
    c = {"name": "x"}
    _normalize_size(c)
    assert "size" not in c


def test_non_string_size_is_noop():
    c = {"size": 5}
    _normalize_size(c)
    assert c["size"] == 5


# ---- strip_markdown_asterisks --------------------------------------------

from app.conversion import strip_markdown_asterisks


def test_strip_asterisks_from_top_level_string():
    assert strip_markdown_asterisks("*A gaunt humanoid*") == "A gaunt humanoid"


def test_strip_double_asterisks_for_bold():
    assert strip_markdown_asterisks("**Frightful Presence.**") == "Frightful Presence."


def test_strip_mixed_asterisks_inside_string():
    assert (
        strip_markdown_asterisks("The *werebat* uses **Multiattack**.")
        == "The werebat uses Multiattack."
    )


def test_strip_asterisks_recurses_into_dicts_and_lists():
    nested = {
        "read_aloud": "*A foot-long lizard*",
        "traits": [
            {"name": "**Echolocation**", "text": "It can *see* in the dark."},
        ],
        "habitat": ["Forest", "Underdark"],
    }
    cleaned = strip_markdown_asterisks(nested)
    assert cleaned["read_aloud"] == "A foot-long lizard"
    assert cleaned["traits"][0]["name"] == "Echolocation"
    assert cleaned["traits"][0]["text"] == "It can see in the dark."
    assert cleaned["habitat"] == ["Forest", "Underdark"]


def test_strip_asterisks_does_not_mutate_input():
    original = {"x": "*y*"}
    strip_markdown_asterisks(original)
    assert original == {"x": "*y*"}


def test_strip_asterisks_passes_non_string_scalars_through():
    assert strip_markdown_asterisks(42) == 42
    assert strip_markdown_asterisks(None) is None
    assert strip_markdown_asterisks(True) is True


def test_strip_asterisks_handles_empty_string():
    assert strip_markdown_asterisks("") == ""
    assert strip_markdown_asterisks("***") == ""
