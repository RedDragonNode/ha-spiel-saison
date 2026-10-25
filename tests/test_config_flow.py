"""Config-Flow: Verbindung zur App, neuer Schlüssel, Spielpartner und Orte als Untereinträge."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

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
    entry = await setup_entry(hass, [make_partner("Frank", 1)])

    flow = await _start_sub(hass, entry, "partner")
    suggested = {str(k): k.description["suggested_value"] for k in flow["data_schema"].schema if k.description}
    assert suggested["number"] == 2  # die 1 ist vergeben
    assert "cams" not in {str(k) for k in flow["data_schema"].schema}  # die Kameras gehören zum Ort

    result = await hass.config_entries.subentries.async_configure(
        flow["flow_id"],
        {
            "name": " Tobias ",
            "number": 2,
            "announce_entity": "notify.echo",
            "announce_start": " Start ",
            "announce_end": "Ende",
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    sub = next(s for s in entry.subentries.values() if s.title == "Tobias")
    assert sub.subentry_type == "partner"
    assert sub.data == {
        "name": "Tobias",
        "number": 2,
        "nickname": "",
        "likes": [],
        "treats": [],
        "announce_entity": "notify.echo",
        "announce_start": "Start",
        "announce_end": "Ende",
    }
    assert sub.unique_id == "tobias"


async def test_partner_announcement_is_optional(hass: HomeAssistant) -> None:
    entry = await setup_entry(hass)
    flow = await _start_sub(hass, entry, "partner")

    result = await hass.config_entries.subentries.async_configure(flow["flow_id"], {"name": "Tobias", "number": 2})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    sub = next(iter(entry.subentries.values()))
    assert sub.data == {
        "name": "Tobias",
        "number": 2,
        "nickname": "",
        "likes": [],
        "treats": [],
        "announce_start": "",
        "announce_end": "",
    }


async def test_partner_flow_has_nickname_and_two_free_preference_fields(hass: HomeAssistant) -> None:
    entry = await setup_entry(
        hass,
        [make_partner("Frank", 1, likes=["Zebra", "Apfel"])],
        like_catalog=["Birne"],
        treat_catalog=["Feder"],
    )

    flow = await _start_sub(hass, entry, "partner")

    fields = {str(k): v for k, v in flow["data_schema"].schema.items()}
    assert list(fields)[:5] == ["name", "number", "nickname", "likes", "treats"]
    assert fields["likes"].config["multiple"] is True and fields["likes"].config["custom_value"] is True
    assert [o["value"] for o in fields["likes"].config["options"]] == [
        "Apfel",
        "Birne",
        "Zebra",
    ]  # gemerkte und die von Frank
    assert [o["value"] for o in fields["treats"].config["options"]] == ["Feder"]

    result = await hass.config_entries.subentries.async_configure(
        flow["flow_id"],
        {"name": "Tobias", "number": 2, "nickname": " Tobi ", "likes": ["Birne", " Neu "], "treats": []},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    sub = next(s for s in entry.subentries.values() if s.title == "Tobias")
    assert sub.data["nickname"] == "Tobi"
    assert sub.data["likes"] == ["Birne", "Neu"]
    assert sub.data["treats"] == []


async def test_new_preferences_are_remembered_for_the_next_partner(hass: HomeAssistant) -> None:
    entry = await setup_entry(hass, like_catalog=["Seil"])
    flow = await _start_sub(hass, entry, "partner")
    await hass.config_entries.subentries.async_configure(
        flow["flow_id"],
        {"name": "Tobias", "number": 1, "likes": ["Seil", "Schal"], "treats": ["Feder"]},
    )
    await hass.async_block_till_done()
    assert entry.data["like_catalog"] == ["Seil", "Schal"]
    assert entry.data["treat_catalog"] == ["Feder"]

    flow = await _start_sub(hass, entry, "partner")  # ein weiterer Spielpartner sieht sie

    fields = {str(k): v for k, v in flow["data_schema"].schema.items()}
    assert [o["value"] for o in fields["likes"].config["options"]] == ["Schal", "Seil"]
    assert [o["value"] for o in fields["treats"].config["options"]] == ["Feder"]


async def test_preference_names_are_cleaned_and_use_the_known_spelling(hass: HomeAssistant) -> None:
    entry = await setup_entry(hass, like_catalog=["Seil"])
    flow = await _start_sub(hass, entry, "partner")

    await hass.config_entries.subentries.async_configure(
        flow["flow_id"], {"name": "Tobias", "number": 1, "likes": ["seil", " SEIL ", "!!!", "Schal", "schal"]}
    )

    sub = next(iter(entry.subentries.values()))
    assert sub.data["likes"] == ["Seil", "Schal"]  # keine Doppelten, keine leeren Namen, bekannte Schreibweise


async def test_partner_flow_checks_name_and_number(hass: HomeAssistant) -> None:
    entry = await setup_entry(hass, [make_partner("Frank", 1)])
    flow = await _start_sub(hass, entry, "partner")

    result = await hass.config_entries.subentries.async_configure(flow["flow_id"], {"name": " frank ", "number": 1})

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"name": "name_taken", "number": "number_taken"}

    result = await hass.config_entries.subentries.async_configure(flow["flow_id"], {"name": "Tobias", "number": 2})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_partner_reconfigure_keeps_own_name_and_number_and_renames_the_place_titles(
    hass: HomeAssistant,
) -> None:
    add_cameras(hass, "fuchs")
    entry = await setup_entry(
        hass,
        [make_partner("Frank", 1), make_partner("Tobias", 2)],
        [make_place("Bett", "Frank", ("camera.fuchs",))],
    )
    flow = await _start_sub(hass, entry, "partner", source="reconfigure", subentry_id="p_Frank")
    assert flow["step_id"] == "reconfigure"

    result = await hass.config_entries.subentries.async_configure(
        flow["flow_id"], {"name": "Franky", "number": 1, "announce_entity": "notify.echo", "announce_end": "Tschüss"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.subentries["p_Frank"].data["announce_entity"] == "notify.echo"
    assert entry.subentries["p_Frank"].data["announce_end"] == "Tschüss"
    assert entry.subentries["o_Bett"].title == "Bett (Franky)"


# ------------------------------------------------------------------------ Orte
def _shown(step: dict) -> dict:
    return {str(k): k.description["suggested_value"] for k in step["data_schema"].schema if k.description}


async def _add_place(
    hass: HomeAssistant, entry, basics: dict, *, ptz: dict | None = None, begin=None, end=None
) -> dict:
    flow = await _start_sub(hass, entry, "place")
    assert flow["step_id"] == "user"
    result = await hass.config_entries.subentries.async_configure(flow["flow_id"], basics)
    if result["type"] is FlowResultType.FORM and result["step_id"] == "user":
        return result  # Fehler im ersten Schritt
    if ptz is not None:
        assert result["step_id"] == "user_ptz"
        result = await hass.config_entries.subentries.async_configure(flow["flow_id"], ptz)
    assert result["step_id"] == "user_begin"
    result = await hass.config_entries.subentries.async_configure(flow["flow_id"], begin or {})
    assert result["step_id"] == "user_end"
    return await hass.config_entries.subentries.async_configure(flow["flow_id"], end or {})


def _ctronics(entity_registry, hass: HomeAssistant, *names: str, presets: str = "") -> None:
    """Legt Ctronics-Kameras an, optional mit den Namen der Positionen in den Optionen der Ctronics-Integration."""
    ctronics = MockConfigEntry(domain="ctronics_cam", options={"presets": presets} if presets else {})
    ctronics.add_to_hass(hass)
    for name in names:
        entity_registry.async_get_or_create(
            "camera", "ctronics_cam", name, suggested_object_id=name, config_entry=ctronics
        )
    add_cameras(hass, *names)


async def test_place_flow_without_ctronics_cameras_skips_the_position_step(hass: HomeAssistant) -> None:
    add_cameras(hass, "fuchs", "hase")
    entry = await setup_entry(hass, [make_partner("Frank", 2)])

    result = await _add_place(
        hass,
        entry,
        {
            "name": " Bett ",
            "partner": "p_Frank",
            "cams": ["camera.fuchs", "camera.hase"],
            "lead_seconds": 30,
            "end_seconds": 10,
        },
        begin={
            "start_off": {"label_id": ["spielen_auf_schaukel"]},
            "start_on": {"label_id": ["spielen_auf_bett"]},
        },
        end={"end_off": {"label_id": ["spielen_auf_bett"]}, "end_on": {"entity_id": ["switch.schrank"]}},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    sub = next(s for s in entry.subentries.values() if s.subentry_type == "place")
    assert sub.title == "Bett (Frank)"
    assert sub.unique_id == "p_Frank:bett"
    assert sub.data == {
        "name": "Bett",
        "partner": "p_Frank",
        "cams": ["camera.fuchs", "camera.hase"],
        "cam_presets": {},
        "lead_seconds": 30,
        "end_seconds": 10,
        "start_off": {"label_id": ["spielen_auf_schaukel"]},
        "start_on": {"label_id": ["spielen_auf_bett"]},
        "end_off": {"label_id": ["spielen_auf_bett"]},
        "end_on": {"entity_id": ["switch.schrank"]},
    }


async def test_place_flow_partner_dropdown_sits_below_the_name(hass: HomeAssistant) -> None:
    entry = await setup_entry(hass, [make_partner("Frank", 2), make_partner("Tobias", 1)])

    flow = await _start_sub(hass, entry, "place")

    fields = [str(k) for k in flow["data_schema"].schema]
    assert fields[:3] == ["name", "partner", "cams"]
    selector = next(v for k, v in flow["data_schema"].schema.items() if str(k) == "partner")
    assert [(o["value"], o["label"]) for o in selector.config["options"]] == [
        ("p_Frank", "Frank"),
        ("p_Tobias", "Tobias"),
    ]


async def test_place_flow_needs_a_partner(hass: HomeAssistant) -> None:
    entry = await setup_entry(hass)

    flow = await _start_sub(hass, entry, "place")

    assert flow["type"] is FlowResultType.ABORT
    assert flow["reason"] == "no_partner"


async def test_place_flow_asks_for_a_position_of_every_ctronics_camera(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    _ctronics(entity_registry, hass, "fuchs", "hase", presets="1: Schaukel\n20: Bett\n5: Schaukel")
    add_cameras(hass, "reh")  # keine Ctronics-Kamera
    entry = await setup_entry(hass, [make_partner("Frank", 2)])
    flow = await _start_sub(hass, entry, "place")

    step = await hass.config_entries.subentries.async_configure(
        flow["flow_id"],
        {"name": "Bett", "partner": "p_Frank", "cams": ["camera.fuchs", "camera.hase", "camera.reh"]},
    )

    assert step["step_id"] == "user_ptz"
    assert [str(k) for k in step["data_schema"].schema] == ["Fuchs", "Hase"]  # je Ctronics-Kamera ein Feld, nach Namen
    choices = next(iter(step["data_schema"].schema.values())).config["options"]
    assert [(o["value"], o["label"]) for o in choices] == [
        ("0", "Do not move"),
        ("1", "Schaukel (1)"),  # gleiche Namen bekommen die Nummer dazu, wie bei der Ctronics-Kamera selbst
        ("5", "Schaukel (5)"),
        ("20", "Bett"),
    ]

    step = await hass.config_entries.subentries.async_configure(flow["flow_id"], {"Fuchs": "20", "Hase": "0"})
    assert step["step_id"] == "user_begin"
    step = await hass.config_entries.subentries.async_configure(flow["flow_id"], {})
    done = await hass.config_entries.subentries.async_configure(flow["flow_id"], {})

    assert done["type"] is FlowResultType.CREATE_ENTRY
    sub = next(s for s in entry.subentries.values() if s.subentry_type == "place")
    assert sub.data["cam_presets"] == {"camera.fuchs": 20}  # „nicht bewegen“ wird nicht gespeichert


async def test_place_flow_offers_all_positions_when_the_camera_has_no_names(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    _ctronics(entity_registry, hass, "fuchs")
    entry = await setup_entry(hass, [make_partner("Frank", 2)])
    flow = await _start_sub(hass, entry, "place")

    step = await hass.config_entries.subentries.async_configure(
        flow["flow_id"], {"name": "Bett", "partner": "p_Frank", "cams": ["camera.fuchs"]}
    )

    choices = next(iter(step["data_schema"].schema.values())).config["options"]
    assert [o["value"] for o in choices] == [str(n) for n in range(0, 65)]
    assert choices[1]["label"] == "Position 1"


async def test_place_flow_everything_but_name_partner_and_cameras_is_optional(hass: HomeAssistant) -> None:
    add_cameras(hass, "fuchs")
    entry = await setup_entry(hass, [make_partner("Frank", 2)])

    result = await _add_place(hass, entry, {"name": "Dach", "partner": "p_Frank", "cams": ["camera.fuchs"]})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    sub = next(s for s in entry.subentries.values() if s.subentry_type == "place")
    assert sub.data == {
        "name": "Dach",
        "partner": "p_Frank",
        "cams": ["camera.fuchs"],
        "cam_presets": {},
        "lead_seconds": 0,
        "end_seconds": 0,
    }


async def test_place_flow_needs_a_camera(hass: HomeAssistant) -> None:
    entry = await setup_entry(hass, [make_partner("Frank", 2)])

    result = await _add_place(hass, entry, {"name": "Dach", "partner": "p_Frank", "cams": []})

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"cams": "cams_required"}


async def test_place_name_must_be_unique_per_partner(hass: HomeAssistant) -> None:
    add_cameras(hass, "fuchs")
    entry = await setup_entry(
        hass, [make_partner("Frank", 2), make_partner("Tobias", 1)], [make_place("Bett", "Frank", ("camera.fuchs",))]
    )

    same = await _add_place(hass, entry, {"name": " BETT ", "partner": "p_Frank", "cams": ["camera.fuchs"]})
    other = await _add_place(hass, entry, {"name": "Bett", "partner": "p_Tobias", "cams": ["camera.fuchs"]})

    assert same["type"] is FlowResultType.FORM and same["errors"] == {"name": "name_taken"}
    assert other["type"] is FlowResultType.CREATE_ENTRY  # derselbe Name bei einem anderen Spielpartner ist erlaubt


async def test_place_reconfigure_walks_the_steps_and_shows_saved_values(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    _ctronics(entity_registry, hass, "fuchs", presets="1: Schaukel\n20: Bett")
    entry = await setup_entry(
        hass,
        [make_partner("Frank", 2)],
        [
            make_place(
                "Bett",
                cams=("camera.fuchs",),
                cam_presets={"camera.fuchs": 20},
                start_on={"entity_id": ["light.bett"]},
                end_on={"entity_id": ["x.y"]},
            )
        ],
    )
    flow = await _start_sub(hass, entry, "place", source="reconfigure", subentry_id="o_Bett")
    assert flow["step_id"] == "reconfigure"
    assert _shown(flow)["partner"] == "p_Frank" and _shown(flow)["cams"] == ["camera.fuchs"]

    ptz = await hass.config_entries.subentries.async_configure(
        flow["flow_id"], {"name": "Bett", "partner": "p_Frank", "cams": ["camera.fuchs"]}
    )
    assert ptz["step_id"] == "reconfigure_ptz"
    assert _shown(ptz) == {"Fuchs": "20"}  # die gespeicherte Position ist vorgewählt
    begin = await hass.config_entries.subentries.async_configure(flow["flow_id"], {"Fuchs": "1"})
    assert begin["step_id"] == "reconfigure_begin"
    assert _shown(begin)["start_on"] == {"entity_id": ["light.bett"]}
    end = await hass.config_entries.subentries.async_configure(flow["flow_id"], {})
    assert end["step_id"] == "reconfigure_end"
    done = await hass.config_entries.subentries.async_configure(flow["flow_id"], {"end_on": {"entity_id": ["x.y"]}})

    assert done["type"] is FlowResultType.ABORT
    assert done["reason"] == "reconfigure_successful"
    data = entry.subentries["o_Bett"].data
    assert data["cam_presets"] == {"camera.fuchs": 1}
    assert data["end_on"] == {"entity_id": ["x.y"]} and "start_on" not in data
    assert entry.subentries["o_Bett"].title == "Bett (Frank)"


# ------------------------------------------- Saison vorbereiten und Saison beenden
async def _options(hass: HomeAssistant, entry: MockConfigEntry) -> dict:
    return await hass.config_entries.options.async_init(entry.entry_id)


async def _next(hass: HomeAssistant, step: dict, user_input: dict | None = None) -> dict:
    return await hass.config_entries.options.async_configure(step["flow_id"], user_input or {})


def _light_states(hass: HomeAssistant) -> None:
    hass.states.async_set("light.stehlampe", "off", {"friendly_name": "Stehlampe"})
    hass.states.async_set("light.decke", "off", {"friendly_name": "Decke"})


async def test_the_integration_has_a_configure_dialog_for_the_two_season_buttons(hass: HomeAssistant) -> None:
    entry = await setup_entry(hass)

    step = await _options(hass, entry)

    assert step["type"] is FlowResultType.FORM and step["step_id"] == "init"
    assert [str(k) for k in step["data_schema"].schema] == ["off", "on", "lights", "cover", "cover_position"]
    assert _shown(step) == {"cover_position": 50, "lights": []}


async def test_both_pages_are_saved_in_the_options(hass: HomeAssistant) -> None:
    entry = await setup_entry(hass)
    step = await _options(hass, entry)

    step = await _next(
        hass, step, {"on": {"entity_id": ["switch.winde"]}, "cover": "cover.winde", "cover_position": 40}
    )
    assert step["step_id"] == "finish"
    assert _shown(step) == {"cover_position": 0, "lights": []}  # beim Beenden fährt das Rollo standardmäßig zu
    done = await _next(
        hass,
        step,
        {"off": {"entity_id": ["switch.winde"]}, "on": {"label_id": ["spielen_normal"]}, "cover": "cover.rollo"},
    )
    await hass.async_block_till_done()

    assert done["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        "prepare": {"on": {"entity_id": ["switch.winde"]}, "cover": "cover.winde", "cover_position": 40},
        "finish": {
            "off": {"entity_id": ["switch.winde"]},
            "on": {"label_id": ["spielen_normal"]},
            "cover": "cover.rollo",
            "cover_position": 0,
        },
    }
    assert entry.runtime_data.prepare_step.cover == "cover.winde"  # der Eintrag wurde neu geladen
    assert entry.runtime_data.finish_step.on == {"label_id": ["spielen_normal"]}


async def test_lights_get_a_colour_and_a_brightness_on_their_own_page(hass: HomeAssistant) -> None:
    _light_states(hass)
    entry = await setup_entry(hass)
    step = await _options(hass, entry)

    step = await _next(hass, step, {"lights": ["light.stehlampe", "light.decke"]})

    assert step["step_id"] == "prepare_lights"
    assert [str(k) for k in step["data_schema"].schema] == [
        "Stehlampe Colour",
        "Stehlampe Brightness",
        "Decke Colour",
        "Decke Brightness",
    ]
    step = await _next(hass, step, {"Stehlampe Colour": [255, 100, 0], "Stehlampe Brightness": 40})
    assert step["step_id"] == "finish"  # ohne Lichter bei „Saison beenden“ folgt keine weitere Seite
    done = await _next(hass, step)
    await hass.async_block_till_done()

    assert done["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["prepare"]["lights"] == {
        "light.stehlampe": {"rgb": [255, 100, 0], "brightness": 40},
        "light.decke": {},
    }
    assert entry.options["finish"] == {}
    assert entry.runtime_data.prepare_step.lights["light.decke"] == {}


async def test_the_end_page_has_its_own_lights(hass: HomeAssistant) -> None:
    _light_states(hass)
    entry = await setup_entry(hass)
    step = await _next(hass, await _options(hass, entry))

    step = await _next(hass, step, {"lights": ["light.decke"]})
    assert step["step_id"] == "finish_lights"
    done = await _next(hass, step, {"Decke Colour": [0, 0, 255]})
    await hass.async_block_till_done()

    assert done["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["finish"]["lights"] == {"light.decke": {"rgb": [0, 0, 255]}}
    assert "lights" not in entry.options["prepare"]


async def test_the_dialog_shows_what_is_saved_and_keeps_colours_of_lights_that_stay(hass: HomeAssistant) -> None:
    _light_states(hass)
    saved = {"on": {"entity_id": ["switch.winde"]}, "cover": "cover.winde", "cover_position": 40}
    lights = {"light.stehlampe": {"rgb": [1, 2, 3], "brightness": 40}}
    entry = await setup_entry(hass, options={"prepare": {**saved, "lights": lights}})
    step = await _options(hass, entry)
    assert _shown(step) == {
        "on": saved["on"],
        "cover": "cover.winde",
        "cover_position": 40,
        "lights": ["light.stehlampe"],
    }

    step = await _next(hass, step, {**saved, "lights": ["light.stehlampe", "light.decke"]})

    assert _shown(step) == {"Stehlampe Colour": [1, 2, 3], "Stehlampe Brightness": 40}  # Decke hat noch nichts
    step = await _next(hass, step, {"Stehlampe Colour": [1, 2, 3]})  # Helligkeit leer gelassen
    done = await _next(hass, step)
    await hass.async_block_till_done()

    assert done["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["prepare"]["lights"] == {"light.stehlampe": {"rgb": [1, 2, 3]}, "light.decke": {}}
