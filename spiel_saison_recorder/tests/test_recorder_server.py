"""Tests der HTTP-API der App."""

import asyncio
import sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from recorder_core import Options, Recorder
from server import create_app

pytestmark = pytest.mark.enable_socket

H = {"Authorization": "Bearer geheimgeheim123"}


@pytest.fixture
async def client(socket_enabled, tmp_path):
    async for c in _client(tmp_path):
        yield c


async def _client(tmp_path):
    fake = tmp_path / "ff.sh"
    fake.write_text('#!/bin/sh\nhead -c 100000 /dev/zero > "$(eval echo \\${$#})"; sleep 300\n')
    fake.chmod(0o755)
    rec = Recorder(tmp_path / "root", Options(ffmpeg_bin=str(fake), min_free_gb=0.001, stop_grace_s=1))
    (tmp_path / "root").mkdir()
    async with TestClient(TestServer(create_app(rec, "geheimgeheim123"))) as c:
        yield c


async def test_auth(client):
    assert (await client.get("/health")).status == 200
    assert (await client.get("/status")).status == 401
    assert (await client.get("/status", headers={"Authorization": "Bearer falsch"})).status == 401
    r = await client.get("/status", headers=H)
    assert r.status == 200 and (await r.json())["aufnahme"] is False


async def test_dirs_and_traversal(client):
    r = await client.post("/mkdir", json={"path": ["P #01", "Saison 01"]}, headers=H)
    assert (await r.json())["pfad"] == "P #01/Saison 01"
    r = await client.get("/ls", params={"path": "P #01"}, headers=H)
    assert (await r.json())["ordner"] == ["Saison 01"]
    r = await client.post("/mkdir", json={"path": ["..", "x"]}, headers=H)
    assert r.status == 400 and "Ungueltig" in (await r.json())["fehler"]


@pytest.mark.skipif(
    sys.platform == "win32", reason="braucht POSIX-Shellskripte bzw. Symlinks, läuft in der CI unter Linux"
)
async def test_start_stop(client):
    cams = [{"id": "fb", "name": "Fuchsblick", "url": "rtsp://u:p@h/s", "stem": "cam"}]
    r = await client.post("/start", json={"path": ["A"], "cams": cams, "session": "S"}, headers=H)
    assert r.status == 200 and (await r.json())["aufnahme"] is True
    r = await client.post("/start", json={"path": ["A"], "cams": cams}, headers=H)
    assert r.status == 400  # laeuft schon
    await asyncio.sleep(2.5)
    st = await (await client.get("/status", headers=H)).json()
    assert st["kameras"]["fb"]["status"] == "nimmt auf" and "u:p" not in str(st)
    st = await (await client.post("/stop", headers=H)).json()
    assert st["aufnahme"] is False
    r = await client.post("/start", json={"path": ["A"], "cams": [{"id": "x"}]}, headers=H)
    assert r.status == 400


async def test_profiles_over_api(client):
    a = [{"id": "a", "name": "A", "url": "rtsp://h/a", "stem": "a"}]
    b = [{"id": "b", "name": "B", "url": "rtsp://h/b", "stem": "b"}]
    assert (await client.post("/start", json={"path": ["T"], "cams": a, "profile": "tag"}, headers=H)).status == 200
    assert (await client.post("/start", json={"path": ["N"], "cams": b, "profile": "nacht"}, headers=H)).status == 200
    r = await client.post("/start", json={"path": ["N2"], "cams": a, "profile": "x"}, headers=H)
    assert r.status == 400 and "anderen Profil" in (await r.json())["fehler"]
    st = await (await client.get("/status", headers=H)).json()
    assert set(st["profile"]) == {"tag", "nacht"}
    st = await (await client.post("/stop", json={"profile": "tag"}, headers=H)).json()
    assert st["profile"]["tag"]["aufnahme"] is False and st["aufnahme"] is True
    st = await (await client.post("/stop", headers=H)).json()
    assert st["aufnahme"] is False
