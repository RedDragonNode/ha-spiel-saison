"""Schalter: Aufnahme eines Spielpartners starten und beenden."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SpielSaisonConfigEntry
from .const import PHASE_RECORDING, PHASE_STARTING
from .entity import PartnerEntity
from .manager import PartnerCfg, SpielSaisonManager


async def async_setup_entry(
    hass: HomeAssistant, entry: SpielSaisonConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    m = entry.runtime_data
    for sid, partner in m.partners.items():
        async_add_entities([RecordingSwitch(m, partner)], config_subentry_id=sid)


class RecordingSwitch(PartnerEntity, SwitchEntity):
    """An: Beginn-Schritt des gewählten Ortes, Vorlauf, dann Aufnahme. Aus: Aufnahme beenden, dann Ende-Schritt."""

    _attr_translation_key = "recording"

    def __init__(self, m: SpielSaisonManager, partner: PartnerCfg) -> None:
        super().__init__(m, partner, "recording")

    @property
    def is_on(self) -> bool:
        return self.manager.phase(self.sid) in (PHASE_STARTING, PHASE_RECORDING)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        run = self.manager.runs.get(self.sid) or {}
        place = self.manager.places.get(run.get("place") or "")
        return {"phase": self.manager.phase(self.sid), "place": place.name if place else None}

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.manager.async_start_recording(self.sid)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.manager.async_stop_recording(self.sid)
