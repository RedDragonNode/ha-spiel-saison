"""Umstellung gespeicherter Einträge von Version 1.1 auf 1.2."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from .conftest import make_partner, setup_entry

OLD_PATTERN = "#{nr}.{saison}.{aufnahme}.Cam ID.{cam_nr} (4K) %DD.%MM.%CCYY"


async def test_the_stored_file_pattern_gets_the_camera_name_instead_of_the_number(hass: HomeAssistant) -> None:
    entry = await setup_entry(hass, [make_partner("Frank", 2, ["camera.fuchs"])], file_pattern=OLD_PATTERN)

    assert entry.minor_version == 2
    assert entry.data["file_pattern"] == "#{nr}.{saison}.{aufnahme}.Cam ID.{cam} (4K) %DD.%MM.%CCYY"


async def test_an_own_file_pattern_is_left_alone(hass: HomeAssistant) -> None:
    entry = await setup_entry(hass, [make_partner("Frank", 2, ["camera.fuchs"])], file_pattern="{cam} {cam_nr}")

    assert entry.minor_version == 2
    assert entry.data["file_pattern"] == "{cam} {cam_nr}"
