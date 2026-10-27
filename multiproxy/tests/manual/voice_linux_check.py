"""Installs and runs Higgs voice on a real Linux device (or a container with a graphics card), through the product's own code, and times lines.

    python voice_linux_check.py <program folder> <build key> <source folder> <work folder> [lines]

    program folder  the unpacked Courier (or MultiProxy) package: its own voice_engine.py and friends are what runs
    build key       linux-cuda12.8 | linux-vulkan | auto (what this device's fit check picks)
    source folder   the engine tarball and the model file, under their own names (the install verifies each against its pinned size and SHA-256)
    work folder     where the voice folder is made

It does what the Voice tab's Install button does (unpack, mark the program executable, put the model in place, start it, warm up, and for Vulkan the
speed check), then makes `lines` short lines and prints the timings as JSON. Run by hand: it needs the real files (several GB) and a graphics card.
"""
import asyncio
import json
import sys
import time
from pathlib import Path

program, key, src, work = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4])
lines = int(sys.argv[5]) if len(sys.argv) > 5 else 5
sys.path.insert(0, str(program))

import voice_engine as ve  # noqa: E402
from voice_downloads import DownloadStore, FolderSource  # noqa: E402

SHORT = "The road north is dangerous, so stay close."


async def main() -> dict:
    hw = ve.detect_hardware(work)
    fit = ve.assess(hw)
    out = {"hardware": {"system": hw.system, "gpu": hw.gpu.name if hw.gpu else None, "driver": hw.driver, "glibc": hw.glibc, "cuda_missing": hw.cuda_missing},
           "fit": fit.to_json()}
    print(json.dumps(out, indent=1), flush=True)
    builds = ve.BUILDS if key == "auto" else {key: ve.BUILDS[key]}
    store = DownloadStore(lambda: work / "downloads")
    engine = ve.VoiceEngine(lambda: work / "voice", store, builds=builds)
    t0 = time.time()
    job = engine.start_install(FolderSource(src))
    while job.state == "running":
        await asyncio.sleep(1)
    out["install"] = {"state": job.state, "error": job.error, "seconds": round(time.time() - t0, 1)}
    out["engine"] = {"state": engine.state, "detail": engine.detail, "speed_check": engine.speed_check}
    exe = engine.find_exe()
    out["executable"] = bool(exe and exe.stat().st_mode & 0o111)
    if engine.state in ("ready", "too_slow"):
        runs = []
        for _ in range(lines):
            r = await engine.synthesize(SHORT, None, timeout=60)
            runs.append({"wall_ms": r.wall_ms, "audio_ms": r.audio_ms, "rtf": r.rtf, "bytes": len(r.wav)})
        out["lines"] = runs
    else:
        out["log_tail"] = engine.log_tail(30) if hasattr(engine, "log_tail") else ""
    await engine.stop()
    await engine.close()
    return out


print("RESULT " + json.dumps(asyncio.run(main()), default=str))
