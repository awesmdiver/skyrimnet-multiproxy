"""Benchmarks: the production wiring of the runner's hooks (what a real PC says about its models, services and graphics card).

Split out of benchmarks_wiring.py so the Courier can use it without the dashboard routes (the Courier package has no web page). benchmarks_wiring re-exports every name here.
"""
import asyncio
import os
from typing import Callable, Optional

import benchmarks_background as bgm
import benchmarks_guard as bguard
import benchmarks_runner as br
import benchmarks_sampler as bsm

_driver_cache: dict = {}


def _driver_version() -> Optional[str]:
    """The graphics driver version (NVIDIA: one read-only `nvidia-smi` call, remembered). None when it cannot be read."""
    if "v" not in _driver_cache:
        import benchmarks_sampler as bsm
        status, text = bsm.run_command(["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader,nounits"], 5.0)
        _driver_cache["v"] = text.splitlines()[0].strip() if status == "ok" and text.strip() else None
    return _driver_cache["v"]


VOICE_ID, DECISION_ID = "service:voice", "service:decision"       # ids of the two services that are not llama.cpp chat models (chat models keep their own id)
SERVICE_READY_WAIT_S = 180.0


def _engine_entry(service_id: str, kind: str, name: str, status: dict, card_index: Optional[int], started: bool = True) -> dict:
    """One background entry from a voice or decision engine's own `status()`: loaded while it is starting or running; the figure is what it reports it holds on the card."""
    loaded = status.get("state") in ("ready", "getting_ready", "too_slow", "starting")
    mb = status.get("gpuMemoryMb")
    return {"id": service_id, "kind": kind, "name": name, "state": bgm.STATE_LOADED if loaded else bgm.STATE_ASSIGNED_STOPPED,
            "vram_mb": {str(card_index): mb} if (loaded and mb is not None and card_index is not None) else None, "size_mb": None, "started_by_multiproxy": started}


def production_hooks(local_svc, load_cfg, skyrim_state, outside_client: dict, version: str, voice_engine=None, decision_engine=None, cards_fn: Optional[Callable[[], list]] = None) -> br.Hooks:
    import local_models
    import skyrim_peak

    def held() -> list:
        return [{"id": mid, "name": getattr(local_svc.find(mid), "name", mid) if hasattr(local_svc, "find") else mid}
                for mid, s in list(local_svc.servers.items()) if s.state in ("running", "starting")]

    async def unload(ids: list) -> None:
        for mid in ids:
            if mid == VOICE_ID and voice_engine is not None:
                await voice_engine.stop()
            elif mid == DECISION_ID and decision_engine is not None:
                await decision_engine.stop()
            else:
                await local_svc.stop(mid)

    def card_index(name: Optional[str]) -> Optional[int]:
        """Which NVIDIA card (in the driver's own order, as the sampler numbers them) an engine reports using; the only card when there is one; None when it cannot be told."""
        try:
            cards = [c for c in (cards_fn() if cards_fn else []) if getattr(c, "vendor", "") == "nvidia"]
        except Exception:
            cards = []
        if name:
            for i, c in enumerate(cards):
                if getattr(c, "name", None) == name:
                    return i
        return 0 if len(cards) == 1 else None

    def entries() -> list:
        """Everything MultiProxy can start on this PC, loaded or not: its chat models, the voice engine and the local decision model."""
        out = []
        for m in local_svc.installed():
            s = local_svc.servers.get(m.id)
            up = bool(s and s.state in ("running", "starting"))
            out.append({"id": m.id, "kind": "chat", "name": m.name, "state": bgm.STATE_LOADED if up else bgm.STATE_ASSIGNED_STOPPED, "vram_mb": None,
                        "size_mb": round(m.size / (1024 * 1024)), "started_by_multiproxy": up})
        if voice_engine is not None and voice_engine.installed():
            st = voice_engine.status()
            out.append(_engine_entry(VOICE_ID, "voice", st.get("modelName") or "Voice", st, card_index(st.get("gpuCard"))))
        if decision_engine is not None and decision_engine.installed():
            st = decision_engine.status()
            out.append(_engine_entry(DECISION_ID, "decision", "Quick-decision model", st, card_index(st.get("gpuCard"))))
        return out

    def loaded() -> list:
        return [e for e in entries() if e["state"] == bgm.STATE_LOADED]

    def assigned() -> list:
        """Stopped services this PC normally runs: the voice engine and the decision model when installed. Chat models are not listed here: which one the game uses is Routing's call, and
        a chat model that is not running is left alone."""
        return [e for e in entries() if e["state"] == bgm.STATE_ASSIGNED_STOPPED and e["kind"] != "chat"]

    async def start_service(sid: str) -> Optional[str]:
        """The engine's own start (its own guard), then wait until it says it is running, so the memory it takes is already there when the test reads the idle card."""
        import time as _t
        eng = voice_engine if sid == VOICE_ID else decision_engine if sid == DECISION_ID else None
        if eng is None:
            return "It isn't set up on this PC."
        try:
            await eng.start()
        except Exception as e:
            return str(getattr(e, "message", "") or e) or "It did not start."
        end = _t.monotonic() + SERVICE_READY_WAIT_S
        while _t.monotonic() < end:
            st = eng.status().get("state")
            if st in ("ready", "too_slow"):
                return None
            if st in ("died", "error", "stopped", "not_installed"):
                return "It stopped while starting."
            await asyncio.sleep(1.0)
        return "It took too long to get ready."

    def fit_next_to(path: str, background_mb: float) -> Optional[str]:
        gpu = getattr(local_svc, "_gpu", None)
        try:
            return "too_big" if local_models.fit_check(os.path.getsize(path) + int(background_mb * 1024 * 1024), gpu, reserve_skyrim=False) == "too_big" else None
        except OSError:
            return None

    def fit(path: str) -> Optional[str]:
        gpu = getattr(local_svc, "_gpu", None)
        try:
            return "too_big" if local_models.fit_check(os.path.getsize(path), gpu, reserve_skyrim=False) == "too_big" else None
        except OSError:
            return None

    def hardware() -> dict:
        gpu = getattr(local_svc, "_gpu", None)
        return {"gpu_name": getattr(gpu, "name", None), "gpu_vendor": getattr(gpu, "vendor", None), "gpu_driver": _driver_version()}

    def engine_state() -> str:
        return br.engine_state_of(bool(local_svc.engine_exe()), load_cfg())

    def memory_now() -> dict:
        """The computer's memory and the busiest card's right now (the gate between models and the before / after record). One read-only card query."""
        d = bguard.read_memory_detail()
        out = {"available_mb": d.get("available_mb"), "total_mb": d.get("total_mb"), "used_mb": d.get("used_mb"), "commit_used_mb": d.get("commit_used_mb"),
               "commit_limit_mb": d.get("commit_limit_mb"), "vram_used_mb": None, "vram_total_mb": None}
        status, text = bsm.run_command(bsm.SMI_QUERY, 4.0)
        cards = [c for c in (bsm.parse_gpu_rows(text) if status == "ok" else []) if c.get("vram_used_mb") is not None and c.get("vram_total_mb")]
        if cards:
            top = max(cards, key=lambda c: c["vram_used_mb"] / c["vram_total_mb"])
            out.update(vram_used_mb=top["vram_used_mb"], vram_total_mb=top["vram_total_mb"])
        return out

    def estimate_before(path: str, plan) -> Optional[dict]:
        """Our guess of the card memory for this run, from the model file's header, with this device's own engine settings. Saved in the run's record before it starts."""
        import benchmarks_estimate as be
        import local_engine_settings
        dev = dict(local_engine_settings.DEFAULT_SETTINGS, **local_svc.engine_settings.effective(local_svc.device_id, "")[0])
        return be.estimate_for_plan(path, plan.levels, plan.prompts, plan.sustained, dev, dict(plan.overrides))

    def model_facts(path: str, plan) -> Optional[dict]:
        """What the model file says about itself and what its cache would cost at the test's context and at 32k (our own estimate), for the live panel."""
        import benchmarks_facts as bf
        import local_engine_settings
        dev = dict(local_engine_settings.DEFAULT_SETTINGS, **local_svc.engine_settings.effective(local_svc.device_id, "")[0])
        return bf.for_model(path, dev, bf.test_ctx(plan.prompts, plan.sustained))

    return br.Hooks(model_facts=model_facts, engine_state=engine_state, game_running=lambda: bool(skyrim_state.running), last_game_request_at=lambda: outside_client.get("at"), loaded_by_multiproxy=held, unload=unload, fit_check=fit,
                    skyrim_peak_mb=lambda: local_svc.skyrim_peak_mb(), usual_skyrim_mb=lambda: None, hardware=hardware, version=version, fit_next_to=fit_next_to,
                    background_loaded=loaded, background_assigned=assigned, start_service=start_service, runs_game=lambda: bool(getattr(local_svc, "HAS_GAME", True)),
                    memory_now=memory_now, engine_working_set_mb=bguard.read_process_working_set_mb, estimate_before=estimate_before)
