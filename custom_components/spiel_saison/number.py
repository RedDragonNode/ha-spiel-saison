"""Zähler je Spielpartner: Saison, Aufnahmen in der Saison und insgesamt. Von Hand änderbar."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SpielSaisonConfigEntry
from .entity import PartnerEntity
from .manager import PartnerCfg, SpielSaisonManager


@dataclass(frozen=True)
class CounterSpec:
    counter: str  # Feld von PartnerState
    key: str
    minimum: int
    maximum: int


SPECS = (
    CounterSpec("season", "season", 1, 99),
    CounterSpec("count_season", "recordings_season", 0, 999),
    CounterSpec("count_total", "recordings_total", 0, 9999),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: SpielSaisonConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    m = entry.runtime_data
    for sid, partner in m.partners.items():
        async_add_entities([CounterNumber(m, partner, spec) for spec in SPECS], config_subentry_id=sid)


class CounterNumber(PartnerEntity, NumberEntity):
    _attr_mode = NumberMode.BOX
    _attr_native_step = 1

    def __init__(self, m: SpielSaisonManager, partner: PartnerCfg, spec: CounterSpec) -> None:
        super().__init__(m, partner, spec.key)
        self.spec = spec
        self._attr_translation_key = spec.key
        self._attr_native_min_value = spec.minimum
        self._attr_native_max_value = spec.maximum

    @property
    def native_value(self) -> int:
        return getattr(self.manager.states[self.sid], self.spec.counter)

    async def async_set_native_value(self, value: float) -> None:
        await self.manager.async_set_counter(self.sid, self.spec.counter, int(value))
