"""Kamera-Helfer: Anzeigename und die PTZ-Positionen der Ctronics-Kameras, dazu der Name einer Entity."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import SelectOptionDict

from . import logic
from .const import CTRONICS_DOMAIN, CTRONICS_PRESETS_OPTION, MAX_PTZ_PRESET

NO_MOVE = "0"  # Wert der Auswahl „nicht bewegen“


def cam_name(hass: HomeAssistant, entity_id: str) -> str:
    """Anzeigename einer Kamera: Gerätename, sonst Name der Entity."""
    ent = er.async_get(hass).async_get(entity_id)
    if ent and ent.device_id and (dev := dr.async_get(hass).async_get(ent.device_id)):
        name = dev.name_by_user or dev.name
        if name:
            return name
    if (state := hass.states.get(entity_id)) and state.name:
        return state.name
    return (ent.name or ent.original_name) if ent and (ent.name or ent.original_name) else entity_id


def entity_title(hass: HomeAssistant, entity_id: str) -> str:
    """Anzeigename einer Entity (zum Beispiel eines Lichts): ihr Name im Zustand, sonst Registry, Gerät, Entity-ID.

    Der Zustand fehlt, solange die Quelle noch nicht geladen ist, etwa direkt nach dem Start von Home Assistant.
    """
    if (state := hass.states.get(entity_id)) and state.name:
        return state.name
    if ent := er.async_get(hass).async_get(entity_id):
        if name := ent.name or ent.original_name:
            return name
        if ent.device_id and (dev := dr.async_get(hass).async_get(ent.device_id)):
            return dev.name_by_user or dev.name or entity_id
    return entity_id


def own_lights(hass: HomeAssistant, config_entry_id: str) -> list[str]:
    """Die Regler, die diese Integration selbst als Lichter anlegt. Man wählt sie nicht als echtes Licht aus."""
    registry = er.async_get(hass)
    return [
        ent.entity_id for ent in er.async_entries_for_config_entry(registry, config_entry_id) if ent.domain == "light"
    ]


def is_ctronics(hass: HomeAssistant, entity_id: str) -> bool:
    """Gehört die Kamera zur Integration Ctronics 4K Cam? Nur diese fahren eine PTZ-Position an."""
    ent = er.async_get(hass).async_get(entity_id)
    return bool(ent and ent.platform == CTRONICS_DOMAIN)


def ctronics_presets(hass: HomeAssistant, entity_id: str) -> dict[int, str] | None:
    """Benannte PTZ-Positionen einer Ctronics-Kamera (Nummer -> Name), None bei jeder anderen Kamera.

    Die Namen trägt man bei der Integration Ctronics 4K Cam unter „Optionen“ ein. Ohne Namen ist das Ergebnis leer.
    """
    ent = er.async_get(hass).async_get(entity_id)
    if not ent or ent.platform != CTRONICS_DOMAIN:
        return None
    ctronics = hass.config_entries.async_get_entry(ent.config_entry_id) if ent.config_entry_id else None
    return logic.parse_presets(ctronics.options.get(CTRONICS_PRESETS_OPTION, ""), MAX_PTZ_PRESET) if ctronics else {}


def preset_options(hass: HomeAssistant, entity_id: str) -> list[SelectOptionDict]:
    """Auswahl der PTZ-Positionen einer Ctronics-Kamera: zuerst „nicht bewegen“, dann ihre benannten Positionen.

    Hat die Kamera noch keine Namen, stehen alle Nummern von 1 bis 64 zur Wahl.
    """
    german = hass.config.language.startswith("de")
    options = [SelectOptionDict(value=NO_MOVE, label="Nicht bewegen" if german else "Do not move")]
    presets = ctronics_presets(hass, entity_id)
    if presets:
        labels = logic.preset_labels(presets)
    else:
        labels = {number: f"Position {number}" for number in range(1, MAX_PTZ_PRESET + 1)}
    options.extend(SelectOptionDict(value=str(number), label=label) for number, label in labels.items())
    return options
