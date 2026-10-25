"""Entities: Geräte, Schalter, Tasten, Zähler, Sensoren und die Ortsauswahl."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.spiel_saison.const import DOMAIN

from .conftest import FakeRecorder, add_cameras, hub_entity, make_partner, make_place, partner_entity, setup_entry

FRANK = "p_Frank"


async def _setup(hass: HomeAssistant, places: list[dict] | None = None):
    add_cameras(hass, "fuchs", "hase")
    return await setup_entry(hass, [make_partner("Frank", 2, ["camera.fuchs", "camera.hase"])], places)


async def test_devices_hang_below_the_hub(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    devices = dr.async_get(hass)

    hub = devices.async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)
    partner = devices.async_get_device_by_identifier((DOMAIN, f"{entry.entry_id}_{FRANK}"), entry.entry_id)

    assert hub and hub.name == "Spiel Saison"
    assert partner and partner.via_device_id == hub.id
    assert partner.name == "Play partner Frank"


async def test_partner_entities_exist(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    for domain, key in (
        ("switch", "recording"),
        ("button", "prepare"),
        ("button", "new_season"),
        ("number", "season"),
        ("number", "recordings_season"),
        ("number", "recordings_total"),
        ("sensor", "status"),
        ("sensor", "duration_season"),
        ("sensor", "duration_total"),
        ("sensor", "longest_season"),
        ("sensor", "last_recording"),
        ("sensor", "folder"),
    ):
        partner_entity(hass, entry, FRANK, domain, key)
    for domain, key in (
        ("sensor", "status"),
        ("sensor", "current_partner"),
        ("sensor", "recorder_free_space"),
        ("binary_sensor", "recorder_online"),
        ("binary_sensor", "recording"),
    ):
        hub_entity(hass, entry, domain, key)


async def test_hub_sensors_show_the_app(hass: HomeAssistant) -> None:
    entry = await _setup(hass)

    assert hass.states.get(hub_entity(hass, entry, "binary_sensor", "recorder_online")).state == STATE_ON
    assert hass.states.get(hub_entity(hass, entry, "sensor", "recorder_free_space")).state == "2500.0"
    assert hass.states.get(hub_entity(hass, entry, "sensor", "status")).state == "idle"
    assert hass.states.get(hub_entity(hass, entry, "binary_sensor", "recording")).state == STATE_OFF
    assert hass.states.get(hub_entity(hass, entry, "sensor", "current_partner")).state == STATE_UNKNOWN


async def test_the_switch_starts_and_stops_the_recording(hass: HomeAssistant, stream) -> None:
    entry = await _setup(hass)
    switch = partner_entity(hass, entry, FRANK, "switch", "recording")

    await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert hass.states.get(switch).state == STATE_ON
    assert hass.states.get(partner_entity(hass, entry, FRANK, "sensor", "status")).state == "recording"
    assert hass.states.get(hub_entity(hass, entry, "binary_sensor", "recording")).state == STATE_ON
    assert hass.states.get(hub_entity(hass, entry, "sensor", "current_partner")).state == "Frank"
    assert hass.states.get(hub_entity(hass, entry, "sensor", "status")).state == "recording"
    assert hass.states.get(partner_entity(hass, entry, FRANK, "number", "recordings_total")).state == "1"
    assert hass.states.get(partner_entity(hass, entry, FRANK, "sensor", "last_recording")).state != STATE_UNKNOWN

    await hass.services.async_call("switch", "turn_off", {"entity_id": switch}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert hass.states.get(switch).state == STATE_OFF
    assert FakeRecorder.calls[-1] == ("stop", FRANK)
    assert hass.states.get(hub_entity(hass, entry, "sensor", "current_partner")).state == STATE_UNKNOWN


async def test_statistics_sensors_follow_a_finished_recording(hass: HomeAssistant, stream) -> None:
    entry = await _setup(hass)
    switch = partner_entity(hass, entry, FRANK, "switch", "recording")
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    entry.runtime_data.runs[FRANK]["start"] = (dt_util.utcnow() - timedelta(hours=1, minutes=30)).isoformat()

    await hass.services.async_call("switch", "turn_off", {"entity_id": switch}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert float(hass.states.get(partner_entity(hass, entry, FRANK, "sensor", "duration_season")).state) == 1.5
    assert float(hass.states.get(partner_entity(hass, entry, FRANK, "sensor", "duration_total")).state) == 1.5
    assert float(hass.states.get(partner_entity(hass, entry, FRANK, "sensor", "longest_season")).state) == 90.0
    last = hass.states.get(partner_entity(hass, entry, FRANK, "sensor", "last_recording"))
    assert last.attributes["duration_min"] == 90.0


async def test_the_counters_can_be_set_by_hand_and_survive_a_reload(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    season = partner_entity(hass, entry, FRANK, "number", "season")
    total = partner_entity(hass, entry, FRANK, "number", "recordings_total")

    await hass.services.async_call("number", "set_value", {"entity_id": season, "value": 3}, blocking=True)
    await hass.services.async_call("number", "set_value", {"entity_id": total, "value": 41}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(season).state == "3"

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get(season).state == "3"
    assert hass.states.get(total).state == "41"


async def test_the_buttons(hass: HomeAssistant, service_calls) -> None:
    entry = await _setup(hass, [make_place("Bett", prepare_on={"entity_id": ["switch.winde"]})])

    await hass.services.async_call(
        "button", "press", {"entity_id": partner_entity(hass, entry, FRANK, "button", "prepare")}, blocking=True
    )
    await hass.services.async_call(
        "button", "press", {"entity_id": partner_entity(hass, entry, FRANK, "button", "new_season")}, blocking=True
    )

    assert [(c.domain, c.service) for c in service_calls] == [("homeassistant", "turn_on")]
    assert hass.states.get(partner_entity(hass, entry, FRANK, "number", "season")).state == "2"


async def test_the_place_select_is_there_only_with_places_and_is_remembered(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    assert er.async_get(hass).async_get_entity_id("select", DOMAIN, f"{entry.entry_id}_place") is None
    assert await hass.config_entries.async_unload(entry.entry_id)

    entry = await _setup_with_places(hass)
    select = hub_entity(hass, entry, "select", "place")
    assert hass.states.get(select).state == "Bett"
    assert hass.states.get(select).attributes["options"] == ["Bett", "Schaukel"]

    await hass.services.async_call(
        "select", "select_option", {"entity_id": select, "option": "Schaukel"}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(select).state == "Schaukel"
    assert entry.runtime_data.place_id == "o_Schaukel"

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(select).state == "Schaukel"


async def _setup_with_places(hass: HomeAssistant):
    from homeassistant.config_entries import ConfigEntryState

    for entry in hass.config_entries.async_entries(DOMAIN):
        assert entry.state in (ConfigEntryState.NOT_LOADED, ConfigEntryState.LOADED)
        await hass.config_entries.async_remove(entry.entry_id)
    return await _setup(hass, [make_place("Bett"), make_place("Schaukel")])


async def test_the_selected_place_decides_which_steps_run(hass: HomeAssistant, stream, service_calls) -> None:
    entry = await _setup(
        hass,
        [
            make_place("Bett", start_on={"entity_id": ["light.bett"]}),
            make_place("Schaukel", start_on={"entity_id": ["light.schaukel"]}),
        ],
    )
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": hub_entity(hass, entry, "select", "place"), "option": "Schaukel"},
        blocking=True,
    )

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": partner_entity(hass, entry, FRANK, "switch", "recording")}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert [dict(c.data) for c in service_calls] == [{"entity_id": ["light.schaukel"]}]
    status = hass.states.get(partner_entity(hass, entry, FRANK, "sensor", "status"))
    assert status.attributes["place"] == "Schaukel"


async def test_the_folder_sensor_shows_the_next_folder(hass: HomeAssistant) -> None:
    entry = await _setup(hass)

    state = hass.states.get(partner_entity(hass, entry, FRANK, "sensor", "folder"))

    assert state.state == f"Spielen/Spielen mit Frank #02/Saison 01 vom {dt_util.now():%d,%m,%Y}"
    assert state.attributes["parts"][0] == "Spielen"
    assert state.attributes["path"] == f"/media/Aufnahmen/{state.state}"


async def test_diagnostics_hide_the_key(hass: HomeAssistant) -> None:
    from custom_components.spiel_saison.diagnostics import async_get_config_entry_diagnostics

    entry = await _setup(hass, [make_place("Bett")])

    data = await async_get_config_entry_diagnostics(hass, entry)

    assert data["entry"]["recorder_key"] == "**REDACTED**"
    assert data["partners"][0]["name"] == "Frank"
    assert data["places"][0]["name"] == "Bett"
    assert "rtsp://" not in str(data)
