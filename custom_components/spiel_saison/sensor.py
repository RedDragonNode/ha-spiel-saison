"""Sensoren: Status, freier Speicher, aktueller Spielpartner und je Spielpartner die Auswertung."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory, UnitOfInformation, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import SpielSaisonConfigEntry
from .const import COUNTED_KINDS, KIND_LIKE, KIND_TREAT, PHASE_IDLE, PHASE_RECORDING, PHASE_STARTING, PHASE_STOPPING
from .entity import PartnerEntity, PreferenceEntity, SpielSaisonEntity, hub_device
from .manager import STATUS_ERROR, STATUS_IDLE, STATUS_RECORDING, PartnerCfg, PartnerState, SpielSaisonManager


async def async_setup_entry(
    hass: HomeAssistant, entry: SpielSaisonConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    m = entry.runtime_data
    async_add_entities([StatusSensor(m), CurrentPartnerSensor(m), FreeSpaceSensor(m)])
    for sid, partner in m.partners.items():
        async_add_entities(
            [
                PartnerStatusSensor(m, partner),
                DurationSensor(m, partner, "duration_season", "seconds_season"),
                DurationSensor(m, partner, "duration_total", "seconds_total"),
                LongestSensor(m, partner),
                LastRecordingSensor(m, partner),
                FolderSensor(m, partner),
            ],
            config_subentry_id=sid,
        )
        for kind, name, _key in m.preferences(partner):
            sensors: list[SensorEntity] = [PreferenceInfoSensor(m, partner, kind, name)]
            if kind in COUNTED_KINDS:
                sensors.append(PreferenceCountSensor(m, partner, kind, name))
            async_add_entities(sensors, config_subentry_id=sid)


class StatusSensor(SpielSaisonEntity, SensorEntity):
    """idle, recording oder error. Die letzte Meldung steht im Attribut `message`."""

    _attr_translation_key = "status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [STATUS_IDLE, STATUS_RECORDING, STATUS_ERROR]  # noqa: RUF012
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, m: SpielSaisonManager) -> None:
        super().__init__(m)
        self._attr_unique_id = f"{m.entry.entry_id}_status"
        self._attr_device_info = hub_device(m)

    @property
    def native_value(self) -> str:
        return self.manager.status

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"message": self.manager.message, "time": self.manager.message_time}


class CurrentPartnerSensor(SpielSaisonEntity, SensorEntity):
    """Bei wem läuft gerade eine Aufnahme? Mehrere Namen stehen durch Komma getrennt, sonst ist der Zustand leer."""

    _attr_translation_key = "current_partner"
    _attr_icon = "mdi:account-voice"

    def __init__(self, m: SpielSaisonManager) -> None:
        super().__init__(m)
        self._attr_unique_id = f"{m.entry.entry_id}_current_partner"
        self._attr_device_info = hub_device(m)

    @property
    def native_value(self) -> str | None:
        return ", ".join(self.manager.recording_partners) or None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"partners": self.manager.recording_partners}


class FreeSpaceSensor(SpielSaisonEntity, SensorEntity):
    """Freier Speicher am Aufnahme-Ort der App."""

    _attr_translation_key = "recorder_free_space"
    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_native_unit_of_measurement = UnitOfInformation.GIGABYTES
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, m: SpielSaisonManager) -> None:
        super().__init__(m)
        self._attr_unique_id = f"{m.entry.entry_id}_recorder_free_space"
        self._attr_device_info = hub_device(m)

    @property
    def available(self) -> bool:
        return bool(self.manager.rec_online)

    @property
    def native_value(self) -> float | None:
        return (self.manager.rec_status.get("speicher") or {}).get("frei_gb")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        storage = self.manager.rec_status.get("speicher") or {}
        return {"total_gb": storage.get("gesamt_gb"), "folder": self.manager.rec_status.get("wurzel")}


class PartnerStatusSensor(PartnerEntity, SensorEntity):
    """idle, starting, recording oder stopping."""

    _attr_translation_key = "partner_status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [PHASE_IDLE, PHASE_STARTING, PHASE_RECORDING, PHASE_STOPPING]  # noqa: RUF012

    def __init__(self, m: SpielSaisonManager, partner: PartnerCfg) -> None:
        super().__init__(m, partner, "status")

    @property
    def native_value(self) -> str:
        return self.manager.phase(self.sid)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        run = self.manager.runs.get(self.sid) or {}
        place = self.manager.places.get(run.get("place") or "")
        return {
            "place": place.name if place else None,
            "since": run.get("start") if run.get("phase") == PHASE_RECORDING else None,
            "recording": run.get("no"),
            "cams": run.get("cams") or [],
            "nickname": self.partner.nickname or None,
            "likes": list(self.partner.likes),
            "treats": list(self.partner.treats),
        }


class _StateSensor(PartnerEntity, SensorEntity):
    @property
    def _state(self) -> PartnerState:
        return self.manager.states[self.sid]


class DurationSensor(_StateSensor):
    """Gesamte Aufnahmedauer in der Saison oder insgesamt, in Stunden."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(self, m: SpielSaisonManager, partner: PartnerCfg, key: str, attr: str) -> None:
        super().__init__(m, partner, key)
        self._attr_translation_key = key
        self._attr = attr

    @property
    def native_value(self) -> float:
        return round(getattr(self._state, self._attr) / 3600, 4)


class LongestSensor(_StateSensor):
    """Längste Aufnahme der Saison, in Minuten."""

    _attr_translation_key = "longest_season"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def __init__(self, m: SpielSaisonManager, partner: PartnerCfg) -> None:
        super().__init__(m, partner, "longest_season")

    @property
    def native_value(self) -> float:
        return round(self._state.longest_season / 60, 2)


class LastRecordingSensor(_StateSensor):
    """Wann die letzte Aufnahme begann. Ihre Dauer steht im Attribut `duration_min`."""

    _attr_translation_key = "last_recording"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, m: SpielSaisonManager, partner: PartnerCfg) -> None:
        super().__init__(m, partner, "last_recording")

    @property
    def native_value(self) -> datetime | None:
        return dt_util.parse_datetime(self._state.last_start) if self._state.last_start else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        seconds = self._state.last_seconds
        return {"duration_min": round(seconds / 60, 1) if seconds is not None else None}


class PreferenceInfoSensor(PreferenceEntity, SensorEntity):
    """Eine Vorliebe für sich, unter „Diagnose“ am Gerät. Der Zustand sagt, um welche Art es geht."""

    _attr_translation_key = "preference"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [KIND_LIKE, KIND_TREAT]  # noqa: RUF012
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:heart-outline"

    def __init__(self, m: SpielSaisonManager, partner: PartnerCfg, kind: str, name: str) -> None:
        super().__init__(m, partner, kind, name, "info")
        self._attr_name = name

    @property
    def native_value(self) -> str:
        return self.kind

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.kind not in COUNTED_KINDS:
            return {}
        return {"active": self.pref_key in self.manager.states[self.sid].pref_on}


class PreferenceCountSensor(PreferenceEntity, SensorEntity):
    """Bei wie vielen Aufnahmen die Vorliebe „dabei“ war (der Schalter dazu war beim Start eingeschaltet)."""

    _attr_translation_key = "preference_count"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:counter"

    def __init__(self, m: SpielSaisonManager, partner: PartnerCfg, kind: str, name: str) -> None:
        super().__init__(m, partner, kind, name, "count")

    @property
    def native_value(self) -> int:
        return self.manager.states[self.sid].pref_counts.get(self.pref_key, 0)


class FolderSensor(PartnerEntity, SensorEntity):
    """Wohin die laufende oder nächste Aufnahme geschrieben wird. Der volle Pfad in der App steht im Attribut `path`."""

    _attr_translation_key = "folder"
    _attr_icon = "mdi:folder-multiple"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, m: SpielSaisonManager, partner: PartnerCfg) -> None:
        super().__init__(m, partner, "folder")

    @property
    def native_value(self) -> str | None:
        text = "/".join(self.manager.preview_folder(self.sid))
        return text[:255] or None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        parts = self.manager.preview_folder(self.sid)
        root = self.manager.rec_status.get("wurzel")
        return {"path": f"{root.rstrip('/')}/{'/'.join(parts)}" if root and parts else None, "parts": parts}
