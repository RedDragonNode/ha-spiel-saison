"""Basisklassen und Geräte der Entities."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from . import logic
from .const import DOMAIN
from .manager import PartnerCfg, SpielSaisonManager


class SpielSaisonEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, manager: SpielSaisonManager) -> None:
        self.manager = manager

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, self.manager.signal, self._update))

    @callback
    def _update(self) -> None:
        self.async_write_ha_state()


class PartnerEntity(SpielSaisonEntity):
    """Entity am Gerät eines Spielpartners."""

    def __init__(self, manager: SpielSaisonManager, partner: PartnerCfg, key: str) -> None:
        super().__init__(manager)
        self.sid = partner.subentry_id
        self._attr_unique_id = f"{manager.entry.entry_id}_{self.sid}_{key}"
        self._attr_device_info = partner_device(manager, partner)

    @property
    def partner(self) -> PartnerCfg:
        return self.manager.partners[self.sid]


class PreferenceEntity(PartnerEntity):
    """Entity einer Vorliebe am Gerät des Spielpartners. Neue Vorlieben im Profil bekommen sie beim Neuladen."""

    def __init__(self, manager: SpielSaisonManager, partner: PartnerCfg, kind: str, name: str, role: str) -> None:
        super().__init__(manager, partner, preference_unique_part(kind, name, role))
        self.kind = kind
        self.pref_name = name
        self.pref_key = logic.preference_key(kind, name)
        self._attr_translation_placeholders = {"name": name}


def preference_unique_part(kind: str, name: str, role: str) -> str:
    """Teil der eindeutigen ID einer Vorliebe-Entity (info, count oder active)."""
    return f"pref_{kind}_{logic.norm(name)}_{role}"


def hub_device(manager: SpielSaisonManager) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, manager.entry.entry_id)},
        name="Spiel Saison",
        manufacturer="RedDragonNode",
        model="Spiel Saison Recorder",
        entry_type=DeviceEntryType.SERVICE,
    )


def partner_device(manager: SpielSaisonManager, partner: PartnerCfg) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"{manager.entry.entry_id}_{partner.subentry_id}")},
        translation_key="partner",
        translation_placeholders={"name": partner.name},
        manufacturer="RedDragonNode",
        model="Spielpartner",
        via_device_id=manager.hub_device_id,
    )
