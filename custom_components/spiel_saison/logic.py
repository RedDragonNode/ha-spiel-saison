"""Reine Logik ohne Home-Assistant-Abhängigkeit (leicht testbar)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime

_UMLAUTE = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "ae", "Ö": "oe", "Ü": "ue"})

_TOKENS = (
    ("%CCYY", "%Y"),
    ("%YY", "%y"),
    ("%MM", "%m"),
    ("%DD", "%d"),
    ("%hh", "%H"),
    ("%mm", "%M"),
    ("%ss", "%S"),
)

# Platzhalter in den Mustern, in geschweiften Klammern: {partner} {nr} {saison} {aufnahme} {cam} {cam_nr} {ort}
PLACEHOLDERS = ("partner", "nr", "saison", "aufnahme", "cam", "cam_nr", "ort")


def norm(text: str) -> str:
    """Name zum Vergleichen: klein, Umlaute aufgelöst, nur Buchstaben und Ziffern."""
    return re.sub(r"[^a-z0-9]", "", str(text).translate(_UMLAUTE).lower())


def pad(number: int) -> str:
    """Zahl zweistellig: 3 -> '03'."""
    return f"{int(number):02d}"


def split_path(text: str) -> list[str]:
    """'Spielen/Spezial' -> ['Spielen', 'Spezial'] (leere Teile und '..' fallen weg)."""
    return [p.strip() for p in re.split(r"[/\\]", text or "") if p.strip() and p.strip() not in (".", "..")]


def render_tokens(text: str, when: datetime) -> str:
    """Datums-Platzhalter (%DD.%MM.%CCYY ...) in echte Werte umsetzen."""
    for token, fmt in _TOKENS:
        text = text.replace(token, when.strftime(fmt))
    return text


def _clean(value: object) -> str:
    """Wert für ein Muster: Pfadtrenner wären sonst plötzlich Unterordner."""
    return re.sub(r"[\\/]+", "-", str(value)).strip()


def render_pattern(pattern: str, when: datetime, values: Mapping[str, object]) -> str:
    """Muster mit {platzhalter} und Datums-Platzhaltern füllen. Unbekannte Platzhalter bleiben stehen."""
    text = pattern
    for key in PLACEHOLDERS:
        if key in values:
            text = text.replace(f"{{{key}}}", _clean(values[key]))
    return render_tokens(text, when)


def unique_stems(stems: list[str]) -> list[str]:
    """Macht gleiche Dateinamen eindeutig (zwei Kameras würden sonst dieselbe Datei beschreiben)."""
    seen: dict[str, int] = {}
    out: list[str] = []
    for index, stem in enumerate(stems, start=1):
        key = stem.casefold()
        seen[key] = seen.get(key, 0) + 1
        out.append(stem if seen[key] == 1 else f"{stem} Cam {pad(index)}")
    return out


def unique_names(names: list[str], fallbacks: list[str]) -> list[str]:
    """Macht doppelte Kamera-Namen eindeutig, indem die Entity-ID-Teile angehängt werden."""
    counts: dict[str, int] = {}
    for name in names:
        counts[name] = counts.get(name, 0) + 1
    return [
        f"{name} ({fallback})" if counts[name] > 1 else name for name, fallback in zip(names, fallbacks, strict=True)
    ]
