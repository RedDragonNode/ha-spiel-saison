"""Kern der Integration: Spielpartner, Orte, Saison-Zähler und der Ablauf einer Aufnahme."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime, time, timedelta
from typing import Any

from homeassistant.components.camera import async_get_stream_source
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import logic
from .cams import cam_name, is_ctronics
from .const import (
    CONF_ANNOUNCE_END,
    CONF_ANNOUNCE_ENTITY,
    CONF_ANNOUNCE_START,
    CONF_BASE_FOLDER,
    CONF_CAM_PRESETS,
    CONF_CAMS,
    CONF_COVER,
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
    CONF_PARTNER_PATTERN,
    CONF_PREPARE_ON,
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
    DEFAULT_FILE_PATTERN,
    DEFAULT_LEAD_SECONDS,
    DEFAULT_PARTNER_PATTERN,
    DEFAULT_SEASON_PATTERN,
    DOMAIN,
    EVENT_SEASON,
    EVENT_STARTED,
    EVENT_STOPPED,
    KIND_LIKE,
    KIND_TREAT,
    PHASE_IDLE,
    PHASE_RECORDING,
    PHASE_STARTING,
    PHASE_STOPPING,
    POLL_SECONDS,
    STORAGE_VERSION,
    SUBENTRY_PARTNER,
    SUBENTRY_PLACE,
)
from .recorder_client import RecorderAuthError, RecorderClient, RecorderError

_LOGGER = logging.getLogger(__name__)

STATUS_IDLE = "idle"
STATUS_RECORDING = "recording"
STATUS_ERROR = "error"

COUNTERS = ("season", "count_season", "count_total")  # von Hand änderbare Zähler


@dataclass
class PartnerCfg:
    subentry_id: str
    name: str
    number: int
    nickname: str = ""
    likes: list[str] = field(default_factory=list)  # eigene Vorlieben
    treats: list[str] = field(default_factory=list)  # Vorlieben zum Bearbeiten seines Partners
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
    prepare_on: dict[str, Any] = field(default_factory=dict)
    cover: str = ""
    cover_position: int = DEFAULT_COVER_POSITION
    start_off: dict[str, Any] = field(default_factory=dict)
    start_on: dict[str, Any] = field(default_factory=dict)
    end_off: dict[str, Any] = field(default_factory=dict)
    end_on: dict[str, Any] = field(default_factory=dict)
    lead_s: int = DEFAULT_LEAD_SECONDS
    end_s: int = DEFAULT_END_SECONDS


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


def _target(value: Any) -> dict[str, Any]:
    """Ziel-Auswahl (Entities, Geräte, Bereiche, Labels) ohne leere Listen."""
    return {k: v for k, v in (value or {}).items() if v} if isinstance(value, dict) else {}


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
        known = {f.name for f in fields(PartnerState)}
        today = dt_util.now().date().isoformat()
        for sid in self.partners:
            raw = (stored.get("partners") or {}).get(sid) or {}
            state = PartnerState(**{k: v for k, v in raw.items() if k in known})
            state.season_start = state.season_start or today
            keys = {key for kind, _name, key in self.preferences(self.partners[sid]) if kind in COUNTED_KINDS}
            state.pref_on = [key for key in state.pref_on if key in keys]
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
                prepare_on=_target(d.get(CONF_PREPARE_ON)),
                cover=d.get(CONF_COVER) or "",
                cover_position=int(d.get(CONF_COVER_POSITION, DEFAULT_COVER_POSITION)),
                start_off=_target(d.get(CONF_START_OFF)),
                start_on=_target(d.get(CONF_START_ON)),
                end_off=_target(d.get(CONF_END_OFF)),
                end_on=_target(d.get(CONF_END_ON)),
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
    async def async_set_counter(self, sid: str, counter: str, value: int) -> None:
        """Zähler von Hand ändern (z. B. nach einer Aufnahme ohne Home Assistant)."""
        if counter not in COUNTERS:
            raise ValueError(counter)
        setattr(self.states[sid], counter, int(value))
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
        self.hass.bus.async_fire(
            EVENT_SEASON,
            {"partner": p.name, "partner_id": p.subentry_id, "season": st.season, "start": st.season_start},
        )
        self._set_message(f"{p.name}: season {st.season}")
        self.notify()
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

    async def _stage_prepare(self, place: PlaceCfg) -> None:
        await self._turn("turn_on", place.prepare_on)
        if place.cover:
            await self._call(
                "cover", "set_cover_position", {"entity_id": place.cover, "position": place.cover_position}
            )

    async def _stage_start(self, partner: PartnerCfg, place: PlaceCfg, cams: list[str]) -> None:
        by_preset: dict[int, list[str]] = {}  # Kameras mit derselben Position in einem Aufruf
        for eid in cams:
            if (number := place.cam_presets.get(eid)) and is_ctronics(self.hass, eid):
                by_preset.setdefault(number, []).append(eid)
        if self.hass.services.has_service(CTRONICS_DOMAIN, "ptz_goto_preset"):
            for number, eids in by_preset.items():
                await self._call(CTRONICS_DOMAIN, "ptz_goto_preset", {"entity_id": eids, "preset": number})
        await self._turn("turn_off", place.start_off)
        await self._turn("turn_on", place.start_on)
        await self._announce(partner, place, partner.announce_start)

    async def _stage_end(self, partner: PartnerCfg, place: PlaceCfg, *, wait: bool = True) -> None:
        if wait and place.end_s:
            await asyncio.sleep(place.end_s)
        await self._turn("turn_off", place.end_off)
        await self._turn("turn_on", place.end_on)
        await self._announce(partner, place, partner.announce_end)

    async def async_prepare(self, ref: str, place_ref: str | None = None) -> dict[str, Any]:
        """Neue Saison beginnen (einmal pro Tag) und den Ort vorbereiten, ohne die Aufnahme zu starten."""
        p = self.partner(ref)
        place = self._place_or_raise(p, place_ref)
        new_season = self.phase(p.subentry_id) == PHASE_IDLE and await self._begin_season(p)
        folder = self._season_folder(p, place, dt_util.now())
        created = await self._ensure_folder(p, folder)
        await self._stage_prepare(place)
        if created is not None:  # sonst bleibt die Meldung zum Saison-Ordner stehen
            self._set_message(f"{p.name}: prepared ({place.name})")
        self.notify()
        return {
            "partner": p.name,
            "place": place.name,
            "season": self.states[p.subentry_id].season,
            "new_season": new_season,
            "folder": "/".join(folder),
            "folder_created": created,
        }

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
            folder, session, stems = self._plan(p, place, now, names)
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

    def _pattern_values(self, p: PartnerCfg, place: PlaceCfg | None) -> dict[str, str]:
        """Werte für die Platzhalter der Ordner- und Dateimuster."""
        st = self.states[p.subentry_id]
        return {
            "partner": p.name,
            "nr": logic.pad(p.number),
            "saison": logic.pad(st.season),
            "aufnahme": logic.pad(st.count_season + 1),
            "ort": place.name if place else "",
        }

    def _season_folder(self, p: PartnerCfg, place: PlaceCfg | None, now: datetime) -> list[str]:
        """Ordner der aktuellen Saison: Basis-Ordner, Ordner des Spielpartners, Ordner der Saison."""
        values = self._pattern_values(p, place)
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
        self, p: PartnerCfg, place: PlaceCfg | None, now: datetime, names: list[str]
    ) -> tuple[list[str], str, list[str]]:
        """Zielordner, Sitzungsname und Dateinamen der Kameras für die nächste Aufnahme."""
        values = self._pattern_values(p, place)
        folder = self._season_folder(p, place, now)
        file_pattern = self._data(CONF_FILE_PATTERN, DEFAULT_FILE_PATTERN)
        stems = logic.unique_stems(
            [
                logic.render_pattern(file_pattern, now, {**values, "cam": name, "cam_nr": logic.pad(index)})
                for index, name in enumerate(names, start=1)
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
        names = [self.cam_name(eid) for eid in place.cams] if place else []
        return self._plan(p, place, dt_util.now(), names or ["Cam"])[0]

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
        st.count_season += 1
        st.count_total += 1
        for key in st.pref_on:
            st.pref_counts[key] = st.pref_counts.get(key, 0) + 1
        run["start"] = st.last_start = dt_util.utcnow().isoformat()
        run["phase"] = PHASE_RECORDING
        self._tasks.pop(sid, None)
        await self._save()
        self.hass.bus.async_fire(
            EVENT_STARTED,
            {
                "partner": p.name,
                "nickname": p.nickname or p.name,
                "partner_id": sid,
                "season": st.season,
                "recording": st.count_season,
                "place": place.name,
                "folder": "/".join(run["folder"]),
                "cams": run["cams"],
            },
        )
        ir.async_delete_issue(self.hass, DOMAIN, f"recording_lost_{sid}")
        self._set_message(f"{p.name}: recording ({len(cams)} cams)")
        self.notify()

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
        self.hass.bus.async_fire(
            EVENT_STOPPED,
            {
                "partner": p.name,
                "nickname": p.nickname or p.name,
                "partner_id": sid,
                "season": st.season,
                "recording": st.count_season,
                "place": place.name if place else None,
                "reason": reason,
                "duration_s": round(seconds),
            },
        )
        self._set_message(f"{p.name}: stopped ({reason})")
        self._tasks[sid] = self.hass.async_create_background_task(
            self._end_sequence(sid, place, wait=recorded), f"{DOMAIN}_end_{sid}", eager_start=False
        )
        self.notify()

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
