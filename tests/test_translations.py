#!/usr/bin/env python3
"""Translation master/derived parity and the 2.1.0 keys.

[REQ-019] ``strings.json`` is the master; ``translations/en.json`` and
``translations/de.json`` are derived and must carry EXACTLY the same key tree.
A missing/extra key means HA renders a raw key (or falls back to English) in one
language - the classic "Burn Lookback" vs. "burn_lookback_5h" bug.

The four new 2.1.0 entity keys and the three burn-lookback option keys are
additionally pinned by value so a rename cannot slip through unnoticed.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent / "custom_components" / "go_gauge"
TRANSLATION_FILES = {
    "master": BASE / "strings.json",
    "en": BASE / "translations" / "en.json",
    "de": BASE / "translations" / "de.json",
}

NEW_NUMBER_KEYS = {
    "burn_lookback_5h": {"name": "5h Burn Lookback"},
    "burn_lookback_week": {"name": "Weekly Burn Lookback"},
    "burn_lookback_month": {"name": "Monthly Burn Lookback"},
}
NEW_BUTTON_KEY = {"reset_burn_lookbacks": {"name": "Reset Burn Lookbacks"}}

NEW_OPTION_KEYS = {
    "burn_lookback_5h_minutes",
    "burn_lookback_week_minutes",
    "burn_lookback_month_minutes",
}


def _read(kind: str) -> dict:
    return json.loads(TRANSLATION_FILES[kind].read_text(encoding="utf-8"))


def _flatten(data: object, prefix: str = "") -> dict[str, object]:
    """Flatten nested dicts to ``dotted.key -> leaf`` (lists treated as leaves)."""
    flat: dict[str, object] = {}
    assert isinstance(data, dict)
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(_flatten(value, path))
        else:
            flat[path] = value
    return flat


@pytest.mark.parametrize("kind", ["en", "de"])
def test_recursive_key_sets_match_the_master(kind):
    """[REQ-019] No missing/extra translation keys in any language."""
    master = set(_flatten(_read("master")))
    derived = set(_flatten(_read(kind)))

    assert derived == master
    assert master - derived == set(), f"{kind}: missing keys"
    assert derived - master == set(), f"{kind}: unexpected keys"


def test_new_number_entity_keys_are_translated_in_english():
    """[REQ-019] Exact English names of the three slider entities."""
    for kind in ("master", "en"):
        numbers = _read(kind)["entity"]["number"]
        for key, expected in NEW_NUMBER_KEYS.items():
            assert numbers.get(key) == expected, f"{kind}: entity.number.{key}"


def test_reset_button_key_is_translated_in_english():
    """[REQ-019] Exact English name of the reset button entity."""
    for kind in ("master", "en"):
        assert _read(kind)["entity"]["button"].get("reset_burn_lookbacks") == (
            NEW_BUTTON_KEY["reset_burn_lookbacks"]
        ), kind


def test_new_entity_keys_are_translated_in_german():
    """[REQ-019] German names exist and are not the raw English fallback."""
    number_names = _read("de")["entity"]["number"]
    assert number_names["burn_lookback_5h"]["name"] == "5h Burn-Lookback"
    assert number_names["burn_lookback_week"]["name"] == "Wöchentlicher Burn-Lookback"
    assert number_names["burn_lookback_month"]["name"] == "Monatlicher Burn-Lookback"
    assert _read("de")["entity"]["button"]["reset_burn_lookbacks"]["name"] == (
        "Burn-Lookbacks zurücksetzen"
    )


@pytest.mark.parametrize("kind", ["master", "en", "de"])
def test_options_form_translates_all_burn_lookbacks(kind):
    """[REQ-019] The options form labels the 3 lookbacks + ``pace_red_percent``."""
    data = _read(kind)["options"]["step"]["init"]["data"]

    assert NEW_OPTION_KEYS <= set(data), kind
    assert "pace_red_percent" in data, kind
    for key in NEW_OPTION_KEYS:
        assert isinstance(data[key], str) and data[key].strip(), f"{kind}: {key}"


def test_new_translation_values_are_not_raw_keys():
    """[REQ-019] No entity shows its own translation_key as the display name."""
    for kind in ("master", "en", "de"):
        numbers = _read(kind)["entity"]["number"]
        buttons = _read(kind)["entity"]["button"]
        for key in NEW_NUMBER_KEYS:
            assert numbers[key]["name"] != key, f"{kind}: {key} is untranslated"
        assert buttons["reset_burn_lookbacks"]["name"] != "reset_burn_lookbacks"
