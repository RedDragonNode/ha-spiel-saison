"""Ablauf einer Aufnahme: Beginn-Schritt, Vorlauf, Recorder, Zähler, Ende-Schritt, Neustart und Verlust."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_capture_events, async_fire_time_changed

from custom_components.spiel_saison.const import (
    DOMAIN,
    EVENT_SEASON,
    EVENT_SEASON_ENDED,
    EVENT_STARTED,
    EVENT_STOPPED,
    POLL_SECONDS,
)
from custom_components.spiel_saison.recorder_client import RecorderError

from .conftest import FakeRecorder, add_cameras, make_partner, make_place, setup_entry

FRANK = "p_Frank"
TOBIAS = "p_Tobias"
BED = {"entity_id": ["light.bett"]}
SWING = {"entity_id": ["light.schaukel"]}
FINISH = {  # Optionen von „Saison beenden“
    "off": {"entity_id": ["switch.winde"]},
    "on": {"entity_id": ["light.zimmer"]},
    "lights": {"light.stehlampe": {"rgb": [255, 100, 0], "brightness": 40}, "light.decke": {}},
    "cover": "cover.rollo",
    "cover_position": 100,
}


def _summary(calls: list[ServiceCall]) -> list[tuple[str, str, dict]]:
    return [(c.domain, c.service, dict(c.data)) for c in calls]


@pytest.fixture
def cams(hass: HomeAssistant) -> None:
    add_cameras(hass, "fuchs", "hase")


async def _setup(hass: HomeAssistant, places: list[dict] | None = None, *, options: dict | None = None, **frank):
    """Frank (Kameras fuchs und hase) und Tobias (nur hase), jeder mit einem Ort. `places` sind die Orte von Frank."""
    return await setup_entry(
        hass,
        [make_partner("Frank", 2, **frank), make_partner("Tobias", 1)],
        [
            *(places if places is not None else [make_place("Spielzimmer")]),
            make_place("Spielzimmer", "Tobias", ("camera.hase",)),
        ],
        options=options,
    )


async def _start(hass: HomeAssistant, partner: str = "Frank", **data) -> dict:
    result = await hass.services.async_call(
        DOMAIN, "start_recording", {"partner": partner, **data}, blocking=True, return_response=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    return result


async def _stop(hass: HomeAssistant, partner: str = "Frank") -> dict:
    result = await hass.services.async_call(
        DOMAIN, "stop_recording", {"partner": partner}, blocking=True, return_response=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    return result


async def _prepare(hass: HomeAssistant, partner: str = "Frank") -> dict:
    return await hass.services.async_call(DOMAIN, "prepare", {"partner": partner}, blocking=True, return_response=True)


async def _end_season(hass: HomeAssistant, partner: str = "Frank", **data) -> dict:
    return await hass.services.async_call(
        DOMAIN, "end_season", {"partner": partner, **data}, blocking=True, return_response=True
    )


def _season_folder(partner: str, number: int, season: int) -> list[str]:
    """Ordner der Saison in den Tests (Basis-Ordner „Spielen“, Muster wie im Standard, Saison beginnt heute)."""
    return [
        "Spielen",
        f"Spielen mit {partner} #{number:02d}",
        f"Saison {season:02d} vom {dt_util.now():%d,%m,%Y}",
    ]


def _days_ago(days: int) -> str:
    return (dt_util.now().date() - timedelta(days=days)).isoformat()


def _entry_id(hass: HomeAssistant) -> str:
    return hass.config_entries.async_entries(DOMAIN)[0].entry_id


def _runtime(entry):
    return entry.runtime_data


async def test_start_runs_the_steps_in_order_and_records(hass, stream, cams, service_calls) -> None:
    place = make_place(
        "Bett", cam_presets={"camera.fuchs": 10}, start_off={"entity_id": ["light.schaukel"]}, start_on=BED
    )
    entry = await _setup(hass, [place], announce_entity="notify.echo", announce_start="Die Aufnahme startet")
    started = async_capture_events(hass, EVENT_STARTED)

    result = await _start(hass)

    assert result["started"] is True
    assert _summary(service_calls) == [
        ("homeassistant", "turn_off", {"entity_id": ["light.schaukel"]}),
        ("homeassistant", "turn_on", {"entity_id": ["light.bett"]}),
        ("notify", "send_message", {"entity_id": "notify.echo", "message": "Die Aufnahme startet"}),
    ]  # camera.fuchs/hase gehören keiner Ctronics-Integration, also keine PTZ-Fahrt
    (call,) = FakeRecorder.calls
    action, path, cams_sent, session, profile = call
    assert action == "start"
    assert path == ["Spielen", "Spielen mit Frank #02", f"Saison 01 vom {dt_util.now():%d,%m,%Y}"]
    assert [c["stem"] for c in cams_sent] == [  # bei „Cam ID“ steht der Name der Kamera, nicht die Nummer
        f"#02.01.01.Cam ID.Fuchs {dt_util.now():%d.%m.%Y}",
        f"#02.01.01.Cam ID.Hase {dt_util.now():%d.%m.%Y}",
    ]
    assert [c["id"] for c in cams_sent] == ["camera.fuchs", "camera.hase"]
    assert cams_sent[0]["url"] == stream.return_value
    assert session == "Frank Saison 01 Aufnahme 01"
    assert profile == FRANK
    assert _runtime(entry).phase(FRANK) == "recording"
    state = _runtime(entry).states[FRANK]
    assert (state.count_season, state.count_total) == (1, 1)
    (event,) = started
    assert event.data["partner"] == "Frank" and event.data["place"] == "Bett" and event.data["recording"] == 1


async def test_start_moves_ctronics_cameras_to_the_preset(hass, stream, service_calls, entity_registry) -> None:
    entity_registry.async_get_or_create("camera", "ctronics_cam", "abc", suggested_object_id="fuchs")
    entity_registry.async_get_or_create("camera", "other", "def", suggested_object_id="hase")
    add_cameras(hass, "fuchs", "hase")
    await _setup(hass, [make_place("Bett", cam_presets={"camera.fuchs": 10, "camera.hase": 10})])

    await _start(hass)

    assert _summary(service_calls) == [  # camera.hase gehört nicht zu Ctronics und bleibt, wo sie ist
        ("ctronics_cam", "ptz_goto_preset", {"entity_id": ["camera.fuchs"], "preset": 10})
    ]


async def test_every_ctronics_camera_moves_to_its_own_position(hass, stream, service_calls, entity_registry) -> None:
    entity_registry.async_get_or_create("camera", "ctronics_cam", "abc", suggested_object_id="fuchs")
    entity_registry.async_get_or_create("camera", "ctronics_cam", "def", suggested_object_id="hase")
    add_cameras(hass, "fuchs", "hase", "reh")
    await setup_entry(
        hass,
        [make_partner("Frank", 2)],
        [
            make_place(
                "Bett",
                cams=("camera.fuchs", "camera.hase", "camera.reh"),
                cam_presets={"camera.fuchs": 20, "camera.hase": 1},
            )
        ],
    )

    await _start(hass)

    assert _summary(service_calls) == [
        ("ctronics_cam", "ptz_goto_preset", {"entity_id": ["camera.fuchs"], "preset": 20}),
        ("ctronics_cam", "ptz_goto_preset", {"entity_id": ["camera.hase"], "preset": 1}),
    ]


async def test_stop_records_the_duration_and_runs_the_end_step(hass, stream, cams, service_calls) -> None:
    place = make_place("Bett", end_off=BED, end_on={"entity_id": ["switch.schrank"]})
    entry = await _setup(hass, [place], announce_entity="notify.echo", announce_end="Ende")
    stopped = async_capture_events(hass, EVENT_STOPPED)
    await _start(hass)
    service_calls.clear()
    _runtime(entry).runs[FRANK]["start"] = (dt_util.utcnow() - timedelta(minutes=30)).isoformat()

    result = await _stop(hass)

    assert result == {"stopped": True}
    assert FakeRecorder.calls[-1] == ("stop", FRANK)
    assert _summary(service_calls) == [
        ("homeassistant", "turn_off", BED),
        ("homeassistant", "turn_on", {"entity_id": ["switch.schrank"]}),
        ("notify", "send_message", {"entity_id": "notify.echo", "message": "Ende"}),
    ]
    runtime = _runtime(entry)
    assert runtime.phase(FRANK) == "idle"
    state = runtime.states[FRANK]
    assert 1790 < state.seconds_season < 1810
    assert state.longest_season == state.seconds_total == state.last_seconds == state.seconds_season
    (event,) = stopped
    assert event.data["reason"] == "manual" and 1790 < event.data["duration_s"] < 1810


async def test_a_plain_place_just_records(hass, stream, cams, service_calls) -> None:
    entry = await _setup(hass)

    result = await _start(hass)

    assert result["started"] is True and result["place"] == "Spielzimmer"
    assert result["cams"] == ["Fuchs", "Hase"]
    assert service_calls == []
    assert _runtime(entry).phase(FRANK) == "recording"


async def test_a_partner_without_a_place_cannot_start(hass, stream, cams) -> None:
    await setup_entry(hass, [make_partner("Frank", 2)], [])

    with pytest.raises(ServiceValidationError) as err:
        await _start(hass)

    assert err.value.translation_key == "no_place"
    assert err.value.translation_placeholders == {"partner": "Frank"}
    assert FakeRecorder.calls == []


async def test_second_start_is_ignored_and_a_stop_without_recording_too(hass, stream, cams) -> None:
    await _setup(hass)
    await _start(hass)
    assert (await _start(hass)) == {"started": False, "reason": "already_recording"}
    assert (await _stop(hass))["stopped"] is True
    assert (await _stop(hass)) == {"stopped": False, "reason": "not_recording"}


async def test_recorder_refusing_the_start_restores_the_place(hass, stream, cams, service_calls) -> None:
    entry = await _setup(hass, [make_place("Bett", end_on=BED)])
    FakeRecorder.start_fail = RecorderError("Recorder: Zu wenig freier Speicher")

    await _start(hass)

    runtime = _runtime(entry)
    assert runtime.phase(FRANK) == "idle"
    assert runtime.states[FRANK].count_total == 0
    assert runtime.status == "error" and "Zu wenig" in runtime.message
    assert _summary(service_calls)[-1] == ("homeassistant", "turn_on", BED)  # Ende-Schritt lief trotzdem


async def test_unreachable_recorder_raises_on_start(hass, stream, cams) -> None:
    entry = await _setup(hass)
    FakeRecorder.fail = RecorderError("Recorder-App nicht erreichbar (test)")

    with pytest.raises(RecorderError):
        await _start(hass)

    assert _runtime(entry).rec_online is False and _runtime(entry).phase(FRANK) == "idle"


async def test_no_usable_camera_is_an_error(hass, stream) -> None:
    await _setup(hass)  # die Kameras gibt es nicht

    with pytest.raises(ServiceValidationError) as err:
        await _start(hass)

    assert err.value.translation_key == "no_cam_available"


async def test_a_partner_whose_only_camera_is_busy_cannot_start(hass, stream, cams) -> None:
    await _setup(hass)
    await _start(hass, "Frank")

    with pytest.raises(ServiceValidationError) as err:  # camera.hase nimmt schon bei Frank auf
        await _start(hass, "Tobias")

    assert err.value.translation_key == "no_cam_available"
    assert "camera.hase (busy)" in err.value.translation_placeholders["details"]


async def test_a_busy_camera_blocks_only_that_partner(hass, stream, cams) -> None:
    await setup_entry(
        hass,
        [make_partner("Frank", 2), make_partner("Tobias", 1)],
        [make_place("Bett", "Frank", ("camera.fuchs",)), make_place("Bett", "Tobias", ("camera.fuchs", "camera.hase"))],
    )
    await _start(hass, "Frank")

    result = await _start(hass, "Tobias")

    assert result["cams"] == ["Hase"]
    assert result["skipped"] == [{"cam": "camera.fuchs", "reason": "busy"}]


async def test_unknown_partner_and_place(hass, stream, cams) -> None:
    await _setup(hass, [make_place("Bett")])
    with pytest.raises(ServiceValidationError) as err:
        await _start(hass, "Niemand")
    assert err.value.translation_key == "partner_not_found"
    with pytest.raises(ServiceValidationError) as err:
        await _start(hass, "Frank", place="Dach")
    assert err.value.translation_key == "place_not_found"


async def test_partner_and_place_are_found_by_a_forgiving_name(hass, stream, cams) -> None:
    await _setup(hass, [make_place("Bett")])

    result = await _start(hass, "  frank ", place="BETT")

    assert result["partner"] == "Frank" and result["place"] == "Bett"


async def test_a_place_belongs_to_its_partner(hass, stream, cams, service_calls) -> None:
    entry = await setup_entry(
        hass,
        [make_partner("Frank", 2), make_partner("Tobias", 1)],
        [
            make_place("Bett", "Frank", start_on=BED),
            make_place("Bett", "Tobias", ("camera.hase",), start_on=SWING),
        ],
    )

    result = await _start(hass, "Tobias", place="Bett")

    assert result["cams"] == ["Hase"]
    assert [dict(c.data) for c in service_calls] == [SWING]  # der Ort von Tobias, nicht der von Frank
    assert set(_runtime(entry).places_of("p_Tobias")) == {"o_Bett_Tobias"}


async def test_a_place_of_another_partner_is_not_found(hass, stream, cams) -> None:
    await _setup(hass, [make_place("Bett")])  # Tobias hat nur „Spielzimmer“

    with pytest.raises(ServiceValidationError) as err:
        await _start(hass, "Tobias", place="Bett")

    assert err.value.translation_key == "place_not_found"


async def test_stop_during_the_lead_time_cancels_the_start(hass, stream, cams, service_calls) -> None:
    entry = await _setup(
        hass, [make_place("Bett", lead_seconds=3600, start_on=BED, end_on={"entity_id": ["switch.schrank"]})]
    )

    result = await hass.services.async_call(
        DOMAIN, "start_recording", {"partner": "Frank"}, blocking=True, return_response=True
    )
    await hass.async_block_till_done()
    assert result["started"] is True and _runtime(entry).phase(FRANK) == "starting"
    assert FakeRecorder.calls == []

    await _stop(hass)

    runtime = _runtime(entry)
    assert runtime.phase(FRANK) == "idle" and runtime.states[FRANK].count_total == 0
    assert ("stop", FRANK) in FakeRecorder.calls
    assert _summary(service_calls)[-1] == ("homeassistant", "turn_on", {"entity_id": ["switch.schrank"]})


async def test_prepare_turns_things_on_and_moves_the_cover(hass, stream, cams, service_calls) -> None:
    options = {"prepare": {"on": {"entity_id": ["switch.winde"]}, "cover": "cover.winde"}}
    await _setup(hass, [make_place("Schaukel")], options=options)

    result = await hass.services.async_call(
        DOMAIN, "prepare", {"partner": "Frank"}, blocking=True, return_response=True
    )

    assert result == {
        "partner": "Frank",
        "place": "Schaukel",
        "season": 1,
        "new_season": False,
        "folder": "/".join(_season_folder("Frank", 2, 1)),
        "folder_created": True,
    }
    assert _summary(service_calls) == [
        ("homeassistant", "turn_on", {"entity_id": ["switch.winde"]}),
        ("cover", "set_cover_position", {"entity_id": "cover.winde", "position": 50}),
    ]


async def test_prepare_begins_the_season_of_the_day_once(hass, stream, cams) -> None:
    entry = await _setup(hass, [make_place("Schaukel")])
    state = _runtime(entry).states[FRANK]
    state.season_start = _days_ago(1)
    season_events = async_capture_events(hass, EVENT_SEASON)

    first = await _prepare(hass)
    again = await _prepare(hass)

    assert (first["season"], first["new_season"]) == (2, True)
    assert (again["season"], again["new_season"]) == (2, False)
    assert state.season == 2 and state.season_start == _days_ago(0)
    assert state.seasons["1"]["start"] == _days_ago(1)
    (event,) = season_events
    assert event.data["season"] == 2
    await _start(hass)
    start = next(call for call in FakeRecorder.calls if call[0] == "start")
    assert start[1][2] == f"Saison 02 vom {dt_util.now():%d,%m,%Y}"


async def test_prepare_creates_the_folder_of_the_current_season_once(hass, stream, cams) -> None:
    entry = await _setup(hass, [make_place("Schaukel")])
    _runtime(entry).states[FRANK].season_start = _days_ago(1)
    folder = _season_folder("Frank", 2, 2)

    first = await _prepare(hass)
    again = await _prepare(hass)

    assert (first["folder"], first["folder_created"]) == ("/".join(folder), True)
    assert again["folder_created"] is False  # gibt es jetzt schon
    assert [call for call in FakeRecorder.calls if call[0] == "mkdir"] == [("mkdir", folder)]


async def test_prepare_keeps_an_existing_season_folder(hass, stream, cams) -> None:
    await _setup(hass, [make_place("Schaukel")])
    folder = _season_folder("Frank", 2, 1)
    FakeRecorder.dirs.update(tuple(folder[: i + 1]) for i in range(len(folder)))

    result = await _prepare(hass)

    assert result["folder_created"] is False
    assert [call for call in FakeRecorder.calls if call[0] == "mkdir"] == []


async def test_prepare_goes_on_when_the_recorder_cannot_be_asked(hass, stream, cams, service_calls) -> None:
    entry = await _setup(hass, [make_place("Schaukel")], options={"prepare": {"on": BED}})
    FakeRecorder.fail = RecorderError("Recorder-App nicht erreichbar (test)")

    result = await _prepare(hass)

    assert result["folder_created"] is None
    assert _summary(service_calls) == [("homeassistant", "turn_on", BED)]  # das Licht geht trotzdem an
    assert "nicht erreichbar" in _runtime(entry).message


async def test_prepare_leaves_the_season_alone_while_recording(hass, stream, cams) -> None:
    entry = await _setup(hass, [make_place("Schaukel")])
    await _start(hass)
    _runtime(entry).states[FRANK].season_start = _days_ago(1)

    result = await _prepare(hass)

    assert (result["season"], result["new_season"]) == (1, False)
    assert _runtime(entry).states[FRANK].count_season == 1


async def test_prepare_works_without_a_place(hass, stream, cams, service_calls) -> None:
    await setup_entry(hass, [make_partner("Frank", 2)], [], options={"prepare": {"on": BED}})

    result = await _prepare(hass)

    assert (result["place"], result["folder_created"]) == ("", True)
    assert _summary(service_calls) == [("homeassistant", "turn_on", BED)]


async def test_the_cover_positions_default_to_50_when_preparing_and_0_when_ending(hass, cams) -> None:
    entry = await _setup(hass, options={"prepare": {"cover": "cover.a"}, "finish": {"cover": "cover.b"}})

    runtime = _runtime(entry)

    assert (runtime.prepare_step.cover_position, runtime.finish_step.cover_position) == (50, 0)


async def test_prepare_moves_the_winch_only_to_the_position_of_configure(hass, stream, cams, service_calls) -> None:
    options = {"prepare": {"cover": "cover.winde", "cover_position": 50}}
    await _setup(hass, [make_place("Schaukel", cover_position=30)], options=options)

    await hass.services.async_call(DOMAIN, "prepare", {"partner": "Frank", "place": "Schaukel"}, blocking=True)

    # die Position des Ortes (30) gilt erst, wenn man die Aufnahme startet
    assert _summary(service_calls) == [("cover", "set_cover_position", {"entity_id": "cover.winde", "position": 50})]


async def test_the_recording_start_moves_the_winch_to_the_position_of_the_place(
    hass, stream, cams, service_calls
) -> None:
    options = {"prepare": {"cover": "cover.winde", "cover_position": 50}}
    entry = await _setup(
        hass,
        [make_place("Schaukel", cover_position=30), make_place("Bett", cover_position=0), make_place("Dach")],
        options=options,
    )

    for place, expected in (("Schaukel", [30]), ("Bett", [0]), ("Dach", [])):  # 0 ist eine Position
        service_calls.clear()
        await _start(hass, place=place)
        await _stop(hass)
        positions = [c.data["position"] for c in service_calls if c.service == "set_cover_position"]
        assert positions == expected, place

    assert _runtime(entry).places["o_Dach"].cover_position is None


async def test_the_winch_moves_first_when_a_recording_starts(hass, stream, cams, service_calls) -> None:
    options = {"prepare": {"cover": "cover.winde"}}
    await _setup(hass, [make_place("Schaukel", cover_position=25, start_on=BED)], options=options)

    await _start(hass)

    assert _summary(service_calls) == [
        ("cover", "set_cover_position", {"entity_id": "cover.winde", "position": 25}),
        ("homeassistant", "turn_on", BED),
    ]


async def test_the_selected_place_decides_the_winch_position(hass, stream, cams, service_calls) -> None:
    options = {"prepare": {"cover": "cover.winde"}}
    await _setup(hass, [make_place("Schaukel"), make_place("Bett", cover_position=10)], options=options)
    select = er.async_get(hass).async_get_entity_id("select", DOMAIN, f"{_entry_id(hass)}_{FRANK}_place")
    await hass.services.async_call("select", "select_option", {"entity_id": select, "option": "Bett"}, blocking=True)

    await _start(hass)

    assert _summary(service_calls) == [("cover", "set_cover_position", {"entity_id": "cover.winde", "position": 10})]


async def test_the_recording_start_leaves_the_winch_alone_without_a_winch(hass, stream, cams, service_calls) -> None:
    await _setup(hass, [make_place("Schaukel", cover_position=30)])  # bei Konfigurieren ist keine Winde gewählt

    await _start(hass)

    assert [c for c in service_calls if c.service == "set_cover_position"] == []


async def test_the_colour_of_a_light_is_sent_a_second_time_once_the_light_is_on(hass, cams, service_calls) -> None:
    lights = {"light.a": {"rgb": [1, 2, 3], "brightness": 20}, "light.b": {"brightness": 70}}
    await _setup(hass, options={"finish": {"lights": lights}})

    await _end_season(hass)

    assert _summary(service_calls) == [
        ("light", "turn_on", {"entity_id": "light.a", "brightness_pct": 20, "rgb_color": [1, 2, 3]}),
        ("light", "turn_on", {"entity_id": "light.b", "brightness_pct": 70}),
        ("light", "turn_on", {"entity_id": "light.a", "brightness_pct": 20, "rgb_color": [1, 2, 3]}),  # nur mit Farbe
    ]


async def test_end_season_moves_the_cover_first_and_switches_the_winch_off_when_the_end_switch_is_on(
    hass, cams, service_calls
) -> None:
    hass.states.async_set("binary_sensor.oben", "on")  # die Winde steht schon oben
    options = {
        "finish": {
            "off": BED,
            "cover": "cover.winde",
            "after_off": {"entity_id": ["switch.winde"]},
            "end_switch": "binary_sensor.oben",
        }
    }
    await _setup(hass, options=options)

    await _end_season(hass)

    assert _summary(service_calls) == [
        ("cover", "set_cover_position", {"entity_id": "cover.winde", "position": 0}),  # zuerst die Winde fahren
        ("homeassistant", "turn_off", BED),
        ("homeassistant", "turn_off", {"entity_id": ["switch.winde"]}),  # zuletzt der Strom der Winde
    ]


async def test_the_winch_stays_on_until_the_end_switch_comes_on(hass, cams, service_calls) -> None:
    hass.states.async_set("binary_sensor.oben", "off")
    options = {
        "finish": {
            "cover": "cover.winde",
            "after_off": {"entity_id": ["switch.winde"]},
            "end_switch": "binary_sensor.oben",
            "wait_seconds": 30,
        }
    }
    await _setup(hass, options=options)

    task = hass.async_create_background_task(_end_season(hass), "test end season")
    await asyncio.sleep(0.05)
    assert [(c.domain, c.service) for c in service_calls] == [("cover", "set_cover_position")]  # noch nicht aus
    assert not task.done()

    hass.states.async_set("binary_sensor.oben", "on")
    result = await task

    assert result["partner"] == "Frank"
    assert _summary(service_calls)[-1] == ("homeassistant", "turn_off", {"entity_id": ["switch.winde"]})


async def test_the_winch_is_not_switched_off_when_the_end_switch_never_comes(hass, cams, service_calls) -> None:
    hass.states.async_set("binary_sensor.oben", "off")
    options = {
        "finish": {
            "after_off": {"entity_id": ["switch.winde"]},
            "end_switch": "binary_sensor.oben",
            "wait_seconds": 0,
        }
    }
    entry = await _setup(hass, options=options)
    events = async_capture_events(hass, EVENT_SEASON_ENDED)

    with pytest.raises(HomeAssistantError) as err:
        await _end_season(hass)
    await hass.async_block_till_done()

    assert err.value.translation_key == "end_switch_timeout"
    assert service_calls == []  # die Winde bleibt an
    assert events == []
    assert _runtime(entry).status == "error" and "binary_sensor.oben" in _runtime(entry).message


async def test_end_season_waits_without_an_end_switch_for_nothing(hass, cams, service_calls) -> None:
    await _setup(hass, options={"finish": {"after_off": {"entity_id": ["switch.winde"]}}})

    await _end_season(hass)

    assert _summary(service_calls) == [("homeassistant", "turn_off", {"entity_id": ["switch.winde"]})]


async def test_end_season_runs_the_settings_of_the_end_page(hass, cams, service_calls) -> None:
    entry = await _setup(hass, options={"finish": FINISH})
    events = async_capture_events(hass, EVENT_SEASON_ENDED)
    state = _runtime(entry).states[FRANK]
    state.count_season, state.seconds_season = 3, 200.4

    result = await _end_season(hass)
    await hass.async_block_till_done()

    assert result == {"partner": "Frank", "season": 1, "recordings": 3, "seconds": 200}
    assert _summary(service_calls) == [
        ("cover", "set_cover_position", {"entity_id": "cover.rollo", "position": 100}),  # beim Beenden zuerst
        ("homeassistant", "turn_off", {"entity_id": ["switch.winde"]}),
        ("homeassistant", "turn_on", {"entity_id": ["light.zimmer"]}),
        ("light", "turn_on", {"entity_id": "light.stehlampe", "brightness_pct": 40, "rgb_color": [255, 100, 0]}),
        ("light", "turn_on", {"entity_id": "light.decke"}),  # ohne Farbe und Helligkeit: so, wie das Licht zuletzt war
        ("light", "turn_on", {"entity_id": "light.stehlampe", "brightness_pct": 40, "rgb_color": [255, 100, 0]}),
    ]
    assert [e.data for e in events] == [{**result, "partner_id": FRANK, "nickname": "", "start": state.season_start}]
    assert state.season == 1 and state.count_season == 3  # beenden zählt nichts um


async def test_end_season_without_settings_still_ends_it(hass, cams, service_calls) -> None:
    await _setup(hass)
    events = async_capture_events(hass, EVENT_SEASON_ENDED)

    await _end_season(hass)
    await hass.async_block_till_done()

    assert service_calls == [] and len(events) == 1


async def test_end_season_works_without_a_place(hass, cams, service_calls) -> None:
    await setup_entry(hass, [make_partner("Frank", 2)], [], options={"finish": {"off": BED}})

    result = await _end_season(hass)

    assert result["partner"] == "Frank"
    assert _summary(service_calls) == [("homeassistant", "turn_off", BED)]


async def test_the_buttons_use_only_their_own_settings(hass, stream, cams, service_calls) -> None:
    options = {
        "prepare": {"on": {"entity_id": ["switch.vorher"]}, "cover": "cover.winde"},
        "finish": FINISH,
    }
    await _setup(hass, [make_place("Schaukel", start_on=SWING, end_off=SWING)], options=options)

    await _prepare(hass)
    assert _summary(service_calls) == [
        ("homeassistant", "turn_on", {"entity_id": ["switch.vorher"]}),
        ("cover", "set_cover_position", {"entity_id": "cover.winde", "position": 50}),
    ]

    service_calls.clear()
    await _start(hass)
    await _stop(hass)
    assert _summary(service_calls) == [  # Beginn und Ende der Aufnahme kennen weder „vorbereiten“ noch „beenden“
        ("homeassistant", "turn_on", SWING),
        ("homeassistant", "turn_off", SWING),
    ]

    service_calls.clear()
    await _end_season(hass)
    assert [(c.domain, c.service) for c in service_calls] == [
        ("cover", "set_cover_position"),
        ("homeassistant", "turn_off"),
        ("homeassistant", "turn_on"),
        ("light", "turn_on"),
        ("light", "turn_on"),
        ("light", "turn_on"),  # die Farbe folgt noch einmal
    ]
    assert "switch.vorher" not in str(_summary(service_calls)) and "light.schaukel" not in str(_summary(service_calls))


async def test_end_season_is_refused_while_recording(hass, stream, cams, service_calls) -> None:
    entry = await _setup(hass, options={"finish": FINISH})
    await _start(hass)
    service_calls.clear()

    with pytest.raises(ServiceValidationError) as err:
        await _end_season(hass)

    assert err.value.translation_key == "season_end_recording_running"
    assert service_calls == [] and _runtime(entry).phase(FRANK) == "recording"


async def test_a_failing_service_does_not_stop_the_recording(hass, stream, cams) -> None:
    entry = await _setup(hass, [make_place("Bett", start_on=BED)])  # homeassistant.turn_on gibt es hier nicht

    await _start(hass)

    assert _runtime(entry).phase(FRANK) == "recording"


async def test_new_season_archives_and_resets(hass, stream, cams) -> None:
    entry = await _setup(hass)
    season_events = async_capture_events(hass, EVENT_SEASON)
    await _start(hass)
    await _stop(hass)
    state = _runtime(entry).states[FRANK]
    state.seconds_season = 7200
    state.season_start = _days_ago(1)

    result = await hass.services.async_call(
        DOMAIN, "new_season", {"partner": "Frank"}, blocking=True, return_response=True
    )

    assert result == {"partner": "Frank", "season": 2, "started": True}
    assert state.season == 2 and state.count_season == 0 and state.seconds_season == 0
    assert state.count_total == 1
    assert state.seasons["1"]["recordings"] == 1 and state.seasons["1"]["seconds"] == 7200
    (event,) = season_events
    assert event.data["season"] == 2
    FakeRecorder.calls.clear()
    await _start(hass)
    assert FakeRecorder.calls[0][1][2].startswith("Saison 02 vom ")
    assert FakeRecorder.calls[0][3] == "Frank Saison 02 Aufnahme 01"


async def test_only_one_season_begins_per_day(hass, stream, cams) -> None:
    entry = await _setup(hass)
    state = _runtime(entry).states[FRANK]  # die erste Saison beginnt mit dem Einrichten, also heute
    season_events = async_capture_events(hass, EVENT_SEASON)
    await _start(hass)
    await _stop(hass)

    result = await hass.services.async_call(
        DOMAIN, "new_season", {"partner": "Frank"}, blocking=True, return_response=True
    )

    assert result == {"partner": "Frank", "season": 1, "started": False, "reason": "already_today"}
    assert state.season == 1 and state.count_season == 1 and season_events == []


async def test_new_season_is_refused_while_recording(hass, stream, cams) -> None:
    await _setup(hass)
    await _start(hass)

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "new_season", {"partner": "Frank"}, blocking=True)

    assert err.value.translation_key == "recording_running"


async def test_a_running_recording_is_taken_over_after_a_reload(hass, stream, cams) -> None:
    entry = await _setup(hass, [make_place("Bett")])
    await _start(hass, place="Bett")
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    runtime = entry.runtime_data
    assert runtime.phase(FRANK) == "recording"
    assert runtime.runs[FRANK]["place"] == "o_Bett"  # aus dem Speicher
    assert runtime.states[FRANK].count_total == 1


async def test_a_recording_lost_by_the_recorder_is_finished_and_reported(hass, stream, cams, service_calls) -> None:
    entry = await _setup(hass, [make_place("Bett", end_on=BED)])
    stopped = async_capture_events(hass, EVENT_STOPPED)
    await _start(hass)
    FakeRecorder.remote_profiles[FRANK] = {"aufnahme": False, "stop_grund": "speicher_voll"}
    service_calls.clear()

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=POLL_SECONDS + 1))
    await hass.async_block_till_done(wait_background_tasks=True)

    assert _runtime(entry).phase(FRANK) == "idle"
    (event,) = stopped
    assert event.data["reason"] == "speicher_voll"
    assert _summary(service_calls) == [("homeassistant", "turn_on", BED)]
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"recording_lost_{FRANK}")
    assert issue and issue.translation_placeholders == {"partner": "Frank", "reason": "speicher_voll"}


async def test_a_recording_the_app_still_runs_is_adopted_on_poll(hass, stream, cams) -> None:
    entry = await _setup(hass)
    FakeRecorder.remote_profiles[FRANK] = {"aufnahme": True, "kameras": {"camera.fuchs": {"status": "nimmt auf"}}}

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=POLL_SECONDS + 1))
    await hass.async_block_till_done()

    assert _runtime(entry).phase(FRANK) == "recording"
    assert _runtime(entry).runs[FRANK]["cams"] == ["camera.fuchs"]


async def test_service_without_a_loaded_entry(hass) -> None:
    await setup_entry(hass)
    assert await hass.config_entries.async_unload(next(iter(hass.config_entries.async_entries(DOMAIN))).entry_id)

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "stop_recording", {"partner": "Frank"}, blocking=True)

    assert err.value.translation_key == "not_loaded"


async def test_a_renamed_camera_entity_is_followed(hass, entity_registry) -> None:
    entity_registry.async_get_or_create("camera", "ctronics_cam", "abc", suggested_object_id="fuchs")
    add_cameras(hass, "fuchs")
    entry = await setup_entry(
        hass,
        [make_partner("Frank", 2)],
        [make_place("Bett", cams=("camera.fuchs",), cam_presets={"camera.fuchs": 3})],
    )

    entity_registry.async_update_entity("camera.fuchs", new_entity_id="camera.fuchs_neu")
    await hass.async_block_till_done()

    data = entry.subentries["o_Bett"].data
    assert data["cams"] == ["camera.fuchs_neu"]
    assert data["cam_presets"] == {"camera.fuchs_neu": 3}


async def test_announcements_fill_in_the_placeholders(hass, stream, cams, service_calls) -> None:
    await setup_entry(
        hass,
        [
            make_partner(
                "Frank",
                2,
                nickname="Franky",
                announce_entity="notify.echo",
                announce_start="Hallo {nickname}, {partner} Nummer {nr}, Saison {saison}, {ort}!",
                announce_end="Tschüss {nickname}",
            )
        ],
        [make_place("Bett")],
    )

    await _start(hass)
    await _stop(hass)

    assert [c.data["message"] for c in service_calls if c.domain == "notify"] == [
        "Hallo Franky, Frank Nummer 02, Saison 01, Bett!",
        "Tschüss Franky",
    ]


async def test_the_nickname_falls_back_to_the_name(hass, stream, cams, service_calls) -> None:
    await setup_entry(
        hass,
        [make_partner("Frank", 2, announce_entity="notify.echo", announce_start="Hallo {nickname}")],
        [make_place("Bett")],
    )

    await _start(hass)

    assert [c.data["message"] for c in service_calls] == ["Hallo Frank"]


async def test_events_carry_the_nickname(hass, stream, cams) -> None:
    await setup_entry(hass, [make_partner("Frank", 2, nickname="Franky")], [make_place("Bett")])
    started = async_capture_events(hass, EVENT_STARTED)
    stopped = async_capture_events(hass, EVENT_STOPPED)

    await _start(hass)
    await _stop(hass)

    assert started[0].data["nickname"] == "Franky" and stopped[0].data["nickname"] == "Franky"
