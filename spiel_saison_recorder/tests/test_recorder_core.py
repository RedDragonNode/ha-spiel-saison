"""Tests des Aufnahme-Kerns mit echtem ffmpeg (Dateien statt Kameras)."""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from recorder_core import (
    CamSpec,
    Options,
    Recorder,
    RecorderError,
    mask_url,
    safe_join,
    sanitize_name,
)


@pytest.fixture(scope="module")
def sample(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("src") / "sample.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=10",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=8000",
            "-t",
            "30",
            "-c:v",
            "mpeg4",
            "-g",
            "10",
            "-c:a",
            "pcm_alaw",
            str(path),
        ],
        check=True,
    )
    return path


def spec(sample: Path, cam_id="fb", stem="#01.02.03.FB.04 (4K) 10.10.2026", loop=True) -> CamSpec:
    extra = ["-re"] + (["-stream_loop", "-1"] if loop else [])
    return CamSpec(cam_id, "Fuchsblick", str(sample), stem, extra)


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
    ).stdout.strip()
    return float(out or 0)


@pytest.mark.skipif(
    sys.platform == "win32", reason="braucht POSIX-Shellskripte bzw. Symlinks, läuft in der CI unter Linux"
)
def test_helpers(tmp_path):
    assert sanitize_name('a/b:c*"d') == "a_b_c__d"
    assert sanitize_name("#01.02 (4K) 10.10.2026") == "#01.02 (4K) 10.10.2026"
    for bad in ("..", "/etc", "a/b", "", "C:\\x"):
        with pytest.raises(RecorderError):
            safe_join(tmp_path, bad)
    assert safe_join(tmp_path, "Spielen mit Frank #01", "Saison 02").is_relative_to(tmp_path.resolve())
    (tmp_path / "out").mkdir()
    (tmp_path / "link").symlink_to("/tmp")
    with pytest.raises(RecorderError):
        safe_join(tmp_path, "link")
    assert mask_url("x rtsp://admin:geheim@192.168.1.5/stream y") == "x rtsp://***@192.168.1.5/stream y"
    # '@' im Passwort darf nichts durchlassen
    assert mask_url("rtsp://admin:ge@heim@192.168.1.5:554/11") == "rtsp://***@192.168.1.5:554/11"


async def test_record_and_stop(tmp_path, sample):
    rec = Recorder(tmp_path, Options(min_free_gb=0.001))
    st = await rec.start(
        ["Spielen mit Frank #01", "Saison 02"], [spec(sample), spec(sample, "hb", "#01.02.03.HB.04")], "Sitzung"
    )
    assert st["aufnahme"]
    await asyncio.sleep(4)
    mid = rec.status()
    assert all(c["status"] == "nimmt auf" for c in mid["kameras"].values()), mid
    result = await rec.stop()
    assert not rec.recording and result["kameras"]["fb"]["status"] == "beendet"
    folder = tmp_path / "Spielen mit Frank #01" / "Saison 02"
    f = folder / "#01.02.03.FB.04 (4K) 10.10.2026.mp4"
    assert f.exists() and duration(f) >= 2
    side = json.loads((folder / "Sitzung.json").read_text())
    assert {c["id"] for c in side["kameras"]} == {"fb", "hb"} and "ende" in side
    assert "ende" in side["kameras"][0]["teile"][0]
    r2 = Recorder(tmp_path, Options(min_free_gb=10**9))
    with pytest.raises(RecorderError):
        await r2.start(["x"], [spec(sample)])


async def test_mkv_and_no_audio(tmp_path, sample):
    rec = Recorder(tmp_path, Options(container="mkv", audio="none", min_free_gb=0.001))
    await rec.start(["a"], [spec(sample)], "s")
    await asyncio.sleep(3)
    await rec.stop()
    f = next((tmp_path / "a").glob("*.mkv"))
    streams = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(f)],
        capture_output=True,
        text=True,
    ).stdout.split()
    assert streams == ["video"]


async def test_restart_creates_part_files(tmp_path, sample):
    rec = Recorder(tmp_path, Options(min_free_gb=0.001, backoff=(1,)))
    # Quelle ohne Endlosschleife: ffmpeg endet nach ~30 s -> wir kuerzen ueber -t
    s = CamSpec("fb", "Fuchsblick", str(sample), "cam", ["-re", "-t", "2"])
    await rec.start(["a"], [s], "s")
    await asyncio.sleep(7)
    await rec.stop()
    names = sorted(p.name for p in (tmp_path / "a").glob("*.mp4"))
    assert "cam.mp4" in names and "cam Teil 2.mp4" in names, names
    assert rec.status()["kameras"]["fb"]["neustarts"] >= 1


@pytest.mark.skipif(
    sys.platform == "win32", reason="braucht POSIX-Shellskripte bzw. Symlinks, läuft in der CI unter Linux"
)
async def test_stall_detection_kills_and_restarts(tmp_path):
    fake = tmp_path / "fakeffmpeg.sh"
    fake.write_text("#!/bin/sh\nsleep 300\n")
    fake.chmod(0o755)
    rec = Recorder(
        tmp_path / "out",
        Options(
            ffmpeg_bin=str(fake), stall_seconds=1, connect_grace_s=1, stop_grace_s=1, min_free_gb=0.001, backoff=(1,)
        ),
    )
    (tmp_path / "out").mkdir()
    await rec.start(["a"], [CamSpec("x", "X", "rtsp://u:p@h/s", "cam")], "s")
    await asyncio.sleep(9)
    st = rec.status()["kameras"]["x"]
    assert st["neustarts"] >= 1 and "Stillstand" in st["letzter_fehler"]
    assert "u:p" not in json.dumps(st)
    await rec.stop()
    assert not rec.recording


async def test_missing_ffmpeg_reports_error(tmp_path):
    rec = Recorder(tmp_path, Options(ffmpeg_bin="/gibt/es/nicht", min_free_gb=0.001))
    await rec.start(["a"], [CamSpec("x", "X", "rtsp://h/s", "cam")], "s")
    await asyncio.sleep(1)
    st = rec.status()["kameras"]["x"]
    assert st["status"] == "fehler" and "nicht startbar" in st["letzter_fehler"]
    await rec.stop()


async def test_double_start_and_dirs(tmp_path, sample):
    rec = Recorder(tmp_path, Options(min_free_gb=0.001))
    assert rec.make_dirs(["P #01", "Saison 01 vom 01,01,2026"]) == "P #01/Saison 01 vom 01,01,2026"
    assert rec.list_dirs(["P #01"]) == ["Saison 01 vom 01,01,2026"]
    assert rec.list_dirs(["gibtsnicht"]) == []
    await rec.start(["a"], [spec(sample)], "s")
    with pytest.raises(RecorderError):
        await rec.start(["a"], [spec(sample)], "s")
    await rec.stop()


async def test_two_profiles_in_parallel_and_busy_cam(tmp_path, sample):
    rec = Recorder(tmp_path, Options(min_free_gb=0.001))
    await rec.start(["Tag"], [spec(sample, "a", "a")], "S1", profile="tag")
    await rec.start(["Nacht"], [spec(sample, "b", "b")], "S2", profile="nacht")
    st = rec.status()
    assert set(st["profile"]) == {"tag", "nacht"} and set(st["kameras"]) == {"a", "b"} and st["aufnahme"]
    with pytest.raises(RecorderError, match="anderen Profil"):
        await rec.start(["Nacht2"], [spec(sample, "a", "a")], "S3", profile="nacht2")
    with pytest.raises(RecorderError, match="bereits"):
        await rec.start(["Tag"], [spec(sample, "c", "c")], "S1", profile="tag")
    await rec.stop(profile="tag")
    assert rec.status()["profile"]["tag"]["aufnahme"] is False and rec.recording
    await rec.start(["Nacht2"], [spec(sample, "a", "a")], "S3", profile="nacht2")  # Cam ist wieder frei
    await rec.stop()
    assert not rec.recording
