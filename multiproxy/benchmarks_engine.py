"""Benchmarks: starting and stopping the llama.cpp engine for a test (the same start the Local device tab uses).

`LlamaLauncher.start()` builds the command with `local_engine_flags.build_chat_command` from the person's saved engine settings (the device's own), changes only the slots (and the
settings the plan overrides), and starts the engine on a spare port on `127.0.0.1` in its own process, with its own log. It never goes through `LocalModels.start`: that would register the
benchmark's engine as one of the game's models. The process id is kept in `engine.pid.json` in the benchmark folder so that a program restart can find and stop THAT engine and no other.
The engine is only ever stopped by its own process id, and only after checking that the id is still a llama-server.
"""
import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Optional

import aiohttp

import benchmarks_estimate
import benchmarks_owned
import gpu_choice
import hidden_process
import local_engine_flags
import local_engine_settings
import local_models
from benchmarks_runner import EngineStartError

PID_FILE = "engine.pid.json"
ALIAS = "benchmark"
CTX_PER_SLOT = 4096                                      # each slot needs room for the long prompt and the reply
LOAD_TIMEOUT_S = local_models.SERVER_LOAD_TIMEOUT
HEALTH_POLL_S = 0.5
ENGINE_LOG_LEVEL = 4                                     # at the default level the engine does not print how many layers went to the card; level 4 does (checked 2026-10-27 on a scratch copy of the pinned engine)

_LAYERS = re.compile(r"offloaded\s+(\d+)\s*/\s*(\d+)\s+layers", re.I)
_LOG_FIELDS = re.compile(r"\b(n_batch|n_ubatch|flash_attn|n_threads|type_k|type_v)\s*[=:]\s*([\w.]+)")
_FLASH_ON = re.compile(r"Flash Attention (enabled|disabled)", re.I)
_KV = re.compile(r"K \((\w+)\).*?V \((\w+)\)")
# llama.cpp's own memory lines, one per device and kind, e.g. `load_tensors:        CUDA0 model buffer size =  7024.40 MiB`, `llama_kv_cache:      CUDA0 KV buffer size =  1472.00 MiB`,
# `llama_context:      CUDA0 compute buffer size =   300.00 MiB`. The host side (`CPU`, `CPU_Mapped`, `CUDA_Host`) is system memory, not the card, so it is not counted.
_BUFFER = re.compile(r"(\S+)\s+(model|KV|RS|compute)\s+buffer size\s*=\s*([\d.]+)\s*MiB", re.I)


def _on_card(device: str) -> bool:
    d = device.rstrip(":").upper()
    return not (d.startswith("CPU") or d.endswith("_HOST") or d == "HOST")


# The engine's own startup lines name the kind of card code it loaded: `ggml_cuda_init: found 1 CUDA devices`, `ggml_vulkan: Found 1 Vulkan devices`, `ggml_metal_init`. HIP builds print the
# CUDA lines too and say `ROCm`/`HIP` in the device line. No such line (and no card code) means the engine ran on the processor.
_BACKENDS = ((re.compile(r"ggml_cuda_init:.*\b(?:ROCm|HIP)\b", re.I), "ROCm"), (re.compile(r"ggml_cuda_init|\bCUDA\d+\b"), "CUDA"),
             (re.compile(r"ggml_vulkan|\bVulkan\d+\b"), "Vulkan"), (re.compile(r"ggml_metal", re.I), "Metal"))


def parse_backend(text: str) -> Optional[str]:
    """`CUDA`, `ROCm`, `Vulkan` or `Metal` when the engine's startup output names it; None when it does not say (never guessed, never 'CPU' from silence)."""
    for rx, name in _BACKENDS:
        if rx.search(text or ""):
            return name
    return None


def parse_slots(data) -> Optional[dict]:
    """The engine's `/slots` answer (a list, one entry per slot, each with `is_processing`) -> `{"slots": n, "busy": k}`; None for anything else."""
    if not isinstance(data, list) or not data or not all(isinstance(x, dict) for x in data):
        return None
    if not all(isinstance(x.get("is_processing"), bool) for x in data):
        return None
    return {"slots": len(data), "busy": sum(1 for x in data if x["is_processing"])}


def parse_buffers(text: str) -> dict:
    """The memory the engine itself says it took on the card, in MB, summed over the card devices: `{"model", "cache", "compute"}`. `cache` is the KV buffer (plus the RS buffer of a model
    that keeps a recurrent state). A kind the log does not print is simply absent (never guessed); a log with none of them gives `{}`."""
    kinds = {"model": "model", "kv": "cache", "rs": "cache", "compute": "compute"}
    out: dict = {}
    for m in _BUFFER.finditer(text or ""):
        if not _on_card(m.group(1)):
            continue
        key = kinds[m.group(2).lower()]
        out[key] = out.get(key, 0.0) + float(m.group(3))
    return {k: int(round(v)) for k, v in out.items()}


def parse_engine_log(text: str) -> dict:
    """What the engine's own startup output says: layers on the card (`layers_on_card` / `layers_total`) and the batch, flash attention, threads and cache types it really used.
    A value the log does not give is simply missing (never guessed)."""
    out: dict = {}
    for m in _LAYERS.finditer(text or ""):
        out["layers_on_card"], out["layers_total"] = int(m.group(1)), int(m.group(2))          # the last line wins
    for m in _LOG_FIELDS.finditer(text or ""):
        out[m.group(1)] = m.group(2)
    m = _FLASH_ON.search(text or "")
    if m:
        out["flash_attn_active"] = m.group(1).lower() == "enabled"
    m = _KV.search(text or "")
    if m:
        out["cache_type_k"], out["cache_type_v"] = m.group(1), m.group(2)
    buffers = parse_buffers(text)
    if buffers:
        out["buffers_mb"] = buffers
    backend = parse_backend(text)
    if backend:
        out["backend"] = backend
    return out


def ctx_from_props(props: dict) -> dict:
    """What the engine's `/props` says about the context. Its `n_ctx` is the size of ONE slot (checked 2026-10-27 on b11361: 8 slots started with 155,648 report 19,456), so the
    whole context is that times `total_slots`."""
    gen = (props or {}).get("default_generation_settings") or {}
    per_slot, slots = gen.get("n_ctx"), (props or {}).get("total_slots")
    out: dict = {"total_slots": slots}
    if isinstance(per_slot, int):
        out["ctx_per_slot"] = per_slot
        if isinstance(slots, int) and slots > 0:
            out["ctx"] = per_slot * slots
    return out


def _read_tail(path: Path, n: int = 4000) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - n))
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


class LlamaHandle:
    def __init__(self, proc, port: int, slots: int, log_path: Path, resolved: dict, pid_file: Path):
        self.proc, self.port, self.slots, self.log_path, self.resolved = proc, port, slots, log_path, resolved
        self.alias, self.pid = ALIAS, proc.pid
        self._pid_file = pid_file
        self.owned = None                                        # the container this engine lives in (`benchmarks_owned.OwnedProcess`), made right after it starts
        self.stop_outcome: dict = {}

    async def alive(self) -> bool:
        return self.proc.returncode is None

    def log_tail(self, n: int = 600) -> str:
        tail = _read_tail(self.log_path)
        words = local_models.engine_words(tail)
        return " | ".join(words) if words else " ".join(tail.split())[-n:]

    async def stop(self) -> dict:
        """Stop THIS engine and everything it started, and say how it went (`benchmarks_owned.OwnedProcess.stop`): ask it to leave, wait 5 seconds, end the owned tree by force, wait up to
        30 seconds for every owned process to be gone. Only by its own identity, never by name or port. Without a container (it could not be made) the old by-id kill is the fallback."""
        out: dict = {}
        if self.owned is not None:
            out = await self.owned.stop(process_gone=lambda: self.proc.returncode is not None)
        elif self.proc.returncode is None:
            await asyncio.to_thread(local_models.kill_tree_sync, self.proc.pid)
            out = {"graceful": False, "forced": True, "owned_gone": None, "identity_mismatch": False, "error": "no container"}
        if self.proc.returncode is None and not out.get("identity_mismatch"):
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=15)
            except asyncio.TimeoutError:
                pass
        try:
            self._pid_file.unlink(missing_ok=True)
        except OSError:
            pass
        self.stop_outcome = out
        return out


class LlamaLauncher:
    def __init__(self, local_svc, pid_dir, cards_fn=None, devices_fn=None):
        self.svc = local_svc
        self.pid_file = Path(pid_dir) / PID_FILE
        self.cards_fn, self.devices_fn = cards_fn, devices_fn      # the graphics-card choice code (`gpu_choice`): which cards exist, which devices the engine lists

    def owned_alive(self) -> bool:
        """Is anything the last benchmark engine started still running?"""
        h = getattr(self, "_last", None)
        o = getattr(h, "owned", None)
        return bool(o is not None and o.ops.container_alive(o.container))

    def settings_for(self, slots: int, overrides: dict) -> tuple:
        """(device settings with the plan's changes and the slots, the per-model override that carries the slots and context)."""
        base, _ = self.svc.engine_settings.effective(self.svc.device_id, "")
        per_slot = int(overrides.get("per_slot_tokens") or CTX_PER_SLOT)               # the biggest prompt of the run and its reply, from the test set
        return benchmarks_estimate.run_settings(dict(local_engine_settings.DEFAULT_SETTINGS, **base), overrides, slots, per_slot, local_engine_settings.DEFAULT_CTX)       # (the estimate at Start uses it too)

    async def start(self, model_path, slots: int, overrides: dict, log_path, run_id: str) -> LlamaHandle:
        exe = self.svc.engine_exe()
        if not exe:
            raise EngineStartError("Benchmarks need the local engine (llama.cpp). Install it from the Local device tab.")
        kind = (self.svc._cfg().get("engine") or {}).get("kind", "")
        settings, override = self.settings_for(slots, overrides)
        port = local_models._free_port()
        plan = local_engine_flags.build_chat_command(exe, model_path, None, port, ALIAS, settings, override, gpu_engine=kind != "cpu",
                                                     spelling_known=self.svc._spelling_known(exe), log_level=ENGINE_LOG_LEVEL)
        cmd = list(plan.cmd)
        for flag, key in (("-b", "batch"), ("-t", "threads")):
            if overrides.get(key):
                cmd += [flag, str(int(overrides[key]))]
        gpu_plan = gpu_choice.Plan()
        if overrides.get("gpu") and self.cards_fn is not None:
            cards = await asyncio.to_thread(self.cards_fn)
            card = next((c for c in cards if c.id == overrides["gpu"]), None)
            choice = gpu_choice.choice_of(card) if card else {"id": overrides["gpu"], "name": "that card"}
            gpu_plan = await asyncio.to_thread(lambda: gpu_choice.plan_for(choice, cards, kind, exe, self.devices_fn or gpu_choice.list_devices))
            cmd += list(gpu_plan.args)
        kwargs: dict = {"env": local_engine_flags.clean_environment(gpu_choice.merge_env(None, gpu_plan))}
        if os.name == "nt":
            kwargs["creationflags"] = 0x08000000 | 0x00000200
        else:
            kwargs["start_new_session"] = True
            kwargs["env"] = local_engine_flags.clean_environment(gpu_choice.merge_env({**os.environ, "LD_LIBRARY_PATH": f"{Path(exe).parent}:{os.environ.get('LD_LIBRARY_PATH', '')}".rstrip(":")}, gpu_plan))
        log_path = Path(log_path)
        try:
            with open(log_path, "wb") as logf:
                proc = await hidden_process.create_subprocess_exec(*cmd, stdout=logf, stderr=asyncio.subprocess.STDOUT, stdin=asyncio.subprocess.DEVNULL, cwd=str(Path(exe).parent), **kwargs)
        except OSError as e:
            raise EngineStartError(f"The model wouldn't start: {e.strerror or e}")
        try:
            self.pid_file.write_text(json.dumps({"pid": proc.pid, "port": port, "run_id": run_id}), encoding="utf-8")
        except OSError:
            pass
        handle = LlamaHandle(proc, port, slots, log_path, {}, self.pid_file)
        try:
            handle.owned = benchmarks_owned.OwnedProcess(proc.pid)
        except Exception:
            handle.owned = None
        self._last = handle
        try:
            healthy = await self._wait_healthy(handle)
        except asyncio.CancelledError:
            await asyncio.shield(handle.stop())                      # cancelled while loading the model: this engine is not left running
            raise
        if not healthy:
            tail = handle.log_tail()
            await handle.stop()
            raise EngineStartError(local_models.explain_failure(_read_tail(log_path), proc.returncode), tail)
        try:
            handle.resolved = await self._resolved(handle, settings, override, exe, cmd)
        except asyncio.CancelledError:
            await asyncio.shield(handle.stop())                      # cancelled while starting: this engine is not left running
            raise
        handle.resolved["gpu_choice"] = {"requested": overrides.get("gpu"), "card": gpu_plan.card.name if gpu_plan.card else None, "note": gpu_plan.note} if overrides.get("gpu") else None
        handle.resolved["batch_requested"], handle.resolved["threads_requested"] = overrides.get("batch"), overrides.get("threads")
        return handle

    async def slot_state(self, h: LlamaHandle) -> Optional[dict]:
        """How many requests this engine handles at once and how many are busy now, from its own `/slots` route (an HTTP call to the engine, no program started). None when the engine
        does not answer it (a build or setting that turns the route off): nothing is drawn then."""
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2)) as session:
                async with session.get(f"http://127.0.0.1:{h.port}/slots") as r:
                    if r.status != 200:
                        return None
                    return parse_slots(await r.json(content_type=None))
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, OSError):
            return None

    async def _wait_healthy(self, h: LlamaHandle) -> bool:
        deadline = time.monotonic() + LOAD_TIMEOUT_S
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            while time.monotonic() < deadline:
                if h.proc.returncode is not None:
                    return False
                try:
                    async with session.get(f"http://127.0.0.1:{h.port}/health") as r:
                        if r.status == 200:
                            return True
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    pass
                await asyncio.sleep(HEALTH_POLL_S)
        return False

    async def _resolved(self, h: LlamaHandle, settings: dict, override: dict, exe, cmd: list) -> dict:
        """What the engine really used: its own startup output and its properties route, plus the settings it was asked for. Missing values stay missing."""
        out = {"slots": h.slots, "ctx_requested": override.get("ctx"), "layers_requested": settings.get("layers"), "flash_attn_requested": settings.get("flashAttn"),
               "cache_type_requested": settings.get("cacheType"), "engine_build": self.svc.engine_build_label(exe), "command": [str(c) for c in cmd]}
        out.update(parse_engine_log(_read_tail(h.log_path, 200000)))
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                async with session.get(f"http://127.0.0.1:{h.port}/props") as r:
                    if r.status == 200:
                        props = await r.json()
                        out.update(ctx_from_props(props))
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            pass
        return out


def stop_leftover_engine(pid_dir, log=lambda m: None) -> bool:
    """At program start: the engine a previous run kept in `engine.pid.json` is stopped, and only that one, and only when its id is still a llama-server."""
    pid_file = Path(pid_dir) / PID_FILE
    try:
        pid = int(json.loads(pid_file.read_text(encoding="utf-8")).get("pid"))
    except (OSError, ValueError, TypeError, AttributeError):
        return False
    killed = False
    if local_models._pid_is_llama_server(pid):
        local_models.kill_tree_sync(pid)
        killed = True
        log(f"Stopped a leftover benchmark engine from a previous run (process ID {pid})")
    try:
        pid_file.unlink(missing_ok=True)
    except OSError:
        pass
    return killed
