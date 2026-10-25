"""Tasten je Spielpartner: Saison vorbereiten (mit Saison-Ordner) und Saison beenden."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
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
        async_add_entities([PrepareButton(m, partner), EndSeasonButton(m, partner)], config_subentry_id=sid)


class PrepareButton(PartnerEntity, ButtonEntity):
    """Beginnt die Saison des Tages (falls es noch keine gibt), legt ihren Ordner an und bereitet den Ort vor."""

    _attr_translation_key = "prepare"

    def __init__(self, m: SpielSaisonManager, partner: PartnerCfg) -> None:
        super().__init__(m, partner, "prepare")

    async def async_press(self) -> None:
        await self.manager.async_prepare(self.sid)


class EndSeasonButton(PartnerEntity, ButtonEntity):
    """Beendet die Saison: führt den Beenden-Schritt des gewählten Ortes aus (nicht während einer Aufnahme)."""

    _attr_translation_key = "end_season"

    def __init__(self, m: SpielSaisonManager, partner: PartnerCfg) -> None:
        super().__init__(m, partner, "end_season")

    async def async_press(self) -> None:
        await self.manager.async_end_season(self.sid)
