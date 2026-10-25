"""Tasten je Spielpartner: Saison beginnen und Ort vorbereiten, neue Saison beginnen."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SpielSaisonConfigEntry
from .const import DOMAIN
from .entity import PartnerEntity
from .manager import PartnerCfg, SpielSaisonManager


async def async_setup_entry(
    hass: HomeAssistant, entry: SpielSaisonConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    m = entry.runtime_data
    for sid, partner in m.partners.items():
        async_add_entities([PrepareButton(m, partner), NewSeasonButton(m, partner)], config_subentry_id=sid)


class PrepareButton(PartnerEntity, ButtonEntity):
    """Beginnt die Saison des Tages (falls es noch keine gibt) und bereitet den gewählten Ort vor, ohne aufzunehmen."""

    _attr_translation_key = "prepare"

    def __init__(self, m: SpielSaisonManager, partner: PartnerCfg) -> None:
        super().__init__(m, partner, "prepare")

    async def async_press(self) -> None:
        await self.manager.async_prepare(self.sid)


class NewSeasonButton(PartnerEntity, ButtonEntity):
    """Beginnt die nächste Saison: Nummer hoch, Zähler und Auswertung der Saison auf null, neuer Saison-Ordner.

    Geht nur einmal pro Tag, gibt es für heute schon eine Saison, meldet die Taste das.
    """

    _attr_translation_key = "new_season"

    def __init__(self, m: SpielSaisonManager, partner: PartnerCfg) -> None:
        super().__init__(m, partner, "new_season")

    async def async_press(self) -> None:
        if not (await self.manager.async_new_season(self.sid))["started"]:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="season_today",
                translation_placeholders={"partner": self.partner.name},
            )
