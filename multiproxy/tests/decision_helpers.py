"""Shared scaffolding for the local decision model's tests: tiny fake "model files", an engine manager wired to start the fake llama-server (which speaks llama.cpp's
`/v1/systemone`) instead of the real one, and hardware to order."""
import hashlib
import sys
from pathlib import Path

import decision_engine as de
from local_models import GpuInfo
from voice_downloads import DownloadStore, FileSpec, FolderSource
from voice_engine import Hardware

FAKE_SERVER = Path(__file__).resolve().parent / "fake_llama_decision_server.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "decisions"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_files(folder: Path) -> dict:
    """Write one fake model file per model into `folder`; return {key: ModelSpec} with specs that match them."""
    folder.mkdir(parents=True, exist_ok=True)
    out = {}
    for key in ("kev-4b", "laya", "lev"):
        data = (b"fake decision model " * 400_000) + key.encode()          # 8 MB: a copy over the relay takes long enough for its progress to be seen
        name = f"model-{key}.gguf"
        (folder / name).write_bytes(data)
        out[key] = de.ModelSpec(key, f"fake {key}", "", FileSpec(name, f"http://unused.invalid/{name}", len(data), sha(data)), graphics_bytes=int(3.8 * de.GIB), memory_bytes=4 * de.GIB)
    return out


def hardware(free_vram_gb=20.0, total_gb=32.0, gpu=True, free_ram_gb=32.0, free_disk_gb=500.0, vendor="nvidia"):
    return Hardware(system="windows", gpu=GpuInfo("NVIDIA GeForce RTX 5090", int(total_gb * 1024), vendor) if gpu else None, driver=617.14,
                    free_vram_bytes=int(free_vram_gb * de.GIB) if free_vram_gb is not None else None, free_ram_bytes=int(free_ram_gb * de.GIB),
                    free_disk_bytes=int(free_disk_gb * de.GIB))


def fake_launcher(exe, model, port, mode, slots, context):
    return [sys.executable, str(FAKE_SERVER), "--port", str(port), "--mode", mode, "--slots", str(slots)]


def make_engine(tmp_path: Path, settings: dict = None, hw=None, port_range=(29100, 29149), slots: int = 4, **kw) -> tuple:
    """(engine, settings dict, source folder). The llama.cpp engine is "already installed" (a path that exists)."""
    src = tmp_path / "source"
    models = make_files(src)
    settings = {"model": "kev-4b", "run_on": "automatic", "limit_seconds": 0, **(settings or {})}
    exe = tmp_path / "llama-server.exe"
    exe.write_bytes(b"x")
    store = DownloadStore(lambda: tmp_path / "downloads")
    hw = hw or hardware()
    kind = kw.pop("kind", "cuda-13.4")

    async def ensure(_progress):
        return exe
    engine = de.DecisionEngine(lambda: tmp_path / "decisions", store, lambda: settings, lambda: exe, ensure, lambda: kind, models=models,
                               hardware_fn=lambda folder: hw, launcher=fake_launcher, port_range=port_range, slots=slots,
                               source_fn=lambda spec: FolderSource(src), **kw)
    return engine, settings, src


async def installed_engine(tmp_path: Path, **kw):
    engine, settings, src = make_engine(tmp_path, **kw)
    job = engine.start_install()
    await job.task
    assert job.state == "done", job.error
    return engine, settings, src
