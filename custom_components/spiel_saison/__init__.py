"""Spiel Saison: Saisons, Spielpartner und Aufnahmen mit Kameras, aufgenommen von der App „Spiel Saison Recorder“."""

from __future__ import annotations

import logging
from types import MappingProxyType
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import CONF_NAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import ConfigType

from . import logic
from .cams import is_ctronics
from .const import (
    ATTR_PARTNER,
    ATTR_PLACE,
    CONF_ANNOUNCE_END,
    CONF_ANNOUNCE_ENTITY,
    CONF_ANNOUNCE_START,
    CONF_CAM_PRESETS,
    CONF_CAMS,
    CONF_COVER,
    CONF_COVER_POSITION,
    CONF_FILE_PATTERN,
    CONF_FINISH_COVER,
    CONF_FINISH_COVER_POSITION,
    CONF_FINISH_OFF,
    CONF_FINISH_ON,
    CONF_LIKE_CATALOG,
    CONF_LIKES,
    CONF_PARTNER,
    CONF_PREPARE_ON,
    CONF_PTZ_PRESET,
    CONF_TREAT_CATALOG,
    CONF_TREATS,
    COUNTED_KINDS,
    DOMAIN,
    OLD_CAM_NUMBER_PART,
    OPT_FINISH,
    OPT_PREPARE,
    SERVICE_END_SEASON,
    SERVICE_NEW_SEASON,
    SERVICE_PREPARE,
    SERVICE_SAVE_LIGHTING,
    SERVICE_START,
    SERVICE_STOP,
    STEP_COVER,
    STEP_COVER_POSITION,
    STEP_OFF,
    STEP_ON,
    SUBENTRY_LIKE,
    SUBENTRY_PARTNER,
    SUBENTRY_PLACE,
    SUBENTRY_TREAT,
)
from .entity import preference_unique_part
from .manager import SpielSaisonManager, counters_store

_LOGGER = logging.getLogger(__name__)

_ANNOUNCE_KEYS = (CONF_ANNOUNCE_ENTITY, CONF_ANNOUNCE_START, CONF_ANNOUNCE_END)

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

    async def end_season(call: ServiceCall) -> dict:
        return await _manager(hass).async_end_season(call.data[ATTR_PARTNER])

    async def save_lighting(call: ServiceCall) -> dict:
        return await _manager(hass).async_save_lighting(call.data[ATTR_PARTNER])

    with_place = vol.Schema({vol.Required(ATTR_PARTNER): cv.string, vol.Optional(ATTR_PLACE): cv.string})
    partner_only = vol.Schema({vol.Required(ATTR_PARTNER): cv.string})
    for name, handler, schema in (
        (SERVICE_START, start, with_place),
        (SERVICE_STOP, stop, partner_only),
        (SERVICE_PREPARE, prepare, with_place),
        (SERVICE_NEW_SEASON, new_season, partner_only),
        (SERVICE_END_SEASON, end_season, partner_only),
        (SERVICE_SAVE_LIGHTING, save_lighting, partner_only),
    ):
        hass.services.async_register(DOMAIN, name, handler, schema, supports_response=SupportsResponse.OPTIONAL)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SpielSaisonConfigEntry) -> bool:
    manager = SpielSaisonManager(hass, entry)
    await manager.async_load()
    entry.runtime_data = manager
    _drop_stale_entities(hass, entry, manager)
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
    """Gespeicherte Einträge auf den heutigen Aufbau umstellen."""
    if entry.version > 1:
        return False
    if entry.minor_version < 2:
        # 1.2: Im Dateinamen steht bei „Cam ID“ der Name der Kamera statt ihrer Nummer
        data = dict(entry.data)
        pattern = data.get(CONF_FILE_PATTERN)
        if isinstance(pattern, str) and OLD_CAM_NUMBER_PART in pattern:
            data[CONF_FILE_PATTERN] = pattern.replace(OLD_CAM_NUMBER_PART, "Cam ID.{cam}")
        hass.config_entries.async_update_entry(entry, data=data, minor_version=2)
    if entry.minor_version < 3:
        # 1.3: Kameras gehören zum Ort, die Ansage zum Spielpartner, und jeder Ort gehört zu einem Spielpartner
        _move_cams_to_places(hass, entry)
        if stale := er.async_get(hass).async_get_entity_id("select", DOMAIN, f"{entry.entry_id}_place"):
            er.async_get(hass).async_remove(stale)  # die Ortsauswahl hängt jetzt am Gerät jedes Spielpartners
        hass.config_entries.async_update_entry(entry, minor_version=3)
    if entry.minor_version < 4:
        # 1.4: Vorlieben sind keine eigenen Einträge mehr, die Spielpartner tragen die Namen selbst
        _preferences_to_names(hass, entry)
        hass.config_entries.async_update_entry(entry, minor_version=4)
    if entry.minor_version < 5:
        # 1.5: „Saison vorbereiten“ und „Saison beenden“ gelten für alle und stehen bei den Optionen des Eintrags
        _season_settings_to_options(hass, entry)
    return True


_LEGACY_STEPS = {
    OPT_PREPARE: {CONF_PREPARE_ON: STEP_ON, CONF_COVER: STEP_COVER, CONF_COVER_POSITION: STEP_COVER_POSITION},
    OPT_FINISH: {
        CONF_FINISH_OFF: STEP_OFF,
        CONF_FINISH_ON: STEP_ON,
        CONF_FINISH_COVER: STEP_COVER,
        CONF_FINISH_COVER_POSITION: STEP_COVER_POSITION,
    },
}


def _season_settings_to_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Die Einstellungen der Tasten aus den Orten (0.6.x) wandern in die Optionen, die des ersten Ortes gelten."""
    places = [s for s in entry.subentries.values() if s.subentry_type == SUBENTRY_PLACE]
    options = dict(entry.options)
    for section, legacy in _LEGACY_STEPS.items():
        found = []
        for place in places:
            step = {new: place.data[old] for old, new in legacy.items() if place.data.get(old)}
            if STEP_COVER not in step:
                step.pop(STEP_COVER_POSITION, None)  # eine Position ohne Rollo oder Winde gilt für nichts
            if step:
                found.append(step)
        if found and section not in options:
            options[section] = found[0]
        if any(step != found[0] for step in found[1:]):
            _LOGGER.warning("Die Orte hatten verschiedene Einstellungen für %s, es gelten die des ersten", section)
        for place in places:
            data = {key: value for key, value in place.data.items() if key not in legacy}
            if data != dict(place.data):
                hass.config_entries.async_update_subentry(entry, place, data=data)
    hass.config_entries.async_update_entry(entry, options=options, minor_version=5)


def _preferences_to_names(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Die Vorlieben-Einträge aus 0.3.x werden zu Namen bei den Spielpartnern und in der Liste für neue Spielpartner."""
    subentries = list(entry.subentries.values())
    names = {
        kind: {s.subentry_id: s.data[CONF_NAME] for s in subentries if s.subentry_type == kind}
        for kind in (SUBENTRY_LIKE, SUBENTRY_TREAT)
    }
    for partner in (s for s in subentries if s.subentry_type == SUBENTRY_PARTNER):
        data = dict(partner.data)
        for kind, key in ((SUBENTRY_LIKE, CONF_LIKES), (SUBENTRY_TREAT, CONF_TREATS)):
            data[key] = logic.clean_names([names[kind][i] for i in data.get(key) or [] if i in names[kind]])
        if data != dict(partner.data):
            hass.config_entries.async_update_subentry(entry, partner, data=data)
    catalogs = {
        key: logic.clean_names([*(entry.data.get(key) or []), *names[kind].values()])
        for kind, key in ((SUBENTRY_LIKE, CONF_LIKE_CATALOG), (SUBENTRY_TREAT, CONF_TREAT_CATALOG))
    }
    if any(catalogs.values()):
        hass.config_entries.async_update_entry(entry, data={**entry.data, **catalogs})
    for kind in names:
        for sid in names[kind]:
            hass.config_entries.async_remove_subentry(entry, sid)


def _move_cams_to_places(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Aus Orten ohne Spielpartner werden Orte je Spielpartner, mit dessen bisherigen Kameras und der alten Position."""
    subentries = list(entry.subentries.values())
    partners = [s for s in subentries if s.subentry_type == SUBENTRY_PARTNER]
    old_places = [s for s in subentries if s.subentry_type == SUBENTRY_PLACE and CONF_PARTNER not in s.data]
    cams_of = {s.subentry_id: list(s.data.get(CONF_CAMS) or []) for s in partners}
    announce: dict[str, Any] = next(
        (
            {key: place.data[key] for key in _ANNOUNCE_KEYS if place.data.get(key)}
            for place in old_places
            if any(place.data.get(key) for key in _ANNOUNCE_KEYS)
        ),
        {},
    )
    for partner in partners:
        data = {key: value for key, value in partner.data.items() if key != CONF_CAMS}
        for key in _ANNOUNCE_KEYS:
            if announce.get(key):
                data.setdefault(key, announce[key])
            elif key != CONF_ANNOUNCE_ENTITY:  # wie im Dialog: Texte immer, die Entity nur wenn gesetzt
                data.setdefault(key, "")
        if data != dict(partner.data):
            hass.config_entries.async_update_subentry(entry, partner, data=data)
    for place in old_places:
        preset = int(place.data.get(CONF_PTZ_PRESET) or 0)
        for index, partner in enumerate(partners):
            cams = cams_of[partner.subentry_id]
            data = {key: value for key, value in place.data.items() if key not in (CONF_PTZ_PRESET, *_ANNOUNCE_KEYS)}
            data[CONF_PARTNER] = partner.subentry_id
            data[CONF_CAMS] = cams
            data[CONF_CAM_PRESETS] = {eid: preset for eid in cams if preset and is_ctronics(hass, eid)}
            title = logic.place_title(data[CONF_NAME], partner.data[CONF_NAME])
            unique_id = f"{partner.subentry_id}:{logic.norm(data[CONF_NAME])}"
            if index == 0:  # der erste Spielpartner behält den vorhandenen Ort samt Auswahl
                hass.config_entries.async_update_subentry(entry, place, data=data, title=title, unique_id=unique_id)
            else:
                hass.config_entries.async_add_subentry(
                    entry,
                    ConfigSubentry(
                        data=MappingProxyType(data), subentry_type=SUBENTRY_PLACE, title=title, unique_id=unique_id
                    ),
                )


def _drop_stale_entities(hass: HomeAssistant, entry: ConfigEntry, manager: SpielSaisonManager) -> None:
    """Entities gestrichener Vorlieben und der entfernten Taste „Neue Saison“ aus der Registry nehmen."""
    wanted = {
        f"{entry.entry_id}_{sid}_{preference_unique_part(kind, name, role)}"
        for sid, partner in manager.partners.items()
        for kind, name, _key in manager.preferences(partner)
        for role in (("info", "count", "active") if kind in COUNTED_KINDS else ("info",))
    }
    registry = er.async_get(hass)
    for ent in er.async_entries_for_config_entry(registry, entry.entry_id):
        stale_preference = "_pref_" in ent.unique_id and ent.unique_id not in wanted
        if stale_preference or ent.unique_id.endswith("_new_season"):  # die Taste „Neue Saison“ gibt es nicht mehr
            registry.async_remove(ent.entity_id)


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
