"""Aufnahme-Kern: schreibt RTSP-Streams per ffmpeg-Stream-Copy auf die Platte.

Reine Standardbibliothek (asyncio). Keine Neukodierung des Videos, daher fast keine CPU/GPU-Last.
Pro Kamera laeuft ein eigener ffmpeg-Prozess mit Aufsicht: Neustart bei Abbruch/Stillstand ("Teil N"),
sauberes Beenden ueber "q", Schutz vor vollen Platten und vor Pfad-Tricks.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger("recorder")

_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class RecorderError(Exception):
    """Fehler mit lesbarer Meldung (wird dem Aufrufer angezeigt)."""


# ---------------------------------------------------------------- Hilfsfunktionen
def sanitize_name(name: str) -> str:
    """Dateinamen-Baustein bereinigen (Windows-kompatibel). '#', '%', Leerzeichen und Klammern bleiben."""
    cleaned = _BAD_CHARS.sub("_", str(name)).strip().rstrip(". ")
    if cleaned in ("", ".", ".."):
        raise RecorderError(f"Ungueltiger Name: {name!r}")
    return cleaned[:150]


def safe_join(root: Path, *parts: str) -> Path:
    """root/parts - verweigert absolute Pfade, '..' und Ausbrueche ueber Symlinks."""
    root = Path(root).resolve()
    target = root
    for part in parts:
        if part in ("", ".") or "/" in part or "\\" in part or part == ".." or os.path.isabs(part):
            raise RecorderError(f"Ungueltiger Pfadteil: {part!r}")
        target = target / sanitize_name(part)
    resolved = target.resolve()
    if resolved != root and root not in resolved.parents:
        raise RecorderError("Pfad liegt ausserhalb des Aufnahme-Ordners")
    return target


def mask_url(text: str) -> str:
    """Zugangsdaten in rtsp://user:pass@host entfernen (fuer Logs und Statusanzeigen)."""
    # greedy bis zum LETZTEN '@' vor dem ersten '/', damit auch ein '@' im Passwort maskiert wird
    return re.sub(r"(rtsps?://)[^/\s]*@", r"\1***@", str(text))


def _utc() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Options:
    container: str = "mp4"  # mp4 (fragmentiert, absturzsicher) | mkv
    audio: str = "aac"  # aac | copy | none
    stall_seconds: int = 20
    min_free_gb: float = 20.0
    wallclock: bool = False
    video_tag: str = ""  # z. B. "hvc1" fuer HEVC in MP4 (Windows/Apple-Player)
    ffmpeg_bin: str = "ffmpeg"
    loglevel: str = "warning"  # warning | info | verbose (Fehlersuche)
    rw_timeout_s: int = 10
    backoff: tuple[float, ...] = (2, 5, 10, 30)
    stop_grace_s: float = 8.0
    disk_check_s: float = 30.0
    connect_grace_s: float = 10.0  # zusaetzliche Wartezeit bis zum ersten Byte

    def validate(self) -> None:
        if self.container not in ("mp4", "mkv"):
            raise RecorderError("container muss 'mp4' oder 'mkv' sein")
        if self.audio not in ("aac", "copy", "none"):
            raise RecorderError("audio muss 'aac', 'copy' oder 'none' sein")
        if self.loglevel not in ("warning", "info", "verbose"):
            raise RecorderError("ffmpeg_loglevel muss 'warning', 'info' oder 'verbose' sein")

    @property
    def ext(self) -> str:
        return "mp4" if self.container == "mp4" else "mkv"


@dataclass
class CamSpec:
    id: str
    name: str
    url: str
    stem: str  # Dateiname ohne Endung
    extra_input: list[str] = field(default_factory=list)  # nur fuer Tests (lavfi/Datei)


# Codec je Stream-Adresse (nur im Speicher), damit der Tag-Test nicht bei jedem Start laeuft
_CODEC_CACHE: dict[str, str] = {}


# ---------------------------------------------------------------- eine Kamera
class CamRecorder:
    def __init__(self, spec: CamSpec, folder: Path, opts: Options) -> None:
        self.spec, self.folder, self.opts = spec, folder, opts
        self.state = "wartet"  # wartet|verbindet|nimmt auf|neustart|beendet|fehler
        self.part = 0
        self.restarts = 0
        self.parts: list[dict[str, Any]] = []
        self.started_at = _utc()
        self.last_error = ""
        self.log_tail: deque[str] = deque(maxlen=20)
        self._proc: asyncio.subprocess.Process | None = None
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.on_change: Any = None  # Rueckruf fuer Sidecar-Updates
        self._skip_tag = False  # Tag passt nicht zum Codec dieser Cam (z. B. hvc1 bei H.264)

    # -- Kommando
    def part_path(self, part: int) -> Path:
        stem = self.spec.stem if part <= 1 else f"{self.spec.stem} Teil {part}"
        return self.folder / f"{stem}.{self.opts.ext}"

    def build_cmd(self, path: Path) -> list[str]:
        o, url = self.opts, self.spec.url
        cmd = [o.ffmpeg_bin, "-hide_banner", "-loglevel", o.loglevel, "-y"]
        if url.lower().startswith(("rtsp://", "rtsps://")):
            cmd += ["-rtsp_transport", "tcp", "-timeout", str(o.rw_timeout_s * 1_000_000)]
            if o.wallclock:
                cmd += ["-use_wallclock_as_timestamps", "1"]
        cmd += [*self.spec.extra_input, "-i", url, "-map", "0:v:0", "-c:v", "copy"]
        if o.video_tag and o.container == "mp4" and not self._skip_tag:
            cmd += ["-tag:v", o.video_tag]
        if o.audio == "none":
            cmd += ["-an"]
        else:
            cmd += ["-map", "0:a:0?"]
            cmd += ["-c:a", "aac", "-b:a", "64k"] if o.audio == "aac" else ["-c:a", "copy"]
        cmd += ["-flush_packets", "1"]  # sofort auf die Platte (Absturzsicherheit, Stillstandserkennung)
        if o.container == "mp4":
            cmd += ["-movflags", "+frag_keyframe+empty_moov+default_base_moof", "-f", "mp4"]
        else:
            cmd += ["-f", "matroska"]
        return [*cmd, str(path)]

    # -- Ablauf
    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name=f"rec-{self.spec.id}")

    async def stop(self) -> None:
        self._stop.set()
        await self._terminate()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=self.opts.stop_grace_s + 5)
            except (TimeoutError, asyncio.CancelledError):
                self._task.cancel()
        if self.state != "fehler":
            self.state = "beendet"
        self._close_part()

    async def _terminate(self) -> None:
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return
        try:
            if proc.stdin:
                proc.stdin.write(b"q\n")
                await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=self.opts.stop_grace_s)
            return
        except TimeoutError:
            pass
        for sig in ("terminate", "kill"):
            try:
                getattr(proc, sig)()
            except ProcessLookupError:
                return
            try:
                await asyncio.wait_for(proc.wait(), timeout=3)
                return
            except TimeoutError:
                continue

    def _close_part(self) -> None:
        if self.parts and "ende" not in self.parts[-1]:
            self.parts[-1]["ende"] = _utc()
            self.parts[-1]["bytes"] = self.size()
            self._changed()

    def _changed(self) -> None:
        if self.on_change:
            try:
                self.on_change()
            except Exception:
                _LOGGER.exception("Sidecar konnte nicht geschrieben werden")

    def size(self) -> int:
        total = 0
        for p in self.parts:
            try:
                total += (self.folder / p["datei"]).stat().st_size
            except OSError:
                pass
        return total

    async def _run(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            self.part += 1
            path = self.part_path(self.part)
            if path.exists():  # nie etwas ueberschreiben
                n = 2
                while self.part_path(self.part + n).exists():
                    n += 1
                self.part += n
                path = self.part_path(self.part)
            self.state = "verbindet"
            ran_ok = await self._run_once(path)
            if not path.exists():
                # nichts geschrieben (Datei wurde entfernt): Teil-Nummer nicht verbrauchen,
                # "Teil 2" gibt es erst nach einem echten Abbruch mit gespeicherten Daten
                self.part -= 1
            if self._stop.is_set():
                break
            attempt = 0 if ran_ok else attempt + 1
            self.restarts += 1
            self.state = "neustart"
            delay = self.opts.backoff[min(attempt - 1, len(self.opts.backoff) - 1)] if attempt else 1
            _LOGGER.warning("%s: Neustart in %ss (%s)", self.spec.name, delay, self.last_error or "unbekannt")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except TimeoutError:
                pass

    async def _probe_codec(self) -> str:
        """Video-Codec der Cam per ffprobe ermitteln ('' wenn unbekannt)."""
        url = self.spec.url
        if url in _CODEC_CACHE:
            return _CODEC_CACHE[url]
        probe = self.opts.ffmpeg_bin.replace("ffmpeg", "ffprobe")
        cmd = [probe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name", "-of", "csv=p=0"]
        if url.lower().startswith("rtsp"):
            cmd += ["-rtsp_transport", "tcp", "-timeout", str(self.opts.rw_timeout_s * 1_000_000)]
        cmd.append(url)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=self.opts.rw_timeout_s + 3)
        except (TimeoutError, OSError):
            return ""
        codec = out.decode(errors="replace").strip().split(",")[0].lower()
        if codec:
            _CODEC_CACHE[url] = codec
        return codec

    async def _run_once(self, path: Path) -> bool:
        """Ein ffmpeg-Lauf. True, wenn nennenswert Daten geschrieben wurden."""
        if self.opts.video_tag and self.opts.container == "mp4" and not self._skip_tag:
            codec = await self._probe_codec()
            if codec and codec != "hevc":  # Tag hvc1 gilt nur fuer HEVC
                self._skip_tag = True
        cmd = self.build_cmd(path)
        _LOGGER.info("%s: %s", self.spec.name, mask_url(" ".join(cmd)))
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as err:
            self.state, self.last_error = "fehler", f"ffmpeg nicht startbar: {err}"
            self._stop.set()
            return False
        proc = self._proc
        entry = {"datei": path.name, "start": _utc()}
        self.parts.append(entry)
        self._changed()
        err_task = asyncio.create_task(self._drain(proc))
        wrote = stalled = False
        last_size, last_growth = -1, time.monotonic()
        started = time.monotonic()
        try:
            while proc.returncode is None and not self._stop.is_set():
                try:
                    await asyncio.wait_for(proc.wait(), timeout=1.0)
                except TimeoutError:
                    pass
                try:
                    size = path.stat().st_size
                except OSError:
                    size = 0
                if size > last_size:
                    last_size, last_growth = size, time.monotonic()
                    if size > 4096:
                        wrote = True
                        self.state = "nimmt auf"
                grace = self.opts.stall_seconds + (self.opts.connect_grace_s if last_size <= 0 else 0)
                if proc.returncode is None and time.monotonic() - last_growth > grace:
                    self.last_error = f"Stillstand seit {int(time.monotonic() - last_growth)} s"
                    stalled = True
                    await self._terminate()
                    break
        finally:
            await self._terminate()
            try:
                await asyncio.wait_for(err_task, timeout=2)
            except (TimeoutError, asyncio.CancelledError):
                err_task.cancel()
        if stalled:
            pass
        elif proc.returncode not in (0, None) and not self._stop.is_set():
            self.last_error = mask_url(self.log_tail[-1]) if self.log_tail else f"ffmpeg Code {proc.returncode}"
        elif wrote and not self._stop.is_set():
            self.last_error = self.last_error or "Verbindung beendet"
        if any("incompatible with output codec" in l for l in self.log_tail) and not self._skip_tag:
            # z. B. hvc1 bei einer H.264-Cam: ab jetzt ohne Tag fuer diese Cam
            self._skip_tag = True
            _LOGGER.warning("%s: Video-Tag passt nicht zum Codec, starte ohne Tag", self.spec.name)
        if not wrote and not self._stop.is_set() and self.log_tail:
            # Fehlersuche: nicht nur die letzte Zeile, sondern die letzten ffmpeg-Meldungen zeigen
            for line in list(self.log_tail)[-8:]:
                _LOGGER.warning("%s: ffmpeg: %s", self.spec.name, line[:300])
        entry["ende"] = _utc()
        entry["bytes"] = path.stat().st_size if path.exists() else 0
        if not wrote and path.exists() and entry["bytes"] == 0:
            try:
                path.unlink()
            except OSError:
                pass
            self.parts.pop()
        self._changed()
        return wrote and (time.monotonic() - started) > 5

    async def _drain(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stderr is not None
        async for line in proc.stderr:
            text = mask_url(line.decode(errors="replace").strip())
            if text:
                self.log_tail.append(text)

    def status(self) -> dict[str, Any]:
        return {
            "id": self.spec.id,
            "name": self.spec.name,
            "status": self.state,
            "teil": self.part,
            "neustarts": self.restarts,
            "bytes": self.size(),
            "datei": self.parts[-1]["datei"] if self.parts else None,
            "letzter_fehler": self.last_error,
        }


# ---------------------------------------------------------------- Sitzung (alle Kameras)
class RecorderSession:
    """Eine Sitzung = ein Profil: eigene Kameras, eigener Ordner, eigene Sidecar-Datei."""

    def __init__(self, root: Path, opts: Options | None = None, busy: Callable[[], set[str]] | None = None) -> None:
        self.root = Path(root)
        self.opts = opts or Options()
        self._busy = busy or (lambda: set())
        self.cams: dict[str, CamRecorder] = {}
        self.session: dict[str, Any] | None = None
        self.stop_reason = ""
        self._folder: Path | None = None
        self._guard: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    @property
    def recording(self) -> bool:
        return self.session is not None and any(c.state != "beendet" for c in self.cams.values())

    def disk(self) -> dict[str, float]:
        try:
            u = shutil.disk_usage(self.root if self.root.exists() else self.root.parent)
        except OSError:
            return {"frei_gb": 0.0, "gesamt_gb": 0.0}
        return {"frei_gb": round(u.free / 1e9, 1), "gesamt_gb": round(u.total / 1e9, 1)}

    # -- Ordner
    def list_dirs(self, rel: list[str]) -> list[str]:
        path = safe_join(self.root, *rel)
        if not path.is_dir():
            return []
        return sorted(p.name for p in path.iterdir() if p.is_dir())

    def make_dirs(self, rel: list[str]) -> str:
        path = safe_join(self.root, *rel)
        path.mkdir(parents=True, exist_ok=True)
        return "/".join(path.relative_to(self.root.resolve()).parts)

    # -- Start / Stop
    async def start(self, rel: list[str], cams: list[CamSpec], session_name: str = "Sitzung") -> dict[str, Any]:
        async with self._lock:
            if self.recording:
                raise RecorderError("Es laeuft bereits eine Aufnahme")
            self.opts.validate()
            if not cams:
                raise RecorderError("Keine Kamera zum Aufnehmen angegeben")
            ids = [c.id for c in cams]
            if len(set(ids)) != len(ids):
                raise RecorderError("Kamera-IDs muessen eindeutig sein")
            busy = sorted(set(ids) & self._busy())
            if busy:
                raise RecorderError("Kamera nimmt bereits in einem anderen Profil auf: " + ", ".join(busy))
            free = self.disk()["frei_gb"]
            if free < self.opts.min_free_gb:
                raise RecorderError(f"Zu wenig freier Speicher: {free} GB (Minimum {self.opts.min_free_gb} GB)")
            folder = safe_join(self.root, *rel)
            folder.mkdir(parents=True, exist_ok=True)
            self._folder = folder
            self.cams, self.stop_reason = {}, ""
            self.session = {"name": sanitize_name(session_name), "start": _utc(), "ordner": "/".join(rel)}
            for spec in cams:
                spec = CamSpec(spec.id, spec.name, spec.url, sanitize_name(spec.stem), spec.extra_input)
                cam = CamRecorder(spec, folder, self.opts)
                cam.on_change = self._write_sidecar
                self.cams[spec.id] = cam
            self._write_sidecar()
            for cam in self.cams.values():
                cam.start()
            self._guard = asyncio.create_task(self._disk_guard(), name="rec-disk-guard")
            return self.status()

    async def stop(self, reason: str = "manuell") -> dict[str, Any]:
        async with self._lock:
            return await self._stop_locked(reason)

    async def _stop_locked(self, reason: str) -> dict[str, Any]:
        if self.session is None:
            return self.status()
        current = asyncio.current_task()
        if self._guard and self._guard is not current:
            self._guard.cancel()
        self.stop_reason = reason
        await asyncio.gather(*(c.stop() for c in self.cams.values()), return_exceptions=True)
        self.session["ende"] = _utc()
        self._write_sidecar()
        result = self.status()
        self.session = None
        return result

    async def _disk_guard(self) -> None:
        while True:
            await asyncio.sleep(self.opts.disk_check_s)
            free = self.disk()["frei_gb"]
            if free < max(1.0, self.opts.min_free_gb / 4):
                _LOGGER.error("Speicher fast voll (%s GB) - Aufnahme wird beendet", free)
                self._guard = None
                async with self._lock:
                    await self._stop_locked("speicher_voll")
                return

    # -- Sidecar / Status
    def _write_sidecar(self) -> None:
        if self.session is None or self._folder is None:
            return
        data = {
            **self.session,
            "kameras": [
                {"id": c.spec.id, "name": c.spec.name, "start": c.started_at, "teile": c.parts}
                for c in self.cams.values()
            ],
            "hinweis": "Startzeiten in UTC; Teile pro Kamera sind lueckenlos nummeriert. Zum Synchronisieren: start-Zeiten vergleichen.",
        }
        target = self._folder / f"{self.session['name']}.json"
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, target)

    def status(self) -> dict[str, Any]:
        return {
            "aufnahme": self.recording,
            "sitzung": self.session,
            "stop_grund": self.stop_reason,
            "kameras": {k: c.status() for k, c in self.cams.items()},
            "speicher": self.disk(),
            "wurzel": str(self.root),
        }


# ---------------------------------------------------------------- Mehrere Sitzungen (Profile)
DEFAULT_PROFILE = "default"


class Recorder:
    """Verwaltet mehrere gleichzeitige Sitzungen (je Profil eine). Eine Kamera nimmt nie in zwei Profilen zugleich auf."""

    def __init__(self, root: Path, opts: Options | None = None) -> None:
        self.root = Path(root)
        self.opts = opts or Options()
        self.sessions: dict[str, RecorderSession] = {}
        self.last_stop: dict[str, str] = {}
        self._lock = asyncio.Lock()

    def _busy_cams(self, exclude: str | None = None) -> set[str]:
        out: set[str] = set()
        for pid, s in self.sessions.items():
            if pid != exclude and s.recording:
                out |= {k for k, c in s.cams.items() if c.state != "beendet"}
        return out

    @property
    def recording(self) -> bool:
        return any(s.recording for s in self.sessions.values())

    def disk(self) -> dict[str, float]:
        return RecorderSession(self.root, self.opts).disk()

    def list_dirs(self, rel: list[str]) -> list[str]:
        return RecorderSession(self.root, self.opts).list_dirs(rel)

    def make_dirs(self, rel: list[str]) -> str:
        return RecorderSession(self.root, self.opts).make_dirs(rel)

    async def start(
        self, rel: list[str], cams: list[CamSpec], session_name: str = "Sitzung", profile: str = DEFAULT_PROFILE
    ) -> dict[str, Any]:
        profile = sanitize_name(profile) or DEFAULT_PROFILE
        async with self._lock:
            old = self.sessions.get(profile)
            if old is not None and old.recording:
                raise RecorderError(
                    "Es laeuft bereits eine Aufnahme"
                    + ("" if profile == DEFAULT_PROFILE else f" im Profil '{profile}'")
                )
            session = RecorderSession(self.root, self.opts, lambda: self._busy_cams(exclude=profile))
            await session.start(rel, cams, session_name)
            self.sessions[profile] = session
            self.last_stop.pop(profile, None)
        return self.status()

    async def stop(self, reason: str = "manuell", profile: str | None = None) -> dict[str, Any]:
        targets = list(self.sessions) if profile is None else [sanitize_name(profile)]
        for pid in targets:
            s = self.sessions.get(pid)
            if s is not None:
                await s.stop(reason)
                self.last_stop[pid] = s.stop_reason
        return self.status()

    def status(self) -> dict[str, Any]:
        profiles: dict[str, Any] = {}
        cams: dict[str, Any] = {}
        for pid, s in self.sessions.items():
            st = s.status()
            if s.stop_reason:
                self.last_stop[pid] = s.stop_reason
            st["stop_grund"] = self.last_stop.get(pid, "")
            profiles[pid] = st
            cams.update(st["kameras"])
        main = self.sessions.get(DEFAULT_PROFILE) or next(iter(self.sessions.values()), None)
        return {
            "aufnahme": self.recording,
            "sitzung": main.session if main else None,
            "stop_grund": self.last_stop.get(DEFAULT_PROFILE, "")
            if DEFAULT_PROFILE in self.sessions
            else (next(iter(self.last_stop.values()), "")),
            "kameras": cams,
            "profile": profiles,
            "speicher": self.disk(),
            "wurzel": str(self.root),
        }
