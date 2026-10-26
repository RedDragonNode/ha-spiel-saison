"""Lichter am Gerät des Spielpartners: Regler, die Zustand und Befehle der echten Lampe weitergeben."""

from __future__ import annotations

from typing import Any

from homeassistant.const import EVENT_CALL_SERVICE, STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.spiel_saison.cams import entity_title, own_lights
from custom_components.spiel_saison.const import DOMAIN

from .conftest import make_partner, partner_entity, setup_entry

FRANK = "p_Frank"
LAMP = {
    "friendly_name": "Stehlampe",
    "supported_color_modes": ["color_temp", "hs"],
    "color_mode": "hs",
    "brightness": 128,
    "hs_color": (120.0, 100.0),
    "color_temp_kelvin": None,
    "min_color_temp_kelvin": 2000,
    "max_color_temp_kelvin": 6500,
}


async def _setup(hass: HomeAssistant, lights: list[str] | None = None) -> MockConfigEntry:
    """Frank darf die Lichter einstellen (`light.a` und `light.b`), Tobias keine."""
    hass.states.async_set("light.a", STATE_ON, LAMP)
    hass.states.async_set("light.b", STATE_OFF, {"friendly_name": "Deckenlampe", "supported_color_modes": ["onoff"]})
    return await setup_entry(
        hass,
        [
            make_partner("Frank", 2, lights=["light.a", "light.b"] if lights is None else lights),
            make_partner("Tobias", 3),
        ],
    )


async def _setup_with_slider(hass: HomeAssistant) -> tuple[MockConfigEntry, str]:
    """Wie `_setup`, dazu die Entity-ID des Reglers für `light.a`."""
    entry = await _setup(hass)
    return entry, partner_entity(hass, entry, FRANK, "light", "plight_light_a")


def _forwarded(events: list[Event], lamp: str) -> list[tuple[str, dict[str, Any]]]:
    """Die Befehle, die an die echte Lampe gingen (nicht die an den Regler)."""
    return [
        (event.data["service"], dict(event.data["service_data"]))
        for event in events
        if event.data["domain"] == "light" and event.data["service_data"].get("entity_id") == lamp
    ]


async def test_a_slider_per_chosen_light_hangs_on_the_partner_device(hass: HomeAssistant) -> None:
    entry, slider = await _setup_with_slider(hass)
    registry = er.async_get(hass)

    own = [e for e in er.async_entries_for_config_entry(registry, entry.entry_id) if e.domain == "light"]
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, f"{entry.entry_id}_{FRANK}"), entry.entry_id)

    assert {e.unique_id.removeprefix(f"{entry.entry_id}_{FRANK}_") for e in own} == {"plight_light_a", "plight_light_b"}
    assert device and {e.device_id for e in own} == {device.id}  # Tobias hat keine Lichter, es gibt nur diese beiden
    assert hass.states.get(slider).attributes["friendly_name"] == "Play partner Frank Stehlampe"
    assert set(own_lights(hass, entry.entry_id)) == {e.entity_id for e in own}


async def test_the_slider_shows_what_the_real_light_does(hass: HomeAssistant) -> None:
    _entry, slider = await _setup_with_slider(hass)

    state = hass.states.get(slider)
    assert state and state.state == STATE_ON
    assert state.attributes["brightness"] == 128
    assert state.attributes["color_mode"] == "hs"
    assert state.attributes["hs_color"] == (120.0, 100.0)
    assert state.attributes["supported_color_modes"] == ["color_temp", "hs"]

    hass.states.async_set("light.a", STATE_OFF, {**LAMP, "brightness": None, "color_mode": None, "hs_color": None})
    await hass.async_block_till_done()
    assert hass.states.get(slider).state == STATE_OFF

    hass.states.async_set("light.a", STATE_UNAVAILABLE)
    await hass.async_block_till_done()
    assert hass.states.get(slider).state == STATE_UNAVAILABLE


async def test_a_light_that_only_switches_gets_an_on_off_slider(hass: HomeAssistant) -> None:
    entry, _slider = await _setup_with_slider(hass)

    state = hass.states.get(partner_entity(hass, entry, FRANK, "light", "plight_light_b"))

    assert state and state.state == STATE_OFF
    assert state.attributes["supported_color_modes"] == ["onoff"]


async def test_turning_the_slider_on_and_off_switches_the_real_light(hass: HomeAssistant) -> None:
    _entry, slider = await _setup_with_slider(hass)
    events = async_capture_events(hass, EVENT_CALL_SERVICE)

    await hass.services.async_call("light", "turn_off", {"entity_id": slider}, blocking=True)
    await hass.services.async_call("light", "turn_on", {"entity_id": slider}, blocking=True)

    assert _forwarded(events, "light.a") == [
        ("turn_off", {"entity_id": "light.a"}),
        ("turn_on", {"entity_id": "light.a"}),
    ]


async def test_brightness_and_colour_go_to_the_real_light_one_after_the_other(hass: HomeAssistant) -> None:
    _entry, slider = await _setup_with_slider(hass)
    events = async_capture_events(hass, EVENT_CALL_SERVICE)

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": slider, "brightness": 77, "hs_color": [0, 100]}, blocking=True
    )

    brightness, colour = _forwarded(events, "light.a")
    assert brightness == ("turn_on", {"entity_id": "light.a", "brightness": 77})
    assert colour[0] == "turn_on" and set(colour[1]) == {"entity_id", "hs_color"}
    assert tuple(colour[1]["hs_color"]) == (0.0, 100.0)


async def test_a_colour_alone_is_sent_without_switching_the_light_on_first(hass: HomeAssistant) -> None:
    _entry, slider = await _setup_with_slider(hass)
    events = async_capture_events(hass, EVENT_CALL_SERVICE)

    await hass.services.async_call("light", "turn_on", {"entity_id": slider, "color_temp_kelvin": 3000}, blocking=True)

    assert _forwarded(events, "light.a") == [("turn_on", {"entity_id": "light.a", "color_temp_kelvin": 3000})]


async def test_a_brightness_alone_is_one_command(hass: HomeAssistant) -> None:
    _entry, slider = await _setup_with_slider(hass)
    events = async_capture_events(hass, EVENT_CALL_SERVICE)

    await hass.services.async_call("light", "turn_on", {"entity_id": slider, "brightness_pct": 40}, blocking=True)

    assert _forwarded(events, "light.a") == [("turn_on", {"entity_id": "light.a", "brightness": 102})]


async def test_partners_without_lights_get_no_sliders(hass: HomeAssistant) -> None:
    entry = await _setup(hass, lights=[])

    assert own_lights(hass, entry.entry_id) == []


async def test_a_light_that_is_no_longer_chosen_loses_its_slider(hass: HomeAssistant) -> None:
    entry, slider = await _setup_with_slider(hass)
    other = partner_entity(hass, entry, FRANK, "light", "plight_light_b")
    sub = entry.subentries[FRANK]

    hass.config_entries.async_update_subentry(entry, sub, data={**sub.data, "lights": ["light.a"]})
    await hass.async_block_till_done()

    assert hass.states.get(slider) is not None
    assert hass.states.get(other) is None
    assert er.async_get(hass).async_get(other) is None
    assert own_lights(hass, entry.entry_id) == [slider]


async def test_stale_sliders_do_not_touch_preferences_with_pref_in_the_light_name(hass: HomeAssistant) -> None:
    hass.states.async_set("light.pref_lampe", STATE_ON, LAMP)
    entry = await setup_entry(hass, [make_partner("Frank", 2, lights=["light.pref_lampe"], likes=["Apfel"])])
    slider = partner_entity(hass, entry, FRANK, "light", "plight_light_pref_lampe")

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    assert hass.states.get(slider) is not None  # „_pref_“ in der ID des Lichts macht ihn nicht zur Vorliebe
    assert registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{FRANK}_pref_like_apfel_info")


async def test_the_title_of_a_light_falls_back_to_the_registry_and_the_device(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain="other", title="Andere")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("other", "1")}, name="Bettlampe"
    )
    registry = er.async_get(hass)
    of_device = registry.async_get_or_create("light", "other", "1", config_entry=entry, device_id=device.id)
    named = registry.async_get_or_create("light", "other", "2", config_entry=entry, original_name="Leselampe")

    assert entity_title(hass, of_device.entity_id) == "Bettlampe"
    assert entity_title(hass, named.entity_id) == "Leselampe"
    assert entity_title(hass, "light.gibt_es_nicht") == "light.gibt_es_nicht"
    hass.states.async_set(named.entity_id, STATE_ON, {"friendly_name": "Wohnzimmer"})
    assert entity_title(hass, named.entity_id) == "Wohnzimmer"
