"""Diagnose-Download (ohne Schlüssel und ohne Stream-Adressen)."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import SpielSaisonConfigEntry
from .const import CONF_RECORDER_KEY

TO_REDACT = {CONF_RECORDER_KEY}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: SpielSaisonConfigEntry) -> dict[str, Any]:
    m = entry.runtime_data
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "recorder": {
            "online": m.rec_online,
            "version": m.rec_status.get("version"),
            "storage": m.rec_status.get("speicher"),
            "running_sessions": sorted(
                pid for pid, ps in (m.rec_status.get("profile") or {}).items() if ps.get("aufnahme")
            ),
        },
        "status": m.status,
        "message": m.message,
        "partners": [asdict(p) for p in m.partners.values()],
        "places": [asdict(p) for p in m.places.values()],
        "selected_place": m.place_id,
        "counters": {sid: asdict(s) for sid, s in m.states.items()},
        "runs": {sid: {k: v for k, v in run.items() if k != "session"} for sid, run in m.runs.items()},
    }
