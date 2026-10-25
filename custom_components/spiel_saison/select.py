"""Auswahl des Ortes, an dem gespielt wird."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SpielSaisonConfigEntry
from .entity import SpielSaisonEntity, hub_device
from .manager import SpielSaisonManager


async def async_setup_entry(
    hass: HomeAssistant, entry: SpielSaisonConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    m = entry.runtime_data
    if m.places:
        async_add_entities([PlaceSelect(m)])


class PlaceSelect(SpielSaisonEntity, SelectEntity):
    """Der Ort, dessen Schritte Vorbereiten und Aufnahme verwenden (Bett, Schaukel ...)."""

    _attr_translation_key = "place"

    def __init__(self, m: SpielSaisonManager) -> None:
        super().__init__(m)
        self._attr_unique_id = f"{m.entry.entry_id}_place"
        self._attr_device_info = hub_device(m)

    @property
    def options(self) -> list[str]:
        return [p.name for p in self.manager.places.values()]

    @property
    def current_option(self) -> str | None:
        place = self.manager.places.get(self.manager.place_id or "")
        return place.name if place else None

    async def async_select_option(self, option: str) -> None:
        for sid, place in self.manager.places.items():
            if place.name == option:
                await self.manager.async_select_place(sid)
                return
