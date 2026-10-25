"""Config-Flow: Verbindung zur App, neuer Schlüssel, Spielpartner und Orte als Untereinträge."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.spiel_saison.const import (
    DEFAULT_BASE_FOLDER,
    DEFAULT_FILE_PATTERN,
    DEFAULT_PARTNER_PATTERN,
    DEFAULT_SEASON_PATTERN,
    DOMAIN,
)
from custom_components.spiel_saison.recorder_client import RecorderAuthError

from .conftest import KEY, URL, FakeRecorder, add_cameras, make_partner, make_place, setup_entry


async def _start_user_flow(hass: HomeAssistant) -> dict:
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})


async def test_user_flow_creates_entry_with_default_patterns(hass: HomeAssistant) -> None:
    result = await _start_user_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"recorder_url": f" {URL}/ ", "recorder_key": f" {KEY} ", "base_folder": " Spielen\\Spezial/ "},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Spiel Saison"
    assert result["data"] == {
        "recorder_url": URL,
        "recorder_key": KEY,
        "base_folder": "Spielen/Spezial",
        "partner_pattern": DEFAULT_PARTNER_PATTERN,
        "season_pattern": DEFAULT_SEASON_PATTERN,
        "file_pattern": DEFAULT_FILE_PATTERN,
    }


async def test_user_flow_suggests_defaults(hass: HomeAssistant) -> None:
    result = await _start_user_flow(hass)
    defaults = {
        str(k): k.default() for k in result["data_schema"].schema if k.default is not None and callable(k.default)
    }
    assert defaults["recorder_url"] == "http://local-spiel-saison-recorder:8099"
    assert defaults["base_folder"] == DEFAULT_BASE_FOLDER
    assert defaults["file_pattern"] == DEFAULT_FILE_PATTERN


async def test_user_flow_unreachable_and_wrong_key(hass: HomeAssistant, unreachable: type[FakeRecorder]) -> None:
    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"recorder_url": URL, "recorder_key": KEY}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}

    FakeRecorder.fail = RecorderAuthError("Schlüssel")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"recorder_url": URL, "recorder_key": KEY}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    FakeRecorder.fail = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"recorder_url": URL, "recorder_key": KEY}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_second_entry_is_refused(hass: HomeAssistant) -> None:
    await setup_entry(hass)
    result = await _start_user_flow(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_reconfigure_changes_connection_and_patterns(hass: HomeAssistant) -> None:
    entry = await setup_entry(hass)
    old_manager = entry.runtime_data
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "recorder_url": "http://neu:8099",
            "recorder_key": "neuerneuerneuer1",
            "base_folder": "Neu",
            "file_pattern": "{partner} {saison}",
        },
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data["recorder_url"] == "http://neu:8099"
    assert entry.data["base_folder"] == "Neu"
    assert entry.data["file_pattern"] == "{partner} {saison}"
    assert entry.data["partner_pattern"] == DEFAULT_PARTNER_PATTERN  # leer = eingebautes Muster
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data is not old_manager  # genau ein Neuladen über den Update-Listener


async def test_reauth_replaces_key(hass: HomeAssistant, bad_key: type[FakeRecorder]) -> None:
    entry = await setup_entry(hass)
    old_manager = entry.runtime_data
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"recorder_key": "falschfalschfalsch"})
    assert result["errors"] == {"base": "invalid_auth"}

    FakeRecorder.fail = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"recorder_key": "richtigrichtig12"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["recorder_key"] == "richtigrichtig12"
    await hass.async_block_till_done()
    assert entry.runtime_data is not old_manager


# ---------------------------------------------------------------- Spielpartner
async def _start_sub(hass: HomeAssistant, entry, kind: str, **context) -> dict:
    return await hass.config_entries.subentries.async_init(
        (entry.entry_id, kind), context={"source": "user", **context}
    )


async def test_partner_flow_creates_subentry_and_suggests_the_next_number(hass: HomeAssistant) -> None:
    add_cameras(hass, "fuchs", "hase")
    entry = await setup_entry(hass, [make_partner("Frank", 1, ["camera.fuchs"])])

    flow = await _start_sub(hass, entry, "partner")
    suggested = {str(k): k.description["suggested_value"] for k in flow["data_schema"].schema if k.description}
    assert suggested["number"] == 2  # die 1 ist vergeben

    result = await hass.config_entries.subentries.async_configure(
        flow["flow_id"], {"name": " Tobias ", "number": 2, "cams": ["camera.fuchs", "camera.hase"]}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    sub = next(s for s in entry.subentries.values() if s.title == "Tobias")
    assert sub.subentry_type == "partner"
    assert sub.data == {"name": "Tobias", "number": 2, "cams": ["camera.fuchs", "camera.hase"]}
    assert sub.unique_id == "tobias"


async def test_partner_flow_checks_name_number_and_cameras(hass: HomeAssistant) -> None:
    add_cameras(hass, "fuchs")
    entry = await setup_entry(hass, [make_partner("Frank", 1, ["camera.fuchs"])])
    flow = await _start_sub(hass, entry, "partner")

    result = await hass.config_entries.subentries.async_configure(
        flow["flow_id"], {"name": " frank ", "number": 1, "cams": []}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"name": "name_taken", "number": "number_taken", "cams": "cams_required"}

    result = await hass.config_entries.subentries.async_configure(
        flow["flow_id"], {"name": "Tobias", "number": 2, "cams": ["camera.fuchs"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_partner_reconfigure_keeps_own_name_and_number(hass: HomeAssistant) -> None:
    add_cameras(hass, "fuchs", "hase")
    entry = await setup_entry(
        hass, [make_partner("Frank", 1, ["camera.fuchs"]), make_partner("Tobias", 2, ["camera.fuchs"])]
    )
    flow = await _start_sub(hass, entry, "partner", source="reconfigure", subentry_id="p_Frank")
    assert flow["step_id"] == "reconfigure"

    result = await hass.config_entries.subentries.async_configure(
        flow["flow_id"], {"name": "Frank", "number": 1, "cams": ["camera.fuchs", "camera.hase"]}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.subentries["p_Frank"].data["cams"] == ["camera.fuchs", "camera.hase"]


# ------------------------------------------------------------------------ Orte
async def _add_place(
    hass: HomeAssistant, entry, basics: dict, begin: dict | None = None, end: dict | None = None
) -> dict:
    flow = await _start_sub(hass, entry, "place")
    assert flow["step_id"] == "user"
    result = await hass.config_entries.subentries.async_configure(flow["flow_id"], basics)
    if result["type"] is FlowResultType.FORM and result["step_id"] == "user":
        return result  # Fehler im ersten Schritt
    assert result["step_id"] == "user_begin"
    result = await hass.config_entries.subentries.async_configure(flow["flow_id"], begin or {})
    assert result["step_id"] == "user_end"
    return await hass.config_entries.subentries.async_configure(flow["flow_id"], end or {})


async def test_place_flow_creates_subentry_in_three_steps(hass: HomeAssistant) -> None:
    entry = await setup_entry(hass)

    result = await _add_place(
        hass,
        entry,
        {
            "name": " Bett ",
            "ptz_preset": 10,
            "lead_seconds": 30,
            "end_seconds": 10,
            "announce_entity": "notify.echo",
            "announce_start": "Start",
            "announce_end": "Ende",
        },
        {
            "prepare_on": {"entity_id": ["switch.winde"]},
            "cover": "cover.winde",
            "cover_position": 40,
            "start_off": {"label_id": ["spielen_auf_schaukel"]},
            "start_on": {"label_id": ["spielen_auf_bett"]},
        },
        {"end_off": {"label_id": ["spielen_auf_bett"]}, "end_on": {"entity_id": ["switch.schrank"]}},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    sub = next(s for s in entry.subentries.values() if s.subentry_type == "place")
    assert sub.title == "Bett"
    assert sub.data == {
        "name": "Bett",
        "ptz_preset": 10,
        "lead_seconds": 30,
        "end_seconds": 10,
        "announce_entity": "notify.echo",
        "announce_start": "Start",
        "announce_end": "Ende",
        "prepare_on": {"entity_id": ["switch.winde"]},
        "cover": "cover.winde",
        "cover_position": 40,
        "start_off": {"label_id": ["spielen_auf_schaukel"]},
        "start_on": {"label_id": ["spielen_auf_bett"]},
        "end_off": {"label_id": ["spielen_auf_bett"]},
        "end_on": {"entity_id": ["switch.schrank"]},
    }


async def test_place_flow_everything_but_the_name_is_optional(hass: HomeAssistant) -> None:
    entry = await setup_entry(hass)

    result = await _add_place(hass, entry, {"name": "Dach"})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    sub = next(s for s in entry.subentries.values() if s.subentry_type == "place")
    assert sub.data == {
        "name": "Dach",
        "ptz_preset": 0,
        "lead_seconds": 0,
        "end_seconds": 0,
        "announce_start": "",
        "announce_end": "",
    }


async def test_place_name_must_be_unique(hass: HomeAssistant) -> None:
    entry = await setup_entry(hass, places=[make_place("Bett")])

    result = await _add_place(hass, entry, {"name": " BETT "})

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"name": "name_taken"}


async def test_place_reconfigure_walks_the_three_steps_and_shows_saved_values(hass: HomeAssistant) -> None:
    entry = await setup_entry(
        hass,
        places=[
            make_place("Bett", ptz_preset=10, start_on={"entity_id": ["light.bett"]}, end_on={"entity_id": ["x.y"]})
        ],
    )
    flow = await _start_sub(hass, entry, "place", source="reconfigure", subentry_id="o_Bett")
    assert flow["step_id"] == "reconfigure"
    assert {str(k): k.description["suggested_value"] for k in flow["data_schema"].schema if k.description}[
        "ptz_preset"
    ] == 10

    step2 = await hass.config_entries.subentries.async_configure(flow["flow_id"], {"name": "Bett", "ptz_preset": 3})
    assert step2["step_id"] == "reconfigure_begin"
    shown = {str(k): k.description["suggested_value"] for k in step2["data_schema"].schema if k.description}
    assert shown["start_on"] == {"entity_id": ["light.bett"]}
    step3 = await hass.config_entries.subentries.async_configure(flow["flow_id"], {})
    assert step3["step_id"] == "reconfigure_end"
    done = await hass.config_entries.subentries.async_configure(flow["flow_id"], {"end_on": {"entity_id": ["x.y"]}})

    assert done["type"] is FlowResultType.ABORT
    assert done["reason"] == "reconfigure_successful"
    data = entry.subentries["o_Bett"].data
    assert data["ptz_preset"] == 3 and data["end_on"] == {"entity_id": ["x.y"]} and "start_on" not in data
