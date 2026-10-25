"""Gemeinsame Fixtures: Home-Assistant-Test-Plugin, ein Fake-Recorder und Hilfen zum Einrichten."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any, ClassVar
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.spiel_saison.const import DOMAIN
from custom_components.spiel_saison.recorder_client import RecorderAuthError, RecorderError

URL = "http://app:8099"
KEY = "geheimgeheim123"
STREAM = "rtsp://user:pass@192.168.1.12/stream"


class FakeRecorder:
    """Ersatz für RecorderClient: merkt sich die Aufrufe und liefert einstellbare Antworten."""

    calls: ClassVar[list[tuple]] = []
    remote_profiles: ClassVar[dict[str, Any]] = {}
    fail: Exception | None = None
    start_fail: Exception | None = None

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def _check(self) -> None:
        if FakeRecorder.fail:
            raise FakeRecorder.fail

    async def status(self) -> dict[str, Any]:
        self._check()
        return {
            "aufnahme": bool(FakeRecorder.remote_profiles),
            "wurzel": "/media/Aufnahmen",
            "speicher": {"frei_gb": 2500.0, "gesamt_gb": 3000.0},
            "version": "0.1.0",
            "kameras": {},
            "profile": FakeRecorder.remote_profiles,
        }

    async def list_dirs(self, path: list[str]) -> list[str]:
        self._check()
        return []

    async def start(self, path: list[str], cams: list[dict], session: str, profile: str) -> dict:
        self._check()
        if FakeRecorder.start_fail:
            raise FakeRecorder.start_fail
        FakeRecorder.calls.append(("start", path, cams, session, profile))
        FakeRecorder.remote_profiles[profile] = {
            "aufnahme": True,
            "kameras": {c["id"]: {"status": "nimmt auf"} for c in cams},
        }
        return {}

    async def stop(self, profile: str) -> dict:
        self._check()
        FakeRecorder.calls.append(("stop", profile))
        FakeRecorder.remote_profiles.pop(profile, None)
        return {}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Lässt Home Assistant die Integration aus `custom_components` laden."""


@pytest.fixture(autouse=True)
def fake_recorder() -> Generator[type[FakeRecorder]]:
    FakeRecorder.calls, FakeRecorder.remote_profiles = [], {}
    FakeRecorder.fail = FakeRecorder.start_fail = None
    with (
        patch("custom_components.spiel_saison.manager.RecorderClient", FakeRecorder),
        patch("custom_components.spiel_saison.config_flow.RecorderClient", FakeRecorder),
    ):
        yield FakeRecorder


@pytest.fixture
def stream() -> Generator[AsyncMock]:
    """Jede Kamera liefert eine Stream-Adresse."""
    with patch("custom_components.spiel_saison.manager.async_get_stream_source", AsyncMock(return_value=STREAM)) as m:
        yield m


@pytest.fixture
def unreachable() -> type[FakeRecorder]:
    FakeRecorder.fail = RecorderError("Recorder-App nicht erreichbar (test)")
    return FakeRecorder


@pytest.fixture
def bad_key() -> type[FakeRecorder]:
    FakeRecorder.fail = RecorderAuthError("Recorder: Schlüssel stimmt nicht")
    return FakeRecorder


@pytest.fixture
def service_calls(hass: HomeAssistant) -> list[ServiceCall]:
    """Fängt homeassistant.turn_on/turn_off, notify.send_message, cover.set_cover_position und ctronics_cam ab."""
    calls: list[ServiceCall] = []

    async def record(call: ServiceCall) -> None:
        calls.append(call)

    for domain, service in (
        ("homeassistant", "turn_on"),
        ("homeassistant", "turn_off"),
        ("notify", "send_message"),
        ("cover", "set_cover_position"),
        ("ctronics_cam", "ptz_goto_preset"),
    ):
        hass.services.async_register(domain, service, record)
    return calls


def make_partner(name: str, number: int, cams: list[str]) -> dict[str, Any]:
    return {"name": name, "number": number, "cams": cams}


def make_place(name: str, **fields: Any) -> dict[str, Any]:
    """Subentry-Daten eines Ortes ohne Vorlauf und ohne Pause (die Tests warten nicht)."""
    return {"name": name, "lead_seconds": 0, "end_seconds": 0, **fields}


def add_cameras(hass: HomeAssistant, *names: str) -> None:
    """Legt Kamera-Zustände an (camera.<name>) ohne echte Quell-Integration."""
    for name in names:
        hass.states.async_set(f"camera.{name}", "idle", {"friendly_name": name.title()})


def partner_entity(hass: HomeAssistant, entry: MockConfigEntry, sid: str, domain: str, key: str) -> str:
    """Entity-ID einer Entity am Gerät eines Spielpartners, gefunden über ihre eindeutige ID."""
    entity_id = er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"{entry.entry_id}_{sid}_{key}")
    assert entity_id, f"{domain} {key} fehlt"
    return entity_id


def hub_entity(hass: HomeAssistant, entry: MockConfigEntry, domain: str, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"{entry.entry_id}_{key}")
    assert entity_id, f"{domain} {key} fehlt"
    return entity_id


async def setup_entry(
    hass: HomeAssistant,
    partners: list[dict[str, Any]] | None = None,
    places: list[dict[str, Any]] | None = None,
    **data: Any,
) -> MockConfigEntry:
    """Richtet Spiel Saison ein (Subentry-IDs: 'p_<Name>' für Spielpartner und 'o_<Name>' für Orte)."""
    subentries = [
        ConfigSubentryData(
            subentry_type="partner",
            title=partner["name"],
            unique_id=partner["name"].lower(),
            subentry_id=f"p_{partner['name']}",
            data=partner,
        )
        for partner in partners or []
    ] + [
        ConfigSubentryData(
            subentry_type="place",
            title=place["name"],
            unique_id=place["name"].lower(),
            subentry_id=f"o_{place['name']}",
            data=place,
        )
        for place in places or []
    ]
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Spiel Saison",
        data={"recorder_url": URL, "recorder_key": KEY, "base_folder": "Spielen", **data},
        subentries_data=subentries,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
