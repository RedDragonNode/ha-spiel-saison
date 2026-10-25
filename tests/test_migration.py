"""Umstellung gespeicherter Einträge auf den heutigen Aufbau (Version 1.5)."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.spiel_saison.const import DOMAIN
from custom_components.spiel_saison.manager import counters_store

from .conftest import KEY, URL, add_cameras, make_partner, make_place, setup_entry

OLD_PATTERN = "#{nr}.{saison}.{aufnahme}.Cam ID.{cam_nr} (4K) %DD.%MM.%CCYY"


def _old_partner(name: str, number: int, *cams: str) -> dict:
    return {"name": name, "number": number, "cams": list(cams)}


def _old_place(name: str, **fields) -> dict:
    return {"name": name, "ptz_preset": 0, "lead_seconds": 30, "end_seconds": 10, **fields}


async def test_the_stored_file_pattern_gets_the_camera_name_instead_of_the_number(hass: HomeAssistant) -> None:
    entry = await setup_entry(hass, minor_version=1, file_pattern=OLD_PATTERN)

    assert entry.minor_version == 5
    assert entry.data["file_pattern"] == "#{nr}.{saison}.{aufnahme}.Cam ID.{cam} (4K) %DD.%MM.%CCYY"


async def test_an_own_file_pattern_is_left_alone(hass: HomeAssistant) -> None:
    entry = await setup_entry(hass, minor_version=1, file_pattern="{cam} {cam_nr}")

    assert entry.minor_version == 5
    assert entry.data["file_pattern"] == "{cam} {cam_nr}"


async def test_cameras_move_from_the_partner_to_the_place(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    entity_registry.async_get_or_create("camera", "ctronics_cam", "abc", suggested_object_id="fuchs")
    add_cameras(hass, "fuchs", "hase")
    entry = await setup_entry(
        hass,
        [_old_partner("Tobias", 1, "camera.fuchs", "camera.hase")],
        [
            _old_place(
                "Schaukel",
                ptz_preset=1,
                announce_entity="notify.echo",
                announce_start="Los",
                announce_end="Fertig",
                start_on={"entity_id": ["light.schaukel"]},
            )
        ],
        minor_version=2,
    )

    assert entry.minor_version == 5
    partner = entry.subentries["p_Tobias"]
    assert partner.data == {
        "name": "Tobias",
        "number": 1,
        "likes": [],
        "treats": [],
        "announce_entity": "notify.echo",
        "announce_start": "Los",
        "announce_end": "Fertig",
    }
    place = entry.subentries["o_Schaukel"]  # derselbe Untereintrag wie vorher
    assert place.title == "Schaukel (Tobias)" and place.unique_id == "p_Tobias:schaukel"
    assert place.data == {
        "name": "Schaukel",
        "partner": "p_Tobias",
        "cams": ["camera.fuchs", "camera.hase"],
        "cam_presets": {"camera.fuchs": 1},  # nur die Ctronics-Kamera fährt die alte Position an
        "lead_seconds": 30,
        "end_seconds": 10,
        "start_on": {"entity_id": ["light.schaukel"]},
    }
    assert list(entry.runtime_data.places_of("p_Tobias")) == ["o_Schaukel"]


async def test_every_partner_gets_a_copy_of_each_old_place_with_its_own_cameras(hass: HomeAssistant) -> None:
    add_cameras(hass, "fuchs", "hase")
    entry = await setup_entry(
        hass,
        [_old_partner("Frank", 2, "camera.fuchs"), _old_partner("Tobias", 1, "camera.hase")],
        [_old_place("Bett", start_on={"entity_id": ["light.bett"]})],
        minor_version=2,
    )

    places = {s.data["partner"]: s for s in entry.subentries.values() if s.subentry_type == "place"}

    assert set(places) == {"p_Frank", "p_Tobias"}
    assert places["p_Frank"].subentry_id == "o_Bett"  # der erste Spielpartner behält den vorhandenen Ort
    assert places["p_Frank"].data["cams"] == ["camera.fuchs"]
    assert places["p_Tobias"].data["cams"] == ["camera.hase"]
    assert places["p_Tobias"].data["start_on"] == {"entity_id": ["light.bett"]}
    assert places["p_Tobias"].title == "Bett (Tobias)"
    assert {p.name for p in entry.runtime_data.places_of("p_Tobias").values()} == {"Bett"}


async def test_the_old_selected_place_is_kept_for_the_partner(hass: HomeAssistant) -> None:
    add_cameras(hass, "fuchs")
    entry_id = "01MIGRATIONENTRY0000000000"
    await counters_store(hass, entry_id).async_save({"partners": {}, "place": "o_Schaukel", "runs": {}})
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from .conftest import KEY, URL

    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id=entry_id,
        minor_version=2,
        data={"recorder_url": URL, "recorder_key": KEY},
        subentries_data=[
            {
                "subentry_type": "partner",
                "title": "Tobias",
                "unique_id": "tobias",
                "subentry_id": "p_Tobias",
                "data": _old_partner("Tobias", 1, "camera.fuchs"),
            },
            {
                "subentry_type": "place",
                "title": "Bett",
                "unique_id": "bett",
                "subentry_id": "o_Bett",
                "data": _old_place("Bett"),
            },
            {
                "subentry_type": "place",
                "title": "Schaukel",
                "unique_id": "schaukel",
                "subentry_id": "o_Schaukel",
                "data": _old_place("Schaukel"),
            },
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.runtime_data.place_ids == {"p_Tobias": "o_Schaukel"}


async def test_the_old_hub_select_is_removed(hass: HomeAssistant, entity_registry: er.EntityRegistry) -> None:
    add_cameras(hass, "fuchs")
    entry = await setup_entry(hass, [make_partner("Tobias", 1)], [])
    stale = entity_registry.async_get_or_create("select", DOMAIN, f"{entry.entry_id}_place", config_entry=entry)
    assert await hass.config_entries.async_unload(entry.entry_id)
    hass.config_entries.async_update_entry(entry, minor_version=2)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entity_registry.async_get(stale.entity_id) is None
    assert entry.minor_version == 5


async def test_the_preference_entries_of_0_3_become_names(hass: HomeAssistant) -> None:
    def sub(kind: str, sid: str, data: dict) -> dict:
        return {"subentry_type": kind, "title": data["name"], "unique_id": sid, "subentry_id": sid, "data": data}

    entry = MockConfigEntry(
        domain=DOMAIN,
        minor_version=3,
        data={"recorder_url": URL, "recorder_key": KEY},
        subentries_data=[
            # die Vorlieben aus 0.3.x: eigene Einträge, auf die der Spielpartner mit IDs zeigte
            sub(
                "partner",
                "p_Tobias",
                {"name": "Tobias", "number": 1, "likes": ["l_Seil", "l_Weg"], "treats": ["t_Feder"]},
            ),
            sub("like", "l_Seil", {"name": "Seil"}),
            sub("like", "l_Frei", {"name": "Frei"}),
            sub("treat", "t_Feder", {"name": "Feder"}),
        ],
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.minor_version == 5
    assert entry.subentries["p_Tobias"].data["likes"] == ["Seil"]  # „Weg“ gab es nicht mehr
    assert entry.subentries["p_Tobias"].data["treats"] == ["Feder"]
    assert not [s for s in entry.subentries.values() if s.subentry_type in ("like", "treat")]
    assert entry.data["like_catalog"] == ["Seil", "Frei"]  # auch nicht gewählte bleiben für neue Spielpartner
    assert entry.data["treat_catalog"] == ["Feder"]
    assert entry.runtime_data.partners["p_Tobias"].likes == ["Seil"]


async def test_the_settings_of_the_season_buttons_move_from_the_place_to_the_options(hass: HomeAssistant) -> None:
    add_cameras(hass, "fuchs")
    entry = await setup_entry(
        hass,
        [make_partner("Frank", 2)],
        [
            make_place(
                "Schaukel",
                cams=("camera.fuchs",),
                start_on={"entity_id": ["light.schaukel"]},
                prepare_on={"entity_id": ["switch.winde"]},
                cover="cover.winde",
                cover_position=50,
                finish_off={"entity_id": ["switch.winde"]},
                finish_cover="cover.rollo",
                finish_cover_position=10,
            ),
            make_place("Bett", cams=("camera.fuchs",), prepare_on={"entity_id": ["light.bett"]}),
        ],
        minor_version=4,
    )

    assert entry.minor_version == 5
    assert entry.options == {
        "prepare": {"on": {"entity_id": ["switch.winde"]}, "cover": "cover.winde", "cover_position": 50},
        "finish": {"off": {"entity_id": ["switch.winde"]}, "cover": "cover.rollo", "cover_position": 10},
    }  # bei verschiedenen Orten gilt der erste
    assert entry.subentries["o_Schaukel"].data == {
        "name": "Schaukel",
        "partner": "p_Frank",
        "cams": ["camera.fuchs"],
        "cam_presets": {},
        "lead_seconds": 0,
        "end_seconds": 0,
        "start_on": {"entity_id": ["light.schaukel"]},
    }  # Beginn und Ende bleiben beim Ort
    assert "prepare_on" not in entry.subentries["o_Bett"].data
    runtime = entry.runtime_data
    assert runtime.prepare_step.cover == "cover.winde" and runtime.finish_step.cover_position == 10


async def test_places_without_settings_leave_the_options_empty(hass: HomeAssistant) -> None:
    add_cameras(hass, "fuchs")
    entry = await setup_entry(
        hass, [make_partner("Frank", 2)], [make_place("Bett", cams=("camera.fuchs",))], minor_version=4
    )

    assert entry.minor_version == 5 and entry.options == {}
