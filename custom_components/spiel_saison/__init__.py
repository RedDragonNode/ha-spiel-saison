"""Spiel Saison: Saisons, Spielpartner und Aufnahmen mit Kameras, aufgenommen von der App „Spiel Saison Recorder“."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_PARTNER,
    ATTR_PLACE,
    CONF_FILE_PATTERN,
    DOMAIN,
    OLD_CAM_NUMBER_PART,
    SERVICE_NEW_SEASON,
    SERVICE_PREPARE,
    SERVICE_START,
    SERVICE_STOP,
)
from .manager import SpielSaisonManager, counters_store

PLATFORMS = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.BUTTON,
]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type SpielSaisonConfigEntry = ConfigEntry[SpielSaisonManager]


def _manager(hass: HomeAssistant) -> SpielSaisonManager:
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        return entry.runtime_data
    raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_loaded")


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async def start(call: ServiceCall) -> dict:
        return await _manager(hass).async_start_recording(call.data[ATTR_PARTNER], call.data.get(ATTR_PLACE))

    async def stop(call: ServiceCall) -> dict:
        return await _manager(hass).async_stop_recording(call.data[ATTR_PARTNER])

    async def prepare(call: ServiceCall) -> dict:
        return await _manager(hass).async_prepare(call.data[ATTR_PARTNER], call.data.get(ATTR_PLACE))

    async def new_season(call: ServiceCall) -> dict:
        return await _manager(hass).async_new_season(call.data[ATTR_PARTNER])

    with_place = vol.Schema({vol.Required(ATTR_PARTNER): cv.string, vol.Optional(ATTR_PLACE): cv.string})
    partner_only = vol.Schema({vol.Required(ATTR_PARTNER): cv.string})
    for name, handler, schema in (
        (SERVICE_START, start, with_place),
        (SERVICE_STOP, stop, partner_only),
        (SERVICE_PREPARE, prepare, with_place),
        (SERVICE_NEW_SEASON, new_season, partner_only),
    ):
        hass.services.async_register(DOMAIN, name, handler, schema, supports_response=SupportsResponse.OPTIONAL)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SpielSaisonConfigEntry) -> bool:
    manager = SpielSaisonManager(hass, entry)
    await manager.async_load()
    entry.runtime_data = manager
    manager.hub_device_id = (
        dr.async_get(hass)
        .async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, entry.entry_id)},
            name="Spiel Saison",
            manufacturer="RedDragonNode",
            model="Spiel Saison Recorder",
            entry_type=dr.DeviceEntryType.SERVICE,
        )
        .id
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    manager.async_start()
    entry.async_on_unload(entry.add_update_listener(_reload_on_change))
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """1.1 -> 1.2: Im Dateinamen steht bei „Cam ID“ der Name der Kamera statt ihrer Nummer."""
    if entry.version > 1:
        return False
    if entry.minor_version < 2:
        data = dict(entry.data)
        pattern = data.get(CONF_FILE_PATTERN)
        if isinstance(pattern, str) and OLD_CAM_NUMBER_PART in pattern:
            data[CONF_FILE_PATTERN] = pattern.replace(OLD_CAM_NUMBER_PART, "Cam ID.{cam}")
        hass.config_entries.async_update_entry(entry, data=data, minor_version=2)
    return True


async def _reload_on_change(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Spielpartner, Orte oder Verbindung wurden geändert -> neu laden."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: SpielSaisonConfigEntry) -> bool:
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_stop()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Beim Löschen der Integration auch die gespeicherten Zähler entfernen."""
    await counters_store(hass, entry.entry_id).async_remove()
