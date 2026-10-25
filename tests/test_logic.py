"""Reine Logik: Muster, Namen und Pfade."""

from __future__ import annotations

from datetime import datetime

from custom_components.spiel_saison import logic
from custom_components.spiel_saison.const import DEFAULT_FILE_PATTERN

WHEN = datetime(2026, 10, 15, 21, 5, 9)


def test_norm_ignores_case_umlauts_and_punctuation() -> None:
    assert logic.norm("Fröhlich-Frank #2") == "froehlichfrank2"


def test_pad_makes_two_digits() -> None:
    assert (logic.pad(2), logic.pad(12), logic.pad(123)) == ("02", "12", "123")


def test_split_path_drops_empty_parts_and_dots() -> None:
    assert logic.split_path("Spielen/ /Spezial/../x") == ["Spielen", "Spezial", "x"]
    assert logic.split_path("") == []


def test_render_pattern_builds_the_names_of_the_old_integration() -> None:
    values = {"partner": "Frank", "nr": "02", "saison": "01", "aufnahme": "03", "cam": "Fuchs", "cam_nr": "01"}
    assert logic.render_pattern("Spielen mit {partner} #{nr}", WHEN, values) == "Spielen mit Frank #02"
    assert logic.render_pattern("Saison {saison} vom %DD,%MM,%CCYY", WHEN, values) == "Saison 01 vom 15,10,2026"
    pattern = "#{nr}.{saison}.{aufnahme}.Cam ID.{cam_nr} %DD.%MM.%CCYY"
    assert logic.render_pattern(pattern, WHEN, values) == "#02.01.03.Cam ID.01 15.10.2026"


def test_the_default_file_pattern_uses_the_name_of_the_camera() -> None:
    values = {"nr": "02", "saison": "01", "aufnahme": "03", "cam": "Fuchsrevier", "cam_nr": "01"}
    assert logic.render_pattern(DEFAULT_FILE_PATTERN, WHEN, values) == "#02.01.03.Cam ID.Fuchsrevier 15.10.2026"


def test_render_pattern_time_tokens() -> None:
    assert logic.render_pattern("%hh-%mm-%ss", WHEN, {}) == "21-05-09"


def test_render_pattern_keeps_unknown_placeholders_and_defuses_separators() -> None:
    assert logic.render_pattern("{partner} {unbekannt}", WHEN, {"partner": "A/B"}) == "A-B {unbekannt}"


def test_unique_stems_only_changes_duplicates() -> None:
    assert logic.unique_stems(["a", "b", "a", "A"]) == ["a", "b", "a Cam 03", "A Cam 04"]


def test_unique_names_appends_entity_part_for_duplicates() -> None:
    assert logic.unique_names(["Fuchs", "Fuchs", "Hase"], ["main", "sub", "x"]) == [
        "Fuchs (main)",
        "Fuchs (sub)",
        "Hase",
    ]


def test_parse_presets_reads_the_ctronics_lines_and_skips_bad_ones() -> None:
    text = "1: Schaukel\n 20 = Bett \n\nkaputt\n99: zu hoch\n0: null\n7:"
    assert logic.parse_presets(text) == {1: "Schaukel", 20: "Bett"}
    assert logic.parse_presets("") == {}


def test_preset_labels_add_the_number_to_equal_names() -> None:
    assert logic.preset_labels({20: "Bett", 5: "Schaukel", 1: "Schaukel"}) == {
        1: "Schaukel (1)",
        5: "Schaukel (5)",
        20: "Bett",
    }


def test_place_title_names_the_partner() -> None:
    assert logic.place_title("Bett", "Frank") == "Bett (Frank)"
