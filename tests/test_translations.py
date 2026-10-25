"""Übersetzungen: gleiche Schlüssel in beiden Sprachen und keine unbekannten Platzhalter."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

TRANSLATIONS = Path(__file__).resolve().parents[1] / "custom_components" / "spiel_saison" / "translations"
# Das Frontend liest jede geschweifte Klammer als Platzhalter und meldet einen Fehler, wenn der Wert fehlt.
ALLOWED_PLACEHOLDERS = {"name", "partner", "place", "reason", "details"}


def _flatten(node: object, prefix: str = "") -> dict[str, str]:
    if isinstance(node, dict):
        out: dict[str, str] = {}
        for key, value in node.items():
            out.update(_flatten(value, f"{prefix}.{key}" if prefix else key))
        return out
    return {prefix: str(node)}


@pytest.fixture(scope="module")
def texts() -> dict[str, dict[str, str]]:
    return {
        lang: _flatten(json.loads((TRANSLATIONS / f"{lang}.json").read_text(encoding="utf-8"))) for lang in ("de", "en")
    }


def test_both_languages_have_the_same_keys(texts: dict[str, dict[str, str]]) -> None:
    assert set(texts["de"]) == set(texts["en"])


@pytest.mark.parametrize("lang", ["de", "en"])
def test_only_known_placeholders(texts: dict[str, dict[str, str]], lang: str) -> None:
    for key, text in texts[lang].items():
        assert set(re.findall(r"\{([^{}]*)\}", text)) <= ALLOWED_PLACEHOLDERS, f"{lang}: {key}"
        assert text.count("{") == text.count("}"), f"{lang}: {key}"


def test_placeholders_match_between_languages(texts: dict[str, dict[str, str]]) -> None:
    for key, de_text in texts["de"].items():
        assert set(re.findall(r"\{(\w+)\}", de_text)) == set(re.findall(r"\{(\w+)\}", texts["en"][key])), key


@pytest.mark.parametrize("lang", ["de", "en"])
def test_no_text_is_empty(texts: dict[str, dict[str, str]], lang: str) -> None:
    assert all(text.strip() for text in texts[lang].values())
