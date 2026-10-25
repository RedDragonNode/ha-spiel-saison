"""Binärsensoren: App erreichbar und läuft gerade eine Aufnahme."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SpielSaisonConfigEntry
from .entity import SpielSaisonEntity, hub_device
from .manager import SpielSaisonManager


async def async_setup_entry(
    hass: HomeAssistant, entry: SpielSaisonConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    m = entry.runtime_data
    async_add_entities([RecorderOnlineSensor(m), RecordingSensor(m)])


class RecorderOnlineSensor(SpielSaisonEntity, BinarySensorEntity):
    _attr_translation_key = "recorder_online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, m: SpielSaisonManager) -> None:
        super().__init__(m)
        self._attr_unique_id = f"{m.entry.entry_id}_recorder_online"
        self._attr_device_info = hub_device(m)

    @property
    def is_on(self) -> bool:
        return bool(self.manager.rec_online)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"version": self.manager.rec_status.get("version")}


class RecordingSensor(SpielSaisonEntity, BinarySensorEntity):
    """Nimmt gerade irgendein Spielpartner auf?"""

    _attr_translation_key = "recording"
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, m: SpielSaisonManager) -> None:
        super().__init__(m)
        self._attr_unique_id = f"{m.entry.entry_id}_recording"
        self._attr_device_info = hub_device(m)

    @property
    def is_on(self) -> bool:
        return bool(self.manager.recording_partners)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"partners": self.manager.recording_partners}
