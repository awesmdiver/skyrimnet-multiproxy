"""Higgs voice on another device, seen from the main device (2026-10-02): the relay, "where Higgs runs", and installing on a Courier device.

  * `RemoteVoiceEngine` looks like the local engine manager to `voice_service.VoiceService`, but a line goes to the Courier device through the existing
    paired connection (pairing code, home network only) and the audio comes back streamed straight to a file, a piece at a time, never whole in
    memory. The engine on that device listens to that device only; **no new port is opened there**. If that device is asleep or unreachable the answer is
    immediate and says which device and why.
  * `RoutedEngine` is the "where Higgs runs" setting: This device or one named Courier device. Changing it changes where lines go; SkyrimNet never knows.
  * `VoiceRemote` installs and manages Higgs on a Courier device from here: the license step (recorded on both devices), getting the files **from this device** by
    default (downloaded here once, kept, then copied over the shared file transfer: resumable, checksum-verified, real progress) or "download it on that
    Device", disk checks on both ends, and start / stop / restart / uninstall / log.
"""
import asyncio
import base64
import json
import secrets
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import aiohttp

import download_meter
import file_transfer
import other_pcs
from voice_downloads import DownloadStore, InternetSource
from local_models import LocalModelError
from voice_engine import DEFAULT_VOICE_MODEL, LICENSE_VERSION, VOICE_MODELS, SpeechResult, VoiceEngineError, gb

# ---------------------------------------------------------------------------------------------------------------------------------
# The relay
# ---------------------------------------------------------------------------------------------------------------------------------
class RemoteVoiceEngine:
    """The engine that is on another device. Same face as `voice_engine.VoiceEngine` for what the voice service uses: `status()`, `synthesize()`, `waiting_fn`."""

    def __init__(self, remotes, pc_fn: Callable[[], str], work_dir_fn: Callable[[], Path], log=lambda m: None):
        self.remotes, self._pc_fn, self._work_dir_fn, self.log = remotes, pc_fn, work_dir_fn, log
        self.waiting_fn: Callable[[], int] = lambda: 0
        self.last_relay_ms: Optional[float] = None          # what the relay added on top of the engine's own time, for the last line

    @property
    def pc(self) -> str:
        return self._pc_fn()

    def _remote(self):
        try:
            return self.remotes._courier(self.pc)
        except Exception:
            return None

    def _voice(self) -> dict:
        r = self._remote()
        return dict((r.report or {}).get("voice") or {}) if r else {}

    def _reachable(self) -> bool:
        r = self._remote()
        return bool(r and r.client is not None and r.online is not False)

    def not_ready_message(self) -> str:
        """Why a line can't go there, in plain words that name the device."""
        pc, r, v = self.pc, self._remote(), self._voice()
        name = str(v.get("modelName") or "Higgs voice")
        if r is None:
            return f"{name} is set to run on {pc}, but {pc} isn't in your list of devices anymore. Choose where it runs again."
        if not self._reachable():
            return f"{name} runs on {pc}, which isn't answering. It may be asleep or turned off."
        if not v.get("installed"):
            return f"{name} isn't installed on {pc}."
        return f"{name} isn't running on {pc}. Start it in MultiProxy."

    def status(self) -> dict:
        v, pc = self._voice(), self.pc
        reachable = self._reachable()
        if not reachable:
            return {"state": "unreachable", "label": "Not ready", "detail": self.not_ready_message(), "ready": False, "installed": bool(v.get("installed")),
                    "pc": pc, "linesWaiting": self.waiting_fn(), "relayMs": self.last_relay_ms}
        s = {**v, "pc": pc, "relayMs": self.last_relay_ms}
        waiting = self.waiting_fn()
        s["linesWaiting"] = waiting
        if s.get("state") == "ready" and waiting:
            s["label"] = f"Busy ({waiting} line{'s' if waiting != 1 else ''} waiting)"
        return s

    async def synthesize(self, text: str, ref_wav: Optional[bytes] = None, *, temperature: Optional[float] = None, seed: Optional[int] = None,
                         top_p: Optional[float] = None, busy_timeout_ms: Optional[int] = None, timeout: float = 60, voice: Optional[str] = None) -> SpeechResult:
        if not self.status().get("ready"):
            raise VoiceEngineError(503, self.not_ready_message())
        r = self._remote()
        folder = Path(self._work_dir_fn()) / "relay"
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / f"relay-{secrets.token_hex(6)}.wav"
        body = {"text": text, "temperature": temperature, "top_p": top_p, "seed": seed, "busy_timeout_ms": busy_timeout_ms, "timeout": timeout, "voice": voice}
        if ref_wav is not None:
            body["ref_b64"] = base64.b64encode(ref_wav).decode("ascii")
        t0 = time.time()
        try:
            headers = await r.client.voice_speak(body, dest, timeout=timeout + 5)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
            dest.unlink(missing_ok=True)
            self.log(f"The voice relay to {self.pc} failed: {e.__class__.__name__}")
            raise VoiceEngineError(503, f"{self.pc} stopped answering. Make sure it is turned on.")
        except (Exception, asyncio.CancelledError):          # (a line SkyrimNet hung up on stops here too: no half-received clip is left behind)
            dest.unlink(missing_ok=True)
            raise

        def num(k):
            try:
                return float(headers.get(k))
            except (TypeError, ValueError):
                return None
        wall = (time.time() - t0) * 1000
        engine_ms = num("X-AudioCPP-Wall-Ms")
        self.last_relay_ms = max(0.0, wall - engine_ms) if engine_ms is not None else None
        return SpeechResult(wav=b"", path=dest, wall_ms=engine_ms or wall, audio_ms=num("X-AudioCPP-Audio-Duration-Ms"), rtf=num("X-AudioCPP-RTF"))


class RoutedEngine:
    """"Where Higgs runs": This device (`location` is "this") or the Courier device with that nickname. Everything the voice service asks goes to the one chosen."""

    def __init__(self, local, remote: RemoteVoiceEngine, location_fn: Callable[[], str]):
        self.local, self.remote, self._location_fn = local, remote, location_fn
        self._waiting_fn: Callable[[], int] = lambda: 0

    @property
    def location(self) -> str:
        return self._location_fn() or "this"

    def _active(self):
        return self.local if self.location == "this" else self.remote

    @property
    def waiting_fn(self):
        return self._waiting_fn

    @waiting_fn.setter
    def waiting_fn(self, fn) -> None:
        self._waiting_fn = fn
        self.local.waiting_fn = fn
        self.remote.waiting_fn = fn

    def status(self) -> dict:
        s = dict(self._active().status())
        s["location"] = self.location
        s.setdefault("pc", None)
        return s

    def not_ready_message(self) -> Optional[str]:
        return self.remote.not_ready_message() if self.location != "this" else None

    async def synthesize(self, *args, **kwargs):
        return await self._active().synthesize(*args, **kwargs)


# ---------------------------------------------------------------------------------------------------------------------------------
# Installing and managing Higgs on a Courier device
# ---------------------------------------------------------------------------------------------------------------------------------
@dataclass
class RemoteInstall:
    pc: str
    source: str                      # "gaming-pc" | "that-pc"
    model: str = DEFAULT_VOICE_MODEL     # which voice model this install is for
    state: str = "running"           # running | done | error | cancelled
    stage: str = "preparing"         # preparing (downloading here) | copying | installing | done
    message: str = ""
    done: int = 0
    total: int = 0
    speed: float = 0.0
    error: str = ""
    bad_file: str = ""               # the file that failed its checksum (the Voice tab then offers "Download it again")
    reason: str = ""                 # why that device's install failed, when it said (one plain clause)
    cancel: bool = False
    trail: list = field(default_factory=list)       # every (stage, message) it went through, in order (the screen shows the current one; tests read them all)
    task: Optional[asyncio.Task] = field(default=None, repr=False)
    meter: Any = field(default_factory=download_meter.Meter, repr=False)      # fed with the running total each time the screen asks: the time left comes from it

    def note(self, stage: str, message: str) -> None:
        self.stage, self.message = stage, message
        self.trail.append((stage, message))

    def to_json(self) -> dict:
        self.meter.observe(self.done)
        return {"pc": self.pc, "source": self.source, "model": self.model, "state": self.state, "stage": self.stage, "message": self.message, "done": self.done, "total": self.total,
                "percent": int(self.done * 100 / self.total) if self.total else 0, "speed_bps": int(self.speed), "error": self.error, "badFile": self.bad_file, "reason": self.reason,
                "left": self.meter.text(self.done, self.total) if self.state == "running" and self.stage != "installing" else ""}


class VoiceRemote:
    def __init__(self, remotes, engine, store: DownloadStore, voice_folder_fn: Callable[[], Path], this_pc_fn: Callable[[], str], log,
                 source_factory: Optional[Callable[[], object]] = None):
        self.remotes, self.engine, self.store, self._folder_fn, self._this_pc_fn, self.log = remotes, engine, store, voice_folder_fn, this_pc_fn, log
        self._source_factory = source_factory or InternetSource          # where files come from when this device doesn't have them yet
        self.jobs: dict[str, RemoteInstall] = {}

    # ---- the license record on this side (the Courier keeps its own) ------------------------------------------------------------------
    @property
    def license_file(self) -> Path:
        return Path(self._folder_fn()) / "voice-license.json"

    def _license_data(self) -> dict:
        try:
            d = json.loads(self.license_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return d if isinstance(d, dict) else {}

    def _licenses(self, model: str = DEFAULT_VOICE_MODEL) -> dict:
        """pc -> record, for one voice model. Higgs keeps its original place in the file (`pcs`); the others sit under `models`."""
        d = self._license_data()
        if model == DEFAULT_VOICE_MODEL:
            return d.get("pcs", {}) if isinstance(d.get("pcs"), dict) else {}
        m = (d.get("models") or {}).get(model)
        return m if isinstance(m, dict) else {}

    def _version(self, model: str) -> str:
        vm = VOICE_MODELS.get(model)
        if vm is None:
            raise VoiceEngineError(400, "MultiProxy doesn't recognize that voice.")
        return LICENSE_VERSION if model == DEFAULT_VOICE_MODEL else vm.license_version

    def license_ok(self, pc: str, model: str = DEFAULT_VOICE_MODEL) -> bool:
        rec = self._licenses(model).get(pc.lower())
        return bool(rec) and rec.get("license_version") == self._version(model)

    def license_record(self, pc: str, model: str = DEFAULT_VOICE_MODEL) -> Optional[dict]:
        return self._licenses(model).get(pc.lower())

    def supports(self, pc: str, model: str) -> bool:
        """Can that device's Courier run this voice? (Its report says which voices it knows; one that says nothing runs Higgs only.)"""
        r = self.remotes._courier(pc)
        if not r.report:
            return True                                       # nothing known about it yet: ask it, and let its own answer say
        return model in other_pcs.courier_voice_models(r.report)

    def needs_newer_text(self, pc: str, model: str) -> str:
        r = self.remotes._courier(pc)
        return f"Courier on {r.label} needs an update."

    async def accept_license(self, pc: str, model: str = DEFAULT_VOICE_MODEL) -> dict:
        """The player accepted a voice model's license for this device: tell it, and keep the record here too (which device, when, which version)."""
        version = self._version(model)
        r = self.remotes._courier(pc)
        if not self.supports(pc, model):
            raise VoiceEngineError(409, self.needs_newer_text(pc, model))
        try:
            await r.client.voice_post("license", {"licenseVersion": version, "from": self._this_pc_fn(), "model": model})
        except Exception as e:
            if "license version" in str(getattr(e, "message", e)):                  # a Courier too old to be asked about its voices: it said so itself
                raise VoiceEngineError(409, self.needs_newer_text(pc, model))
            raise
        d = self._license_data()
        rec = {"pc": r.id, "accepted_at": int(time.time()), "license_version": version}
        if model == DEFAULT_VOICE_MODEL:
            d.setdefault("pcs", {})[r.id.lower()] = rec
        else:
            d.setdefault("models", {}).setdefault(model, {})[r.id.lower()] = rec
        self.license_file.parent.mkdir(parents=True, exist_ok=True)
        self.license_file.write_text(json.dumps(d, indent=1), encoding="utf-8")
        await self._refresh(r)
        return rec

    @staticmethod
    def _device_says_accepted(rep: Optional[dict], model: str) -> bool:
        """Does that device's own report say it holds this voice's license? (A Courier from before the voices list said only Higgs's.)"""
        v = (rep or {}).get("voice") or {}
        if isinstance(v.get("licenses"), dict):
            return bool(v["licenses"].get(model))
        return bool(v.get("licenseAccepted")) if model == DEFAULT_VOICE_MODEL else False

    async def ensure_license(self, pc: str, model: str = DEFAULT_VOICE_MODEL) -> bool:
        """The player accepted this voice's license on this device: make sure that device really holds it. Our own note that we told it is not enough (the device may have
        lost its record), so ask the device itself, and tell it again when it doesn't have it. Returns True when it had to be told."""
        r = self.remotes._courier(pc)
        await self._refresh(r)
        if self.license_ok(r.id, model) and self._device_says_accepted(r.report, model):
            return False
        await self.accept_license(pc, model)
        return True

    # ---- what is known about that device ------------------------------------------------------------------------------------------------
    async def _refresh(self, r) -> None:
        try:
            self.remotes._apply_report(r, await r.client.fetch_report())
        except Exception:
            pass

    async def describe(self, pc: str, model: str = DEFAULT_VOICE_MODEL) -> dict:
        r = self.remotes._courier(pc)
        out = {"pc": r.id, "online": r.online is not False, "license": self.license_record(r.id, model), "licenseOk": self.license_ok(r.id, model),
               "licenseVersion": self._version(model), "model": model, "job": self.jobs[r.id].to_json() if r.id in self.jobs else None,
               "voice": (r.report or {}).get("voice"), "keptBytes": self.store.listing()["totalBytes"]}
        if r.online is not False:
            try:
                out["fit"] = await r.client.voice_get(f"fit?model={model}")
            except Exception as e:
                out["fit"] = None
                out["fitError"] = f"{r.label} didn't answer ({getattr(e, 'message', e.__class__.__name__)})."
        return out

    # ---- install ----------------------------------------------------------------------------------------------------------------------
    def start_install(self, pc: str, source: str = "gaming-pc", model: str = DEFAULT_VOICE_MODEL) -> RemoteInstall:
        r = self.remotes._courier(pc)
        if source not in ("gaming-pc", "that-pc"):
            raise VoiceEngineError(400, "Unknown way to get the files.")
        self._version(model)
        if not self.supports(pc, model):
            raise VoiceEngineError(409, self.needs_newer_text(pc, model))
        live = self.jobs.get(r.id)
        if live and live.state == "running":
            return live
        if not self.license_ok(r.id, model):
            raise VoiceEngineError(403, f"Accept the {VOICE_MODELS[model].name} license for {r.label} first.")
        job = RemoteInstall(r.id, source, model)
        self.jobs[r.id] = job
        job.task = asyncio.ensure_future(self._run(r, job))
        return job

    def cancel(self, pc: str) -> bool:
        job = self.jobs.get(self.remotes._courier(pc).id)
        if job and job.state == "running":
            job.cancel = True
            return True
        return False

    def _gaming_free(self) -> Optional[int]:
        p = self.store.folder
        while not p.exists() and p.parent != p:
            p = p.parent
        try:
            return shutil.disk_usage(p).free
        except OSError:
            return None

    async def _run(self, r, job: RemoteInstall) -> None:
        try:
            try:
                info = await r.client.voice_get(f"fit?model={job.model}")
            except Exception:
                raise VoiceEngineError(502, f"{r.label} can't be reached right now. Turn it on, then press Next: Download to carry on.")
            fit = info["fit"]
            if fit["verdict"] == "refuse":
                raise VoiceEngineError(400, " ".join(fit["reasons"]).replace("this device", r.id))
            if job.source == "gaming-pc":
                await self._get_files_over(r, job, info)
            else:
                job.note("installing", f"{r.label} is downloading the voice files itself…")
            await self._install_there(r, job, "kept" if job.source == "gaming-pc" else "internet")
            job.state, job.stage, job.message = "done", "done", ""
            self.log(f"{VOICE_MODELS[job.model].name} is installed on {r.label}")
        except asyncio.CancelledError:
            job.state = "cancelled"
            raise
        except _Cancelled:
            job.state, job.message = "cancelled", ""
            try:
                await r.client.voice_post("cancel")
            except Exception:
                pass
        except file_transfer.TransferError as e:
            job.state, job.error = "error", e.message.replace("{pc}", r.id)
            if e.status == 422 and "safety check" in e.message:
                job.bad_file = e.message.split(" didn't pass")[0]
        except LocalModelError as e:
            job.state, job.error = "error", e.message
            if "safety check" in e.message:
                job.bad_file = e.message.split(" didn't pass")[0]
        except VoiceEngineError as e:
            job.state, job.error = "error", e.message
        except Exception as e:
            job.state, job.error = "error", f"Setting up the voice on {r.label} stopped ({getattr(e, 'message', e.__class__.__name__)})."
            self.log(f"{VOICE_MODELS[job.model].name} install on {r.label} failed: {e.__class__.__name__}: {e}")
        if job.state == "error":
            self.log(f"{VOICE_MODELS[job.model].name} install on {r.label} failed: {job.error}")
        await self._refresh(r)

    async def _get_files_over(self, r, job: RemoteInstall, info: dict) -> None:
        """The files this device needs: fetched here first when they aren't kept yet (once), then copied to that device."""
        specs = {f.name: f for b in self.engine.builds.values() for f in b.files}
        for k, vm in self.engine.catalog.items():
            specs[vm.file.name] = self.engine.model if k == self.engine.model_key else vm.file
            if vm.companion:
                specs[vm.companion.name] = vm.companion
        model_names = {self.engine.catalog[job.model].file.name} | ({self.engine.catalog[job.model].companion.name} if self.engine.catalog[job.model].companion else set())
        wanted = []
        for f in info["files"]:
            spec = specs.get(f["name"])
            if spec is None or spec.size != f["size"] or spec.sha256 != f["sha256"]:
                raise VoiceEngineError(409, f"{r.label} has a different version of MultiProxy Courier. Update it (use the newest MultiProxy Courier download) and try again.")
            if not f["have"]:
                wanted.append(spec)
        if not wanted:
            return
        need_there = sum(s.size for s in wanted)
        free_there = info.get("freeDiskBytes")
        if free_there is not None and free_there < need_there + file_transfer.SPACE_MARGIN_BYTES:
            raise VoiceEngineError(507, f"{r.label} doesn't have room for this. It needs {gb(need_there)} more, but only has {gb(free_there)} free.")
        need_here = self.store.missing_bytes(wanted)
        free_here = self._gaming_free()
        if need_here and free_here is not None and free_here < need_here + file_transfer.SPACE_MARGIN_BYTES:
            raise VoiceEngineError(507, f"This device doesn't have room for the voice files. They need {gb(need_here)}, but only {gb(free_here)} is free. "
                                        f"You can let {r.label} download them itself instead.")
        # stage 1: fetch here, once (nothing to do when they are already kept)
        if need_here:
            job.total, job.done = need_here, 0
            job.note("preparing", "Downloading the voice files on this device (once)…")
            base = 0
            for spec in wanted:
                if self.store.has(spec):
                    continue
                size_before = base

                def on_bytes(n, size_before=size_before):
                    if job.cancel:
                        raise _Cancelled()
                    job.done = max(0, job.done + n)
                await self.store.get(spec, self._source_factory(), on_bytes)
        # stage 2: copy to that device
        job.done, job.total = 0, need_there
        base = 0
        for spec in wanted:
            role_word = "model" if spec.name in model_names else "engine"
            job.note("copying", f"Copying the voice {role_word} from this device to {r.label}…")
            tspec = {"kind": "voice", "model": "higgs-voice", "file": spec.name, "sha256": spec.sha256, "size": spec.size}

            def progress(done, total, speed, base=base):
                job.done, job.speed = base + done, speed

            def paused(msg):
                job.message = f"Waiting for {r.label} to answer. It will finish on its own."
            await file_transfer.send_file(r.client, tspec, self.store.path_of(spec.name), progress, lambda: job.cancel, on_pause=paused)
            base += spec.size
        job.done = job.total

    async def _install_there(self, r, job: RemoteInstall, source: str) -> None:
        job.note("installing", f"Setting up {VOICE_MODELS[job.model].name} on {r.label}…")
        job.done, job.total, job.speed = 0, 0, 0.0
        await r.client.voice_post("install", {"source": source, "model": job.model})
        while True:
            if job.cancel:
                raise _Cancelled()
            st = await r.client.voice_get("status")
            inst = st.get("install") or {}
            job.done, job.total = int(inst.get("done") or 0), int(inst.get("total") or 0)
            if inst.get("state") == "done" or (inst.get("state") is None and st.get("ready")):
                break
            if inst.get("state") in ("error", "cancelled"):
                job.reason = str(inst.get("reason") or "")
                raise VoiceEngineError(502, inst.get("error") or f"{VOICE_MODELS[job.model].name} didn't install on {r.label}.")
            await asyncio.sleep(1.0)

    # ---- the rest of the engine's actions, on that device -------------------------------------------------------------------------------------
    async def act(self, pc: str, action: str, body: Optional[dict] = None) -> dict:
        if action not in ("start", "stop", "restart", "uninstall", "cancel", "select"):
            raise VoiceEngineError(400, "Unknown action.")
        r = self.remotes._courier(pc)
        try:
            out = await r.client.voice_post(action, body or {}, timeout=300 if action in ("start", "restart") else 60)
        except Exception as e:
            raise VoiceEngineError(getattr(e, "status", 502), getattr(e, "message", f"{r.label} didn't answer."))
        await self._refresh(r)
        return out

    async def engine_output(self, pc: str, since: int = 0, limit: int = 200) -> dict:
        r = self.remotes._courier(pc)
        return await r.client.voice_get(f"output?since={int(since)}&limit={int(limit)}")

    async def engine_log(self, pc: str) -> str:
        r = self.remotes._courier(pc)
        return (await r.client.voice_get("log")).get("log", "")

    # ---- the player's own voice clips: they live on the device that runs Higgs (voice_clips.py); here is how this device reaches those on another device -----------
    async def clips_list(self, pc: str) -> list:
        r = self.remotes._courier(pc)
        return (await self._clip_call(r, r.client.voice_get("clips"))).get("clips", [])

    async def clips_audio(self, pc: str, voice: str, which: str = "") -> Optional[bytes]:
        from urllib.parse import quote
        r = self.remotes._courier(pc)
        try:
            d = await r.client.voice_get(f"clips/audio?voice={quote(voice)}&which={quote(which)}")
        except Exception as e:
            if getattr(e, "status", None) == 404:
                return None
            raise VoiceEngineError(getattr(e, "status", 502), getattr(e, "message", f"{r.label} didn't answer."))
        return base64.b64decode(d.get("wavB64") or "") or None

    async def clips_add(self, pc: str, voice: str, data: bytes, kind: str = "yours", words: Optional[str] = None) -> dict:
        r = self.remotes._courier(pc)
        return await self._clip_call(r, r.client.voice_post("clips/add", {"voice": voice, "kind": kind, "dataB64": base64.b64encode(data).decode("ascii"), **({"words": words} if words is not None else {})}, timeout=120))

    async def clips_words(self, pc: str, voice: str, words: str) -> Optional[dict]:
        r = self.remotes._courier(pc)
        return (await self._clip_call(r, r.client.voice_post("clips/words", {"voice": voice, "words": words}))).get("clip")

    async def clips_remove(self, pc: str, voice: str) -> dict:
        r = self.remotes._courier(pc)
        return await self._clip_call(r, r.client.voice_post("clips/remove", {"voice": voice}))

    @staticmethod
    async def _clip_call(r, coro) -> dict:
        try:
            return await coro
        except VoiceEngineError:
            raise
        except Exception as e:
            raise VoiceEngineError(getattr(e, "status", 502), getattr(e, "message", f"{r.label} didn't answer."))

    async def carry_clips(self, source: str, target: str) -> int:
        """Moving Higgs from `source` to `target` ("this" or a device's name): copy the player's clips and the kept SkyrimNet clips across, never replacing one
        the target already has and never removing anything from the source. Returns how many clips were copied. Raises when the target can't be reached."""
        if source == target:
            return 0

        async def listing(pc):
            return self.engine.clips.listing() if pc == "this" else await self.clips_list(pc)

        async def audio(pc, voice, which):
            return self.engine.clips.audio(voice, which) if pc == "this" else await self.clips_audio(pc, voice, which)

        have = {row["voice"]: row for row in await listing(target)}
        copied = 0
        for row in await listing(source):
            mine = have.get(row["voice"], {})
            for kind, which, present in (("yours", "yours", row["source"] == "yours" and row["supported"]),
                                         ("skyrimnet", "skyrimnet", row["hasSkyrimNet"])):
                if not present:
                    continue
                if kind == "yours" and mine.get("source") == "yours":
                    continue
                if kind == "skyrimnet" and mine.get("hasSkyrimNet"):
                    continue
                data = await audio(source, row["voice"], which)
                if not data:
                    continue
                if target == "this":
                    if kind == "yours":
                        self.engine.clips.add(row["voice"], data)
                    else:
                        self.engine.clips.keep_skyrimnet(row["voice"], data)
                else:
                    await self.clips_add(target, row["voice"], data, kind)
                copied += 1
        return copied


class _Cancelled(Exception):
    pass
