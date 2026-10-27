"""Overview's "Check everything" button (2026-10-25): runs a short list of checks and returns only what is wrong.

The list grows: each check is one `Check(...)` entry in REGISTRY (a key, a function that tests and returns the problems it found, nothing else). A future "usual cause"
is one more function and one more line there. Rules every check follows:
  - It tests something MultiProxy can really measure; a check that cannot run (no answer, an error, too slow) says nothing: a guess is never shown.
  - It names the problem and what to do. It never stops another program and never changes a Windows setting or a firewall rule (those need the player's own hands, so
    the steps are shown instead). The only button is for MultiProxy's own thing (the engine update).
The words live in LINES (and the step lists next to them) so the design side's Gemini pass is a one-spot swap.
"""
import asyncio
import inspect
import os
from dataclasses import dataclass
from typing import Any, Callable, Optional

import hidden_process

CHECK_TIMEOUT_S = 8.0     # a check that takes longer is dropped for this run (the others are not held up)

LINES = {
    "all_well": "No problems found.",
    "port_main_title": "Another program is using port {port}",
    "port_main_detail": "SkyrimNet might not be able to reach MultiProxy. {who} Close it, or change SkyrimNet's address.",
    "port_claude_title": "Another program is using port {port}",
    "port_claude_detail": "Claude cannot sign in through MultiProxy. {who} Close it, then restart MultiProxy.",
    "port_who_named": "It is {name}.",
    "port_who_unknown": "Windows did not say which program it is.",
    "pc_title": "{name} cannot be reached",
    "pc_detail_default": "MultiProxy gets no answer from it.",
    "engine_title": "The model engine is too old for the decision model",
    "engine_detail": "Update it now.",
    "claude_title": "Claude Code is out of date",
    "claude_detail": "Update it, then sign in again.",
}

PC_STEPS = (
    "Check that {name} is switched on and awake.",
    "Check that MultiProxy Courier is running on {name}.",
    "Check that both PCs are on the same home network (guest Wi-Fi will not work).",
    "On a Windows PC: open Windows Security, then Firewall & network protection, then Allow an app through firewall. Tick Private for MultiProxy Courier.",
)
CLAUDE_STEPS = (
    "Close Claude Code.",
    "Open a terminal and run: `claude update`",
    "Sign in to Claude Code again.",
)
ENGINE_UPDATE_URL = "/config/local-models/engine/update"


@dataclass
class Context:
    """What the checks may look at. MultiProxy fills it in; a test fills it with stand-ins."""
    main_port: int = 8000
    claude_port: int = 9999
    own_pid: int = 0
    listener_pids: Callable[[int], list] = lambda port: []
    process_name: Callable[[int], Optional[str]] = lambda pid: None
    pcs: Callable[[], list] = lambda: []                       # [{"name", "kind", "online", "error", "codeChanged"}]
    engine: Callable[[], Any] = lambda: None                   # (may be async) {"old": bool, "canUpdate": bool} when the decision model is installed here, else None
    claude_too_old: Callable[[], bool] = lambda: False


def finding(key: str, title: str, detail: str = "", steps=None, action: Optional[dict] = None) -> dict:
    return {"key": key, "title": title, "detail": detail, "steps": list(steps) if steps else None, "action": action}


# ---- the checks ----

def check_ports(ctx: Context) -> list:
    out = []
    for port, kind in ((ctx.main_port, "main"), (ctx.claude_port, "claude")):
        others = [p for p in ctx.listener_pids(port) if p and p != ctx.own_pid]
        if not others:
            continue
        name = ctx.process_name(others[0])
        who = LINES["port_who_named"].format(name=name) if name else LINES["port_who_unknown"]
        out.append(finding(f"port-{port}", LINES[f"port_{kind}_title"].format(port=port), LINES[f"port_{kind}_detail"].format(port=port, who=who)))
    return out


def check_pcs(ctx: Context) -> list:
    out = []
    for pc in ctx.pcs():
        if pc.get("kind") not in ("courier", "multiproxy") or pc.get("online") is not False or pc.get("codeChanged"):
            continue          # not checked yet, reachable, or reachable but wants a new code (its own row says so)
        name = pc.get("name") or "A paired PC"
        out.append(finding(f"pc-{pc.get('id') or name}", LINES["pc_title"].format(name=name), (pc.get("error") or "").strip() or LINES["pc_detail_default"],
                           steps=[s.format(name=name) for s in PC_STEPS]))
    return out


async def check_engine(ctx: Context) -> list:
    e = ctx.engine()
    if inspect.isawaitable(e):
        e = await e
    if not e or not e.get("old"):
        return []
    action = {"label": "Update", "url": ENGINE_UPDATE_URL} if e.get("canUpdate") else None
    return [finding("engine-old", LINES["engine_title"], LINES["engine_detail"], action=action)]


def check_claude(ctx: Context) -> list:
    return [finding("claude-old", LINES["claude_title"], LINES["claude_detail"], steps=CLAUDE_STEPS)] if ctx.claude_too_old() else []


@dataclass(frozen=True)
class Check:
    key: str
    run: Callable[[Context], Any]       # returns a list of findings; may be async


REGISTRY = [
    Check("ports", check_ports),
    Check("pcs", check_pcs),
    Check("engine", check_engine),
    Check("claude", check_claude),
]


async def _run_one(check: Check, ctx: Context, timeout: float) -> list:
    try:
        if inspect.iscoroutinefunction(check.run):
            res = await asyncio.wait_for(check.run(ctx), timeout)
        else:
            res = await asyncio.wait_for(asyncio.to_thread(check.run, ctx), timeout)
        return [f for f in (res or []) if isinstance(f, dict) and f.get("title")]
    except Exception:           # includes a timeout: a check that cannot run says nothing
        return []


async def run_all(ctx: Context, registry=None, timeout: float = CHECK_TIMEOUT_S) -> dict:
    """{"problems": [finding, ...], "allWell": text-or-None}. The checks run side by side; one failing or timing out never affects the others."""
    done = await asyncio.gather(*[_run_one(c, ctx, timeout) for c in (REGISTRY if registry is None else registry)])
    problems = [f for part in done for f in part]
    return {"problems": problems, "allWell": None if problems else LINES["all_well"]}


# ---- looking at this PC (Windows and Linux) ----

def parse_netstat_listeners(text: str, port: int) -> list:
    """Every process id listening (TCP) on `port`, from `netstat -ano` output."""
    needle, pids = f":{port}", []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0] == "TCP" and parts[3] == "LISTENING" and parts[1].endswith(needle):
            try:
                pids.append(int(parts[-1]))
            except ValueError:
                continue
    return pids


def windows_listener_pids(port: int, run=hidden_process.run) -> list:
    try:
        res = run(["netstat", "-ano"], capture_output=True, text=True, timeout=5)
    except Exception:
        return []
    return parse_netstat_listeners(res.stdout or "", port)


def process_name_of(pid: int, run=hidden_process.run) -> Optional[str]:
    """The program's name (for example `nginx.exe`), or None when Windows will not say."""
    try:
        if os.name == "nt":
            res = run(["tasklist", "/FI", f"PID eq {int(pid)}", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=5)
            line = (res.stdout or "").strip().splitlines()[0] if (res.stdout or "").strip() else ""
            if line.startswith('"'):
                return line.split('","')[0].strip('"') or None
            return None
        with open(f"/proc/{int(pid)}/comm", encoding="utf-8", errors="replace") as f:
            return f.read().strip() or None
    except Exception:
        return None
