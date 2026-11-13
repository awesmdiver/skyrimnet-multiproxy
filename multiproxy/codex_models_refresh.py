"""Asking Codex to refresh its own model list (4.2.1).

MultiProxy never asks OpenAI for the model list and never writes `models_cache.json`: that file is Codex's own. It is only read, and Codex rewrites it on its own schedule, which
for someone who rarely runs Codex by hand means a month-old list (a model released since never shows on the ChatGPT card).

How Codex is asked (measured 2026-11-12 on Codex 0.167.2, with a scratch CODEX_HOME holding a copy of the sign-in and a deliberately stale copy of the cache): `codex app-server`
speaks JSON-RPC over its standard input and output. Sending `initialize`, the `initialized` notification and a `model/list` request makes Codex fetch the account's current models
and rewrite `models_cache.json` (new `fetched_at`, new `client_version`) in about 1.5 seconds. It is a model-list call only: no chat or model request, no tokens spent. The server
is then ended.

This file only starts that exchange; it never opens the cache for writing. Everything here fails quietly (False): the old list stays.
"""
import asyncio
import json
import re
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

MAX_AGE_SECONDS = 24 * 3600            # a list older than this is refreshed
NOTE_AFTER_SECONDS = 7 * 24 * 3600     # a list older than this, that could not be refreshed, is said to be old
WORDS = {"old": "This list is from {date}. Sign in to Codex to update it."}


def parse_time(text) -> Optional[float]:
    """Epoch seconds from the cache's `fetched_at` (ISO, any number of fraction digits, Z or an offset), or None."""
    if not isinstance(text, str) or not text:
        return None
    t = re.sub(r"(\.\d{6})\d+", r"\1", text.strip().replace("Z", "+00:00"))
    try:
        d = datetime.fromisoformat(t)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


def read_meta(path: str) -> Optional[dict]:
    """{"fetched": epoch or None, "version": str or None} from the cache file (read only), or None when it is missing or unreadable."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    v = raw.get("client_version")
    return {"fetched": parse_time(raw.get("fetched_at")), "version": v if isinstance(v, str) else None}


def needs_refresh(meta: Optional[dict], installed_version: Optional[str], now: Optional[float] = None) -> bool:
    """Missing, older than a day, or written by a different Codex than the installed one."""
    now = time.time() if now is None else now
    if meta is None or meta.get("fetched") is None:
        return True
    if now - meta["fetched"] > MAX_AGE_SECONDS:
        return True
    return bool(installed_version and meta.get("version") and meta["version"] != installed_version)


def old_note(meta: Optional[dict], now: Optional[float] = None) -> str:
    """The muted line for a list older than a week ('' otherwise)."""
    now = time.time() if now is None else now
    if not meta or meta.get("fetched") is None or now - meta["fetched"] <= NOTE_AFTER_SECONDS:
        return ""
    d = datetime.fromtimestamp(meta["fetched"])
    return WORDS["old"].format(date=f"{d.strftime('%b')} {d.day}, {d.year}")


def installed_version(text: str) -> Optional[str]:
    m = re.search(r"(\d+\.\d+\.\d+)", text or "")
    return m.group(1) if m else None


async def ask(spawn: Callable[..., Awaitable], exe: str, *, env: Optional[dict] = None, timeout: float = 20.0, kill: Optional[Callable[[int], None]] = None) -> bool:
    """Start `codex app-server`, send initialize / initialized / model/list, wait for the model/list answer, end the server. True when Codex answered it."""
    proc = None
    ok = False
    try:
        proc = await spawn(exe, "app-server", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env=env)

        def send(o):
            proc.stdin.write((json.dumps(o) + "\n").encode("utf-8"))

        async def exchange():
            nonlocal ok
            send({"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "skyrimnet-multiproxy", "version": "1"}}})
            await proc.stdin.drain()
            while True:                                          # the server answers initialize before it takes anything else
                line = await proc.stdout.readline()
                if not line:
                    return
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if o.get("id") == 1:
                    break
            send({"method": "initialized"})
            send({"id": 2, "method": "model/list", "params": {"includeHidden": True}})
            await proc.stdin.drain()
            while True:
                line = await proc.stdout.readline()
                if not line:
                    return
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if o.get("id") == 2:
                    ok = "error" not in o
                    return
        await asyncio.wait_for(exchange(), timeout=timeout)
    except Exception:
        pass
    finally:
        if proc is not None:
            try:
                if proc.stdin:
                    proc.stdin.close()
            except Exception:
                pass
            if proc.returncode is None:
                try:
                    if kill:
                        kill(proc.pid)
                    else:
                        proc.kill()
                except Exception:
                    pass
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except Exception:
                    pass
    if ok:
        await asyncio.sleep(0.3)                 # Codex has written the file by the time it answers; this only lets the write settle
    return ok
