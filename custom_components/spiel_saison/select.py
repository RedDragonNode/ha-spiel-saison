"""Auswahl des Ortes am Gerät jedes Spielpartners."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SpielSaisonConfigEntry
from .entity import PartnerEntity
from .manager import PartnerCfg, SpielSaisonManager


async def async_setup_entry(
    hass: HomeAssistant, entry: SpielSaisonConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    m = entry.runtime_data
    for sid, partner in m.partners.items():
        if m.places_of(sid):
            async_add_entities([PlaceSelect(m, partner)], config_subentry_id=sid)


class PlaceSelect(PartnerEntity, SelectEntity):
    """Der Ort, dessen Schritte Vorbereiten und Aufnahme verwenden. Zur Wahl stehen nur die Orte des Spielpartners."""

    _attr_translation_key = "place"

    def __init__(self, m: SpielSaisonManager, partner: PartnerCfg) -> None:
        super().__init__(m, partner, "place")

    @property
    def options(self) -> list[str]:
        return [p.name for p in self.manager.places_of(self.sid).values()]

    @property
    def current_option(self) -> str | None:
        place = self.manager.place(self.sid, None)
        return place.name if place else None

    async def async_select_option(self, option: str) -> None:
        for pid, place in self.manager.places_of(self.sid).items():
            if place.name == option:
                await self.manager.async_select_place(self.sid, pid)
                return
