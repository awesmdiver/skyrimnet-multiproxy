"""Higgs voice on a Courier device (2026-10-02): the Courier manages the voice engine on its own device, using the same engine manager the main device uses.

What the Courier device does here, all behind the pairing code and the home-network rule like everything else the Courier answers:

  * runs the engine manager (`voice_engine.VoiceEngine`: fit check, install, start, stop, restart, uninstall, status, log). The engine listens to
    THIS device only (a loopback port from 18950 to 18999); **no new port is opened**: the main device reaches it through the Courier's one door.
  * keeps the license record for the voice model and **refuses the model until the main device says the player accepted the license for this device**.
  * takes the voice files (engine build, model) **from the main device** ("download once, share at home"): the main device pushes them through the
    shared file transfer (`file_transfer.py`, kind `voice`); this module only decides where each file goes and refuses anything that isn't one of the
    pinned files (name, size and checksum all have to match what the engine manager knows). The player may instead choose "download it on that device".
  * relays a line: `speak` hands one line to the engine and streams the audio straight back, a piece at a time, never holding it whole in memory.

Nothing here knows about MultiProxy's web app; `courier.py` only wires the routes.
"""
import asyncio
import base64
import json
import time
from pathlib import Path
from typing import Callable, Optional

from aiohttp import web

import file_transfer
from local_models import LocalModelError
from voice_downloads import DownloadStore, FileSpec, Source
from voice_engine import (DEFAULT_VOICE_MODEL, LICENSE_VERSION, EngineBusy, EngineTokenLimit, VoiceEngine, VoiceEngineError, gb)

LICENSE_REQUIRED = "This device doesn't know you accepted the license yet. On your main device, press Next: Download again to send it."
STABLE_STATES = ("ready", "stopped", "not_installed", "died", "error", "too_slow")
STATUS_CACHE_SECONDS = 3.0           # the engine's status asks the graphics card for its memory use: not on every report


class NotCopiedYet(Source):
    """"Use what the main device sent": never fetches anything itself, so an install that is missing a file says so instead of going to the internet."""

    async def fetch(self, spec: FileSpec, dest: Path, on_bytes: Callable[[int], None]) -> None:
        raise LocalModelError(409, f"{spec.name} hasn't been copied to this device yet. Start the install from your main device.")


def _error_body(kind: str, message: str) -> dict:
    return {"error": {"type": kind, "message": message}}


def license_mismatch_text(sent: str, mine: str) -> str:
    """This Courier was sent a license version it does not know. A newer one than its own means this Courier is the older one (update it here); an older one than its
    own means the main device is (update MultiProxy there). Anything that cannot be compared points at this Courier: it is the one that said no."""
    try:
        if int(sent) < int(mine):
            return "MultiProxy on your main device needs to be updated."
    except (TypeError, ValueError):
        pass
    return "The Courier on this device needs an update."


class CourierVoice:
    def __init__(self, folder_fn: Callable[[], Path], downloads_fn: Callable[[], Path], log, redact: Callable[[str], str] = lambda s: s,
                 engine: Optional[VoiceEngine] = None, store: Optional[DownloadStore] = None, changed: Callable[[], None] = lambda: None,
                 gpu_choice_fn: Callable[[], Optional[dict]] = lambda: None, cards_fn: Optional[Callable[[], list]] = None):
        self.log, self.changed = log, changed
        self.store = store or (engine.store if engine else DownloadStore(downloads_fn))
        self.engine = engine or VoiceEngine(folder_fn, self.store, redact=redact, gpu_choice_fn=gpu_choice_fn, **({"cards_fn": cards_fn} if cards_fn else {}))
        self._cache: Optional[tuple] = None

    # ---- the license record (which device asked, when, which version) ------------------------------------------------------------------
    @property
    def license_file(self) -> Path:
        return self.engine.folder / "voice-license.json"

    def _license_data(self) -> dict:
        try:
            d = json.loads(self.license_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return d if isinstance(d, dict) else {}

    def license_record(self, model: Optional[str] = None) -> Optional[dict]:
        """The accepted-license record for a voice model (None: the chosen one). Higgs's record keeps its original place in the file (older Couriers wrote only that)."""
        key = model or self.engine.model_key
        d = self._license_data()
        rec = d if key == DEFAULT_VOICE_MODEL and d.get("accepted") else (d.get("models") or {}).get(key)
        return rec if isinstance(rec, dict) and rec.get("accepted") else None

    def license_ok(self, model: Optional[str] = None) -> bool:
        rec = self.license_record(model)
        return bool(rec) and rec.get("license_version") == self.engine.model_for(model).license_version

    def accept_license(self, gaming_pc: str, license_version: str, model: Optional[str] = None) -> dict:
        vm = self.engine.model_for(model)
        if license_version != vm.license_version:
            raise VoiceEngineError(409, license_mismatch_text(license_version, vm.license_version))
        rec = {"accepted": True, "accepted_at": int(time.time()), "license_version": license_version, "from": str(gaming_pc or "")[:60]}
        d = self._license_data()
        if vm.key == DEFAULT_VOICE_MODEL:
            d.update(rec)
        else:
            d.setdefault("models", {})[vm.key] = rec
        self.engine.folder.mkdir(parents=True, exist_ok=True)
        self.license_file.write_text(json.dumps(d, indent=1), encoding="utf-8")
        self.log.info(f"The {vm.name} license was accepted for this device from {rec['from'] or 'the main device'} (version {license_version})")
        self.invalidate()
        return rec

    # ---- which files, and where they go --------------------------------------------------------------------------------------------
    def _specs(self) -> dict:
        specs = {vm.file.name: vm.file for vm in self.engine.catalog.values()}
        specs.update({vm.companion.name: vm.companion for vm in self.engine.catalog.values() if vm.companion})
        specs[self.engine.model.name] = self.engine.model
        for b in self.engine.builds.values():
            for f in b.files:
                specs[f.name] = f
        return specs

    def resolve_voice_dest(self, spec: dict) -> Path:
        """Where a voice file sent from the main device goes: only the pinned files, only with the size and checksum the engine manager expects, and the
        model only after the license."""
        known = self._specs().get(str(spec.get("file", "")))
        if not known:
            raise file_transfer.TransferError(400, "MultiProxy doesn't send files with that name.")
        try:
            size = int(spec.get("size"))
        except (TypeError, ValueError):
            size = -1
        if size != known.size or str(spec.get("sha256", "")).lower() != known.sha256:
            raise file_transfer.TransferError(400, "That isn't the file this version of MultiProxy needs for voices.")
        for key, vm in self.engine.catalog.items():             # a voice model's file (and the model that comes with it) waits for the license of THAT model
            names = (self.engine.model.name if key == self.engine.model_key else vm.file.name, vm.companion.name if vm.companion else None)
            if known.name in names and not self.license_ok(key):
                raise file_transfer.TransferError(403, LICENSE_REQUIRED)
        return self.store.path_of(known.name)

    def on_voice_file_done(self, inc) -> None:
        known = self._specs().get(inc.file)
        if known:
            self.store.adopt(known)
        self.invalidate()

    def files_needed(self, model: Optional[str] = None) -> dict:
        """What installing needs here and what is already here: the build for this device (by its graphics card and driver), the model, and the room."""
        vm = self.engine.model_for(model)
        fit = self.engine.fit_check(vm.key)
        out = {"fit": fit.to_json(), "licenseOk": self.license_ok(vm.key), "licenseVersion": vm.license_version, "files": [],
               "freeDiskBytes": None, "installed": self.engine.installed() and vm.key == self.engine.model_key, "model": vm.key}
        if fit.build:
            hw = self.engine.hardware()
            out["freeDiskBytes"] = hw.free_disk_bytes
            for spec in list(self.engine.builds[fit.build].files) + [vm.file] + ([vm.companion] if vm.companion else []):
                out["files"].append({"name": spec.name, "size": spec.size, "sha256": spec.sha256, "have": self.store.has(spec),
                                     "role": "engine" if spec in self.engine.builds[fit.build].files else "model"})
        return out

    # ---- install and run (the engine manager's own actions) -----------------------------------------------------------------------
    def start_install(self, source: str = "kept", model: Optional[str] = None) -> dict:
        if source not in ("kept", "internet"):
            raise VoiceEngineError(400, "Unknown way to get the files.")
        if model:
            self.engine.model_for(model)                          # a plain 400 for a voice we don't know
            if model != self.engine.model_key:
                raise VoiceEngineError(409, "Choose that voice first.")      # (the Courier's route chooses it before it gets here)
        if not self.license_ok():
            raise VoiceEngineError(403, LICENSE_REQUIRED)
        from voice_downloads import InternetSource
        self.engine.start_install(NotCopiedYet() if source == "kept" else InternetSource())
        self.invalidate()
        return self.status_json(fresh=True)

    # ---- what the report says ------------------------------------------------------------------------------------------------------
    def invalidate(self) -> None:
        self._cache = None
        self.changed()

    def status_json(self, fresh: bool = False, receiving: Optional[dict] = None) -> dict:
        now = time.monotonic()
        busy = self.engine.job is not None and self.engine.job.state == "running"
        if not fresh and not busy and self._cache and now - self._cache[0] < STATUS_CACHE_SECONDS:
            base = self._cache[1]
        else:
            base = self.engine.status()
            if base.get("state") in STABLE_STATES:               # a state that is still changing (starting, installing...) is never remembered
                self._cache = (now, base)
        rec = self.license_record()
        return {**base, "licenseAccepted": self.license_ok(), "licenseVersion": self.engine.vmodel.license_version, "licenseRecord": rec,
                "licenses": {k: self.license_ok(k) for k in self.engine.catalog}, "supportedModels": list(self.engine.catalog), "receiving": receiving or {}}

    # ---- a line, relayed -------------------------------------------------------------------------------------------------------------
    async def speak(self, request: web.Request) -> web.StreamResponse:
        """POST /courier/v1/voice/speak: one line in, the engine's audio streamed straight back. Errors come back as JSON with a `type` the main device understands
        (busy, token_limit, not_running, error); the audio is only started once the engine has accepted the line, so a refusal is always a plain error."""
        try:
            body = await request.json()
        except ValueError:
            return web.json_response(_error_body("error", "That wasn't a voice request."), status=400)
        text = str((body or {}).get("text") or "")
        if not text.strip():
            return web.json_response(_error_body("error", "There was no text to speak."), status=400)
        ref = None
        if body.get("ref_b64"):
            try:
                ref = base64.b64decode(body["ref_b64"], validate=True)
            except Exception:
                return web.json_response(_error_body("error", "The voice sample couldn't be read."), status=400)
        t0 = time.time()
        try:
            async with self.engine.open_speech(text, ref, temperature=body.get("temperature"), seed=body.get("seed"), top_p=body.get("top_p"),
                                               busy_timeout_ms=body.get("busy_timeout_ms"), timeout=float(body.get("timeout") or 60),
                                               voice=str(body["voice"]) if body.get("voice") else None) as r:
                headers = {"Content-Type": "audio/wav"}
                for k in ("X-AudioCPP-Wall-Ms", "X-AudioCPP-Audio-Duration-Ms", "X-AudioCPP-RTF"):
                    if r.headers.get(k) is not None:
                        headers[k] = r.headers[k]
                resp = web.StreamResponse(status=200, headers=headers)
                await resp.prepare(request)
                async for chunk in r.content.iter_chunked(64 * 1024):
                    await resp.write(chunk)
                await resp.write_eof()

                def num(k):
                    try:
                        return float(r.headers.get(k))
                    except (TypeError, ValueError):
                        return None
                self.engine.note_line_done(num("X-AudioCPP-Wall-Ms") or (time.time() - t0) * 1000, num("X-AudioCPP-RTF"))
                return resp
        except EngineBusy as e:
            return web.json_response(_error_body("busy", e.message), status=503)
        except EngineTokenLimit as e:
            return web.json_response(_error_body("token_limit", e.message), status=502)
        except VoiceEngineError as e:
            kind = "not_running" if e.status == 503 and "isn't running" in e.message else "error"
            return web.json_response(_error_body(kind, e.message), status=e.status)

    # ---- the player's own voice clips on this device (voice_clips.py): the main device lists, adds, removes and plays them through these ------------------------
    def clips_listing(self) -> dict:
        return {"clips": self.engine.clips_rows()}

    def clips_audio(self, voice: str, which: str) -> Optional[bytes]:
        return self.engine.clips.audio(voice, which)

    def clips_add(self, voice: str, kind: str, data_b64: str, words: Optional[str] = None) -> dict:
        """`kind` "yours": the player's clip for that voice (the old one, if any, is moved aside). `kind` "skyrimnet": the clip SkyrimNet first sent for it
        (kept only when this device has none yet), sent along when Higgs moves here."""
        try:
            data = base64.b64decode(data_b64 or "", validate=True)
        except Exception:
            raise VoiceEngineError(400, "That clip couldn't be read.")
        if kind == "skyrimnet":
            self.engine.clips.keep_skyrimnet(voice, data)
            return {"voice": voice, "kept": True}
        return self.engine.clips.add(voice, data, words)

    def clips_remove(self, voice: str) -> dict:
        return {"removed": self.engine.clips.remove(voice)}

    def clips_words(self, voice: str, words: str) -> dict:
        return {"clip": self.engine.clips.set_words_row(voice, words)}

    async def close(self) -> None:
        await self.engine.close()
