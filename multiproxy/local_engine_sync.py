"""Local model settings reach the other devices and stay in step (2026-10-04).

The settings of a device live on the MultiProxy server (the main device), one record per device keyed by the device's permanent id (`local_engine_settings.py`). A
device that runs models through the Courier does not keep a copy of its own: it is SENT its record, with a version hash, and it never edits it. This module has both ends:

  * `SettingsSender` (the main device): `push(device)` sends a device its record. Triggers: the device is first seen (its report carries no hash), its settings are saved,
    and every time it (re)connects (its report says the hash it holds, or that it has not been told yet since it connected: `on_report` pushes then). A device that is
    offline simply is not told: its record stays here and the screen says "Waiting to send" (`status`), and the connection report that arrives when it comes back
    triggers the push. What the device says it holds (its report) is the only proof of "sent": the server never assumes.
  * `DeviceReceiver` (the Courier): `apply(payload)` stores the record exactly as sent, in the Courier's own settings file under device `local` (the same store, the same
    launch command builder and the same flag spelling as everywhere: nothing here knows a flag). `gate()` is what `LocalModels.start_gate` calls before EVERY model
    start: once a MultiProxy that sends settings has ever talked to this device, no model starts before the current settings have arrived since the last (re)connect
    (it waits a moment for them, then refuses with a plain sentence), and none starts at all while the settings were made for another engine build than this
    Courier's (`pinnedBuild`), until a matching set arrives.

The payload is the record, never a command line: only the device knows where its engine and model files are, so it builds each command itself with the shared builder.
A device whose own engine file is not the pinned build (a `llama-server` the player named) gets the conservative command (`local_models._spelling_known`), and says
which engine and command a model really started with in its report (`models[].started`), so the screens can show it.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Optional

import local_engine_settings as eset

PROTOCOL = 1
SYNC_KEY = "local_engine_sync"           # in the device's own settings file: {"managed", "hash", "at", "error", "pinnedBuild"}
GATE_WAIT_SECONDS = 20.0                 # how long a model start waits for the settings of a device that has not been told yet since it connected

WAIT_MESSAGE = "This device is waiting for your main device to send its model settings. Check that MultiProxy is running there, then try again."


def mismatch_message(mine: str, theirs: str) -> str:
    return f"This device runs engine {mine}, but your main device sent settings made for {theirs}. Update MultiProxy Courier on this device, then try again."


class SyncError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def payload_for(store: "eset.EngineSettingsStore", device: str, pinned_build: str, restart=None) -> dict:
    """What is sent to a device: its record, the hash of it, the engine build the checks were made for, and (optionally) which models to restart with it."""
    out = dict(store.export(device))
    out.update({"protocol": PROTOCOL, "pinnedBuild": pinned_build})
    if restart:
        out["restart"] = restart
    return out


# --------------------------------------------------------------------------------------------
# The device's end
# --------------------------------------------------------------------------------------------
class DeviceReceiver:
    def __init__(self, store: "eset.EngineSettingsStore", load_cfg: Callable[[], dict], save_cfg: Callable[[dict], None], pinned_build: str, log=None,
                 wait_seconds: float = GATE_WAIT_SECONDS):
        self.store, self._load_cfg, self._save_cfg, self.pinned_build, self.log = store, load_cfg, save_cfg, pinned_build, log
        self.wait_seconds = wait_seconds
        self._synced = asyncio.Event()

    # -- what is remembered between runs
    def _state(self) -> dict:
        d = self._load_cfg().get(SYNC_KEY)
        return dict(d) if isinstance(d, dict) else {}

    def _put(self, **changes) -> None:
        cfg = self._load_cfg()
        d = dict(cfg.get(SYNC_KEY) or {})
        d.update(changes)
        cfg[SYNC_KEY] = d
        self._save_cfg(cfg)

    @property
    def managed(self) -> bool:
        """A MultiProxy that sends settings has talked to this device at some point: from then on no model starts without its current settings."""
        return bool(self._state().get("managed"))

    @property
    def synced(self) -> bool:
        return self._synced.is_set()

    def status(self) -> dict:
        """What the device tells the main device in every report: the hash of what it was last sent, its own engine build, whether it has been told since it connected."""
        st = self._state()
        return {"hash": st.get("hash") or None, "pinnedBuild": self.pinned_build, "error": st.get("error") or "", "synced": self.synced, "managed": bool(st.get("managed")),
                "reset": self.store.get(eset.DEVICE_LOCAL).reset, "protocol": PROTOCOL}

    def connected(self) -> None:
        """A (new) connection from the main device: whatever it sent before may be out of date, so wait to be told again before starting anything."""
        self._synced.clear()

    # -- receiving
    def apply(self, payload) -> dict:
        if not isinstance(payload, dict) or not isinstance(payload.get("settings"), dict):
            raise SyncError(400, "Received an invalid request.")
        proto = payload.get("protocol")
        if isinstance(proto, int) and proto > PROTOCOL:
            raise SyncError(409, "The Courier on this device needs an update.")
        theirs = str(payload.get("pinnedBuild") or "")
        if theirs and theirs != self.pinned_build:
            msg = mismatch_message(self.pinned_build, theirs)
            self._put(managed=True, error=msg)
            self._synced.set()                         # (told: the answer is a refusal, and gate() says it)
            if self.log:
                self.log.warning(msg)
            raise SyncError(409, msg)
        new_hash = str(payload.get("hash") or "")
        if new_hash and new_hash == self._state().get("hash") and not self._state().get("error"):
            self._put(managed=True, at=time.time())      # the same set again: nothing to rewrite, but this device has been told
        else:
            self.store.replace_all(eset.DEVICE_LOCAL, payload["settings"], payload.get("models") or {}, str(payload.get("build") or ""))
            self._put(managed=True, hash=new_hash, at=time.time(), error="", pinnedBuild=theirs or self.pinned_build)
            if self.log:
                self.log.info("Received this device's model settings from your main device")
        self._synced.set()
        return {"ok": True, "hash": new_hash}

    # -- the check every model start passes first
    async def gate(self) -> Optional[str]:
        st = self._state()
        if st.get("error"):
            return str(st["error"])
        if not st.get("managed") or self._synced.is_set():
            return None
        try:
            await asyncio.wait_for(self._synced.wait(), self.wait_seconds)
        except asyncio.TimeoutError:
            return WAIT_MESSAGE
        return str(self._state().get("error") or "") or None


# --------------------------------------------------------------------------------------------
# The main device's end
# --------------------------------------------------------------------------------------------
class SettingsSender:
    """`remote_for(device)` -> the Remote (other_pcs.Remote: `.client`, `.report`, `.online`, `.instance`, `.id`) of that device, or None."""

    def __init__(self, store: "eset.EngineSettingsStore", remote_for: Callable[[str], Any], pinned_build: str, log=None):
        self.store, self.remote_for, self.pinned_build, self.log = store, remote_for, pinned_build, log
        self._busy: set = set()
        self._errors: dict = {}                    # device -> the last refusal's sentence
        self._refused: dict = {}                   # device -> (hash, the device's engine build) that was refused: not sent again until one of them changes
        self.sent_at: dict = {}

    @staticmethod
    def device_key(remote) -> str:
        """The permanent id of a Courier device (its nickname before it has said it)."""
        return getattr(remote, "instance", "") or remote.id

    def _report_state(self, remote) -> Optional[dict]:
        rep = getattr(remote, "report", None)
        return (rep or {}).get("engine_settings") if isinstance(rep, dict) else None

    def status(self, device: str) -> dict:
        """For the screen: `needed` (a remote device), `sent` (it says it holds exactly the current settings and has been told since it connected), `online`, the
        reason a send was refused, and whether the device is too old to take settings at all (`unsupported`)."""
        remote = self.remote_for(device)
        if remote is None:
            return {"needed": True, "sent": False, "online": None, "error": "", "unsupported": False, "hash": self.store.export(device)["hash"], "deviceHash": None}
        es = self._report_state(remote)
        want = self.store.export(device)["hash"]
        have = (es or {}).get("hash")
        return {"needed": True, "sent": bool(es and have == want and es.get("synced") and not es.get("error")), "online": getattr(remote, "online", None),
                "error": self._errors.get(device) or (es or {}).get("error") or "", "unsupported": es is None and bool(getattr(remote, "report", None)),
                "hash": want, "deviceHash": have, "deviceBuild": (es or {}).get("pinnedBuild")}

    async def push(self, device: str, restart=None) -> dict:
        """Send `device` its settings now. -> {"sent": bool, "reason": plain words when it was not}. Never raises: an offline device is "waiting"."""
        import other_pcs
        remote = self.remote_for(device)
        if remote is None or getattr(remote, "client", None) is None:
            return {"sent": False, "reason": "That device isn't connected."}
        if getattr(remote, "online", None) is False:
            return {"sent": False, "reason": "That device is offline. It gets its settings when it connects."}
        if device in self._busy:
            return {"sent": False, "reason": "Already sending."}
        self._busy.add(device)
        try:
            payload = payload_for(self.store, device, self.pinned_build, restart)
            await remote.client.engine_settings_post(payload)
            self._errors.pop(device, None)
            self._refused.pop(device, None)
            self.sent_at[device] = time.time()
            return {"sent": True, "reason": "", "hash": payload["hash"]}
        except other_pcs.OtherPcsError as e:
            if e.status == 409:                      # the device answered and refused (another engine build, or an older Courier): say so, and do not keep trying
                self._errors[device] = e.message
                es = self._report_state(remote) or {}
                self._refused[device] = (self.store.export(device)["hash"], es.get("pinnedBuild"))
            if self.log:
                self.log.warning(f"Couldn't send {getattr(remote, 'id', device)} its model settings: {e.message}")
            return {"sent": False, "reason": e.message}
        finally:
            self._busy.discard(device)

    def on_report(self, remote) -> None:
        """Called for every report a Courier pushes: tell it when it does not hold the current settings or has not been told since it connected."""
        es = self._report_state(remote)
        if es is None:
            return                                   # an older Courier: it has no settings to send them to
        device = self.device_key(remote)
        want = self.store.export(device)["hash"]
        if es.get("hash") == want and es.get("synced") and not es.get("error"):
            return
        if self._refused.get(device) == (want, es.get("pinnedBuild")):
            return                                   # already refused for exactly this: sending it again cannot help
        try:
            asyncio.get_running_loop().create_task(self.push(device))
        except RuntimeError:
            pass
