"""Ablauf einer Aufnahme: Beginn-Schritt, Vorlauf, Recorder, Zähler, Ende-Schritt, Neustart und Verlust."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from homeassistant.core import Event, HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events, async_fire_time_changed

from custom_components.spiel_saison import logic
from custom_components.spiel_saison.const import (
    DOMAIN,
    EVENT_SEASON,
    EVENT_SEASON_ENDED,
    EVENT_STARTED,
    EVENT_STOPPED,
    POLL_SECONDS,
)
from custom_components.spiel_saison.recorder_client import RecorderError

from .conftest import FakeRecorder, add_cameras, hub_entity, make_partner, make_place, partner_entity, setup_entry

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
    assert [c["stem"] for c in cams_sent] == [  # bei „Cam ID“ steht der Name der Kamera (ohne Präfix), nicht die Nummer
        f"#02.01.01.Cam ID.Fuchs {dt_util.now():%d.%m.%Y}",
        f"#02.01.01.Cam ID.Hase {dt_util.now():%d.%m.%Y}",
    ]
    assert [c["id"] for c in cams_sent] == ["camera.fuchs", "camera.hase"]
    assert cams_sent[0]["url"] == stream.return_value
    assert session == "Frank Saison 01 Aufnahme 01"
    assert profile == FRANK
    assert _runtime(entry).phase(FRANK) == "recording"
    state = _runtime(entry).states[FRANK]
    assert (state.count_season, state.count_total) == (1, 2)  # ein Start, zwei Videos
    (event,) = started
    assert event.data["partner"] == "Frank" and event.data["place"] == "Bett" and event.data["recording"] == 1


async def test_the_start_event_names_the_session_and_the_files_but_hides_the_stream_urls(hass, stream, cams) -> None:
    await _setup(hass, [make_place("Bett")])
    started = async_capture_events(hass, EVENT_STARTED)

    await _start(hass)

    (event,) = started
    today = f"{dt_util.now():%d.%m.%Y}"
    assert event.data["cams"] == ["camera.fuchs", "camera.hase"]  # bleibt wie bisher
    assert event.data["session"] == "Frank Saison 01 Aufnahme 01"
    assert event.data["files"] == [  # Dateiname ohne Endung, die App hängt .mp4 oder .mkv an
        {"cam": "camera.fuchs", "name": f"#02.01.01.Cam ID.Fuchs {today}"},
        {"cam": "camera.hase", "name": f"#02.01.01.Cam ID.Hase {today}"},
    ]
    assert stream.return_value not in str(event.data)  # die Adresse kann Zugangsdaten enthalten


async def test_the_total_count_goes_into_the_file_name_and_counts_on_with_each_recording(hass, stream, cams) -> None:
    entry = await setup_entry(
        hass,
        [make_partner("Frank", 2)],
        [make_place("Bett")],
        file_pattern="#{nr}.{saison}.{aufnahme}.{cam}.{gesamt}",
    )
    _runtime(entry).states[FRANK].count_total = 4  # vier Aufnahmen (Videos) gab es schon, in anderen Saisons

    await _start(hass)
    await _stop(hass)
    await _start(hass)

    stems = [[c["stem"] for c in call[2]] for call in FakeRecorder.calls if call[0] == "start"]
    assert stems == [
        ["#02.01.01.Fuchs.06", "#02.01.01.Hase.06"],  # beide Kameras des Starts teilen die Nummern, 4 + 2 Videos
        ["#02.01.02.Fuchs.08", "#02.01.02.Hase.08"],  # die Saison zählte eins weiter, insgesamt zwei Videos mehr
    ]


async def test_the_season_counts_the_recordings_and_the_total_counts_the_videos(hass, stream) -> None:
    cams = tuple(f"camera.c{i}" for i in range(1, 8))
    add_cameras(hass, *(cam.split(".")[1] for cam in cams))
    entry = await setup_entry(hass, [make_partner("Frank", 2)], [make_place("Bett", cams=cams)])
    started = async_capture_events(hass, EVENT_STARTED)
    stopped = async_capture_events(hass, EVENT_STOPPED)

    await _start(hass)
    state = _runtime(entry).states[FRANK]
    assert (state.count_season, state.count_total) == (1, 7)  # eine Aufnahme mit sieben Videos
    await _stop(hass)
    await _start(hass)

    assert (state.count_season, state.count_total) == (2, 14)
    sessions = [call[3] for call in FakeRecorder.calls if call[0] == "start"]
    assert sessions == ["Frank Saison 01 Aufnahme 01", "Frank Saison 01 Aufnahme 02"]
    assert [event.data["recording"] for event in started] == [1, 2]  # die Nummer in den Dateinamen
    assert [event.data["recording"] for event in stopped] == [1]  # das Ende nennt dieselbe Nummer


async def test_a_camera_that_does_not_record_does_not_count(hass, stream) -> None:
    add_cameras(hass, "c1", "c2")  # camera.c3 gibt es nicht und nimmt deshalb nicht auf
    entry = await setup_entry(
        hass,
        [make_partner("Frank", 2)],
        [make_place("Bett", cams=("camera.c1", "camera.c2", "camera.c3"))],
    )

    result = await _start(hass)

    assert result["skipped"] == [{"cam": "camera.c3", "reason": "missing"}]
    state = _runtime(entry).states[FRANK]
    assert (state.count_season, state.count_total) == (1, 2)  # ein Start, zwei Videos


async def test_the_counters_of_the_preferences_still_count_once_per_start(hass, stream, cams) -> None:
    entry = await _setup(hass, [make_place("Bett")], treats=["Feder"])  # zwei Kameras nehmen auf
    state = _runtime(entry).states[FRANK]
    state.pref_on = [logic.preference_key("treat", "Feder")]

    await _start(hass)

    assert state.pref_counts == {logic.preference_key("treat", "Feder"): 1}
    assert state.count_total == 2


def _states_when(hass, event_type: str, read) -> list:
    """Hört auf ein Ereignis und liest im selben Augenblick Zustände (read bekommt hass.states)."""
    seen: list = []

    @callback
    def listener(_event: Event) -> None:
        seen.append(read(hass.states))

    hass.bus.async_listen(event_type, listener)
    return seen


async def test_a_new_partner_starts_at_season_zero_and_the_first_prepare_begins_season_one(hass, cams) -> None:
    entry = await setup_entry(hass, [make_partner("Frank", 2)], [make_place("Bett")], season_begun=False)
    season = partner_entity(hass, entry, FRANK, "sensor", "season")
    events = async_capture_events(hass, EVENT_SEASON)
    state = _runtime(entry).states[FRANK]
    assert hass.states.get(season).state == "0"  # wie alle Zähler bei null
    assert (state.season, state.season_start, state.seasons) == (0, "", {})

    first = await _prepare(hass)
    again = await _prepare(hass)

    assert (first["season"], first["new_season"]) == (1, True)  # das Vorbereiten zählt die erste Saison
    assert (again["season"], again["new_season"]) == (1, False)  # am selben Tag nur einmal
    assert first["folder"].endswith(f"Saison 01 vom {dt_util.now():%d,%m,%Y}")
    assert hass.states.get(season).state == "1"
    assert state.seasons == {}  # vor Saison 1 gab es nichts zu archivieren
    assert [event.data["season"] for event in events] == [1]


async def test_the_first_recording_of_a_new_partner_begins_season_one_by_itself(hass, stream, cams) -> None:
    entry = await setup_entry(hass, [make_partner("Frank", 2)], [make_place("Bett")], season_begun=False)
    events = async_capture_events(hass, EVENT_SEASON)

    await _start(hass)

    (call,) = FakeRecorder.calls
    assert call[1] == _season_folder("Frank", 2, 1)  # Saison 01, nicht 00
    assert call[3] == "Frank Saison 01 Aufnahme 01"
    assert [event.data["season"] for event in events] == [1]
    assert _runtime(entry).states[FRANK].season == 1


async def test_a_season_reset_to_zero_makes_the_next_prepare_begin_season_one_again(hass, cams) -> None:
    entry = await setup_entry(hass, [make_partner("Frank", 2)], [make_place("Bett")])
    runtime = _runtime(entry)
    runtime.states[FRANK].season = 4

    await runtime.async_edit_statistics(FRANK, {"season": 0})
    assert (runtime.states[FRANK].season, runtime.states[FRANK].season_start) == (0, "")
    result = await _prepare(hass)

    assert (result["season"], result["new_season"]) == (1, True)
    assert runtime.states[FRANK].seasons == {}  # nichts aus der zurückgesetzten Saison wird archiviert


async def test_the_sensors_show_the_new_counters_when_the_start_event_arrives(hass, stream, cams) -> None:
    entry = await _setup(hass, [make_place("Bett")])
    season = partner_entity(hass, entry, FRANK, "sensor", "recordings_season")
    total = partner_entity(hass, entry, FRANK, "sensor", "recordings_total")
    seen = _states_when(hass, EVENT_STARTED, lambda states: (states.get(season).state, states.get(total).state))

    await _start(hass)

    assert seen == [("1", "2")]  # beim Ereignis steht der Zustand schon da: ein Start, zwei Videos


async def test_the_sensors_are_up_to_date_when_the_stop_event_arrives(hass, stream, cams) -> None:
    entry = await _setup(hass, [make_place("Bett")])
    status = partner_entity(hass, entry, FRANK, "sensor", "status")
    hub = hub_entity(hass, entry, "sensor", "status")
    await _start(hass)
    seen = _states_when(
        hass, EVENT_STOPPED, lambda states: (states.get(status).state, states.get(hub).attributes["message"])
    )

    await _stop(hass)

    assert seen == [("stopping", "Frank: stopped (manual)")]


async def test_the_sensors_are_up_to_date_when_the_season_events_arrive(hass, stream, cams) -> None:
    entry = await _setup(hass)
    season = partner_entity(hass, entry, FRANK, "sensor", "season")
    recordings = partner_entity(hass, entry, FRANK, "sensor", "recordings_season")
    hub = hub_entity(hass, entry, "sensor", "status")
    await _start(hass)
    await _stop(hass)
    _runtime(entry).states[FRANK].season_start = _days_ago(1)
    began = _states_when(hass, EVENT_SEASON, lambda states: (states.get(season).state, states.get(recordings).state))
    ended = _states_when(hass, EVENT_SEASON_ENDED, lambda states: states.get(hub).attributes["message"])

    await hass.services.async_call(DOMAIN, "new_season", {"partner": "Frank"}, blocking=True)
    await _end_season(hass)

    assert began == [("2", "0")]  # die neue Saison mit leerem Zähler
    assert ended == ["Frank: season 2 ended"]


async def test_the_total_in_the_file_names_is_the_value_the_sensor_shows_after_the_start(hass, stream) -> None:
    cams = tuple(f"camera.c{i}" for i in range(1, 8))
    add_cameras(hass, *(cam.split(".")[1] for cam in cams))
    entry = await setup_entry(
        hass,
        [make_partner("Frank", 2)],
        [make_place("Bett", cams=cams)],
        file_pattern="#{nr}.{saison}.{aufnahme}.{cam}.{gesamt}",
    )
    state = _runtime(entry).states[FRANK]
    state.season, state.count_season, state.count_total = 2, 5, 35

    await _start(hass)

    (_, _, cams_sent, *_rest) = FakeRecorder.calls[-1]
    assert [c["stem"] for c in cams_sent] == [f"#02.02.06.C{i}.42" for i in range(1, 8)]  # alle sieben mit 42
    sensor = partner_entity(hass, entry, FRANK, "sensor", "recordings_total")
    assert hass.states.get(sensor).state == "42"  # dieselbe Zahl wie im Dateinamen


async def test_the_start_event_lists_the_preferences_that_are_on_in_the_order_of_the_partner(
    hass, stream, cams
) -> None:
    entry = await _setup(hass, [make_place("Bett")], likes=["Seil"], treats=["Feder", "Kette", "Schal"])
    started = async_capture_events(hass, EVENT_STARTED)
    _runtime(entry).states[FRANK].pref_on = [logic.preference_key("treat", name) for name in ("Schal", "Feder")]

    await _start(hass)
    await _stop(hass)
    _runtime(entry).states[FRANK].pref_on = []
    await _start(hass)

    first, second = started
    assert first.data["preferences"] == ["Feder", "Schal"]  # wie beim Spielpartner eingetragen, nicht wie eingeschaltet
    assert second.data["preferences"] == []  # keine an: eine leere Liste, kein fehlendes Feld


def _ctronics_cam(hass, registry, name: str, options: dict) -> None:
    """Eine Kamera der Integration Ctronics 4K Cam mit ihrem Eintrag (die Optionen tragen das Präfix).

    Vor `add_cameras` aufrufen, sonst vergibt die Registry für die Entity-ID ein „_2“.
    """
    ctronics = MockConfigEntry(domain="ctronics_cam", title=name, options=options)
    ctronics.add_to_hass(hass)
    registry.async_get_or_create(
        "camera", "ctronics_cam", f"uid_{name}", suggested_object_id=name, config_entry=ctronics
    )


async def test_the_file_names_carry_the_prefix_of_the_camera_not_its_name(hass, stream, entity_registry) -> None:
    _ctronics_cam(hass, entity_registry, "fuchs", {"prefix": " #1 "})
    _ctronics_cam(hass, entity_registry, "hase", {"ptz_step_ms": 400})  # eingerichtet, bevor es Präfixe gab
    add_cameras(hass, "fuchs", "hase", "reh")
    await setup_entry(
        hass,
        [make_partner("Frank", 2)],
        [make_place("Bett", cams=("camera.fuchs", "camera.hase", "camera.reh"))],  # reh: keine Ctronics-Kamera
    )

    await _start(hass)

    (_, _, cams_sent, *_rest) = FakeRecorder.calls[-1]
    today = f"{dt_util.now():%d.%m.%Y}"
    assert [c["stem"] for c in cams_sent] == [
        f"#02.01.01.Cam ID.#1 {today}",  # das Präfix der Ctronics-Kamera
        f"#02.01.01.Cam ID.Hase {today}",  # ohne Präfix gilt der Name
        f"#02.01.01.Cam ID.Reh {today}",
    ]
    assert [c["name"] for c in cams_sent] == ["Fuchs", "Hase", "Reh"]  # die App zeigt weiter die Namen


async def test_cameras_with_the_same_prefix_get_different_file_names(hass, stream, entity_registry) -> None:
    _ctronics_cam(hass, entity_registry, "fuchs", {"prefix": "Spielzimmer"})
    _ctronics_cam(hass, entity_registry, "hase", {"prefix": "spielzimmer"})
    add_cameras(hass, "fuchs", "hase")
    await setup_entry(hass, [make_partner("Frank", 2)], [make_place("Bett")])

    await _start(hass)

    (_, _, cams_sent, *_rest) = FakeRecorder.calls[-1]
    today = f"{dt_util.now():%d.%m.%Y}"
    assert [c["stem"] for c in cams_sent] == [
        f"#02.01.01.Cam ID.Spielzimmer {today}",
        f"#02.01.01.Cam ID.spielzimmer {today} Cam 02",
    ]


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


async def test_brightness_and_colour_go_in_two_commands_and_the_colour_comes_alone(hass, cams, service_calls) -> None:
    lights = {"light.a": {"rgb": [1, 2, 3], "brightness": 20}, "light.b": {"brightness": 70}}
    await _setup(hass, options={"finish": {"lights": lights}})

    await _end_season(hass)

    assert _summary(service_calls) == [
        ("light", "turn_on", {"entity_id": "light.a", "brightness_pct": 20}),
        ("light", "turn_on", {"entity_id": "light.b", "brightness_pct": 70}),
        (
            "light",
            "turn_on",
            {"entity_id": "light.a", "rgb_color": [1, 2, 3]},
        ),  # nur Lichter mit Farbe, ohne Helligkeit
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
        ("light", "turn_on", {"entity_id": "light.stehlampe", "brightness_pct": 40}),  # erst an, mit Helligkeit
        ("light", "turn_on", {"entity_id": "light.decke"}),  # ohne Farbe und Helligkeit: so, wie das Licht zuletzt war
        ("light", "turn_on", {"entity_id": "light.stehlampe", "rgb_color": [255, 100, 0]}),  # dann die Farbe allein
    ]
    assert [e.data for e in events] == [{**result, "partner_id": FRANK, "nickname": "", "start": state.season_start}]
    assert state.season == 1 and state.count_season == 3  # beenden zählt nichts um


async def test_white_goes_to_the_lamp_as_a_colour_temperature_and_a_colour_as_a_colour(
    hass, cams, service_calls
) -> None:
    lights = {
        "light.weiss": {"rgb": [169, 167, 167], "brightness": 50},  # Grau aus dem Farbwähler, Sättigung etwa 1 %
        "light.rot": {"rgb": [255, 24, 0], "brightness": 25},
        "light.nur_farbe": {"rgb": [0, 0, 255]},  # ohne Helligkeit
    }
    await _setup(hass, options={"finish": {"lights": lights}})

    await _end_season(hass)

    data = [call[2] for call in _summary(service_calls)]
    assert data[:3] == [
        {"entity_id": "light.weiss", "brightness_pct": 50},
        {"entity_id": "light.rot", "brightness_pct": 25},
        {"entity_id": "light.nur_farbe"},  # ohne Helligkeit geht das Licht einfach an
    ]
    white, red, blue = data[3:]
    assert white["entity_id"] == "light.weiss" and "rgb_color" not in white and "brightness_pct" not in white
    assert 5000 <= white["color_temp_kelvin"] <= 6500
    assert red == {"entity_id": "light.rot", "rgb_color": [255, 24, 0]}
    assert blue == {"entity_id": "light.nur_farbe", "rgb_color": [0, 0, 255]}


DRIVE_UP = {
    "cover": "cover.winde",
    "up_switch": "switch.hoch",
    "end_switch": "binary_sensor.oben",
    "after_off": {"entity_id": ["switch.strom"]},
    "wait_seconds": 30,
}


async def test_the_winch_is_driven_up_with_the_up_switch_until_the_end_switch_is_on(hass, cams, service_calls) -> None:
    hass.states.async_set("binary_sensor.oben", "off")
    await _setup(hass, options={"finish": DRIVE_UP})

    task = hass.async_create_background_task(_end_season(hass), "test end season")
    await asyncio.sleep(0.05)
    assert _summary(service_calls) == [("homeassistant", "turn_on", {"entity_id": ["switch.hoch"]})]  # keine Position
    assert not task.done()

    hass.states.async_set("binary_sensor.oben", "on")  # die Winde hat den Endschalter erreicht
    await task

    assert _summary(service_calls) == [
        ("homeassistant", "turn_on", {"entity_id": ["switch.hoch"]}),
        ("homeassistant", "turn_off", {"entity_id": ["switch.hoch"]}),  # danach der Schalter wieder aus
        ("homeassistant", "turn_off", {"entity_id": ["switch.strom"]}),  # und erst dann der Strom der Winde
    ]


async def test_the_winch_stands_still_when_it_is_already_at_the_end_switch(hass, cams, service_calls) -> None:
    hass.states.async_set("binary_sensor.oben", "on")
    await _setup(hass, options={"finish": DRIVE_UP})

    await _end_season(hass)

    assert _summary(service_calls) == [("homeassistant", "turn_off", {"entity_id": ["switch.strom"]})]


async def test_nothing_is_switched_off_when_the_up_switch_stops_before_the_end_switch(
    hass, cams, service_calls
) -> None:
    hass.states.async_set("binary_sensor.oben", "off")
    hass.states.async_set("switch.hoch", "off")
    entry = await _setup(hass, options={"finish": DRIVE_UP})

    task = hass.async_create_background_task(_end_season(hass), "test end season")
    await asyncio.sleep(0.05)
    hass.states.async_set("switch.hoch", "on")
    await asyncio.sleep(0)
    hass.states.async_set("switch.hoch", "off")  # die Winde hat sich selbst gestoppt, der Endschalter blieb aus
    with pytest.raises(HomeAssistantError) as err:
        await task

    assert err.value.translation_key == "end_switch_timeout"
    assert _summary(service_calls) == [
        ("homeassistant", "turn_on", {"entity_id": ["switch.hoch"]}),
        ("homeassistant", "turn_off", {"entity_id": ["switch.hoch"]}),  # der Schalter ist sicher aus
    ]  # der Strom der Winde bleibt an
    assert _runtime(entry).status == "error"


async def test_the_up_switch_is_turned_off_again_when_the_end_switch_never_comes(hass, cams, service_calls) -> None:
    hass.states.async_set("binary_sensor.oben", "off")
    await _setup(hass, options={"finish": {**DRIVE_UP, "wait_seconds": 0}})

    with pytest.raises(HomeAssistantError):
        await _end_season(hass)

    assert [(c.service, dict(c.data)["entity_id"]) for c in service_calls] == [
        ("turn_on", ["switch.hoch"]),
        ("turn_off", ["switch.hoch"]),
    ]


async def test_an_up_switch_without_an_end_switch_is_ignored(hass, cams, service_calls) -> None:
    options = {"finish": {"cover": "cover.winde", "up_switch": "switch.hoch"}}
    await _setup(hass, options=options)

    await _end_season(hass)

    assert _summary(service_calls) == [("cover", "set_cover_position", {"entity_id": "cover.winde", "position": 0})]


def _light(hass, entity_id: str, state: str, **attributes) -> None:
    hass.states.async_set(entity_id, state, attributes)


async def test_the_lighting_of_a_partner_is_saved_as_it_is_now(hass, cams, service_calls) -> None:
    _light(hass, "light.a", "on", brightness=128, color_mode="hs", rgb_color=(64, 255, 169))
    _light(hass, "light.b", "on", brightness=255, color_mode="color_temp", color_temp_kelvin=3000)
    _light(hass, "light.c", "off")
    _light(hass, "light.d", "unavailable")
    entry = await _setup(hass, lights=["light.a", "light.b", "light.c", "light.d"])

    result = await hass.services.async_call(
        DOMAIN, "save_lighting", {"partner": "Frank"}, blocking=True, return_response=True
    )

    assert result == {"partner": "Frank", "lights": 3, "on": 2}  # das nicht erreichbare Licht fehlt
    assert _runtime(entry).states[FRANK].lighting == {
        "light.a": {"on": True, "brightness": 50, "rgb": [64, 255, 169]},
        "light.b": {"on": True, "brightness": 100, "kelvin": 3000},
        "light.c": {"on": False},
    }
    assert service_calls == []  # Speichern schaltet nichts


async def test_the_saved_lighting_stays_when_a_light_cannot_be_reached_later(hass, cams) -> None:
    _light(hass, "light.a", "on", brightness=255, color_mode="hs", rgb_color=(1, 2, 3))
    entry = await _setup(hass, lights=["light.a"])
    await hass.services.async_call(DOMAIN, "save_lighting", {"partner": "Frank"}, blocking=True)
    _light(hass, "light.a", "unavailable")

    await hass.services.async_call(DOMAIN, "save_lighting", {"partner": "Frank"}, blocking=True)

    assert _runtime(entry).states[FRANK].lighting["light.a"]["rgb"] == [1, 2, 3]


async def test_saving_needs_lights_that_can_be_read(hass, cams) -> None:
    await _setup(hass, lights=["light.a"])  # light.a gibt es nicht
    with pytest.raises(ServiceValidationError) as unreadable:
        await hass.services.async_call(DOMAIN, "save_lighting", {"partner": "Frank"}, blocking=True)
    assert unreadable.value.translation_key == "lighting_unreadable"

    with pytest.raises(ServiceValidationError) as none_chosen:
        await hass.services.async_call(DOMAIN, "save_lighting", {"partner": "Tobias"}, blocking=True)  # keine Lichter
    assert none_chosen.value.translation_key == "no_partner_lights"


async def test_the_saved_lighting_is_set_with_the_first_recording_of_the_season_only(
    hass, stream, cams, service_calls
) -> None:
    entry = await _setup(
        hass, [make_place("Schaukel", start_on=BED)], lights=["light.a", "light.b", "light.c", "light.nicht_gewaehlt"]
    )
    _runtime(entry).states[FRANK].lighting = {
        "light.a": {"on": True, "brightness": 50, "rgb": [64, 255, 169]},
        "light.b": {"on": True, "brightness": 100, "kelvin": 3000},
        "light.c": {"on": False},
    }

    await _start(hass)
    first = _summary(service_calls)
    await _stop(hass)
    service_calls.clear()
    await _start(hass)  # die zweite Aufnahme derselben Saison

    assert first[0] == ("homeassistant", "turn_on", BED)  # zuerst der Beginn des Ortes
    assert first[1:] == [
        ("light", "turn_off", {"entity_id": ["light.c"]}),
        ("light", "turn_on", {"entity_id": "light.a", "brightness_pct": 50}),
        ("light", "turn_on", {"entity_id": "light.b", "brightness_pct": 100}),
        ("light", "turn_on", {"entity_id": "light.a", "rgb_color": [64, 255, 169]}),
        ("light", "turn_on", {"entity_id": "light.b", "color_temp_kelvin": 3000}),
    ]
    assert [c for c in _summary(service_calls) if c[0] == "light"] == []


async def test_a_new_season_sets_the_lighting_again(hass, stream, cams, service_calls) -> None:
    entry = await _setup(hass, lights=["light.a"])
    _runtime(entry).states[FRANK].lighting = {"light.a": {"on": True, "brightness": 30}}
    await _start(hass)
    await _stop(hass)
    _runtime(entry).states[FRANK].season_start = _days_ago(1)
    await _prepare(hass)  # beginnt die Saison des Tages, die Zähler der Saison stehen wieder auf null
    service_calls.clear()

    await _start(hass)

    assert _summary(service_calls) == [("light", "turn_on", {"entity_id": "light.a", "brightness_pct": 30})]


async def test_a_light_that_is_no_longer_chosen_leaves_the_saved_lighting(hass, cams) -> None:
    entry = await _setup(hass, lights=["light.a", "light.b"])
    _runtime(entry).states[FRANK].lighting = {"light.a": {"on": False}, "light.b": {"on": False}}
    await _runtime(entry)._save()
    sub = entry.subentries[FRANK]

    hass.config_entries.async_update_subentry(entry, sub, data={**sub.data, "lights": ["light.a"]})
    await hass.async_block_till_done()

    assert list(_runtime(entry).states[FRANK].lighting) == ["light.a"]  # nach dem Neuladen


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
    assert state.count_total == 2
    assert state.seasons["1"]["recordings"] == 1 and state.seasons["1"]["seconds"] == 7200
    (event,) = season_events
    assert event.data["season"] == 2
    FakeRecorder.calls.clear()
    await _start(hass)
    assert FakeRecorder.calls[0][1][2].startswith("Saison 02 vom ")
    assert FakeRecorder.calls[0][3] == "Frank Saison 02 Aufnahme 01"  # in der neuen Saison zählt es wieder bei 1


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
    assert runtime.states[FRANK].count_total == 2


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
