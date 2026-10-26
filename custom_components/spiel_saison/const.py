"""Konstanten der Integration Spiel Saison."""

from __future__ import annotations

DOMAIN = "spiel_saison"

SUBENTRY_PARTNER = "partner"
SUBENTRY_PLACE = "place"
SUBENTRY_LIKE = "like"  # nur 0.3.x: Vorlieben waren eigene Einträge (siehe Umstellung), heute sind es Namen
SUBENTRY_TREAT = "treat"  # nur 0.3.x
KIND_LIKE = "like"  # eigene Vorliebe
KIND_TREAT = "treat"  # Vorliebe zum Bearbeiten seines Partners
COUNTED_KINDS = (KIND_TREAT,)  # nur diese Vorlieben haben Schalter „dabei“ und Zähler

# --- Verbindung zur App und Ordner-Muster (config_entry.data) ----------------------
CONF_RECORDER_URL = "recorder_url"
CONF_RECORDER_KEY = "recorder_key"
DEFAULT_RECORDER_URL = "http://local-spiel-saison-recorder:8099"

CONF_BASE_FOLDER = "base_folder"  # Ordner unter dem Aufnahme-Ordner der App, z. B. "Spielen/Spezial"
CONF_PARTNER_PATTERN = "partner_pattern"  # Ordner je Spielpartner
CONF_SEASON_PATTERN = "season_pattern"  # Ordner je Saison, darin liegen die Aufnahmen
CONF_FILE_PATTERN = "file_pattern"  # Dateiname je Kamera (ohne Endung)
CONF_LIKE_CATALOG = "like_catalog"  # alle je eingegebenen eigenen Vorlieben, stehen bei neuen Spielpartnern zur Wahl
CONF_TREAT_CATALOG = "treat_catalog"  # dasselbe für die Vorlieben zum Bearbeiten

DEFAULT_BASE_FOLDER = "Spiel_Saison"
DEFAULT_PARTNER_PATTERN = "Spielen mit {partner} #{nr}"
DEFAULT_SEASON_PATTERN = "Saison {saison} vom %DD,%MM,%CCYY"
DEFAULT_FILE_PATTERN = "#{nr}.{saison}.{aufnahme}.Cam ID.{cam} %DD.%MM.%CCYY"
OLD_CAM_NUMBER_PART = "Cam ID.{cam_nr}"  # bis 0.1.0 stand hier die Nummer der Kamera statt ihres Namens

# --- Spielpartner (Subentry-Daten) -------------------------------------------------
CONF_NUMBER = "number"  # Nummer des Spielpartners ("nr" in den Mustern)
CONF_NICKNAME = "nickname"  # Spitzname, in den Ansagen als Platzhalter nickname benutzbar
CONF_LIKES = "likes"  # Namen der eigenen Vorlieben
CONF_TREATS = "treats"  # Namen der Vorlieben zum Bearbeiten seines Partners
CONF_ANNOUNCE_ENTITY = "announce_entity"  # notify-Entity für Ansagen
CONF_ANNOUNCE_START = "announce_start"
CONF_ANNOUNCE_END = "announce_end"

# --- Ort (Subentry-Daten) ----------------------------------------------------------
CONF_PARTNER = "partner"  # Subentry-ID des Spielpartners, zu dem der Ort gehört
CONF_CAMS = "cams"  # Kamera-Entities (camera.*) des Ortes
CONF_CAM_PRESETS = "cam_presets"  # Kamera-Entity -> PTZ-Position der Ctronics-Kamera (fehlt = nicht bewegen)
CONF_PTZ_PRESET = "ptz_preset"  # bis 0.1.x: eine PTZ-Position für alle Kameras (nur noch für die Umstellung)
# Winden-Position des Ortes: ersetzt bei „Saison vorbereiten“ die Position von „Konfigurieren“, wenn er gewählt ist
CONF_COVER_POSITION = "cover_position"
# bis 0.6.x standen die Einstellungen der beiden Tasten beim Ort (nur noch für die Umstellung auf 1.5)
CONF_COVER = "cover"
CONF_FINISH_COVER = "finish_cover"
CONF_FINISH_COVER_POSITION = "finish_cover_position"
CONF_PREPARE_ON = "prepare_on"
CONF_FINISH_OFF = "finish_off"
CONF_FINISH_ON = "finish_on"
CONF_START_OFF = "start_off"
CONF_START_ON = "start_on"
CONF_END_OFF = "end_off"
CONF_END_ON = "end_on"
CONF_LEAD_SECONDS = "lead_seconds"  # Vorlauf zwischen Beginn-Schritt und Aufnahme
CONF_END_SECONDS = "end_seconds"  # Pause zwischen Aufnahme-Ende und Ende-Schritt

DEFAULT_LEAD_SECONDS = 30
DEFAULT_END_SECONDS = 10
DEFAULT_COVER_POSITION = 50
DEFAULT_FINISH_COVER_POSITION = 0
DEFAULT_END_SWITCH_WAIT = 120  # Sekunden, so lange wartet „Saison beenden“ höchstens auf den Endschalter
WHITE_SATURATION = 10.0  # Prozent: ein Licht mit weniger Sättigung wird als Weiß über die Farbtemperatur eingestellt
MIN_KELVIN = 2000  # so warm und so kalt darf das Weiß sein, die Lampe begrenzt es auf ihren Bereich
MAX_KELVIN = 6500
END_SWITCH_GRACE_S = 3.0  # so lange darf der Endschalter nach dem Halt der Winde noch brauchen, um „an“ zu melden
LIGHT_COLOR_DELAY_S = 2.0  # so lange nach dem Einschalten (mit Helligkeit) wartet „Saison …“, bevor die Farbe folgt

TARGET_FIELDS = (CONF_START_OFF, CONF_START_ON, CONF_END_OFF, CONF_END_ON)

# --- Einstellungen der Tasten „Saison vorbereiten“ und „Saison beenden“ (config_entry.options) ----
OPT_PREPARE = "prepare"  # gelten für alle Spielpartner und Orte, nicht für die Aufnahme
OPT_FINISH = "finish"
STEP_OFF = "off"  # Ziele (Entities, Bereiche, Labels), die ausgeschaltet werden
STEP_ON = "on"  # Ziele, die eingeschaltet werden
STEP_LIGHTS = "lights"  # Licht-Entity -> {rgb: [r, g, b], brightness: 1-100}, wird mit diesen Werten eingeschaltet
STEP_COVER = "cover"  # Rollo, Winde oder Ähnliches, das auf eine Position fährt
STEP_COVER_POSITION = "cover_position"
STEP_AFTER_OFF = "after_off"  # Ziele, die erst ausgeschaltet werden, wenn der Endschalter an ist (nur „Saison beenden“)
STEP_END_SWITCH = "end_switch"  # Endschalter, zum Beispiel „Endschalter oben“ einer Winde
STEP_UP_SWITCH = "up_switch"  # Schalter „hoch“ der Winde, fährt sie bis zum Endschalter (nur „Saison beenden“)
STEP_WAIT = "wait_seconds"  # so lange wartet die Taste höchstens auf den Endschalter
LIGHT_RGB = "rgb"
LIGHT_BRIGHTNESS = "brightness"

CTRONICS_DOMAIN = "ctronics_cam"  # nur diese Kameras fahren eine PTZ-Position an
CTRONICS_PRESETS_OPTION = "presets"  # Option der Ctronics-Integration: Zeilen wie "1: Schaukel"
MAX_PTZ_PRESET = 64  # so viele Positionen kann eine Ctronics-Kamera speichern

STORAGE_VERSION = 1

SERVICE_START = "start_recording"
SERVICE_STOP = "stop_recording"
SERVICE_PREPARE = "prepare"
SERVICE_NEW_SEASON = "new_season"
SERVICE_END_SEASON = "end_season"
ATTR_PARTNER = "partner"
ATTR_PLACE = "place"

EVENT_STARTED = f"{DOMAIN}_recording_started"
EVENT_STOPPED = f"{DOMAIN}_recording_stopped"
EVENT_SEASON = f"{DOMAIN}_season_started"
EVENT_SEASON_ENDED = f"{DOMAIN}_season_ended"

PHASE_IDLE = "idle"
PHASE_STARTING = "starting"
PHASE_RECORDING = "recording"
PHASE_STOPPING = "stopping"

# Abstand, in dem der Zustand der App abgefragt wird
POLL_SECONDS = 15
