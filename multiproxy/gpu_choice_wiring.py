"""Which graphics card each local AI uses: the routes and the saved choices (Routing, step 8, 2026-10-02).

What is saved: `gpu_choices` {AI key: {"id", "name", "memoryGb"}} in config.json (the main device) or courier.json (a Courier device). No entry = Automatic. AI keys:
`local:<model id>` (a llama.cpp model), `decisions` (the local decision model), `voice` (Higgs voice). The engines read their own choice through the functions made
here (`choice_fn`), so nothing is stored anywhere else and a player who never opens the card never has a `gpu_choices` key at all.

The pure part (`build_block`, `set_choice`) is shared with the Courier, which puts the same block into its report; FastAPI is only imported inside `setup`, because a
Courier device doesn't install it.

Routes on the main device:
  GET  /config/gpu-choice   {"pcs": [{"id": "this"|<nickname>, "name", "online", "cards": [...], "ais": [...]}], "shown": bool}: only devices with more than one real card
  POST /config/gpu-choice   {"pc", "ai", "cardId": id|null} -> the same, plus {"restartNeeded": bool, "ai": key} for the AI that was changed
"""
import asyncio
from typing import Any, Callable, Optional

import gpu_choice

LOCAL = "local:"
DECISIONS = "decisions"
VOICE = "voice"
DECISIONS_LABEL = "Local decision model"
VOICE_LABEL = "Higgs voice"

NO_AI = "That AI isn't installed on that device."
NO_CARD = "That graphics card isn't in that device anymore."
NO_PC = "That device isn't set up for this."
PC_OFFLINE = "That device is offline right now, so you can't change its graphics card."
NO_ANSWER = "That device didn't answer."


def stored_choices(load_cfg: Callable[[], dict]) -> dict:
    raw = (load_cfg() or {}).get("gpu_choices")
    return raw if isinstance(raw, dict) else {}


def choice_fn(load_cfg: Callable[[], dict], key: str) -> Callable[[], Optional[dict]]:
    """The function an engine is given: this AI's remembered card choice, or None (Automatic). Read fresh every time, so a change applies at the next start."""
    def read() -> Optional[dict]:
        c = stored_choices(load_cfg).get(key)
        return c if isinstance(c, dict) and c.get("id") else None
    return read


def local_choice_fn(load_cfg: Callable[[], dict]) -> Callable[[str], Optional[dict]]:
    """For LocalModels: gets a model id, looks up `local:<id>`."""
    return lambda model_id: choice_fn(load_cfg, LOCAL + str(model_id))()


def card_json(card) -> dict:
    return {**card.to_json(), "shortName": gpu_choice.short_name(card.name)}


def _row(key: str, label: str, running: bool, engine_card: Optional[str], engine_note: str, choices: dict, cards: list) -> dict:
    choice = choices.get(key) if isinstance(choices.get(key), dict) and choices[key].get("id") else None
    card = next((c for c in cards if choice and c.id == choice["id"]), None)
    note = engine_note or (gpu_choice.CARD_GONE.format(name=choice.get("name") or "that card") if choice and card is None else "")
    expected = card.name if card else None
    # A running AI keeps the card it started on: the choice only applies at its next start. A card the engine said it can't use (the note names it) isn't a pending change.
    restart = bool(running and expected != engine_card and not (expected and expected in (engine_note or "")))
    return {"key": key, "label": label, "choice": choice, "note": note, "running": bool(running), "restartNeeded": restart}


def collect_ais(local, decisions, voice, choices: dict, cards: list) -> list:
    """The AIs on this device that can be kept on a card: every installed llama.cpp model, the decision model and Higgs voice when installed."""
    out = []
    if local is not None:
        try:
            installed = local.installed()
        except Exception:
            installed = []
        for m in installed:
            state, _ = local.state_of(m.id)
            out.append(_row(LOCAL + m.id, m.name, state in ("running", "starting"), getattr(local, "gpu_cards", {}).get(m.id),
                            getattr(local, "gpu_notes", {}).get(m.id, ""), choices, cards))
    for key, label, eng in ((DECISIONS, DECISIONS_LABEL, decisions), (VOICE, VOICE_LABEL, voice)):
        if eng is None:
            continue
        try:
            if not eng.installed():
                continue
        except Exception:
            continue
        out.append(_row(key, label, getattr(eng, "state", "") in ("starting", "getting_ready", "ready"), getattr(eng, "gpu_card", None),
                        getattr(eng, "gpu_note", "") or "", choices, cards))
    return out


def build_block(local, decisions, voice, choices: dict, cards: list) -> dict:
    """This device's `gpu_choice` block: the cards that count, and its AIs (also what a Courier puts in its report)."""
    real = gpu_choice.choosable(cards)
    return {"cards": [card_json(c) for c in real], "ais": collect_ais(local, decisions, voice, choices, real)}


class ChoiceError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def set_choice(cfg: dict, block: dict, cards: list, ai: str, card_id: Optional[str]) -> bool:
    """Write (or clear) one AI's choice into `cfg["gpu_choices"]`. `block` is the device's current block (the AIs and cards it has). Returns True when the value changed."""
    if ai not in [a["key"] for a in block["ais"]]:
        raise ChoiceError(404, NO_AI)
    choices = dict(cfg.get("gpu_choices") if isinstance(cfg.get("gpu_choices"), dict) else {})
    before = choices.get(ai)
    if card_id in (None, "", "automatic"):
        choices.pop(ai, None)
    else:
        card = next((c for c in gpu_choice.choosable(cards) if c.id == card_id), None)
        if card is None:
            raise ChoiceError(404, NO_CARD)
        choices[ai] = gpu_choice.choice_of(card)
    if choices:
        cfg["gpu_choices"] = choices
    else:
        cfg.pop("gpu_choices", None)            # nothing chosen: the key goes away, so the file looks exactly as it did before
    return before != choices.get(ai)


def setup(app, load_cfg: Callable[[], dict], save_cfg: Callable[[dict], None], local_svc, decisions_engine, voice_engine, remotes=None,
          cards_fn: Callable[[], list] = gpu_choice.cached_cards) -> dict:
    from fastapi import Request
    from fastapi.responses import JSONResponse

    def err(status: int, message: str):
        return JSONResponse({"detail": message}, status_code=status)

    def couriers() -> list:
        return [r for r in (remotes.all() if remotes is not None else []) if r.kind == "courier"]

    def this_block() -> tuple:
        cards = cards_fn()
        return build_block(local_svc, decisions_engine, voice_engine, stored_choices(load_cfg), cards), cards

    def pc_json(pid: str, name: str, online, block: dict) -> dict:
        return {"id": pid, "name": name, "online": online, "cards": block["cards"], "ais": block["ais"]}

    def all_pcs() -> list:
        block, _ = this_block()
        pcs = [pc_json("this", "This device", True, block)]
        for r in couriers():
            b = (r.report or {}).get("gpu_choice")
            if isinstance(b, dict):
                pcs.append(pc_json(r.id, r.name, r.online is not False, {"cards": b.get("cards") or [], "ais": b.get("ais") or []}))
        return [p for p in pcs if len(p["cards"]) > 1]

    def answer(extra: Optional[dict] = None) -> dict:
        pcs = all_pcs()
        return {"pcs": pcs, "shown": bool(pcs), **(extra or {})}

    @app.get("/config/gpu-choice")
    async def gpu_choice_get():
        try:
            return await asyncio.to_thread(answer)
        except Exception:
            return err(500, "Couldn't read the graphics cards.")

    @app.post("/config/gpu-choice")
    async def gpu_choice_set(request: Request):
        try:
            data = await request.json()
        except Exception:
            data = {}
        data = data if isinstance(data, dict) else {}
        pc, ai, card_id = str(data.get("pc") or "this"), str(data.get("ai") or ""), data.get("cardId")
        if pc == "this":
            def apply() -> bool:
                block, cards = this_block()
                cfg = load_cfg()
                set_choice(cfg, block, cards, ai, card_id)
                save_cfg(cfg)
                new, _ = this_block()
                return next((a["restartNeeded"] for a in new["ais"] if a["key"] == ai), False)
            try:
                restart = await asyncio.to_thread(apply)
            except ChoiceError as e:
                return err(e.status, e.message)
            return await asyncio.to_thread(lambda: answer({"ai": ai, "restartNeeded": restart}))
        r = next((x for x in couriers() if x.id.lower() == pc.lower()), None)
        if r is None or r.client is None:
            return err(404, NO_PC)
        if r.online is False:
            return err(409, PC_OFFLINE)
        try:
            block = await r.client.gpu_post({"ai": ai, "cardId": card_id})
        except Exception as e:
            return err(getattr(e, "status", 502) if isinstance(getattr(e, "status", None), int) else 502, getattr(e, "message", "") or NO_ANSWER)
        if r.report is not None and isinstance(block, dict):
            r.report["gpu_choice"] = {"cards": block.get("cards") or [], "ais": block.get("ais") or []}
        restart = next((a.get("restartNeeded", False) for a in (block or {}).get("ais", []) if a.get("key") == ai), False)
        return await asyncio.to_thread(lambda: answer({"ai": ai, "restartNeeded": bool(restart)}))

    return {"answer": answer}
