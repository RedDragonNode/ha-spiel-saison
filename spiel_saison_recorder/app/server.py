"""HTTP-API der App Spiel Saison Recorder (nur im internen Home-Assistant-Netz erreichbar)."""

from __future__ import annotations

import hmac
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from aiohttp import web
from recorder_core import CamSpec, Options, Recorder, RecorderError

_LOGGER = logging.getLogger("recorder.server")
VERSION = "0.1.0"


def _path(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [p for p in value.split("/") if p]
    if not isinstance(value, list) or not all(isinstance(p, str) for p in value):
        raise RecorderError("'path' muss eine Liste von Ordnernamen sein")
    return value


def create_app(recorder: Recorder, api_key: str) -> web.Application:
    @web.middleware
    async def guard(request: web.Request, handler):
        if request.path != "/health":
            sent = request.headers.get("Authorization", "")
            if not hmac.compare_digest(sent.encode(), f"Bearer {api_key}".encode()):
                return web.json_response({"fehler": "Nicht autorisiert"}, status=401)
        try:
            return await handler(request)
        except RecorderError as err:
            return web.json_response({"fehler": str(err)}, status=400)

    async def body(request: web.Request) -> dict[str, Any]:
        try:
            data = await request.json()
        except (json.JSONDecodeError, ValueError):
            raise RecorderError("Ungueltiges JSON") from None
        if not isinstance(data, dict):
            raise RecorderError("JSON-Objekt erwartet")
        return data

    async def health(_: web.Request) -> web.Response:
        return web.json_response({"ok": True, "version": VERSION})

    async def status(_: web.Request) -> web.Response:
        return web.json_response({**recorder.status(), "version": VERSION})

    async def ls(request: web.Request) -> web.Response:
        return web.json_response({"ordner": recorder.list_dirs(_path(request.query.get("path", "")))})

    async def mkdir(request: web.Request) -> web.Response:
        return web.json_response({"pfad": recorder.make_dirs(_path((await body(request)).get("path")))})

    async def start(request: web.Request) -> web.Response:
        data = await body(request)
        cams = []
        for raw in data.get("cams") or []:
            if not isinstance(raw, dict) or not all(
                isinstance(raw.get(k), str) and raw.get(k) for k in ("id", "name", "url", "stem")
            ):
                raise RecorderError("Jede Kamera braucht id, name, url und stem")
            cams.append(CamSpec(raw["id"], raw["name"], raw["url"], raw["stem"]))
        result = await recorder.start(
            _path(data.get("path")), cams, str(data.get("session") or "Sitzung"), str(data.get("profile") or "default")
        )
        return web.json_response(result)

    async def stop(request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except (json.JSONDecodeError, ValueError):
            data = {}
        profile = data.get("profile") if isinstance(data, dict) else None
        return web.json_response(await recorder.stop("manuell", str(profile) if profile else None))

    async def on_shutdown(_: web.Application) -> None:
        await recorder.stop("addon_beendet")

    app = web.Application(middlewares=[guard], client_max_size=1024 * 256)
    app.add_routes(
        [
            web.get("/health", health),
            web.get("/status", status),
            web.get("/ls", ls),
            web.post("/mkdir", mkdir),
            web.post("/start", start),
            web.post("/stop", stop),
        ]
    )
    app.on_shutdown.append(on_shutdown)
    return app


def load_options() -> dict[str, Any]:
    path = Path(os.environ.get("OPTIONS_FILE", "/data/options.json"))
    return json.loads(path.read_text()) if path.exists() else {}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    o = load_options()
    api_key = str(o.get("api_key") or "")
    if len(api_key) < 12:
        sys.exit("FEHLER: In den App-Einstellungen muss ein api_key (mindestens 12 Zeichen) gesetzt sein.")
    root = Path(o.get("aufnahme_ordner") or "/media/aufnahmen")
    root.mkdir(parents=True, exist_ok=True)
    opts = Options(
        container=str(o.get("container", "mp4")),
        audio=str(o.get("audio", "aac")),
        stall_seconds=int(o.get("stillstand_sekunden", 20)),
        min_free_gb=float(o.get("mindest_frei_gb", 20)),
        wallclock=bool(o.get("wallclock_zeitstempel", False)),
        video_tag=str(o.get("video_tag", "") or ""),
        loglevel=str(o.get("ffmpeg_loglevel", "warning") or "warning"),
    )
    opts.validate()
    _LOGGER.info("Recorder %s startet - Ordner %s, Container %s, Audio %s", VERSION, root, opts.container, opts.audio)
    web.run_app(
        create_app(Recorder(root, opts), api_key), host="0.0.0.0", port=int(os.environ.get("PORT", "8099")), print=None
    )


if __name__ == "__main__":
    main()
