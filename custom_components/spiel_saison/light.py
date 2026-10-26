"""Lichter am Gerät des Spielpartners: je Licht, das er einstellen darf, ein Regler, der die echte Lampe steuert."""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from . import SpielSaisonConfigEntry
from .cams import entity_title
from .const import LIGHT_COLOR_DELAY_S, MAX_KELVIN, MIN_KELVIN
from .entity import PartnerEntity, partner_light_unique_part
from .manager import PartnerCfg, SpielSaisonManager

_COLOR_KEYS = ("hs_color", "xy_color", "rgb_color", "rgbw_color", "rgbww_color", "color_temp_kelvin")
_BRIGHTNESS_KEYS = ("brightness", "white")
_MODES = {mode.value: mode for mode in ColorMode}


async def async_setup_entry(
    hass: HomeAssistant, entry: SpielSaisonConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    m = entry.runtime_data
    for sid, partner in m.partners.items():
        if partner.lights:
            async_add_entities(
                [PartnerLight(m, partner, target) for target in dict.fromkeys(partner.lights)], config_subentry_id=sid
            )


class PartnerLight(PartnerEntity, LightEntity):
    """Ein Licht, das der Spielpartner einstellen darf. Zustand und Fähigkeiten stammen von der echten Lampe."""

    _attr_translation_key = "partner_light"

    def __init__(self, m: SpielSaisonManager, partner: PartnerCfg, target: str) -> None:
        super().__init__(m, partner, partner_light_unique_part(target))
        self.target = target
        self._attr_translation_placeholders = {"name": entity_title(m.hass, target)}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(async_track_state_change_event(self.hass, [self.target], self._source_changed))

    @callback
    def _source_changed(self, _event: Event) -> None:
        self.async_write_ha_state()

    @property
    def _source(self) -> State | None:
        return self.hass.states.get(self.target)

    def _attribute(self, name: str) -> Any:
        source = self._source
        return source.attributes.get(name) if source else None

    @property
    def available(self) -> bool:
        source = self._source
        return source is not None and source.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)

    @property
    def is_on(self) -> bool:
        source = self._source
        return source is not None and source.state == STATE_ON

    @property
    def supported_color_modes(self) -> set[ColorMode]:
        modes = {_MODES[mode] for mode in self._attribute("supported_color_modes") or [] if mode in _MODES}
        return modes or {ColorMode.ONOFF}

    @property
    def color_mode(self) -> ColorMode:
        """Der Modus der Lampe. Ausgeschaltet meldet sie keinen, dann gilt der erste unterstützte."""
        mode = _MODES.get(self._attribute("color_mode") or "")
        supported = self.supported_color_modes
        return mode if mode in supported else min(supported, key=lambda item: item.value)

    @property
    def brightness(self) -> int | None:
        return self._attribute("brightness")

    @property
    def hs_color(self) -> tuple[float, float] | None:
        return self._attribute("hs_color")

    @property
    def xy_color(self) -> tuple[float, float] | None:
        return self._attribute("xy_color")

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        return self._attribute("rgb_color")

    @property
    def rgbw_color(self) -> tuple[int, int, int, int] | None:
        return self._attribute("rgbw_color")

    @property
    def rgbww_color(self) -> tuple[int, int, int, int, int] | None:
        return self._attribute("rgbww_color")

    @property
    def color_temp_kelvin(self) -> int | None:
        return self._attribute("color_temp_kelvin")

    @property
    def min_color_temp_kelvin(self) -> int:
        return int(self._attribute("min_color_temp_kelvin") or MIN_KELVIN)

    @property
    def max_color_temp_kelvin(self) -> int:
        return int(self._attribute("max_color_temp_kelvin") or MAX_KELVIN)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Die echte Lampe einschalten oder einstellen.

        Kommen Helligkeit und Farbe zugleich, gehen sie nacheinander an die Lampe: erst die Helligkeit, nach einer
        kurzen Pause die Farbe. Gemeinsam übernehmen manche Lampen (zum Beispiel Matter-Lampen) die Farbe nicht.
        """
        target = {"entity_id": self.target}
        brightness = {key: kwargs[key] for key in _BRIGHTNESS_KEYS if key in kwargs}
        colour = {key: kwargs[key] for key in _COLOR_KEYS if key in kwargs}
        if brightness or not colour:  # nur eine Farbe geht allein an die Lampe, ohne vorher einzuschalten
            await self._call("turn_on", {**target, **brightness})
            if colour:
                await asyncio.sleep(LIGHT_COLOR_DELAY_S)
        if colour:
            await self._call("turn_on", {**target, **colour})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._call("turn_off", {"entity_id": self.target})

    async def _call(self, service: str, data: dict[str, Any]) -> None:
        await self.hass.services.async_call("light", service, data, blocking=True, context=self._context)
