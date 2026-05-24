"""Unit tests for the flatten transformer.

Verifies each rule in the brief's "Flatten transformer — exact mapping"
table against the werebat fixture (the converter's reference output).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.flatten import flatten_creature

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def werebat() -> dict:
    return json.loads((FIXTURES / "werebat.json").read_text(encoding="utf-8"))


@pytest.fixture
def flat(werebat) -> dict:
    return flatten_creature(werebat)


# ---- Identity, top-level scalars -----------------------------------------

def test_id_is_slug_of_name(flat):
    assert flat["id"] == "werebat"


def test_name_passthrough(flat):
    assert flat["name"] == "Werebat"


def test_size_type_alignment(flat):
    assert flat["size"] == "Medium"
    assert flat["type"] == "Monstrosity"
    assert flat["alignment"] == "Neutral Evil"


# ---- ac.value / hp.average -----------------------------------------------

def test_ac_is_number_from_value(flat):
    assert flat["ac"] == 13
    assert isinstance(flat["ac"], int)


def test_hp_is_number_from_average(flat):
    assert flat["hp"] == 58
    assert isinstance(flat["hp"], int)


# ---- speed ---------------------------------------------------------------

def test_speed_walk_unlabeled(flat):
    # werebat has walk 30 + fly 40
    assert flat["speed"] == "30 ft., Fly 40 ft."


# ---- initiative passthrough ----------------------------------------------

def test_initiative_passthrough(flat):
    assert flat["initiative"] == "+4"


# ---- abilities ------------------------------------------------------------

def test_ability_scores(flat):
    assert flat["str"] == 15
    assert flat["dex"] == 15
    assert flat["con"] == 14
    assert flat["int"] == 10
    assert flat["wis"] == 11
    assert flat["cha"] == 10


# ---- saves ---------------------------------------------------------------

def test_dex_save_includes_pb(flat):
    # Werebat is DEX-proficient (pb=2), dex=15 -> mod +2 -> save +2 + 2 = +4
    assert flat["dex_save"] == "+4"


def test_str_save_no_pb(flat):
    # Werebat is NOT STR-proficient. str=15 -> mod +2 -> save = +2
    assert flat["str_save"] == "+2"


def test_int_save_negative_or_zero(flat):
    # int=10 -> mod 0 -> "+0"
    assert flat["int_save"] == "+0"


# ---- skills --------------------------------------------------------------

def test_skills_string(flat):
    # {"Perception": 4, "Stealth": 4} -> "Perception +4, Stealth +4"
    assert flat["skills"] == "Perception +4, Stealth +4"


# ---- immunities ----------------------------------------------------------

def test_immunities_damage_only(flat):
    # Werebat has damage immunity but no condition immunities -> no semicolon.
    assert flat["immunities"].startswith("Bludgeoning, Piercing, Slashing")
    assert ";" not in flat["immunities"]


def test_immunities_both_sides_joined_with_semicolon():
    nested = {
        "name": "x", "abilities": {}, "ac": {"value": 10}, "hp": {"average": 1},
        "damage_immunities": ["Fire"],
        "condition_immunities": ["Charmed", "Frightened"],
    }
    out = flatten_creature(nested)
    assert out["immunities"] == "Fire; Charmed, Frightened"


def test_immunities_condition_only_drops_semicolon():
    nested = {
        "name": "x", "abilities": {}, "ac": {"value": 10}, "hp": {"average": 1},
        "condition_immunities": ["Charmed"],
    }
    out = flatten_creature(nested)
    assert out["immunities"] == "Charmed"


# ---- senses --------------------------------------------------------------

def test_senses_includes_passive_perception(flat):
    # werebat senses already contain Passive Perception 14 — should not duplicate.
    assert "Passive Perception 14" in flat["senses"]
    assert flat["senses"].count("Passive Perception") == 1


def test_senses_appends_passive_if_missing():
    nested = {
        "name": "x",
        "abilities": {"wis": 14},
        "ac": {"value": 10}, "hp": {"average": 1},
        "senses": ["Darkvision 60 ft."],
        "skills": {"Perception": 5},
    }
    out = flatten_creature(nested)
    # Perception +5 -> Passive 15
    assert out["senses"].endswith("Passive Perception 15")


# ---- languages, cr, xp ---------------------------------------------------

def test_languages_csv(flat):
    assert flat["languages"] == "Common (can't speak in bat form)"


def test_cr_integer_stringified(flat):
    assert flat["cr"] == "3"


@pytest.mark.parametrize("nested_cr,expected", [
    (0.125, "1/8"),
    (0.25, "1/4"),
    (0.5, "1/2"),
    (1, "1"),
    (15, "15"),
])
def test_cr_fractional_mapping(nested_cr, expected):
    nested = {
        "name": "x", "abilities": {}, "ac": {"value": 10}, "hp": {"average": 1},
        "cr": nested_cr,
    }
    assert flatten_creature(nested)["cr"] == expected


def test_xp_passthrough(flat):
    assert flat["xp"] == 700


# ---- traits / actions blocks ---------------------------------------------

def test_traits_block_join(flat):
    assert flat["traits"].startswith("Shapechanger.")
    assert "\nEcholocation." in flat["traits"]
    assert "\nSunlight Sensitivity." in flat["traits"]


def test_actions_block_join(flat):
    assert flat["actions"].startswith("Multiattack.")
    assert "\nClaw (Hybrid Form Only)." in flat["actions"]
    assert "\nBite (Bat or Hybrid Form Only)." in flat["actions"]


def test_legendary_flag_absent_when_no_legendary_actions(flat):
    # werebat has no legendary actions
    assert flat["legendary_actions"] == ""
    assert "legendary" not in flat


def test_legendary_flag_set_when_legendary_actions_present():
    nested = {
        "name": "x", "abilities": {}, "ac": {"value": 10}, "hp": {"average": 1},
        "legendary_actions": [{"name": "Move", "text": "The thing moves."}],
    }
    out = flatten_creature(nested)
    assert out["legendary"] is True
    assert out["legendary_actions"].startswith("Move.")


# ---- source, setting, custom, image_url ----------------------------------

def test_source_pulled_from_source_title(flat):
    assert flat["source"].startswith("TSR 02122")


def test_setting_passthrough(flat):
    assert flat["setting"] == "Ravenloft"


def test_custom_is_false_for_converted_records(flat):
    assert flat["custom"] is False


def test_image_url_defaults_to_relative_assets_path(flat):
    assert flat["image_url"] == "assets/monster_images/werebat.webp"


def test_image_url_override_for_legacy_records():
    nested = {"name": "Werebat", "abilities": {}, "ac": {"value": 10}, "hp": {"average": 1}}
    out = flatten_creature(nested, image_url="https://www.aidedd.org/img/werebat.jpg")
    assert out["image_url"] == "https://www.aidedd.org/img/werebat.jpg"


# ---- new optional fields passed through ----------------------------------

def test_read_aloud_passthrough(flat):
    assert flat["read_aloud"].startswith("A gaunt humanoid")


def test_salient_features_passthrough(flat):
    assert isinstance(flat["salient_features"], list)
    assert flat["salient_features"][0]["title"] == "Three Forms"


def test_habitat_passthrough(flat):
    assert flat["habitat"] == ["Forest", "Underdark"]


# ---- conversion_notes is dropped per brief -------------------------------

def test_conversion_notes_dropped(flat, werebat):
    assert "conversion_notes" in werebat
    assert "conversion_notes" not in flat
