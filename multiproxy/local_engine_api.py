"""What the local-model settings screens talk to (2026-10-04): the logic behind the `/config/local-models/settings...` routes in multiproxy.py.

One object, `EngineSettingsApi`, so the routes stay thin and every rule is testable without a web server:

  * `state(device)`      the device's saved settings, its per-model overrides, the presets (and which one the settings are), the default for "memory to keep
                         free", the checks for the device and for each model on it, the memory estimate for its busiest model, and what was reset when the engine
                         build changed.
  * `save(device, body)` validate first (a value the engine would refuse, a refused or unavailable flag in the extra flags, or any `error` check, is refused
                         with the list of problems; warnings are accepted), then save. `apply` says when it takes effect: `save` (the next start),
                         `restart-model` (that one model now) or `restart-all` (every running model now).
  * `parse(text)`        sort a pasted command line (see local_engine_flags.parse_command_line).
  * `estimate(device, body)`  "about X of Y GB", with `confirm: true` also the engine's own dry run (runs in a thread, takes about a second; falls back to the estimate).
  * `failure(model_id)` / `start_without_extras(model_id)`   the "engine did not start" message in the engine's own words, and the one-time start without the
                         player's extra flags.

A device that is not this device (`device` other than `local`) has its settings saved here too; they are sent to it by the Courier link (`store.export(device)` is what
is sent, with its `hash`). Nothing here talks to another device. Its models and graphics card come from the callbacks the caller passes in.
"""
from __future__ import annotations

import asyncio
import socket
from typing import Awaitable, Callable, Optional

import gguf_header
import local_engine_checks as checks_mod
import local_engine_flags as flags
import local_engine_memory as memory
import local_engine_settings as eset

APPLY_MODES = ("save", "restart-model", "restart-all")


class ApiError(Exception):
    def __init__(self, status: int, message: str, checks: Optional[list] = None):
        super().__init__(message)
        self.status, self.message, self.checks = status, message, checks or []


class EngineSettingsApi:
    def __init__(self, local_svc, hardware_fn: Optional[Callable[[str], Awaitable[eset.HardwareFacts]]] = None,
                 models_fn: Optional[Callable[[str], list]] = None, online_fn: Optional[Callable[[str], Optional[bool]]] = None,
                 sender=None, resolve_fn: Optional[Callable[[str], str]] = None, name_fn: Optional[Callable[[str], str]] = None):
        self.local = local_svc
        self._hardware_fn = hardware_fn
        self._models_fn = models_fn
        self._online_fn = online_fn
        self._sender = sender              # local_engine_sync.SettingsSender: sends a remote device its settings and says whether it holds them
        self._resolve_fn = resolve_fn      # a device's nickname or permanent id -> its permanent id (the key its settings are kept under)
        self._name_fn = name_fn            # a device's key -> what to call it
        self.store = local_svc.engine_settings

    # ---- inputs ------------------------------------------------------------------------------
    async def hardware(self, device: str) -> eset.HardwareFacts:
        if self._hardware_fn and device != eset.DEVICE_LOCAL:
            return await self._hardware_fn(device)
        gpu = await self.local.gpu()
        if not gpu:
            return eset.HardwareFacts()
        try:
            import gpu_choice
            real = gpu_choice.choosable(await asyncio.to_thread(self.local._cards_fn))
        except Exception:
            real = []
        return eset.HardwareFacts(gpu.name, gpu.vendor, max(1, len(real)), gpu.memory_mb)

    def models(self, device: str) -> list:
        """The models on the device: the installed ones on this device, `[]` for another device unless the caller knows them. Each is an object with `id`, `name`,
        `path` and `mmproj` (like `local_models.Installed`)."""
        if self._models_fn:
            return list(self._models_fn(device))
        return list(self.local.installed()) if device == eset.DEVICE_LOCAL else []

    def _has_game(self, device: str) -> bool:
        return device == eset.DEVICE_LOCAL and bool(getattr(self.local, "HAS_GAME", True))

    def _gpu_engine(self, device: str) -> bool:
        if device != eset.DEVICE_LOCAL:
            return True
        return ((self.local._cfg().get("engine") or {}).get("kind", "")) != "cpu"

    def _key(self, device: str) -> str:
        """The key a device's settings are kept under: this device's `local`, or another device's permanent id (a nickname is turned into it)."""
        device = device or eset.DEVICE_LOCAL
        return self._resolve_fn(device) if (self._resolve_fn and device != eset.DEVICE_LOCAL) else device

    def _estimate(self, m, settings: dict, override: dict, card_mb: Optional[int]) -> memory.MemoryEstimate:
        return memory.estimate_memory(m.path, m.mmproj, settings, override, card_mb)       # (a model on another device has no file here: it says so, and gives no number)

    def _started(self, device: str, m) -> Optional[dict]:
        """What the model's last start really did (command, engine build, layers on the card when known), as the device itself reported it."""
        if device == eset.DEVICE_LOCAL:
            srv = self.local.servers.get(m.id)
            return srv.started if srv and srv.state == "running" else None
        return getattr(m, "started", None)

    def _model_checks(self, m, settings: dict, override: dict, hw: eset.HardwareFacts, gpu_engine: bool) -> tuple:
        facts = gguf_header.read_model_facts(m.path)
        est = self._estimate(m, settings, override, hw.memory_mb)
        found = checks_mod.run_checks(settings, override=override, facts=facts, hardware=hw, estimate=est, model_name=m.name, gpu_engine=gpu_engine)
        return found, est

    # ---- reading -----------------------------------------------------------------------------
    async def state(self, device: str = eset.DEVICE_LOCAL) -> dict:
        device = self._key(device)
        rec = self.store.get(device)
        hw = await self.hardware(device)
        has_game = self._has_game(device)
        peak = self.local.skyrim_peak_mb() if has_game else None
        presets = eset.presets_for(hw, has_game, peak)
        settings = dict(rec.settings, preset=eset.detect_preset(rec.settings, presets))
        fit_mb, fit_source = eset.resolve_fit_target_mb(settings, has_game=has_game, skyrim_peak_mb=peak, card_mb=hw.memory_mb)
        gpu_engine = self._gpu_engine(device)
        device_checks = checks_mod.run_checks(settings, hardware=hw, gpu_engine=gpu_engine)
        models, busiest = [], None
        for m in await asyncio.to_thread(self.models, device):
            override = rec.models.get(m.id, {})
            found, est = await asyncio.to_thread(self._model_checks, m, settings, override, hw, gpu_engine)
            ctx = int(override.get("ctx") or settings["ctx"])
            par = int(override.get("parallel") or settings["parallel"])
            models.append({"id": m.id, "name": m.name, "override": override, "overridden": sorted(override),
                           "effective": {"ctx": ctx, "parallel": par, "perRequest": ctx // max(1, par), "pages": round(ctx / flags.PAGE_TOKENS),
                                         "pagesPerRequest": round(ctx // max(1, par) / flags.PAGE_TOKENS)},
                           "checks": [c.to_json() for c in found], "estimate": est.to_json(), "started": self._started(device, m)})
            if est.total_mb is not None and (busiest is None or est.total_mb > busiest[1].total_mb):
                busiest = (m, est)
        return {
            "device": device, "deviceName": self._device_name(device), "build": rec.build or self.local.engine_build_label(), "saved": rec.saved, "hash": rec.hash,
            "sync": self._sync(device),
            "settings": settings, "defaults": eset.DEFAULT_SETTINGS, "hardware": hw.to_json(), "hasGame": has_game,
            "presets": presets,
            "fitTarget": {"mb": fit_mb, "source": fit_source, "ignored": settings["layers"] != "auto"},
            "skyrimPeakMb": peak,
            "pageTokens": flags.PAGE_TOKENS,
            "checks": [c.to_json() for c in device_checks],
            "models": models,
            "estimate": ({"model": busiest[0].name, **busiest[1].to_json()} if busiest else None),
            "reset": rec.reset,
            "promptPeaks": self.local.prompt_peaks.all(),
        }

    def _sync(self, device: str) -> dict:
        """Does the device hold the current settings? Only what the device itself reports counts (`sent`); an offline device is simply "waiting to send"."""
        if device == eset.DEVICE_LOCAL:
            return {"needed": False, "sent": False, "online": None}
        if self._sender is not None:
            return self._sender.status(device)
        return {"needed": True, "sent": False, "online": self._online_fn(device) if self._online_fn else None}

    def _device_name(self, device: str) -> str:
        """What the card calls the device: this device's own name, or the other device's nickname."""
        if device != eset.DEVICE_LOCAL:
            return (self._name_fn(device) if self._name_fn else "") or device
        try:
            return socket.gethostname() or "this device"
        except OSError:
            return "this device"

    def command_for(self, m, settings: dict, override: dict, device: str, hw: eset.HardwareFacts) -> str:
        """The command MultiProxy would run for model `m` with these settings, for the "The command MultiProxy will run" box: the file names without their folders and the
        port as "<port>" (it is picked when the model starts)."""
        import local_engine_flags as flags
        from pathlib import Path
        exe = self.local.engine_exe()
        fit_mb, _ = eset.resolve_fit_target_mb(settings, has_game=self._has_game(device), skyrim_peak_mb=self.local.skyrim_peak_mb(), card_mb=hw.memory_mb)
        plan = flags.build_chat_command("llama-server", Path(str(m.path)).name if m.path else f"{m.id}.gguf", Path(str(m.mmproj)).name if m.mmproj else None, "<port>", m.id, settings, override,
                                        gpu_engine=self._gpu_engine(device), fit_target_mb=fit_mb, spelling_known=self.local._spelling_known(exe) if exe else True)
        return " ".join(f'"{t}"' if " " in t else t for t in plan.cmd)

    def parse(self, text: str) -> dict:
        return flags.parse_command_line(text).to_json()

    # ---- writing -----------------------------------------------------------------------------
    def _merge(self, rec: "eset.DeviceRecord", body: dict) -> tuple:
        """-> (new settings, new per-model overrides: {id: dict or None}). Raises ApiError for a value that is not valid at all."""
        raw = body.get("settings")
        new = dict(rec.settings)
        if raw is not None:
            if not isinstance(raw, dict):
                raise ApiError(400, "The settings must be a list of values.")
            new, problems = eset.normalise_settings(raw, base=rec.settings)
            if problems:
                p = problems[0]
                raise ApiError(400, f"{p['setting']}: {p['reason']}.", [checks_mod.Check("error", "bad-value", f"{p['setting']}: {p['reason']}.", field=p["setting"]).to_json() for p in problems])
        # Flags in the extra flags that are really settings move to the settings (a pasted line); the rest stay as typed.
        parsed = flags.parse_command_line(new.get("extraArgs") or [])
        new.update(parsed.settings)
        new["extraArgs"] = parsed.extra
        problems = ([f"{d['flag']} {d['value']}".strip() + f": {d['reason']}" for d in parsed.refused] +
                    [f"{d['flag']}: {d['reason']} Use {d['replacement']} instead." if d.get("replacement") else f"{d['flag']}: {d['reason']}" for d in parsed.unavailable] +
                    [f"{d['flag']} {d['value']}".strip() + f": {d['reason']}" for d in parsed.invalid])
        if problems:
            raise ApiError(400, "Some of the flags you added can't be used: " + " ".join(problems),
                           [checks_mod.Check("error", "extra-flag-refused", t, field="extraArgs").to_json() for t in problems])
        models = body.get("models")
        overrides = None
        if models is not None:
            if not isinstance(models, dict):
                raise ApiError(400, "The per-model settings must be a list of models.")
            overrides = {str(k): (eset.normalise_override(v) or None) for k, v in models.items()}
        return new, overrides

    async def save(self, device: str, body: dict) -> dict:
        body = body if isinstance(body, dict) else {}
        device = self._key(device)
        apply_mode = str(body.get("apply") or "save")
        if apply_mode not in APPLY_MODES:
            raise ApiError(400, "That isn't a way to apply settings.")
        rec = self.store.get(device)
        new, overrides = self._merge(rec, body)
        merged_models = dict(rec.models)
        for k, v in (overrides or {}).items():
            if v:
                merged_models[k] = v
            else:
                merged_models.pop(k, None)
        hw = await self.hardware(device)
        gpu_engine = self._gpu_engine(device)
        presets = eset.presets_for(hw, self._has_game(device), self.local.skyrim_peak_mb() if self._has_game(device) else None)
        new["preset"] = eset.detect_preset(new, presets)
        found = checks_mod.run_checks(new, hardware=hw, gpu_engine=gpu_engine)
        for m in await asyncio.to_thread(self.models, device):
            f, _ = await asyncio.to_thread(self._model_checks, m, new, merged_models.get(m.id, {}), hw, gpu_engine)
            found += [c for c in f if all(c.text != x.text for x in found)]
        errors = [c for c in found if c.level == "error"]
        if errors:
            raise ApiError(400, errors[0].text, [c.to_json() for c in found])
        if device == eset.DEVICE_LOCAL:
            build = self.local.engine_build_label(self.local.engine_exe()) if self.local.engine_exe() else self.local.engine_build_label()
        else:
            import local_models
            build = local_models.LLAMA_CPP_BUILD            # another device's settings are checked against the engine MultiProxy pins, which is what that device runs
        rec = self.store.save(device, new, overrides, build)
        restarted = []
        pushed = False
        if device != eset.DEVICE_LOCAL and self._sender is not None:
            restart = [str(body["modelId"])] if apply_mode == "restart-model" and body.get("modelId") else ("all" if apply_mode == "restart-all" else None)
            res = await self._sender.push(device, restart)
            pushed = bool(res.get("sent"))
            restarted = ([restart[0]] if isinstance(restart, list) else ["all"]) if (pushed and restart) else []
        if device == eset.DEVICE_LOCAL:
            if apply_mode == "restart-model" and body.get("modelId"):
                await self.local.restart(str(body["modelId"]))
                restarted = [str(body["modelId"])]
            elif apply_mode == "restart-all":
                restarted = await self.local.restart_running()
        return {"saved": True, "device": device, "hash": rec.hash, "build": rec.build, "applied": "now" if restarted else "next start", "restarted": restarted,
                "pushed": pushed, "needsMemoryCheck": new["preset"] == "custom" or bool(new.get("extraArgs")),
                "checks": [c.to_json() for c in found]}

    async def reset(self, device: str, model_id: Optional[str] = None, to_suggested: bool = False) -> dict:
        """The device back to MultiProxy's defaults (or to the suggested preset), or one model back to following the device."""
        device = self._key(device)
        build = self.local.engine_build_label()
        if device != eset.DEVICE_LOCAL:
            import local_models
            build = local_models.LLAMA_CPP_BUILD
        if to_suggested and not model_id:
            hw = await self.hardware(device)
            sug = next((p for p in eset.presets_for(hw, self._has_game(device), self.local.skyrim_peak_mb() if self._has_game(device) else None)
                        if p["id"] == "suggested" and p["settings"]), None)
            self.store.reset_to_defaults(device, build)
            if sug:
                self.store.save(device, sug["settings"], None, build)
        else:
            self.store.reset_to_defaults(device, build, model_id)
        if device != eset.DEVICE_LOCAL and self._sender is not None:
            await self._sender.push(device)
        return {"reset": True, "device": device, "modelId": model_id}

    # ---- estimates and failures --------------------------------------------------------------
    async def estimate(self, device: str, body: dict) -> dict:
        body = body if isinstance(body, dict) else {}
        device = self._key(device)
        rec = self.store.get(device)
        new, overrides = self._merge(rec, body)
        models = await asyncio.to_thread(self.models, device)
        wanted = str(body.get("modelId") or "")
        hw = await self.hardware(device)
        merged = {**rec.models, **{k: v for k, v in (overrides or {}).items() if v}}
        presets = eset.presets_for(hw, self._has_game(device), self.local.skyrim_peak_mb() if self._has_game(device) else None)
        preset = eset.detect_preset(new, presets)
        if not models:          # nothing to work out the memory for: the device's own checks still apply
            found = checks_mod.run_checks(new, hardware=hw, gpu_engine=self._gpu_engine(device))
            return {"model": None, "modelId": None, "totalMb": None, "summary": "", "preset": preset, "command": "", "checks": [c.to_json() for c in found]}
        picks = [m for m in models if m.id == wanted] if wanted else models
        if not picks:
            raise ApiError(404, "That model isn't on this device.")
        best = None
        for m in picks:
            est = await asyncio.to_thread(self._estimate, m, new, merged.get(m.id, {}), hw.memory_mb)
            if best is None or (est.total_mb or 0) > (best[1].total_mb or 0):
                best = (m, est)
        m, est = best
        if body.get("confirm") and device == eset.DEVICE_LOCAL:          # the engine's own dry run needs the model file, which only this device has
            exe = self.local.engine_exe()
            fit_mb, _ = eset.resolve_fit_target_mb(new, has_game=self._has_game(device), skyrim_peak_mb=self.local.skyrim_peak_mb(), card_mb=hw.memory_mb)
            check = await asyncio.to_thread(memory.check_with_engine, exe, m.path, new, merged.get(m.id, {}), fit_mb) if exe else None
            est = memory.apply_engine_check(est, check, bool(m.mmproj))
        facts = gguf_header.read_model_facts(m.path)
        found = checks_mod.run_checks(new, override=merged.get(m.id, {}), facts=facts, hardware=hw, estimate=est, model_name=m.name, gpu_engine=self._gpu_engine(device))
        command = await asyncio.to_thread(self.command_for, m, new, merged.get(m.id, {}), device, hw)
        return {"model": m.name, "modelId": m.id, **est.to_json(), "preset": preset, "command": command, "checks": [c.to_json() for c in found]}

    def failure(self, model_id: str) -> dict:
        return self.local.failure_details(model_id)

    async def start_without_extras(self, model_id: str) -> dict:
        s = await self.local.start(model_id, skip_extras=True)
        return {"status": s.state, "message": s.message}
