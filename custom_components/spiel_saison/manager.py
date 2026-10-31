"""Kern der Integration: Spielpartner, Orte, Saison-Zähler und der Ablauf einer Aufnahme."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime, time, timedelta
from typing import Any

from homeassistant.components.camera import async_get_stream_source
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME, STATE_OFF, STATE_ON
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import color as color_util
from homeassistant.util import dt as dt_util

from . import logic
from .cams import cam_name, cam_prefix, is_ctronics
from .const import (
    CONF_ANNOUNCE_END,
    CONF_ANNOUNCE_ENTITY,
    CONF_ANNOUNCE_START,
    CONF_BASE_FOLDER,
    CONF_CAM_PRESETS,
    CONF_CAMS,
    CONF_COVER_POSITION,
    CONF_END_OFF,
    CONF_END_ON,
    CONF_END_SECONDS,
    CONF_FILE_PATTERN,
    CONF_LEAD_SECONDS,
    CONF_LIKES,
    CONF_NICKNAME,
    CONF_NUMBER,
    CONF_PARTNER,
    CONF_PARTNER_LIGHTS,
    CONF_PARTNER_PATTERN,
    CONF_RECORDER_KEY,
    CONF_RECORDER_URL,
    CONF_SEASON_PATTERN,
    CONF_START_OFF,
    CONF_START_ON,
    CONF_TREATS,
    COUNTED_KINDS,
    CTRONICS_DOMAIN,
    DEFAULT_BASE_FOLDER,
    DEFAULT_COVER_POSITION,
    DEFAULT_END_SECONDS,
    DEFAULT_END_SWITCH_WAIT,
    DEFAULT_FILE_PATTERN,
    DEFAULT_FINISH_COVER_POSITION,
    DEFAULT_LEAD_SECONDS,
    DEFAULT_PARTNER_PATTERN,
    DEFAULT_SEASON_PATTERN,
    DOMAIN,
    END_SWITCH_GRACE_S,
    EVENT_SEASON,
    EVENT_SEASON_ENDED,
    EVENT_STARTED,
    EVENT_STOPPED,
    KIND_LIKE,
    KIND_TREAT,
    LIGHT_BRIGHTNESS,
    LIGHT_COLOR_DELAY_S,
    LIGHT_KELVIN,
    LIGHT_RGB,
    MAX_KELVIN,
    MIN_KELVIN,
    OPT_FINISH,
    OPT_PREPARE,
    PHASE_IDLE,
    PHASE_RECORDING,
    PHASE_STARTING,
    PHASE_STOPPING,
    POLL_SECONDS,
    STEP_AFTER_OFF,
    STEP_COVER,
    STEP_COVER_POSITION,
    STEP_END_SWITCH,
    STEP_LIGHTS,
    STEP_OFF,
    STEP_ON,
    STEP_UP_SWITCH,
    STEP_WAIT,
    STORAGE_VERSION,
    SUBENTRY_PARTNER,
    SUBENTRY_PLACE,
    WHITE_SATURATION,
)
from .recorder_client import RecorderAuthError, RecorderClient, RecorderError

_LOGGER = logging.getLogger(__name__)

STATUS_IDLE = "idle"
STATUS_RECORDING = "recording"
STATUS_ERROR = "error"

COUNTERS = ("season", "count_season", "count_total")  # bei „Spielpartner bearbeiten“ änderbare Zähler
RESETTABLE = ("seconds_season", "seconds_total", "longest_season")  # Dauern, die man dort auf null setzen kann


@dataclass
class PartnerCfg:
    subentry_id: str
    name: str
    number: int
    nickname: str = ""
    likes: list[str] = field(default_factory=list)  # eigene Vorlieben
    treats: list[str] = field(default_factory=list)  # Vorlieben zum Bearbeiten seines Partners
    lights: list[str] = field(default_factory=list)  # Lichter, die er einstellen darf (seine Beleuchtung)
    announce_entity: str = ""
    announce_start: str = ""
    announce_end: str = ""


@dataclass
class PlaceCfg:
    """Ein Ort gehört zu einem Spielpartner und bringt dessen Kameras für diesen Platz mit."""

    subentry_id: str
    name: str
    partner_id: str
    cams: list[str] = field(default_factory=list)
    cam_presets: dict[str, int] = field(default_factory=dict)  # Kamera -> PTZ-Position, fehlt = nicht bewegen
    start_off: dict[str, Any] = field(default_factory=dict)
    start_on: dict[str, Any] = field(default_factory=dict)
    end_off: dict[str, Any] = field(default_factory=dict)
    end_on: dict[str, Any] = field(default_factory=dict)
    cover_position: int | None = None  # Position der Winde bei „Saison vorbereiten“, ersetzt die von „Konfigurieren“
    lead_s: int = DEFAULT_LEAD_SECONDS
    end_s: int = DEFAULT_END_SECONDS


@dataclass
class SeasonStep:
    """Was eine der Tasten „Saison vorbereiten“ und „Saison beenden“ schaltet (für alle Spielpartner gleich)."""

    off: dict[str, Any] = field(default_factory=dict)
    on: dict[str, Any] = field(default_factory=dict)
    lights: dict[str, dict[str, Any]] = field(default_factory=dict)  # Licht -> rgb und brightness, beides optional
    cover: str = ""
    cover_position: int = DEFAULT_COVER_POSITION
    after_off: dict[str, Any] = field(default_factory=dict)  # erst aus, wenn der Endschalter an ist (nur „beenden“)
    end_switch: str = ""
    up_switch: str = ""  # Schalter „hoch“: fährt die Winde bis zum Endschalter, statt über die Position
    wait_s: int = DEFAULT_END_SWITCH_WAIT


@dataclass
class PartnerState:
    """Zähler und Auswertung eines Spielpartners (gespeichert)."""

    season: int = 1
    season_start: str = ""  # ISO-Datum, an dem die Saison begann
    count_season: int = 0
    count_total: int = 0
    seconds_season: float = 0.0
    seconds_total: float = 0.0
    longest_season: float = 0.0
    last_start: str | None = None
    last_seconds: float | None = None
    seasons: dict[str, dict[str, Any]] = field(default_factory=dict)  # abgeschlossene Saisons
    pref_counts: dict[str, int] = field(default_factory=dict)  # Schlüssel der Vorliebe -> Aufnahmen mit „dabei“
    pref_on: list[str] = field(default_factory=list)  # Schlüssel der Vorlieben, die „dabei“ sind
    lighting: dict[str, dict[str, Any]] = field(default_factory=dict)  # gespeicherte Beleuchtung: Licht -> Zustand


def _target(value: Any) -> dict[str, Any]:
    """Ziel-Auswahl (Entities, Geräte, Bereiche, Labels) ohne leere Listen."""
    return {k: v for k, v in (value or {}).items() if v} if isinstance(value, dict) else {}


def _lighting_of(state: State) -> dict[str, Any] | None:
    """Zustand eines Lichts zum Speichern der Beleuchtung. None, wenn das Licht nicht erreichbar ist."""
    if state.state == STATE_OFF:
        return {"on": False}
    if state.state != STATE_ON:
        return None
    look: dict[str, Any] = {"on": True}
    if (brightness := state.attributes.get("brightness")) is not None:
        look[LIGHT_BRIGHTNESS] = max(1, round(int(brightness) * 100 / 255))
    mode = state.attributes.get("color_mode")
    if mode == "color_temp" and (kelvin := state.attributes.get("color_temp_kelvin")):
        look[LIGHT_KELVIN] = int(kelvin)
    elif mode in ("hs", "xy", "rgb", "rgbw", "rgbww") and (rgb := state.attributes.get("rgb_color")):
        look[LIGHT_RGB] = [int(part) for part in rgb[:3]]
    return look


def _season_step(raw: Any, default_position: int) -> SeasonStep:
    """Einstellungen einer der beiden Tasten aus den Optionen des Eintrags."""
    raw = raw if isinstance(raw, dict) else {}
    return SeasonStep(
        off=_target(raw.get(STEP_OFF)),
        on=_target(raw.get(STEP_ON)),
        lights={
            eid: {key: value for key, value in (values or {}).items() if key in (LIGHT_RGB, LIGHT_BRIGHTNESS) and value}
            for eid, values in (raw.get(STEP_LIGHTS) or {}).items()
        },
        cover=raw.get(STEP_COVER) or "",
        cover_position=int(raw.get(STEP_COVER_POSITION, default_position)),
        after_off=_target(raw.get(STEP_AFTER_OFF)),
        end_switch=raw.get(STEP_END_SWITCH) or "",
        up_switch=raw.get(STEP_UP_SWITCH) or "",
        wait_s=int(raw.get(STEP_WAIT, DEFAULT_END_SWITCH_WAIT)),
    )


def counters_store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    """Speicher für Zähler, Auswertung, gewählten Ort und laufende Aufnahmen."""
    return Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}")


class SpielSaisonManager:
    """Hält Spielpartner, Orte und Zähler eines Config-Entries und steuert die Aufnahmen."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.signal = f"{DOMAIN}_update_{entry.entry_id}"
        self._store = counters_store(hass, entry.entry_id)
        self._unsubs: list[CALLBACK_TYPE] = []
        self.partners: dict[str, PartnerCfg] = {}
        self.places: dict[str, PlaceCfg] = {}
        self.prepare_step = SeasonStep()
        self.finish_step = SeasonStep(cover_position=DEFAULT_FINISH_COVER_POSITION)
        self.states: dict[str, PartnerState] = {}
        self.runs: dict[str, dict[str, Any]] = {}  # laufende Abläufe je Spielpartner
        self.place_ids: dict[str, str] = {}  # gewählter Ort je Spielpartner
        self.rec_status: dict[str, Any] = {}
        self.rec_online: bool | None = None
        self.message: str | None = None
        self.message_time: str | None = None
        self.failed = False
        self.hub_device_id: str | None = None
        self._saved_runs: dict[str, dict[str, Any]] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()
        self._reauth_started = False

    # ------------------------------------------------------------------ Setup
    async def async_load(self) -> None:
        """Zähler laden und Spielpartner und Orte aus den Untereinträgen aufbauen."""
        stored = await self._store.async_load() or {}
        self._read_subentries()
        self.prepare_step = _season_step(self.entry.options.get(OPT_PREPARE), DEFAULT_COVER_POSITION)
        self.finish_step = _season_step(self.entry.options.get(OPT_FINISH), DEFAULT_FINISH_COVER_POSITION)
        known = {f.name for f in fields(PartnerState)}
        today = dt_util.now().date().isoformat()
        for sid in self.partners:
            raw = (stored.get("partners") or {}).get(sid) or {}
            state = PartnerState(**{k: v for k, v in raw.items() if k in known})
            state.season_start = state.season_start or today
            keys = {key for kind, _name, key in self.preferences(self.partners[sid]) if kind in COUNTED_KINDS}
            state.pref_on = [key for key in state.pref_on if key in keys]
            lights = self.partners[sid].lights
            state.lighting = {eid: look for eid, look in state.lighting.items() if eid in lights}
            self.states[sid] = state
        chosen = stored.get("places") or {}
        legacy = stored.get("place")  # bis 0.1.x gab es einen gewählten Ort für alle
        for sid in self.partners:
            options = self.places_of(sid)
            pick = next((c for c in (chosen.get(sid), legacy) if c in options), next(iter(options), None))
            if pick:
                self.place_ids[sid] = pick
        self._saved_runs = {sid: run for sid, run in (stored.get("runs") or {}).items() if sid in self.partners}

    def _read_subentries(self) -> None:
        self.partners, self.places = {}, {}
        for sid, sub in self.entry.subentries.items():
            d = sub.data
            if sub.subentry_type == SUBENTRY_PARTNER:
                self.partners[sid] = PartnerCfg(
                    sid,
                    d[CONF_NAME],
                    int(d.get(CONF_NUMBER) or 1),
                    nickname=(d.get(CONF_NICKNAME) or "").strip(),
                    likes=logic.clean_names(d.get(CONF_LIKES) or []),
                    treats=logic.clean_names(d.get(CONF_TREATS) or []),
                    lights=list(d.get(CONF_PARTNER_LIGHTS) or []),
                    announce_entity=d.get(CONF_ANNOUNCE_ENTITY) or "",
                    announce_start=d.get(CONF_ANNOUNCE_START) or "",
                    announce_end=d.get(CONF_ANNOUNCE_END) or "",
                )
        for sid, sub in self.entry.subentries.items():
            d = sub.data
            if sub.subentry_type != SUBENTRY_PLACE:
                continue
            if d.get(CONF_PARTNER) not in self.partners:
                _LOGGER.warning("Ort %s gehört zu keinem Spielpartner und wird nicht benutzt", d.get(CONF_NAME))
                continue
            cams = list(d.get(CONF_CAMS) or [])
            self.places[sid] = PlaceCfg(
                sid,
                d[CONF_NAME],
                d[CONF_PARTNER],
                cams=cams,
                cam_presets={
                    eid: int(number)
                    for eid, number in (d.get(CONF_CAM_PRESETS) or {}).items()
                    if eid in cams and number
                },
                start_off=_target(d.get(CONF_START_OFF)),
                start_on=_target(d.get(CONF_START_ON)),
                end_off=_target(d.get(CONF_END_OFF)),
                end_on=_target(d.get(CONF_END_ON)),
                cover_position=None if d.get(CONF_COVER_POSITION) is None else int(d[CONF_COVER_POSITION]),
                lead_s=int(d.get(CONF_LEAD_SECONDS, DEFAULT_LEAD_SECONDS)),
                end_s=int(d.get(CONF_END_SECONDS, DEFAULT_END_SECONDS)),
            )

    @staticmethod
    def preferences(partner: PartnerCfg) -> list[tuple[str, str, str]]:
        """Alle Vorlieben eines Spielpartners als (Art, Name, Schlüssel)."""
        return [
            (kind, name, logic.preference_key(kind, name))
            for kind, names in ((KIND_LIKE, partner.likes), (KIND_TREAT, partner.treats))
            for name in names
        ]

    def active_preferences(self, partner: PartnerCfg) -> list[str]:
        """Namen der Vorlieben, bei denen „dabei“ an ist, in der Reihenfolge der Vorlieben des Spielpartners."""
        on = self.states[partner.subentry_id].pref_on
        return [name for _kind, name, key in self.preferences(partner) if key in on]

    async def async_set_preference(self, sid: str, key: str, *, on: bool) -> None:
        """Eine Vorliebe „dabei“ ein- oder ausschalten. Beim Start einer Aufnahme zählen die eingeschalteten."""
        state = self.states[sid]
        if on and key not in state.pref_on:
            state.pref_on.append(key)
        elif not on and key in state.pref_on:
            state.pref_on.remove(key)
        self.notify()  # der Schalter zeigt sofort den neuen Zustand, das Speichern dauert etwas länger
        await self._save()

    def places_of(self, sid: str) -> dict[str, PlaceCfg]:
        """Die Orte eines Spielpartners (Subentry-ID -> Ort)."""
        return {pid: place for pid, place in self.places.items() if place.partner_id == sid}

    async def _save(self) -> None:
        runs = {
            sid: {k: run.get(k) for k in ("place", "no", "start", "folder", "cams", "session")}
            for sid, run in self.runs.items()
            if run["phase"] == PHASE_RECORDING
        }
        await self._store.async_save(
            {"partners": {sid: asdict(s) for sid, s in self.states.items()}, "places": self.place_ids, "runs": runs}
        )

    @callback
    def async_start(self) -> None:
        self._unsubs.append(async_track_time_interval(self.hass, self._poll_recorder, timedelta(seconds=POLL_SECONDS)))
        self._unsubs.append(
            self.hass.bus.async_listen(
                er.EVENT_ENTITY_REGISTRY_UPDATED, self._entity_registry_updated, event_filter=_is_rename
            )
        )
        self.hass.async_create_background_task(self._poll_recorder(None), name=f"{DOMAIN}_poll")
        self._drop_stale_issues()

    async def async_stop(self) -> None:
        """Abläufe beenden. Eine laufende Aufnahme bleibt in der App bestehen und wird beim nächsten Start erkannt."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        for task in list(self._tasks.values()):
            task.cancel()
        for task in list(self._tasks.values()):
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()

    # ---------------------------------------------------------------- Helfer
    @callback
    def notify(self) -> None:
        async_dispatcher_send(self.hass, self.signal)

    def _set_message(self, text: str, *, failed: bool = False) -> None:
        self.message, self.message_time, self.failed = text, dt_util.now().isoformat(), failed

    @property
    def status(self) -> str:
        if self.failed:
            return STATUS_ERROR
        return STATUS_RECORDING if self.recording_partners else STATUS_IDLE

    @property
    def recording_partners(self) -> list[str]:
        """Namen der Spielpartner, bei denen gerade eine Aufnahme läuft oder startet."""
        return sorted(
            self.partners[sid].name
            for sid, run in self.runs.items()
            if sid in self.partners and run["phase"] in (PHASE_STARTING, PHASE_RECORDING)
        )

    def phase(self, sid: str) -> str:
        run = self.runs.get(sid)
        return run["phase"] if run else PHASE_IDLE

    def _data(self, key: str, default: str) -> str:
        value = self.entry.data.get(key)
        return value.strip() if isinstance(value, str) and value.strip() else default

    def _recorder(self) -> RecorderClient:
        url, key = self.entry.data.get(CONF_RECORDER_URL), self.entry.data.get(CONF_RECORDER_KEY)
        if not url or not key:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="recorder_not_configured")
        return RecorderClient(async_get_clientsession(self.hass), url, key)

    def cam_name(self, entity_id: str) -> str:
        return cam_name(self.hass, entity_id)

    def cam_prefix(self, entity_id: str) -> str:
        """Was bei {cam} im Dateinamen steht (Präfix der Kamera, sonst ihr Name)."""
        return cam_prefix(self.hass, entity_id)

    def partner(self, ref: str) -> PartnerCfg:
        if ref in self.partners:
            return self.partners[ref]
        wanted = logic.norm(ref)
        for p in self.partners.values():
            if wanted == logic.norm(p.name):
                return p
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="partner_not_found", translation_placeholders={"partner": ref}
        )

    def place(self, sid: str, ref: str | None) -> PlaceCfg | None:
        """Ein Ort des Spielpartners: der angegebene, ohne Angabe der am Spielpartner gewählte.

        None, wenn der Spielpartner keinen Ort hat.
        """
        options = self.places_of(sid)
        if not ref:
            return options.get(self.place_ids.get(sid) or "")
        if ref in options:
            return options[ref]
        wanted = logic.norm(ref)
        for p in options.values():
            if wanted == logic.norm(p.name):
                return p
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="place_not_found", translation_placeholders={"place": ref}
        )

    @callback
    def _entity_registry_updated(self, event: Event) -> None:
        """Wird eine Kamera-Entity umbenannt, ziehen die Orte mit."""
        old, new = event.data["old_entity_id"], event.data["entity_id"]
        for sid, place in self.places.items():
            if old in place.cams:
                sub = self.entry.subentries[sid]
                presets = dict(sub.data.get(CONF_CAM_PRESETS) or {})
                if old in presets:
                    presets[new] = presets.pop(old)
                data = {
                    **sub.data,
                    CONF_CAMS: [new if c == old else c for c in sub.data[CONF_CAMS]],
                    CONF_CAM_PRESETS: presets,
                }
                self.hass.config_entries.async_update_subentry(self.entry, sub, data=data)

    # ------------------------------------------------------------------ Recorder
    async def _poll_recorder(self, _now: datetime | None) -> None:
        """Zustand der App holen; nach einem HA-Neustart weiterlaufende und verlorene Aufnahmen erkennen."""
        try:
            st = await self._recorder().status()
        except RecorderAuthError:
            self.rec_online = False
            self.notify()
            if not self._reauth_started:
                self._reauth_started = True
                self.entry.async_start_reauth(self.hass)
            return
        except (RecorderError, ServiceValidationError):
            if self.rec_online is not False:
                self.rec_online = False
                self.notify()
            return
        self.rec_status, self.rec_online = st, True
        await self._sync_runs(st)
        self.notify()

    async def _sync_runs(self, st: dict[str, Any]) -> None:
        if "profile" not in st:
            return
        remote = st["profile"]
        for sid, p in list(self.partners.items()):
            ps = remote.get(sid) or {}
            run = self.runs.get(sid)
            if ps.get("aufnahme") and run is None and sid not in self._tasks:
                saved = self._saved_runs.get(sid) or {}
                cams = [k for k, c in (ps.get("kameras") or {}).items() if c.get("status") != "beendet"]
                self.runs[sid] = {
                    "phase": PHASE_RECORDING,
                    "place": saved.get("place"),
                    "no": saved.get("no") or self.states[sid].count_season,
                    "start": saved.get("start") or dt_util.utcnow().isoformat(),
                    "folder": saved.get("folder") or [],
                    "cams": cams,
                    "session": saved.get("session") or "",
                }
            elif (
                not ps.get("aufnahme")
                and run is not None
                and run["phase"] == PHASE_RECORDING
                and not self._lock.locked()
                and sid not in self._tasks
            ):
                reason = ps.get("stop_grund") or "unknown"
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    f"recording_lost_{sid}",
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key="recording_lost",
                    translation_placeholders={"partner": p.name, "reason": reason},
                )
                self._set_message(f"{p.name}: stopped by the recorder ({reason})", failed=True)
                async with self._lock:
                    await self._finish(p, run, reason)

    @callback
    def _drop_stale_issues(self) -> None:
        registry = ir.async_get(self.hass)
        for domain, issue_id in list(registry.issues):
            if domain == DOMAIN and issue_id.startswith("recording_lost_") and issue_id[15:] not in self.partners:
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    def _busy_cam_ids(self, exclude: str) -> set[str]:
        """Kameras, die gerade bei einem anderen Spielpartner aufnehmen (die App kennt auch fremde Aufnahmen)."""
        busy: set[str] = set()
        for sid, run in self.runs.items():
            if sid != exclude and run["phase"] in (PHASE_STARTING, PHASE_RECORDING):
                busy |= set(run.get("cams") or [])
        for pid, ps in (self.rec_status.get("profile") or {}).items():
            if pid != exclude and ps.get("aufnahme"):
                busy |= {k for k, c in (ps.get("kameras") or {}).items() if c.get("status") != "beendet"}
        return busy

    # ------------------------------------------------------------- Zähler und Saison
    async def async_edit_statistics(
        self,
        sid: str,
        counters: Mapping[str, int] | None = None,
        reset: Iterable[str] = (),
        reset_preferences: Iterable[str] = (),
    ) -> None:
        """Zähler ändern (zum Beispiel nach einer Aufnahme ohne Home Assistant) und Dauern auf null setzen.

        `counters` nennt die Felder aus `COUNTERS` mit ihrem neuen Wert, `reset` die Felder aus `RESETTABLE`,
        `reset_preferences` die Schlüssel der Vorlieben, deren Zähler auf null gehen.
        """
        state = self.states[sid]
        counters, reset, reset_preferences = dict(counters or {}), list(reset), list(reset_preferences)
        if unknown := [key for key in (*counters, *reset) if key not in (*COUNTERS, *RESETTABLE)]:
            raise ValueError(unknown[0])
        for counter, value in counters.items():
            setattr(state, counter, int(value))
        for attr in reset:
            setattr(state, attr, 0.0)
        for key in reset_preferences:
            state.pref_counts.pop(key, None)  # ohne Eintrag zählt der Sensor 0
        await self._save()
        self.notify()

    def season_today(self, sid: str) -> bool:
        """Gibt es für heute schon eine Saison? Pro Tag beginnt höchstens eine."""
        return self.states[sid].season_start == dt_util.now().date().isoformat()

    async def async_new_season(self, ref: str) -> dict[str, Any]:
        """Neue Saison beginnen, sofern es für heute noch keine gibt."""
        p = self.partner(ref)
        if self.phase(p.subentry_id) != PHASE_IDLE:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="recording_running",
                translation_placeholders={"partner": p.name},
            )
        started = await self._begin_season(p)
        st = self.states[p.subentry_id]
        result: dict[str, Any] = {"partner": p.name, "season": st.season, "started": started}
        if not started:
            result["reason"] = "already_today"
        return result

    async def _begin_season(self, p: PartnerCfg) -> bool:
        """Saison +1, Zähler der Saison auf null. False, wenn heute schon eine Saison begonnen hat."""
        st = self.states[p.subentry_id]
        if self.season_today(p.subentry_id):
            return False
        st.seasons[str(st.season)] = {
            "start": st.season_start,
            "recordings": st.count_season,
            "seconds": round(st.seconds_season),
            "longest": round(st.longest_season),
        }
        st.season += 1
        st.season_start = dt_util.now().date().isoformat()
        st.count_season, st.seconds_season, st.longest_season = 0, 0.0, 0.0
        await self._save()
        self._set_message(f"{p.name}: season {st.season}")
        self.notify()  # erst der Zustand, dann das Ereignis: wer es hört, sieht die Sensoren schon aktualisiert
        self.hass.bus.async_fire(
            EVENT_SEASON,
            {"partner": p.name, "partner_id": p.subentry_id, "season": st.season, "start": st.season_start},
        )
        return True

    async def async_select_place(self, sid: str, place_id: str) -> None:
        """Den Ort wählen, den Vorbereiten und Aufnahme des Spielpartners verwenden."""
        if place_id in self.places_of(sid):
            self.place_ids[sid] = place_id
            await self._save()
            self.notify()

    # --------------------------------------------------------------------- Stufen
    async def _call(self, domain: str, service: str, data: dict[str, Any]) -> None:
        """Dienst aufrufen. Ein Fehler hält den Ablauf nicht an, die Aufnahme ist wichtiger als das Licht."""
        try:
            await self.hass.services.async_call(domain, service, data, blocking=True)
        except HomeAssistantError as err:
            _LOGGER.warning("%s.%s fehlgeschlagen: %s", domain, service, err)
            self._set_message(f"{domain}.{service}: {err}")

    async def _turn(self, service: str, target: dict[str, Any]) -> None:
        if target:
            await self._call("homeassistant", service, dict(target))

    async def _announce(self, partner: PartnerCfg, place: PlaceCfg, text: str) -> None:
        """Ansage über die notify-Entity des Spielpartners. Platzhalter: partner, nickname, nr, saison, ort."""
        if partner.announce_entity and text:
            values = {
                "partner": partner.name,
                "nickname": partner.nickname or partner.name,
                "nr": logic.pad(partner.number),
                "saison": logic.pad(self.states[partner.subentry_id].season),
                "ort": place.name,
            }
            message = logic.render_pattern(text, dt_util.now(), values)
            await self._call("notify", "send_message", {"entity_id": partner.announce_entity, "message": message})

    async def _move_cover(self, step: SeasonStep) -> None:
        """Rollo oder Winde auf die Position von „Konfigurieren“ fahren."""
        if step.cover:
            await self._call("cover", "set_cover_position", {"entity_id": step.cover, "position": step.cover_position})

    async def _winch_to_place(self, place: PlaceCfg) -> None:
        """Beim Start einer Aufnahme fährt die Winde (die von „Saison vorbereiten“) auf die Position des Ortes."""
        cover = self.prepare_step.cover
        if cover and place.cover_position is not None:
            await self._call("cover", "set_cover_position", {"entity_id": cover, "position": place.cover_position})

    def _is_on(self, entity_id: str) -> bool:
        state = self.hass.states.get(entity_id)
        return state is not None and state.state == STATE_ON

    def _end_switch_error(self, p: PartnerCfg, step: SeasonStep) -> HomeAssistantError:
        """Fehler, wenn der Endschalter nicht rechtzeitig an war: die Ziele für „sobald er an ist“ bleiben an."""
        self._set_message(f"{p.name}: {step.end_switch} not on after {step.wait_s} s, not switched off", failed=True)
        self.notify()
        return HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="end_switch_timeout",
            translation_placeholders={"partner": p.name, "details": f"{step.end_switch}, {step.wait_s} s"},
        )

    async def _drive_up(self, p: PartnerCfg, step: SeasonStep) -> None:
        """Die Winde mit dem Schalter „hoch“ bis zum Endschalter fahren, wie das Skript beim Kalibrieren.

        Fährt die Winde nur nach der Zeitschätzung auf Position 0, bleibt sie oft knapp vor dem Endschalter stehen und
        der Endschalter geht nie an. Der Schalter „hoch“ läuft, bis der Endschalter an ist oder er sich selbst
        ausschaltet. Steht die Winde schon oben (Endschalter an), passiert nichts.
        """
        if self._is_on(step.end_switch):
            return
        up = {"entity_id": [step.up_switch]}
        await self._turn("turn_on", up)
        try:
            await self._wait_end_or_stop(step.end_switch, step.up_switch, step.wait_s)
        finally:
            await self._turn("turn_off", up)
        if not self._is_on(step.end_switch) and not await self._wait_until_on(step.end_switch, END_SWITCH_GRACE_S):
            raise self._end_switch_error(p, step)

    async def _wait_end_or_stop(self, end_switch: str, up_switch: str, seconds: float) -> None:
        """Wartet höchstens `seconds`, bis der Endschalter an ist oder der Schalter „hoch“ ausgeht."""
        if self._is_on(end_switch):
            return
        finished = asyncio.Event()

        @callback
        def changed(event: Event) -> None:
            new = event.data["new_state"]
            if new is None:
                return
            if (event.data["entity_id"] == end_switch and new.state == STATE_ON) or (
                event.data["entity_id"] == up_switch and new.state == STATE_OFF
            ):
                finished.set()

        unsub = async_track_state_change_event(self.hass, [end_switch, up_switch], changed)
        try:
            async with asyncio.timeout(seconds):
                await finished.wait()
        except TimeoutError:
            pass  # der Aufrufer prüft den Endschalter
        finally:
            unsub()

    async def _wait_until_on(self, entity_id: str, seconds: float) -> bool:
        """Wartet höchstens `seconds`, bis die Entity an ist. False, wenn sie es bis dahin nicht wurde."""
        if (state := self.hass.states.get(entity_id)) and state.state == STATE_ON:
            return True
        reached = asyncio.Event()

        @callback
        def changed(event: Event) -> None:
            if (new := event.data["new_state"]) and new.state == STATE_ON:
                reached.set()

        unsub = async_track_state_change_event(self.hass, [entity_id], changed)
        try:
            async with asyncio.timeout(seconds):
                await reached.wait()
        except TimeoutError:
            return False
        finally:
            unsub()
        return True

    @staticmethod
    def _light_color(look: dict[str, Any]) -> dict[str, Any]:
        """Die Farbe eines Lichts als Dienstdaten: ein Weiß als Farbtemperatur, jede andere Farbe als `rgb_color`."""
        if look.get(LIGHT_KELVIN):
            return {"color_temp_kelvin": max(MIN_KELVIN, min(MAX_KELVIN, int(look[LIGHT_KELVIN])))}
        if LIGHT_RGB not in look:
            return {}
        red, green, blue = (int(part) for part in look[LIGHT_RGB])
        if max(red, green, blue) and color_util.color_RGB_to_hs(red, green, blue)[1] < WHITE_SATURATION:
            # Weiß (fast ohne Farbe) bekommt die Lampe als Farbtemperatur, nicht als Farbton mit Sättigung nahe null
            kelvin = color_util.color_xy_to_temperature(*color_util.color_RGB_to_xy(red, green, blue))
            return {"color_temp_kelvin": max(MIN_KELVIN, min(MAX_KELVIN, kelvin))}
        return {"rgb_color": [red, green, blue]}

    async def _lights_on(self, lights: dict[str, dict[str, Any]]) -> None:
        """Lichter einschalten, erst mit der Helligkeit, dann folgt die Farbe allein.

        Ein Befehl mit Helligkeit und Farbe zugleich lässt Matter-Lampen (H600D) in ihrer letzten Farbe leuchten, auch
        wenn sie die neue Farbe melden. Die Farbe allein, so wie die Farbwahl in Home Assistant sie schickt, nehmen sie
        an. Darum geht zuerst das Licht an (mit Helligkeit), und nach kurzer Zeit kommt die Farbe als eigener Befehl.
        """
        for entity_id, look in lights.items():
            data: dict[str, Any] = {"entity_id": entity_id}
            if LIGHT_BRIGHTNESS in look:
                data["brightness_pct"] = int(look[LIGHT_BRIGHTNESS])
            await self._call("light", "turn_on", data)
        coloured = {
            entity_id: self._light_color(look)
            for entity_id, look in lights.items()
            if LIGHT_RGB in look or look.get(LIGHT_KELVIN)
        }
        if coloured:
            await asyncio.sleep(LIGHT_COLOR_DELAY_S)
            for entity_id, color in coloured.items():
                await self._call("light", "turn_on", {"entity_id": entity_id, **color})

    async def _apply_lighting(self, p: PartnerCfg) -> None:
        """Die gespeicherte Beleuchtung einstellen: gespeicherte Aus-Lichter aus, der Rest wie gespeichert."""
        saved = self.states[p.subentry_id].lighting
        off = [entity_id for entity_id in p.lights if entity_id in saved and not saved[entity_id].get("on", True)]
        if off:
            await self._call("light", "turn_off", {"entity_id": off})
        await self._lights_on(
            {entity_id: look for entity_id, look in saved.items() if entity_id in p.lights and look.get("on", True)}
        )

    async def async_save_lighting(self, ref: str) -> dict[str, Any]:
        """Die Beleuchtung, wie sie jetzt ist, für den Spielpartner speichern (für die nächsten Saisons)."""
        p = self.partner(ref)
        if not p.lights:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="no_partner_lights",
                translation_placeholders={"partner": p.name},
            )
        st = self.states[p.subentry_id]
        saved: dict[str, dict[str, Any]] = {}
        for entity_id in p.lights:
            state = self.hass.states.get(entity_id)
            if state is not None and (look := _lighting_of(state)) is not None:
                saved[entity_id] = look
            elif entity_id in st.lighting:  # nicht erreichbar: der gespeicherte Zustand bleibt
                saved[entity_id] = st.lighting[entity_id]
        if not saved:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="lighting_unreadable",
                translation_placeholders={"partner": p.name},
            )
        st.lighting = saved
        await self._save()
        on = sum(1 for look in saved.values() if look.get("on", True))
        self._set_message(f"{p.name}: lighting saved ({len(saved)} lights, {on} on)")
        self.notify()
        return {"partner": p.name, "lights": len(saved), "on": on}

    async def _stage_season(self, p: PartnerCfg, step: SeasonStep, *, cover_first: bool = False) -> None:
        """„Saison vorbereiten“ oder „Saison beenden“: ausschalten, einschalten, Lichter mit Farbe, Rollo oder Winde.

        Beim Beenden fährt das Rollo oder die Winde zuerst (sie braucht dafür noch Strom). Danach wartet die Taste auf
        den Endschalter und schaltet erst dann die Ziele „ausschalten, sobald der Endschalter an ist“ aus. Kommt der
        Endschalter nicht rechtzeitig, bleiben sie an und der Aufruf meldet einen Fehler.
        """
        if cover_first:
            if step.up_switch and step.end_switch:
                await self._drive_up(p, step)
            else:
                await self._move_cover(step)
        await self._turn("turn_off", step.off)
        await self._turn("turn_on", step.on)
        await self._lights_on(step.lights)
        if not cover_first:
            await self._move_cover(step)
        if not step.after_off:
            return
        if step.end_switch and not await self._wait_until_on(step.end_switch, step.wait_s):
            raise self._end_switch_error(p, step)
        await self._turn("turn_off", step.after_off)

    async def _stage_start(self, partner: PartnerCfg, place: PlaceCfg, cams: list[str]) -> None:
        await self._winch_to_place(place)
        by_preset: dict[int, list[str]] = {}  # Kameras mit derselben Position in einem Aufruf
        for eid in cams:
            if (number := place.cam_presets.get(eid)) and is_ctronics(self.hass, eid):
                by_preset.setdefault(number, []).append(eid)
        if self.hass.services.has_service(CTRONICS_DOMAIN, "ptz_goto_preset"):
            for number, eids in by_preset.items():
                await self._call(CTRONICS_DOMAIN, "ptz_goto_preset", {"entity_id": eids, "preset": number})
        await self._turn("turn_off", place.start_off)
        await self._turn("turn_on", place.start_on)
        if self.states[partner.subentry_id].count_season == 0:  # erst mit der ersten Aufnahme der Saison
            await self._apply_lighting(partner)
        await self._announce(partner, place, partner.announce_start)

    async def _stage_end(self, partner: PartnerCfg, place: PlaceCfg, *, wait: bool = True) -> None:
        if wait and place.end_s:
            await asyncio.sleep(place.end_s)
        await self._turn("turn_off", place.end_off)
        await self._turn("turn_on", place.end_on)
        await self._announce(partner, place, partner.announce_end)

    async def async_prepare(self, ref: str, place_ref: str | None = None) -> dict[str, Any]:
        """Neue Saison beginnen (einmal pro Tag), ihren Ordner anlegen und „Saison vorbereiten“ ausführen.

        Der Ort (der angegebene, sonst der gewählte) füllt nur den Platzhalter `ort` der Ordner-Muster. Die Winde fährt
        auf die Position von „Konfigurieren“, die Position des Ortes gilt erst beim Start einer Aufnahme.
        Ein Spielpartner ohne Ort kann die Saison trotzdem vorbereiten.
        """
        p = self.partner(ref)
        place = self.place(p.subentry_id, place_ref)
        new_season = self.phase(p.subentry_id) == PHASE_IDLE and await self._begin_season(p)
        folder = self._season_folder(p, place, dt_util.now())
        created = await self._ensure_folder(p, folder)
        await self._stage_season(p, self.prepare_step)
        if created is not None:  # sonst bleibt die Meldung zum Saison-Ordner stehen
            self._set_message(f"{p.name}: prepared")
        self.notify()
        return {
            "partner": p.name,
            "place": place.name if place else "",
            "season": self.states[p.subentry_id].season,
            "new_season": new_season,
            "folder": "/".join(folder),
            "folder_created": created,
        }

    async def async_end_season(self, ref: str) -> dict[str, Any]:
        """Die Saison des Spielpartners beenden: „Saison beenden“ ausführen, nur ohne laufende Aufnahme."""
        p = self.partner(ref)
        if self.phase(p.subentry_id) != PHASE_IDLE:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="season_end_recording_running",
                translation_placeholders={"partner": p.name},
            )
        st = self.states[p.subentry_id]
        await self._stage_season(p, self.finish_step, cover_first=True)
        result = {
            "partner": p.name,
            "season": st.season,
            "recordings": st.count_season,
            "seconds": round(st.seconds_season),
        }
        self._set_message(f"{p.name}: season {st.season} ended")
        self.notify()  # erst der Zustand, dann das Ereignis
        self.hass.bus.async_fire(
            EVENT_SEASON_ENDED,
            {**result, "partner_id": p.subentry_id, "nickname": p.nickname, "start": st.season_start},
        )
        return result

    async def _ensure_folder(self, p: PartnerCfg, folder: list[str]) -> bool | None:
        """Ordner der aktuellen Saison anlegen, falls es ihn im Zielordner noch nicht gibt.

        True = neu angelegt, False = gab es schon, None = die App ließ sich nicht fragen (das Vorbereiten läuft weiter).
        """
        try:
            client = self._recorder()
            if folder[-1] in await client.list_dirs(folder[:-1]):
                return False
            await client.make_dir(folder)
        except (RecorderError, ServiceValidationError) as err:
            _LOGGER.warning("%s: Saison-Ordner %s nicht angelegt: %s", p.name, "/".join(folder), err)
            self._set_message(f"{p.name}: {err}")
            return None
        return True

    def _place_or_raise(self, p: PartnerCfg, place_ref: str | None) -> PlaceCfg:
        place = self.place(p.subentry_id, place_ref)
        if place is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="no_place", translation_placeholders={"partner": p.name}
            )
        return place

    # ---------------------------------------------------------------------- Start
    async def _stream_url(self, entity_id: str) -> str | None:
        try:
            return await async_get_stream_source(self.hass, entity_id)
        except HomeAssistantError as err:
            _LOGGER.warning("Stream-Quelle von %s nicht lesbar: %s", entity_id, err)
            return None

    async def async_start_recording(self, ref: str, place_ref: str | None = None) -> dict[str, Any]:
        """Aufnahme eines Spielpartners starten. Beginn-Schritt und Vorlauf laufen danach im Hintergrund."""
        p = self.partner(ref)
        place = self._place_or_raise(p, place_ref)
        sid = p.subentry_id
        client = self._recorder()
        async with self._lock:
            phase = self.phase(sid)
            if phase in (PHASE_STARTING, PHASE_RECORDING):
                return {"started": False, "reason": "already_recording"}
            if phase == PHASE_STOPPING:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="still_stopping",
                    translation_placeholders={"partner": p.name},
                )
            try:  # frischer Stand der App, damit Aufnahmen anderer Spielpartner nicht übersehen werden
                self.rec_status, self.rec_online = await client.status(), True
            except RecorderError as err:
                self.rec_online = False
                self._set_message(f"{p.name}: {err}", failed=True)
                self.notify()
                raise
            busy = self._busy_cam_ids(sid)
            skipped: list[dict[str, str]] = []
            ready: list[tuple[str, str]] = []
            for eid in place.cams:
                if self.hass.states.get(eid) is None:
                    skipped.append({"cam": eid, "reason": "missing"})
                elif eid in busy:
                    skipped.append({"cam": eid, "reason": "busy"})
                elif not (url := await self._stream_url(eid)):
                    skipped.append({"cam": eid, "reason": "no_stream"})
                else:
                    ready.append((eid, url))
            if not ready:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="no_cam_available",
                    translation_placeholders={"partner": p.name, "details": _describe(skipped)},
                )
            now = dt_util.now()
            names = logic.unique_names(
                [self.cam_name(eid) for eid, _ in ready], [eid.split(".", 1)[1] for eid, _ in ready]
            )
            folder, session, stems = self._plan(p, place, now, [self.cam_prefix(eid) for eid, _ in ready])
            cams = [
                {"id": eid, "name": name, "url": url, "stem": stem}
                for (eid, url), name, stem in zip(ready, names, stems, strict=True)
            ]
            run = {
                "phase": PHASE_STARTING,
                "place": place.subentry_id,
                "no": self.states[sid].count_season + 1,
                "start": dt_util.utcnow().isoformat(),
                "folder": folder,
                "cams": [c["id"] for c in cams],
                "session": session,
            }
            self.runs[sid] = run
            # Hintergrundaufgabe, die nicht sofort loslaufen darf: der Ablauf trägt sich selbst aus _tasks aus
            # und darf erst nach dem Eintragen enden. Ein langer Vorlauf hält so auch das Herunterfahren nicht auf.
            self._tasks[sid] = self.hass.async_create_background_task(
                self._start_sequence(p, place, run, cams, client), f"{DOMAIN}_start_{sid}", eager_start=False
            )
        self.notify()
        return {
            "started": True,
            "partner": p.name,
            "place": place.name,
            "cams": [c["name"] for c in cams],
            "skipped": skipped,
            "folder": "/".join(folder),
        }

    def _pattern_values(self, p: PartnerCfg, place: PlaceCfg | None, videos: int = 1) -> dict[str, str]:
        """Werte für die Platzhalter der Ordner- und Dateimuster.

        `videos` ist die Zahl der Kameras des Starts: {gesamt} ist der Stand von „Aufnahmen gesamt“, wie ihn der Sensor
        nach dem Start zeigt, {aufnahme} der Stand von „Aufnahmen in Saison“ danach.
        """
        st = self.states[p.subentry_id]
        return {
            "partner": p.name,
            "nr": logic.pad(p.number),
            "saison": logic.pad(st.season),
            "aufnahme": logic.pad(st.count_season + 1),
            "gesamt": logic.pad(st.count_total + videos),
            "ort": place.name if place else "",
        }

    def _season_folder(self, p: PartnerCfg, place: PlaceCfg | None, now: datetime, videos: int = 1) -> list[str]:
        """Ordner der aktuellen Saison: Basis-Ordner, Ordner des Spielpartners, Ordner der Saison."""
        values = self._pattern_values(p, place, videos)
        season_day = datetime.combine(date.fromisoformat(self.states[p.subentry_id].season_start), time())
        return [
            *logic.split_path(self._data(CONF_BASE_FOLDER, DEFAULT_BASE_FOLDER)),
            *logic.split_path(
                logic.render_pattern(self._data(CONF_PARTNER_PATTERN, DEFAULT_PARTNER_PATTERN), now, values)
            ),
            *logic.split_path(
                logic.render_pattern(self._data(CONF_SEASON_PATTERN, DEFAULT_SEASON_PATTERN), season_day, values)
            ),
        ]

    def _plan(
        self, p: PartnerCfg, place: PlaceCfg | None, now: datetime, prefixes: list[str]
    ) -> tuple[list[str], str, list[str]]:
        """Zielordner, Sitzungsname und Dateinamen der Kameras für die nächste Aufnahme.

        `prefixes` sind die Präfixe der Kameras in ihrer Reihenfolge, sie stehen bei {cam} im Dateinamen. Gleiche
        Dateinamen macht `unique_stems` eindeutig. Ihre Zahl ist die Zahl der Videos des Starts für {gesamt}.
        """
        videos = max(1, len(prefixes))
        values = self._pattern_values(p, place, videos)
        folder = self._season_folder(p, place, now, videos)
        file_pattern = self._data(CONF_FILE_PATTERN, DEFAULT_FILE_PATTERN)
        stems = logic.unique_stems(
            [
                logic.render_pattern(file_pattern, now, {**values, "cam": prefix, "cam_nr": logic.pad(index)})
                for index, prefix in enumerate(prefixes, start=1)
            ]
        )
        session = f"{p.name} Saison {values['saison']} Aufnahme {values['aufnahme']}"
        return folder, session, stems

    def preview_folder(self, sid: str) -> list[str]:
        """Zielordner der laufenden oder nächsten Aufnahme eines Spielpartners."""
        run = self.runs.get(sid)
        if run and run.get("folder"):
            return list(run["folder"])
        p = self.partners[sid]
        place = self.place(sid, None)
        prefixes = [self.cam_prefix(eid) for eid in place.cams] if place else []
        return self._plan(p, place, dt_util.now(), prefixes or ["Cam"])[0]

    async def _start_sequence(
        self,
        p: PartnerCfg,
        place: PlaceCfg,
        run: dict[str, Any],
        cams: list[dict[str, str]],
        client: RecorderClient,
    ) -> None:
        sid = p.subentry_id
        try:
            await self._stage_start(p, place, run["cams"])
            if place.lead_s:
                await asyncio.sleep(place.lead_s)
            await client.start(run["folder"], cams, run["session"], sid)
        except RecorderError as err:
            _LOGGER.warning("%s: Aufnahme konnte nicht starten: %s", p.name, err)
            self._set_message(f"{p.name}: {err}", failed=True)
            run["phase"] = PHASE_STOPPING
            self.notify()
            await self._end_sequence(sid, place, wait=False)
            return
        except asyncio.CancelledError:
            raise
        st = self.states[sid]
        st.count_season += 1  # eine Aufnahme je Start, egal wie viele Kameras
        st.count_total += len(cams)  # insgesamt zählt jedes Video, also jede Kamera, die aufnimmt
        for key in st.pref_on:
            st.pref_counts[key] = st.pref_counts.get(key, 0) + 1
        preferences = self.active_preferences(p)  # dieselben, die eben mitgezählt wurden
        run["start"] = st.last_start = dt_util.utcnow().isoformat()
        run["phase"] = PHASE_RECORDING
        self._tasks.pop(sid, None)
        await self._save()
        ir.async_delete_issue(self.hass, DOMAIN, f"recording_lost_{sid}")
        self._set_message(f"{p.name}: recording ({len(cams)} cams)")
        self.notify()  # erst der Zustand, dann das Ereignis: die Zähler-Sensoren zeigen den neuen Wert schon
        self.hass.bus.async_fire(
            EVENT_STARTED,
            {
                "partner": p.name,
                "nickname": p.nickname or p.name,
                "partner_id": sid,
                "season": st.season,
                "recording": run["no"],  # die Nummer in den Dateinamen, alle Kameras des Starts teilen sie
                "place": place.name,
                "folder": "/".join(run["folder"]),
                "cams": run["cams"],
                "session": run["session"],
                # Dateiname ohne Endung je Kamera. Die Stream-Adresse bleibt draußen, sie kann Zugangsdaten tragen.
                "files": [{"cam": cam["id"], "name": cam["stem"]} for cam in cams],
                "preferences": preferences,
            },
        )

    # ----------------------------------------------------------------------- Stop
    async def async_stop_recording(self, ref: str) -> dict[str, Any]:
        """Aufnahme eines Spielpartners beenden. Der Ende-Schritt läuft danach im Hintergrund."""
        p = self.partner(ref)
        sid = p.subentry_id
        async with self._lock:
            run = self.runs.get(sid)
            if run is None:
                return {"stopped": False, "reason": "not_recording"}
            if run["phase"] == PHASE_STOPPING:
                return {"stopped": False, "reason": "already_stopping"}
            if run["phase"] == PHASE_STARTING:
                task = self._tasks.pop(sid, None)
                if task:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
                with contextlib.suppress(RecorderError):  # falls die App schon gestartet hatte
                    await self._recorder().stop(sid)
                await self._finish(p, run, "cancelled", recorded=False)
                return {"stopped": True}
            run["phase"] = PHASE_STOPPING
            self.notify()
            try:
                await self._recorder().stop(sid)
            except RecorderError as err:
                run["phase"] = PHASE_RECORDING
                self._set_message(f"{p.name}: {err}", failed=True)
                self.notify()
                raise
            await self._finish(p, run, "manual")
            return {"stopped": True}

    async def _finish(self, p: PartnerCfg, run: dict[str, Any], reason: str, *, recorded: bool = True) -> None:
        """Auswertung eintragen, Ereignis senden und den Ende-Schritt im Hintergrund starten."""
        sid = p.subentry_id
        st = self.states[sid]
        seconds = 0.0
        if recorded:
            started = dt_util.parse_datetime(run["start"])
            seconds = max(0.0, (dt_util.utcnow() - started).total_seconds()) if started else 0.0
            st.seconds_season += seconds
            st.seconds_total += seconds
            st.longest_season = max(st.longest_season, seconds)
            st.last_seconds = seconds
        run["phase"] = PHASE_STOPPING
        await self._save()
        place = self.places.get(run.get("place") or "")
        self._set_message(f"{p.name}: stopped ({reason})")
        self._tasks[sid] = self.hass.async_create_background_task(
            self._end_sequence(sid, place, wait=recorded), f"{DOMAIN}_end_{sid}", eager_start=False
        )
        self.notify()  # erst der Zustand, dann das Ereignis: die Sensoren zeigen Auswertung und Phase schon
        self.hass.bus.async_fire(
            EVENT_STOPPED,
            {
                "partner": p.name,
                "nickname": p.nickname or p.name,
                "partner_id": sid,
                "season": st.season,
                "recording": run.get("no") if recorded else st.count_season,
                "place": place.name if place else None,
                "reason": reason,
                "duration_s": round(seconds),
            },
        )

    async def _end_sequence(self, sid: str, place: PlaceCfg | None, *, wait: bool) -> None:
        try:
            if place and (p := self.partners.get(sid)):
                await self._stage_end(p, place, wait=wait)
        finally:
            self.runs.pop(sid, None)
            if self._tasks.get(sid) is asyncio.current_task():
                self._tasks.pop(sid, None)
            await self._save()
            self.notify()


@callback
def _is_rename(data: er.EventEntityRegistryUpdatedData) -> bool:
    return data["action"] == "update" and "old_entity_id" in data


def _describe(skipped: list[dict[str, str]]) -> str:
    return ", ".join(f"{s['cam']} ({s['reason']})" for s in skipped) or "-"
