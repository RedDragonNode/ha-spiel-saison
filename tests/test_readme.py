"""Das README behält seine Gliederung (ein Patch hat einmal Abschnitte verschluckt)."""

from __future__ import annotations

from pathlib import Path

README = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")


def test_the_readme_keeps_its_sections() -> None:
    headings = [line for line in README.splitlines() if line.startswith("## ")]
    assert headings == [
        "## Installation",
        "## Einrichten",
        "## Entities",
        "## Dienste und Ereignisse",
        "## Von „Spiel Saison aufnehmen“ umsteigen",
        "## Fehlersuche",
        "## Entwicklung",
        "## English",
        "## Lizenz",
    ]
    for section in (
        "### Spielpartner",
        "### Vorlieben",
        "### Orte",
        "### Saison vorbereiten und Saison beenden",
        "### Saison",
    ):
        assert section in README
