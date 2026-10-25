"""Konstanten der Integration Spiel Saison."""

from __future__ import annotations

DOMAIN = "spiel_saison"

SUBENTRY_PARTNER = "partner"
SUBENTRY_PLACE = "place"

# --- Verbindung zur App und Ordner-Muster (config_entry.data) ----------------------
CONF_RECORDER_URL = "recorder_url"
CONF_RECORDER_KEY = "recorder_key"
DEFAULT_RECORDER_URL = "http://local-spiel-saison-recorder:8099"

CONF_BASE_FOLDER = "base_folder"  # Ordner unter dem Aufnahme-Ordner der App, z. B. "Spielen/Spezial"
CONF_PARTNER_PATTERN = "partner_pattern"  # Ordner je Spielpartner
CONF_SEASON_PATTERN = "season_pattern"  # Ordner je Saison, darin liegen die Aufnahmen
CONF_FILE_PATTERN = "file_pattern"  # Dateiname je Kamera (ohne Endung)

DEFAULT_BASE_FOLDER = "Spiel_Saison"
DEFAULT_PARTNER_PATTERN = "Spielen mit {partner} #{nr}"
DEFAULT_SEASON_PATTERN = "Saison {saison} vom %DD,%MM,%CCYY"
DEFAULT_FILE_PATTERN = "#{nr}.{saison}.{aufnahme}.Cam ID.{cam} %DD.%MM.%CCYY"
OLD_CAM_NUMBER_PART = "Cam ID.{cam_nr}"  # bis 0.1.0 stand hier die Nummer der Kamera statt ihres Namens

# --- Spielpartner (Subentry-Daten) -------------------------------------------------
CONF_NUMBER = "number"  # Nummer des Spielpartners ("nr" in den Mustern)
CONF_ANNOUNCE_ENTITY = "announce_entity"  # notify-Entity für Ansagen
CONF_ANNOUNCE_START = "announce_start"
CONF_ANNOUNCE_END = "announce_end"

# --- Ort (Subentry-Daten) ----------------------------------------------------------
CONF_PARTNER = "partner"  # Subentry-ID des Spielpartners, zu dem der Ort gehört
CONF_CAMS = "cams"  # Kamera-Entities (camera.*) des Ortes
CONF_CAM_PRESETS = "cam_presets"  # Kamera-Entity -> PTZ-Position der Ctronics-Kamera (fehlt = nicht bewegen)
CONF_PTZ_PRESET = "ptz_preset"  # bis 0.1.x: eine PTZ-Position für alle Kameras (nur noch für die Umstellung)
CONF_PREPARE_ON = "prepare_on"  # Ziele (Entities, Bereiche, Labels), die beim Vorbereiten eingeschaltet werden
CONF_COVER = "cover"  # Rollo, Winde oder Ähnliches, das beim Vorbereiten auf eine Position fährt
CONF_COVER_POSITION = "cover_position"
CONF_START_OFF = "start_off"
CONF_START_ON = "start_on"
CONF_END_OFF = "end_off"
CONF_END_ON = "end_on"
CONF_LEAD_SECONDS = "lead_seconds"  # Vorlauf zwischen Beginn-Schritt und Aufnahme
CONF_END_SECONDS = "end_seconds"  # Pause zwischen Aufnahme-Ende und Ende-Schritt

DEFAULT_LEAD_SECONDS = 30
DEFAULT_END_SECONDS = 10
DEFAULT_COVER_POSITION = 50

TARGET_FIELDS = (CONF_PREPARE_ON, CONF_START_OFF, CONF_START_ON, CONF_END_OFF, CONF_END_ON)

CTRONICS_DOMAIN = "ctronics_cam"  # nur diese Kameras fahren eine PTZ-Position an
CTRONICS_PRESETS_OPTION = "presets"  # Option der Ctronics-Integration: Zeilen wie "1: Schaukel"
MAX_PTZ_PRESET = 64  # so viele Positionen kann eine Ctronics-Kamera speichern

STORAGE_VERSION = 1

SERVICE_START = "start_recording"
SERVICE_STOP = "stop_recording"
SERVICE_PREPARE = "prepare"
SERVICE_NEW_SEASON = "new_season"
ATTR_PARTNER = "partner"
ATTR_PLACE = "place"

EVENT_STARTED = f"{DOMAIN}_recording_started"
EVENT_STOPPED = f"{DOMAIN}_recording_stopped"
EVENT_SEASON = f"{DOMAIN}_season_started"

PHASE_IDLE = "idle"
PHASE_STARTING = "starting"
PHASE_RECORDING = "recording"
PHASE_STOPPING = "stopping"

# Abstand, in dem der Zustand der App abgefragt wird
POLL_SECONDS = 15
