"""Shared test scaffolding for the voice items: a fake engine "release" (a zip with a program in it), a small fake "model", and an engine manager
wired to start the fake engine script instead of the real audiocpp_server."""
import hashlib
import io
import os
import sys
import zipfile
from pathlib import Path

from voice_downloads import DownloadStore, FileSpec
from voice_engine import ENGINE_EXE, Build, Hardware, VoiceEngine
from local_models import GpuInfo

FAKE_SERVER = Path(__file__).resolve().parent / "fake_audiocpp_server.py"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_release(folder: Path, model_bytes: bytes = b"fake model " * 1000, backend: str = "cuda") -> tuple:
    """Write the fake engine zip and fake model into `folder` (as a download source) and return (builds, model_spec)."""
    folder.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"bin/{ENGINE_EXE}", b"not a real program")
        z.writestr("bin/ggml.dll", b"x" * 100)
    zbytes = buf.getvalue()
    (folder / "engine-fake.zip").write_bytes(zbytes)
    (folder / "model-fake.gguf").write_bytes(model_bytes)
    engine_spec = FileSpec("engine-fake.zip", "http://unused.invalid/engine-fake.zip", len(zbytes), sha(zbytes))
    model_spec = FileSpec("model-fake.gguf", "http://unused.invalid/model-fake.gguf", len(model_bytes), sha(model_bytes))
    build = Build("fake", "fake build", backend, (engine_spec,), 1000, needs_speed_check=(backend == "vulkan"))
    return {"fake": build}, model_spec


def good_hardware(_folder=None) -> Hardware:
    return Hardware(system="windows", gpu=GpuInfo("NVIDIA GeForce RTX 5090", 32768, "nvidia"), driver=617.14,
                    free_vram_bytes=20 * 1024**3, free_ram_bytes=32 * 1024**3, free_disk_bytes=500 * 1024**3)


def fake_launcher(exe, config, trace):
    return [sys.executable, str(FAKE_SERVER), str(config)]


def make_engine(tmp_path: Path, backend: str = "cuda", hardware=good_hardware, model_bytes: bytes = b"fake model " * 1000):
    src = tmp_path / "source"
    builds, model = make_release(src, model_bytes, backend)
    store = DownloadStore(lambda: tmp_path / "downloads")
    engine = VoiceEngine(lambda: tmp_path / "voice", store, builds=builds, model=model, launcher=fake_launcher, hardware_fn=hardware, port_range=(28950, 28999))
    return engine, src, store
