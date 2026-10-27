"""Higgs voice, on this device: the small amount of glue that puts the voice engine manager and the SkyrimNet-facing routes into MultiProxy (2026-10-02).

Everything real lives in `voice_engine.py` (manager), `voice_downloads.py` (kept downloads) and `voice_service.py` (SkyrimNet's voice language).
This file only decides where things live on disk (two settings, both a folder the player may choose, shown on the Voice tab in a later item)
and exposes the manager's actions as plain routes the Voice tab will call. No dashboard screen is built here.

Settings (in config.json): `voice_folder` (where the engine, the model, the player's clips and the log live; default `<Local models folder>/voice`, or `<settings folder>/voice`
with no models folder), `voice_downloads_folder` (the kept downloads; default `<Local models folder>/voice/downloads`, see `downloads_plan`) and `voice_location` (where Higgs runs: `this` or the
nickname of a Courier device; step 2, see voice_remote.py).
"""
import asyncio
import json
import re
import time
from pathlib import Path
from urllib.parse import quote
from typing import Callable, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

import courier_system
import other_pcs
import poll_cache
import safe_paths
from voice_clips import BAD_CLIP, CLIP_MAX_BYTES, FORMATS_SAY, SOUND_EXTENSIONS, ClipFormatError, voice_key
from voice_downloads import DownloadStore, default_voice_folder, downloads_plan
from voice_engine import DEFAULT_VOICE_MODEL, ENGINE_VERSION, LICENSE_VERSION, VOICE_MODELS, VoiceEngine, VoiceEngineError
from voice_remote import RemoteVoiceEngine, RoutedEngine, VoiceRemote
from stt_xai import CONFIG_KEY as STT_CONFIG_KEY, XAI_URL as XAI_STT_URL, XaiStt
from voice_service import VoiceService, mount


# Where the player reads the model's full license (the model's own page; the file is called LICENSE there).
LICENSE_URL = "https://huggingface.co/bosonai/higgs-tts-3-4b/blob/main/LICENSE"


def endpoint_address(port: int) -> str:
    """The address SkyrimNet's Higgs tab takes in its Endpoint box. The Voice card and Settings > SkyrimNet connection both show this one."""
    return f"http://127.0.0.1:{port}"


def stt_address(port: int) -> str:
    """The address SkyrimNet's External Whisper box takes. The Speech to text card and Settings > SkyrimNet connection both show this one."""
    return endpoint_address(port) + "/v1/audio/transcriptions"


class VoiceHandles:
    def __init__(self, engine: VoiceEngine, service: VoiceService, store: DownloadStore, remote: Optional[VoiceRemote] = None,
                 routed: Optional[RoutedEngine] = None, autostart: Optional[Callable] = None):
        self.engine, self.service, self.store, self.remote, self.routed = engine, service, store, remote, routed
        self._autostart = autostart
        self.license_ok: Callable[[], bool] = lambda: False       # whether the voice model's licence is accepted on this device (Set me up reads it; it never accepts one)

    async def autostart(self) -> None:
        """At MultiProxy's start: start Higgs voice on this device if the player turned on "Start with this device" and it is installed."""
        if self._autostart is not None:
            await self._autostart()


TEST_LINE = "The road north is long, but the inn ahead will have a warm fire."        # (one plain, neutral sentence)
TEST_NEEDS_CLIP = "Add a voice clip below to test it."
TEST_FAILED = "The voice couldn't speak a line. Click **Log** to see why."


def plain_test_failure(message: str) -> str:
    """The voice rows' own plain sentences pass; anything that looks like raw program output (braces, a bare status code) is replaced by one short sentence."""
    m = str(message or "").strip()
    if not m or re.search(r"[{}\[\]]|\(\d{3}\)|Traceback|Error:", m):
        return TEST_FAILED
    return m


def setup(app: FastAPI, load_cfg: Callable[[], dict], save_cfg: Callable[[dict], None], settings_dir: Callable[[], str],
          redact: Callable[[str], str], logger, remotes=None, this_pc: Callable[[], str] = lambda: "this device", engine: Optional[VoiceEngine] = None,
          source_factory=None, main_port: Callable[[], int] = lambda: 8000, open_folder: Optional[Callable[[str], None]] = None,
          enable_tags: Optional[Callable[[], bool]] = None, pick_file: Optional[Callable[..., Optional[str]]] = None,
          gpu_choice_fn: Callable[[], Optional[dict]] = lambda: None, cards_fn: Optional[Callable[[], list]] = None,
          xai_key: Callable[[], Optional[str]] = lambda: None, stt_url: str = XAI_STT_URL) -> VoiceHandles:
    """`main_port`: MultiProxy's own port (the address SkyrimNet is given: no separate voice port). `open_folder`: shows a folder in the file manager.
    `enable_tags`: switches on "My NPCs use the Higgs voice engine" and says whether it changed anything (the Voice tab then says so).
    `pick_file`: a native file dialog (title, filetypes) -> a path or None, for "Add a clip..."."""
    def models_folder() -> Optional[str]:
        return (load_cfg().get("local_models") or {}).get("folder") or None

    def voice_folder() -> Path:
        return Path(load_cfg().get("voice_folder") or default_voice_folder(settings_dir(), models_folder()))

    def plan() -> dict:
        return downloads_plan(settings_dir(), models_folder(), load_cfg().get("voice_downloads_folder"))

    def downloads_folder() -> Path:
        return plan()["folder"]

    def location() -> str:
        return str(load_cfg().get("voice_location") or "this")

    store = engine.store if engine is not None else DownloadStore(downloads_folder)
    engine = engine or VoiceEngine(voice_folder, store, redact=redact, gpu_choice_fn=gpu_choice_fn,
                                    **({"cards_fn": cards_fn} if cards_fn else {}))      # (tests hand in one with a fake release behind it)
    remote_engine = RemoteVoiceEngine(remotes, lambda: location() if location() != "this" else "", lambda: voice_folder() / "work", log=lambda m: logger.warning(m))
    routed = RoutedEngine(engine, remote_engine, location)
    service = VoiceService(routed, lambda: voice_folder() / "work", log=lambda m: (logger.warning(m), engine.log(m)))
    stt = XaiStt(load_cfg, xai_key, service.log, url=stt_url)          # speech to text through xAI: off unless the player turns it on (stt_xai.py)
    mount(app, service, stt)
    remote =VoiceRemote(remotes, engine, store, voice_folder, this_pc, logger.info, source_factory) if remotes is not None else None

    def err(e: VoiceEngineError):
        return JSONResponse({"detail": e.message}, status_code=e.status)

    async def body_of(request: Request) -> dict:
        try:
            d = await request.json()
        except Exception:
            return {}
        return d if isinstance(d, dict) else {}

    @app.get("/config/voice/fit")
    async def voice_fit(model: str = ""):
        """Can this device run the voice model (default: the chosen one), and how: {verdict: ok|warn|refuse, build, reasons[], warnings[], needDiskBytes, speedCheck}."""
        try:
            return await asyncio.to_thread(lambda: engine.fit_check(model or None).to_json())
        except VoiceEngineError as e:
            return err(e)

    @app.post("/config/voice/model")
    async def voice_model_choose(request: Request):
        """Choose which voice model this device uses: {"model": "higgs" | "qwen3tts" | "chatterbox"}. A running voice is stopped (it is not started again by itself)."""
        data = await body_of(request)
        try:
            await engine.select_model(str(data.get("model") or ""))
        except VoiceEngineError as e:
            return err(e)
        return service.status()

    @app.post("/config/voice/install")
    async def voice_install(request: Request):
        data = await body_of(request)
        try:
            if data.get("model"):
                await engine.select_model(str(data["model"]))
        except VoiceEngineError as e:
            return err(e)
        if not this_license_ok():
            return JSONResponse({"detail": f"You must accept the {engine.voice_name} license first."}, status_code=403)
        try:
            job = engine.start_install()
        except VoiceEngineError as e:
            return err(e)
        when_installed(job, "this")
        return service.status()

    @app.post("/config/voice/cancel")
    async def voice_cancel():
        return {"cancelled": engine.cancel_install()}

    @app.post("/config/voice/start")
    async def voice_start():
        try:
            await engine.start()
        except VoiceEngineError as e:
            return err(e)
        return service.status()

    @app.post("/config/voice/stop")
    async def voice_stop():
        await engine.stop()
        return service.status()

    @app.post("/config/voice/restart")
    async def voice_restart():
        try:
            await engine.restart()
        except VoiceEngineError as e:
            return err(e)
        return service.status()

    @app.post("/config/voice/uninstall")
    async def voice_uninstall(request: Request):
        try:
            data = await request.json()
        except Exception:
            data = {}
        try:
            key = str(data.get("model") or "") or None
            installed_before = engine.installed_models()
            freed = await engine.uninstall(keep_clips=not bool(data.get("alsoClips")), model_key=key)
            if data.get("deleteFiles") is True and key in engine.catalog:        # "Remove, delete files": that voice's kept downloads go too, never a file another installed voice uses
                def files_of(k):
                    vm = engine.catalog[k]
                    return {vm.file.name} | ({vm.companion.name} if vm.companion else set())
                still = set().union(*[files_of(k) for k in installed_before if k != key and k in engine.catalog]) if len(installed_before) > 1 else set()
                freed += await asyncio.to_thread(engine.store.clear, sorted(files_of(key) - still))
        except VoiceEngineError as e:
            return err(e)
        cfg = load_cfg()
        if cfg.pop("voice_tags_switched_on", None) is not None:
            save_cfg(cfg)
        return {"freedBytes": freed, **service.status()}

    @app.get("/config/voice/log")
    async def voice_log():
        return {"log": engine.log_tail(200)}

    @app.get("/config/voice/output")
    async def voice_output(since: int = 0, limit: int = 200):
        """The Engine output window for the voice on this device: what the engine printed after line `since` (the last `limit` lines when 0)."""
        return engine.output_since(since, limit)

    @app.get("/config/voice/downloads")
    async def voice_downloads():
        return store.listing()

    @app.post("/config/voice/downloads/clear")
    async def voice_downloads_clear():
        freed = await asyncio.to_thread(store.clear)
        return {"freedBytes": freed, **store.listing()}

    @app.post("/config/voice/downloads/open-folder")
    async def voice_downloads_open_folder():
        """Show the folder the kept downloads are in (the one in use now, nothing else can be asked for)."""
        folder = store.folder
        if open_folder is None:
            return JSONResponse({"detail": "Couldn't open that folder."}, status_code=500)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            open_folder(str(folder))
        except Exception as e:
            return JSONResponse({"detail": getattr(e, "detail", None) or "Couldn't open that folder."}, status_code=500)
        return {"status": "opened"}

    @app.post("/config/voice/downloads/move")
    async def voice_downloads_move(request: Request):
        """The offer "Move your kept voice downloads to <folder>?": {"move": true} moves them (checked first), {"move": false} is "Not now" (asked once)."""
        data = await request.json()
        cfg = load_cfg()
        if not data.get("move"):
            cfg["voice_downloads_move_declined"] = True
            save_cfg(cfg)
            return {"declined": True}
        pl = plan()
        if not pl["offer"]:
            return JSONResponse({"detail": "There is nothing to move."}, status_code=409)
        if engine.job and engine.job.state == "running":
            return JSONResponse({"detail": "Wait for the install to finish before moving files."}, status_code=409)
        old = DownloadStore(lambda: pl["old"])
        try:
            out = await asyncio.to_thread(old.move_kept, pl["target"])
        except OSError:
            return JSONResponse({"detail": "The files couldn't be moved, so they stayed where they were."}, status_code=500)
        return {**out, "folder": str(downloads_folder())}

    @app.post("/config/voice/folders")
    async def voice_folders(request: Request):
        """Choose where Higgs voice lives and/or where its downloads are kept: {"voiceFolder": "...", "downloadsFolder": "..."}."""
        data = await request.json()
        cfg = load_cfg()
        for key, cfg_key in (("voiceFolder", "voice_folder"), ("downloadsFolder", "voice_downloads_folder")):
            if key in data:
                p = str(data[key] or "").strip()
                if p:
                    if cfg_key == "voice_folder" and engine.installed():
                        return JSONResponse({"detail": f"Uninstall {engine.voice_name} before moving it to a new folder."}, status_code=409)
                    if not safe_paths.is_local_fixed(p):
                        return JSONResponse({"detail": safe_paths.NOT_LOCAL_SENTENCE}, status_code=400)
                    try:
                        Path(p).mkdir(parents=True, exist_ok=True)
                    except OSError:
                        return JSONResponse({"detail": "That folder can't be used."}, status_code=400)
                    cfg[cfg_key] = p
                else:
                    cfg.pop(cfg_key, None)
        save_cfg(cfg)
        return {"voiceFolder": str(voice_folder()), "downloadsFolder": str(downloads_folder())}

    # ---- the Voice tab: the license on this device, one overview for the screen, and the small extras ------------------------------------------
    def license_version_of(model: str) -> str:
        return LICENSE_VERSION if model == DEFAULT_VOICE_MODEL else VOICE_MODELS[model].license_version

    def this_license_file(model: Optional[str] = None) -> Path:
        model = model or engine.model_key
        return voice_folder() / ("voice-license-this-pc.json" if model == DEFAULT_VOICE_MODEL else f"voice-license-this-pc-{model}.json")      # (Higgs keeps its original file)

    def this_license(model: Optional[str] = None) -> Optional[dict]:
        try:
            d = json.loads(this_license_file(model).read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else None
        except (OSError, ValueError):
            return None

    def this_license_ok(model: Optional[str] = None) -> bool:
        rec = this_license(model)
        return bool(rec) and rec.get("license_version") == license_version_of(model or engine.model_key)

    @app.post("/config/voice/license")
    async def voice_license(request: Request):
        """The player accepted a voice model's license for THIS device (default: the chosen model; the other devices: /config/voice/pc/license). Saved: which device, when, which version."""
        model = str((await body_of(request)).get("model") or "") or engine.model_key
        if model not in VOICE_MODELS:
            return JSONResponse({"detail": "MultiProxy doesn't recognize that voice."}, status_code=400)
        rec = {"pc": this_pc(), "accepted_at": int(time.time()), "license_version": license_version_of(model), "model": model}
        this_license_file(model).parent.mkdir(parents=True, exist_ok=True)
        this_license_file(model).write_text(json.dumps(rec, indent=1), encoding="utf-8")
        return {"license": rec, "licenseOk": True}

    def tags_on() -> None:
        """Installing Higgs turns on the Higgs voice-tag fixing (once it is installed); the tab says so when it did. Uninstalling leaves it as it is."""
        if enable_tags is None:
            return
        try:
            changed = enable_tags()
        except Exception as e:
            logger.warning(f"Couldn't switch on the Higgs voice setting: {e.__class__.__name__}")
            return
        if changed:
            cfg = load_cfg()
            cfg["voice_tags_switched_on"] = True
            save_cfg(cfg)

    async def stop_here_after_move() -> str:
        """The voice moved off this device: stop the copy here (it would otherwise keep holding graphics memory for nothing). Returns a short warning when it would not stop, else ""."""
        try:
            await engine.stop()
        except Exception as e:
            logger.warning(f"Couldn't stop the voice on this PC after moving it: {getattr(e, 'message', e)}")
            return "Couldn't stop the model on this PC. Please stop it manually."
        return ""

    def when_installed(job, pc: str = "this") -> None:
        """When an install finishes well: Higgs voice now runs on that device (lines go there; SkyrimNet's address doesn't change), and the tag fixing is on."""
        def done(_t) -> None:
            if job.state != "done":
                return
            came_from = location()
            cfg = load_cfg()
            if pc == "this":
                cfg.pop("voice_location", None)
            else:
                cfg["voice_location"] = pc
            save_cfg(cfg)
            tags_on()
            if came_from == "this" and pc != "this":
                asyncio.ensure_future(stop_here_after_move())
            if came_from != pc:
                asyncio.ensure_future(carry_clips(came_from, pc, record=True))
        if job is not None and getattr(job, "task", None) is not None:
            job.task.add_done_callback(done)

    async def autostart() -> None:
        if load_cfg().get("voice_autostart") and engine.installed() and location() == "this":
            try:
                await engine.start()
            except VoiceEngineError as e:
                logger.warning(f"Higgs voice didn't start with MultiProxy: {e.message}")

    async def autostart_here() -> None:
        """The voice came back to this device: start it here again."""
        try:
            await engine.start()
        except VoiceEngineError as e:
            logger.warning(f"Higgs voice didn't start after moving back: {e.message}")

    stopped_pcs: set = set()          # devices whose voice the player stopped since MultiProxy started: left alone until the player starts it (or MultiProxy starts again)

    def pc_autostart(pc: str) -> bool:
        raw = load_cfg().get("voice_autostart_pcs")
        v = raw.get(pc) if isinstance(raw, dict) else None
        return True if v is None else bool(v)          # on unless the player switched it off for that device

    async def autostart_pc(r) -> None:
        """A device came online (or MultiProxy started and it answered): start the voice there when the player asked for that, it is installed with its license accepted, it is the
        voice in use and it is not already running. Once per coming-online; the player's own Stop is respected. A failure is one log line, never retried until the device comes online again."""
        v = (r.report or {}).get("voice") or {}
        if (not pc_autostart(r.id) or str(location()).lower() != r.id.lower() or r.id in stopped_pcs or not v.get("installed") or not v.get("licenseAccepted")
                or v.get("state") in ("ready", "starting", "getting_ready", "installing")):
            return
        logger.info(f"Starting the voice on {r.label} automatically")
        try:
            await need_remote().act(r.id, "start", {})
        except Exception as e:
            logger.warning(f"The voice didn't start on {r.label} by itself: {getattr(e, 'message', e.__class__.__name__)}")

    def on_online(r) -> None:
        if r.kind != "courier":
            return
        try:
            asyncio.get_running_loop().create_task(autostart_pc(r))
        except RuntimeError:
            pass
    if remotes is not None:
        remotes.online_hooks.append(on_online)

    @app.post("/config/voice/autostart")
    async def voice_autostart(request: Request):
        """"Start with this device": start Higgs voice on this device whenever MultiProxy starts. {"on": true|false}. With "pc": the same for the voice on that other device: it starts
        when MultiProxy starts and that device is online, and whenever it comes online later."""
        data = await request.json()
        cfg = load_cfg()
        pc = str(data.get("pc") or "this").strip()
        if pc not in ("", "this"):
            raw = dict(cfg.get("voice_autostart_pcs") if isinstance(cfg.get("voice_autostart_pcs"), dict) else {})
            if data.get("on"):
                raw.pop(pc, None)                      # on is the default: nothing is written
            else:
                raw[pc] = False
            if raw:
                cfg["voice_autostart_pcs"] = raw
            else:
                cfg.pop("voice_autostart_pcs", None)
            save_cfg(cfg)
            return {"autostart": pc_autostart(pc), "pc": pc}
        if data.get("on"):
            cfg["voice_autostart"] = True
        else:
            cfg.pop("voice_autostart", None)
        save_cfg(cfg)
        return {"autostart": bool(cfg.get("voice_autostart"))}

    @app.get("/config/voice/engine-check")
    async def voice_engine_check():
        """"Check for a newer engine": MultiProxy only ever moves to engine versions it has been tested with, so this reads the version on this device
        against the one MultiProxy is built for (no internet needed)."""
        have = engine._read_state().get("engine") if engine.installed() else None
        return {"installed": have, "newest": ENGINE_VERSION, "upToDate": bool(have) and have == ENGINE_VERSION}

    def read_card() -> dict:
        """Ask the graphics driver and the system for this device's card and memory (starts a program: only ever called on a worker thread)."""
        gpu, ram = None, None
        try:
            g = engine.hardware().gpu
            gpu = g.to_json() if g else None
        except Exception:
            pass
        try:
            m = courier_system.read_pc_memory()
            ram = round(m["total_mb"] / 1024, 1) if m and m.get("total_mb") else None
        except Exception:
            pass
        return {"name": this_pc(), "gpu": gpu, "ramGb": ram}

    # Read at most every 30 seconds, never on the event loop, and never waited for by a poll after the first one (see decision_wiring).
    card = poll_cache.SlowValue(read_card, 30.0)

    def this_pc_card() -> dict:
        return card.get()

    def uninstall_frees_bytes() -> int:
        """What uninstalling would give back: the engine and the model, except a model file that is also a kept download (that part comes back
        only when the kept downloads are cleared: the tab says so)."""
        total = 0
        for d in (engine.engine_dir, engine.models_dir):
            if d.is_dir():
                for p in d.rglob("*"):
                    try:
                        if p.is_file() and not p.is_symlink() and (p.stat().st_nlink <= 1 or d != engine.models_dir):
                            total += p.stat().st_size
                    except OSError:
                        pass
        return total

    # What uninstalling frees walks every file of the engine and the model: read on a worker thread at most every 10 s, never waited for by a poll.
    frees = poll_cache.SlowValue(uninstall_frees_bytes, 10.0)

    def model_row(m) -> dict:
        """One voice model as the Setup step's picker shows it."""
        file = engine.model if m.key == engine.model_key else m.file
        return {"key": m.key, "name": m.name, "label": m.label, "oneLine": m.one_line, "sizeBytes": file.size, "licenseWho": m.license_who, "licenseUrl": m.license_url,
                "licenseNote": m.license_note, "licenseVersion": license_version_of(m.key), "clones": True, "needsGpuGb": round(m.vram_bytes / 1024**3, 1)}

    def stt_card() -> dict:
        return {**stt.status(), "address": stt_address(main_port())}

    @app.post("/config/voice/stt")
    async def voice_stt(request: Request):
        """The "Turn my voice into text with xAI" switch. {"on": true|false}. Turning it on needs the xAI key saved on the Providers tab."""
        data = await body_of(request)
        cfg = load_cfg()
        if data.get("on") is True:
            if not xai_key():
                return JSONResponse({"detail": "Add your xAI key on the Providers tab first."}, status_code=400)
            cfg[STT_CONFIG_KEY] = True
        else:
            cfg.pop(STT_CONFIG_KEY, None)
        save_cfg(cfg)
        return stt_card()

    @app.get("/config/voice/overview")
    async def voice_overview():
        """Everything the Voice tab shows, in one cheap read (no hardware probing: `/config/voice/fit` and `/config/voice/pc` do that, on demand)."""
        if engine.installed():
            await frees.ensure()
        await card.ensure()        # (the card read starts a program, 190-880 ms every 30 s: on a worker thread, kept 30 s; see decision_wiring)
        pcs = []
        for r in courier_pcs():
            rep = r.report or {}
            mem = (rep.get("pc_memory") or {}).get("total_mb")
            pcs.append({"id": r.id, "name": r.label, "online": r.online is not False, "gpu": r.gpu, "ramGb": round(mem / 1024, 1) if mem else None,
                        "voice": rep.get("voice"), "hasVoice": "voice" in rep, "supportedModels": other_pcs.courier_voice_models(rep), "courierOlder": other_pcs.courier_is_older(rep), "licenseOk": remote.license_ok(r.id, engine.model_key) if remote else False,
                        "licenses": {k: (remote.license_ok(r.id, k) if remote else False) for k in VOICE_MODELS}, "autostart": pc_autostart(r.id),
                        "job": remote.jobs[r.id].to_json() if remote and r.id in remote.jobs else None})
        cfg = load_cfg()
        status = service.status()
        vm = engine.vmodel
        return {"stt": stt_card(), "status": status, "location": location(), "pcs": pcs,"thisPc": {**this_pc_card(), "licenseOk": this_license_ok(), "license": this_license(),
                                                                                 "licenses": {k: this_license_ok(k) for k in VOICE_MODELS},
                                                                                 "installedModels": engine.installed_models(), "autostart": bool(cfg.get("voice_autostart"))},
                "model": vm.key, "models": [model_row(m) for m in engine.catalog.values()],
                "thisEngine": status if location() == "this" else engine.status(),
                "carried": last_carry.pop("v", None), "address": endpoint_address(main_port()), "licenseUrl": vm.license_url, "licenseVersion": license_version_of(vm.key),
                "modelBytes": engine.model.size + engine._companion_size(), "downloads": store.listing(), "tagsSwitchedOn": bool(cfg.get("voice_tags_switched_on")),
                "uninstallFreesBytes": frees.get() if engine.installed() else 0, "voiceFolder": str(voice_folder()), "downloadsFolder": str(store.folder),
                "moveOffer": ({"to": str(plan()["target"])} if plan()["offer"] and not cfg.get("voice_downloads_move_declined") else None)}

    @app.post("/config/voice/open-folder")
    async def voice_open_folder():
        """Show the folder the engine lives in (for adding it to antivirus's allowed list)."""
        folder = engine.folder
        if open_folder is None or not folder.is_dir():
            return JSONResponse({"detail": "That folder isn't there yet."}, status_code=404)
        try:
            open_folder(str(folder))
        except Exception as e:
            return JSONResponse({"detail": getattr(e, "detail", None) or "Couldn't open that folder."}, status_code=500)
        return {"status": "opened"}

    # ---- your own voice clips (voice_clips.py): they live on the device that runs Higgs; from here they are reached on that device through the Courier ------------
    def clips_pc() -> str:
        return location()

    last_carry: dict = {}     # the install's own move (nobody is waiting for its answer): the Voice tab reads it once, from the overview

    async def carry_clips(source: str, target: str, record: bool = False):
        """Higgs moved from `source` to `target`: copy the clips across. (count, plain reason or ""). `record`: keep the result for the overview."""
        if source == target or remote is None:
            return 0, ""
        try:
            n = await remote.carry_clips(source, target)
            if record:
                last_carry["v"] = {"pc": target, "count": n, "error": ""}
            if n:
                logger.info(f"Voice clips: {n} copied to {'this device' if target == 'this' else target}")
            return n, ""
        except Exception as e:
            logger.warning(f"Voice clips couldn't be copied to {target}: {getattr(e, 'message', e.__class__.__name__)}")
            say = f"The voice clips couldn't be copied to {'this device' if target == 'this' else target} ({getattr(e, 'message', 'it did not answer')})."
            if record:
                last_carry["v"] = {"pc": target, "count": 0, "error": say}
            return 0, say

    @app.get("/config/voice/clips")
    async def voice_clips_list():
        """{pc, onThisPc, formats, folder, clips: [{voice, source: "yours"|"skyrimnet", file, seconds, supported, hasSkyrimNet}]} for the device Higgs runs on."""
        pc = clips_pc()
        try:
            clips = engine.clips_rows() if pc == "this" else await need_remote().clips_list(pc)
        except Exception as e:
            return pc_error(e)
        return {"pc": pc, "onThisPc": pc == "this", "formats": FORMATS_SAY, "folder": str(engine.clips_dir) if pc == "this" else None, "clips": clips}

    @app.post("/config/voice/clips/pick")
    async def voice_clips_pick():
        """The native "choose a sound file" window (on this device, in front of everything). {path} or {path: null} when cancelled / no screen."""
        if pick_file is None:
            return {"path": None}
        return {"path": await asyncio.to_thread(pick_file, "Choose a voice clip", [("WAV sound files", "*.wav"), ("All files", "*.*")])}

    @app.post("/config/voice/clips/add")
    async def voice_clips_add(request: Request):
        """Use a clip for a voice: {"voice": "femalenord", "path": "<a file on this device>"} (or "dataB64"). The old clip of the player's, if any, is moved aside."""
        data = await request.json()
        voice = str(data.get("voice") or "").strip()
        if not voice_key(voice):
            return JSONResponse({"detail": "Which voice is this clip for?"}, status_code=400)
        blob = None
        if data.get("dataB64"):
            import base64
            try:
                blob = base64.b64decode(str(data["dataB64"]), validate=True)
            except Exception:
                return JSONResponse({"detail": "That clip couldn't be read."}, status_code=400)
        else:
            path = Path(str(data.get("path") or ""))
            if not path.is_file():
                return JSONResponse({"detail": "That file isn't there."}, status_code=400)
            if path.suffix.lower() not in SOUND_EXTENSIONS:
                return JSONResponse({"detail": BAD_CLIP}, status_code=400)
            if path.stat().st_size > CLIP_MAX_BYTES:
                return JSONResponse({"detail": "That clip is too big. A clear recording of 8 to 15 seconds is plenty."}, status_code=400)
            blob = await asyncio.to_thread(path.read_bytes)
        pc = clips_pc()
        words = str(data["words"]) if data.get("words") is not None else None          # the words spoken in the clip (the Qwen3-TTS voice needs them)
        try:
            row = engine.clips.add(voice, blob, words) if pc == "this" else await need_remote().clips_add(pc, voice, blob, "yours", words)
        except (VoiceEngineError, ClipFormatError) as e:
            return err(e)
        except Exception as e:
            return pc_error(e)
        return {"clip": row, "pc": pc}

    @app.post("/config/voice/clips/words")
    async def voice_clips_words(request: Request):
        """Change the words spoken in a voice's clip, without choosing a file: {"voice": "femalenord", "words": "..."}. Typed words always win over the ones the voice worked out."""
        data = await request.json()
        voice, pc = str(data.get("voice") or "").strip(), clips_pc()
        if not voice_key(voice):
            return JSONResponse({"detail": "Which voice is this clip for?"}, status_code=400)
        words = str(data.get("words") or "")
        try:
            row = engine.clips.set_words_row(voice, words) if pc == "this" else await need_remote().clips_words(pc, voice, words)
        except Exception as e:
            return pc_error(e)
        return {"clip": row, "pc": pc}

    @app.post("/config/voice/clips/remove")
    async def voice_clips_remove(request: Request):
        """Back to SkyrimNet's clip for that voice: {"voice": "femalenord"}. The player's clip is moved to the replaced folder, not deleted."""
        data = await request.json()
        pc, voice = clips_pc(), str(data.get("voice") or "")
        try:
            out = {"removed": engine.clips.remove(voice)} if pc == "this" else await need_remote().clips_remove(pc, voice)
        except Exception as e:
            return pc_error(e)
        return {**out, "pc": pc}

    @app.get("/config/voice/clips/audio")
    async def voice_clips_audio(voice: str = "", which: str = ""):
        """One clip's sound (`which`: "yours" or "skyrimnet"; empty is the one in use), for the Play button."""
        pc = clips_pc()
        try:
            wav = engine.clips.audio(voice, which) if pc == "this" else await need_remote().clips_audio(pc, voice, which)
        except Exception as e:
            return pc_error(e)
        if not wav:
            return JSONResponse({"detail": "That voice doesn't have a sample clip."}, status_code=404)
        return Response(content=wav, media_type="audio/wav", headers={"Cache-Control": "no-store"})

    # ---- the Test button: one short line, made the way SkyrimNet's lines are, played in the page --------------------------------------------------------
    @app.post("/config/voice/test")
    async def voice_test(request: Request):
        """{"voice": "<a clip>"} (optional). The sound of one short line made with the installed voice, on the device it runs on (a Courier relays it like any line), using the player's
        own clip (the one asked for, else the first), else the first clip SkyrimNet sent. With no clip at all: `{"needsClip": true, "detail": ...}` and nothing is made."""
        data = await body_of(request)
        st = service.status()
        if not st.get("ready"):
            return JSONResponse({"detail": routed.not_ready_message() or "The voice isn't running."}, status_code=409)
        pc = clips_pc()
        try:
            rows = engine.clips_rows() if pc == "this" else await need_remote().clips_list(pc)
        except Exception as e:
            return pc_error(e)
        usable = [r for r in rows if r.get("supported")]
        mine = [r for r in usable if r.get("source") == "yours"]
        kept = [r for r in usable if r.get("source") == "skyrimnet"]
        want = str(data.get("voice") or "")
        pick = next((r for r in mine if r.get("voice") == want), None) or (mine[0] if mine else (kept[0] if kept else None))
        if pick is None:
            return {"needsClip": True, "detail": TEST_NEEDS_CLIP}
        try:
            wav = await service.speak_test(TEST_LINE, str(pick.get("voice") or ""))
        except VoiceEngineError as e:
            status = e.status if isinstance(e.status, int) and 400 <= e.status < 600 else 502
            return JSONResponse({"detail": plain_test_failure(e.message)}, status_code=status)
        except Exception as e:
            logger.warning(f"The voice test line failed: {e.__class__.__name__}")
            return JSONResponse({"detail": TEST_FAILED}, status_code=502)
        return Response(content=wav, media_type="audio/wav", headers={"Cache-Control": "no-store", "X-Voice-Clip": quote(str(pick.get("voice") or ""))})

    @app.post("/config/voice/clips/open-folder")
    async def voice_clips_open_folder():
        """Show the clips folder (on this device, when Higgs runs here)."""
        if clips_pc() != "this":
            return JSONResponse({"detail": f"The clips folder is on {clips_pc()}. Click Add a clip… to send one there."}, status_code=400)
        if open_folder is None:
            return JSONResponse({"detail": "Couldn't open that folder."}, status_code=500)
        engine.clips_dir.mkdir(parents=True, exist_ok=True)
        try:
            open_folder(str(engine.clips_dir))
        except Exception as e:
            return JSONResponse({"detail": getattr(e, "detail", None) or "Couldn't open that folder."}, status_code=500)
        return {"status": "opened"}

    # ---- where Higgs runs, and Higgs on another device (voice_remote.py) --------------------------------------------------------------------
    def courier_pcs() -> list:
        return [r for r in (remotes.all() if remotes is not None else []) if r.kind == "courier"]

    def need_remote() -> VoiceRemote:
        if remote is None:
            raise VoiceEngineError(404, "Other devices aren't available here.")
        return remote

    async def enroll_where_it_runs(key: str, data: bytes, words: str) -> None:
        """A voice SkyrimNet registers (`POST /v1/audio/voices`) is kept on the device the voice runs on: here, or sent to that Courier device with its words (the
        Courier's existing clip and words routes, so a Courier needs no new code for it; an earlier SkyrimNet clip of the same voice there is kept as it is)."""
        pc = location()
        if pc == "this":
            engine.clips.enroll(key, data, words)
            return
        r = need_remote()
        await r.clips_add(pc, key, data, "skyrimnet")
        if words:
            await r.clips_words(pc, key, words)

    service.enroll_hook = enroll_where_it_runs

    def pc_error(e: Exception):
        status = getattr(e, "status", 500)
        return JSONResponse({"detail": getattr(e, "message", str(e))}, status_code=status if isinstance(status, int) and 400 <= status < 600 else 500)

    @app.get("/config/voice/location")
    async def voice_location_get():
        """{location: "this" | <PC>, pcs: [{id, online, voice}]}: where Higgs runs now, and which of the other devices could run it."""
        return {"location": location(), "pcs": [{"id": r.id, "online": r.online is not False, "voice": (r.report or {}).get("voice")} for r in courier_pcs()],
                "status": service.status()}

    @app.post("/config/voice/location")
    async def voice_location_set(request: Request):
        """Move Higgs to another device (or back): {"pc": "this"} or {"pc": "<nickname>"}. SkyrimNet's Endpoint doesn't change."""
        data = await request.json()
        pc = str(data.get("pc") or "this").strip()
        if pc.lower() != "this":
            match = next((r for r in courier_pcs() if r.id.lower() == pc.lower()), None)
            if match is None:
                return JSONResponse({"detail": "That device is missing MultiProxy Courier. Install it first."}, status_code=404)
            pc = match.id
        came_from = location()
        cfg = load_cfg()
        if pc.lower() == "this":
            cfg.pop("voice_location", None)
        else:
            cfg["voice_location"] = pc
        save_cfg(cfg)
        carried, carry_error = await carry_clips(came_from, location())          # the clips go along (copied; the old device keeps its own)
        warn = await stop_here_after_move() if came_from == "this" and location() != "this" else ""
        if location() == "this" and came_from != "this" and engine.installed():
            asyncio.ensure_future(autostart_here())
        return {"location": location(), "status": service.status(), "clipsCarried": carried, "clipsError": carry_error, "moveWarning": warn}

    @app.get("/config/voice/pc")
    async def voice_pc(id: str = "", model: str = DEFAULT_VOICE_MODEL):
        try:
            return await need_remote().describe(id, model or DEFAULT_VOICE_MODEL)
        except Exception as e:
            return pc_error(e)

    @app.post("/config/voice/pc/license")
    async def voice_pc_license(request: Request):
        """The player accepted a voice model's license for that device: {"id": "<device>", "model": "higgs" (default) | "qwen3tts" | "chatterbox"}."""
        data = await request.json()
        try:
            return await need_remote().accept_license(str(data.get("id", "")), str(data.get("model") or DEFAULT_VOICE_MODEL))
        except Exception as e:
            return pc_error(e)

    @app.post("/config/voice/pc/install")
    async def voice_pc_install(request: Request):
        """Install a voice model on that device: {"id": "<device>", "source": "gaming-pc" (default) | "that-pc", "model": "higgs" (default) | "qwen3tts" | "chatterbox"}."""
        data = await request.json()
        model = str(data.get("model") or DEFAULT_VOICE_MODEL)
        try:
            rem = need_remote()
            if this_license_ok(model):
                await rem.ensure_license(str(data.get("id", "")), model)        # asked once per model: what was accepted on this device covers the other devices (a newer license asks again); the device is asked what it really holds, so a lost record is sent again
            job = rem.start_install(str(data.get("id", "")), str(data.get("source") or "gaming-pc"), model)
            when_installed(job, job.pc)
            return job.to_json()
        except Exception as e:
            return pc_error(e)

    @app.post("/config/voice/redownload")
    async def voice_redownload(request: Request):
        """"Download it again" after a file failed its check: forget that kept file, then install again (it is fetched fresh, then carried on).
        {"pc": "this" | "<PC>", "file": "<file name>"}."""
        data = await request.json()
        pc, name = str(data.get("pc") or "this"), str(data.get("file") or "")
        known = {f.name for b in engine.builds.values() for f in b.files} | {engine.model.name} | {m.file.name for m in engine.catalog.values()} | {m.companion.name for m in engine.catalog.values() if m.companion}
        if name not in known:
            return JSONResponse({"detail": "That is not a voice file."}, status_code=400)
        if not this_license_ok():
            return JSONResponse({"detail": f"You must accept the {engine.voice_name} license first."}, status_code=403)
        await asyncio.to_thread(store.discard, name)
        try:
            if pc == "this":
                job = engine.start_install()
                when_installed(job, "this")
                return service.status()
            rem = need_remote()
            await rem.ensure_license(pc, engine.model_key)
            job = rem.start_install(pc, "gaming-pc", engine.model_key)
            when_installed(job, job.pc)
            return job.to_json()
        except VoiceEngineError as e:
            return err(e)
        except Exception as e:
            return pc_error(e)

    @app.get("/config/voice/pc/job")
    async def voice_pc_job(id: str = ""):
        try:
            r = need_remote().remotes._courier(id)
        except Exception as e:
            return pc_error(e)
        job = remote.jobs.get(r.id)
        return job.to_json() if job else {"state": "none"}

    @app.post("/config/voice/pc/{action}")
    async def voice_pc_action(action: str, request: Request):
        """cancel | start | stop | restart | uninstall on that device: {"id": "<PC>"} (uninstall: {"alsoClips": true} to remove the player's clips too)."""
        data = await request.json()
        try:
            rem = need_remote()
            if action == "stop":
                stopped_pcs.add(str(data.get("id", "")))              # a deliberate Stop is respected until the player starts it again
            elif action in ("start", "restart"):
                stopped_pcs.discard(str(data.get("id", "")))
            if action == "cancel" and rem.cancel(str(data.get("id", ""))):
                return {"cancelled": True}
            return await rem.act(str(data.get("id", "")), action, data)
        except Exception as e:
            return pc_error(e)

    @app.get("/config/voice/pc-log")
    async def voice_pc_log(id: str = ""):
        try:
            return {"log": await need_remote().engine_log(id)}
        except Exception as e:
            return pc_error(e)

    @app.get("/config/voice/pc-output")
    async def voice_pc_output(id: str = "", since: int = 0, limit: int = 200):
        try:
            return await need_remote().engine_output(id, since, limit)
        except Exception as e:
            if getattr(e, "status", None) == 404:
                return JSONResponse({"detail": "That device has an older MultiProxy Courier that can't show engine output. Update it (use the newest MultiProxy Courier download)."}, status_code=404)
            return pc_error(e)

    handles = VoiceHandles(engine, service, store, remote, routed, autostart)
    handles.license_ok = lambda: this_license_ok()
    return handles
