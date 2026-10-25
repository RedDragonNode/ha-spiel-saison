"""Reine Logik ohne Home-Assistant-Abhängigkeit (leicht testbar)."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
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

# Platzhalter in Mustern und Ansagen, in geschweiften Klammern:
# {partner} {nickname} {nr} {saison} {aufnahme} {cam} {cam_nr} {ort}
PLACEHOLDERS = ("partner", "nickname", "nr", "saison", "aufnahme", "cam", "cam_nr", "ort")


def norm(text: str) -> str:
    """Name zum Vergleichen: klein, Umlaute aufgelöst, nur Buchstaben und Ziffern."""
    return re.sub(r"[^a-z0-9]", "", str(text).translate(_UMLAUTE).lower())


def place_title(place: str, partner: str) -> str:
    """Titel eines Ortes in der Liste der Einträge: der Name allein wäre bei zwei Spielpartnern doppelt."""
    return f"{place} ({partner})"


def clean_names(names: Iterable[object], known: Iterable[str] = ()) -> list[str]:
    """Eingegebene Namen bereinigen: ohne Leerzeichen am Rand, ohne Doppelte, in der Schreibweise der bekannten.

    Namen ohne Buchstaben und Ziffern fallen weg. Die Reihenfolge der Eingabe bleibt.
    """
    spelling = {norm(name): name for name in known}
    out: dict[str, str] = {}
    for raw in names:
        name = str(raw).strip()
        if key := norm(name):
            out.setdefault(key, spelling.get(key, name))
    return list(out.values())


def preference_key(kind: str, name: str) -> str:
    """Schlüssel einer Vorliebe in den gespeicherten Zählern: Art und Name, ohne Groß- und Kleinschreibung."""
    return f"{kind}:{norm(name)}"


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


_PRESET_LINE = re.compile(r"^\s*(\d+)\s*[:=]\s*(.+?)\s*$")


def parse_presets(text: str, maximum: int = 64) -> dict[int, str]:
    """Zeilen wie '1: Schaukel' (so stehen sie bei Ctronics 4K Cam) als Nummer -> Name. Fehlerhaftes fällt weg."""
    presets: dict[int, str] = {}
    for line in str(text or "").splitlines():
        if (match := _PRESET_LINE.match(line)) and 1 <= int(match.group(1)) <= maximum:
            presets[int(match.group(1))] = match.group(2)
    return presets


def preset_labels(presets: Mapping[int, str]) -> dict[int, str]:
    """Namen zum Anzeigen. Gleiche Namen bekommen die Nummer dazu, genau wie in der Auswahl der Ctronics-Kamera."""
    names = list(presets.values())
    return {
        number: name if names.count(name) == 1 else f"{name} ({number})" for number, name in sorted(presets.items())
    }


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
