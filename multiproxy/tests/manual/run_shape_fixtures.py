"""Sends every fixture in tests/fixtures/skyrimnet_shapes.py through the REAL proxy app (the actual
FastAPI /v1/chat/completions endpoint, in-process via TestClient -- not a hand-rolled simulation of
it), streaming and non-streaming, and prints a table: task, provider, mode, ok/failed, time to first
chunk, total time, characters returned, and whether the stream ended cleanly with a `[DONE]` rather
than being cut off. Truncation is the failure mode that just shipped to a user (the Antigravity
streaming crash, 2026-09-16) -- this is the thing the runner is loudest about.

NOT collected by pytest (its filename doesn't match test_*.py/*_test.py) and never run automatically
-- invoke it by hand:

    python tests/manual/run_shape_fixtures.py                    # offline, local stub provider (default, free)
    python tests/manual/run_shape_fixtures.py --self-test        # confirms the runner itself can detect a broken stream
    python tests/manual/run_shape_fixtures.py --provider gemini  # real calls -- costs quota, needs a real key configured
    python tests/manual/run_shape_fixtures.py --provider geminicli --model geminicli/gemini-3.8-flash-low

Default is the offline stub -- real provider calls need an explicit --provider naming one, per this
item's own "keep it free by default" requirement. Never run via pytest; nothing here is wired into
the automated suite.
"""
import argparse
import asyncio
import json
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import multiproxy as proxy  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from tests.fixtures.skyrimnet_shapes import FIXTURES, Fixture  # noqa: E402


# ---- The local stub provider: routes through ollama/ (a keyless, "runs on your own machine"
# provider by design), with its own call_ollama_direct/call_ollama_streaming replaced so the
# default run never touches a network or a subprocess. "Local stub provider" is not a figure of
# speech here -- it's this exact substitution. ----

STUB_BREAK_STREAM = False  # flipped on by --self-test for exactly one deliberately-truncated run
STUB_EMPTY_REPLY = False  # flipped on by --self-test for exactly one deliberately-empty reply


def _stub_reply_text(system_prompt, messages) -> str:
    if STUB_EMPTY_REPLY:
        # Real bug, 2026-09-16: a misrouted run returned a clean [DONE] with zero characters and
        # the harness scored it a pass. This is what --self-test uses to prove that's fixed.
        return ""
    user_text = messages[-1]["content"] if messages else ""
    return f"[stub reply] saw {len(system_prompt or '')} system chars, {len(user_text)} user chars."


async def _stub_direct(system_prompt, messages, model, max_tokens, **extra_params):
    reply = _stub_reply_text(system_prompt, messages)
    return reply, {"prompt_tokens": (len(system_prompt or "") + len(messages[-1]["content"])) // 4, "completion_tokens": len(reply) // 4}


async def _stub_streaming(system_prompt, messages, model, max_tokens, **extra_params):
    cmpl_id = f"chatcmpl-{uuid.uuid4().hex[:16]}"
    created = int(time.time())
    reply = _stub_reply_text(system_prompt, messages)
    pieces = [reply[i:i + 40] for i in range(0, len(reply), 40)] or [""]
    for i, piece in enumerate(pieces):
        chunk = {
            "id": cmpl_id, "object": "chat.completion.chunk", "created": created, "model": model,
            "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(chunk)}\n\n"
        if STUB_BREAK_STREAM and i == len(pieces) // 2:
            # Deliberately truncate -- stop without ever sending [DONE], the same real shape
            # Ollama Cloud's own known bug produces (see synthesize_done in multiproxy.py). This is
            # what --self-test uses to prove the runner can actually fail, not just pass.
            return
    stop_chunk = {
        "id": cmpl_id, "object": "chat.completion.chunk", "created": created, "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(stop_chunk)}\n\n"
    yield "data: [DONE]\n\n"


# ---- Real-provider model defaults (used only when --provider names one of these explicitly) ----

REAL_PROVIDER_DEFAULT_MODEL = {
    "gemini": "gemini/gemini-3.6-flash",
    "geminicli": "geminicli/gemini-3.8-flash-low",
    "openrouter": "openrouter/openai/gpt-oss-120b",
    "glm": "glm/glm-4.6",
    "nano": "nano/gpt-oss-120b",
    "openai": "openai/gpt-5-mini",
    "kilo": "kilo/kilo-auto/balanced",
    "ollama": "ollama/llama3.1",
    "codex": "codex/gpt-5-codex",
}


def build_client() -> TestClient:
    """Same 'skip the lifespan' trick tests/conftest.py's own test_client fixture uses -- no
    subprocess spawning or real network calls on 'startup', regardless of which provider mode
    this run is in."""
    client = TestClient(proxy.app, raise_server_exceptions=True)
    client.get("/")
    return client


class Result:
    def __init__(self, fixture: Fixture, mode: str):
        self.fixture = fixture
        self.mode = mode
        self.ok = False
        self.clean_end = None  # None = not applicable (non-streaming)
        self.ttfc = None
        self.total_time = None
        self.chars_returned = 0
        self.error = ""


def _run_direct(client: TestClient, fixture: Fixture, model: str) -> Result:
    r = Result(fixture, "direct")
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": fixture.system_prompt},
            {"role": "user", "content": fixture.user_content},
        ],
        "max_tokens": 200,
        "stream": False,
    }
    start = time.perf_counter()
    try:
        resp = client.post("/v1/chat/completions", json=body)
        r.total_time = time.perf_counter() - start
        if resp.status_code != 200:
            r.error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            return r
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        r.chars_returned = len(text or "")
        # A clean HTTP 200 with an empty reply is not a pass -- to a player, an NPC that says
        # nothing is exactly as broken as one cut off mid-sentence (real bug, 2026-09-16: a
        # misrouted run scored ok/clean_end YES/chars 0, which is silence dressed up as success).
        if r.chars_returned == 0:
            r.error = "empty reply -- zero characters returned"
        r.ok = r.chars_returned > 0
    except Exception as e:
        r.total_time = time.perf_counter() - start
        r.error = f"{type(e).__name__}: {e}"
    return r


def _run_streaming(client: TestClient, fixture: Fixture, model: str) -> Result:
    r = Result(fixture, "streaming")
    r.clean_end = False
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": fixture.system_prompt},
            {"role": "user", "content": fixture.user_content},
        ],
        "max_tokens": 200,
        "stream": True,
    }
    start = time.perf_counter()
    saw_any_chunk = False
    saw_done = False
    exhausted_cleanly = False
    try:
        with client.stream("POST", "/v1/chat/completions", json=body) as resp:
            if resp.status_code != 200:
                r.total_time = time.perf_counter() - start
                r.error = f"HTTP {resp.status_code}"
                return r
            for line in resp.iter_lines():
                if not line:
                    continue
                if not saw_any_chunk:
                    r.ttfc = time.perf_counter() - start
                    saw_any_chunk = True
                if not line.startswith("data: "):
                    continue
                payload = line[len("data: "):]
                if payload == "[DONE]":
                    saw_done = True
                    continue
                try:
                    obj = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                delta = obj.get("choices", [{}])[0].get("delta", {})
                content = delta.get("content") or ""
                r.chars_returned += len(content)
                if "error" in obj or (content and content.startswith("[") and "error" in content.lower()):
                    r.error = (r.error + " " + content).strip()
            exhausted_cleanly = True  # the for-loop above finished without an exception
    except Exception as e:
        r.error = (r.error + f" {type(e).__name__}: {e}").strip()
    r.total_time = time.perf_counter() - start
    # "Ended cleanly" needs BOTH: the generator actually finished (no exception mid-iteration) AND
    # a real [DONE] was seen. Either one alone is exactly the truncation this whole item is about --
    # a generator that finishes without [DONE] is silently dropped content, and [DONE] seen only
    # because the exception happened to land after it would be a coincidence, not a guarantee.
    r.clean_end = exhausted_cleanly and saw_done
    if not r.clean_end and not r.error:
        r.error = "stream ended without a clean [DONE] -- truncated"
    elif r.clean_end and not r.error and r.chars_returned == 0:
        # A clean [DONE] with zero characters is not a pass -- to a player, an NPC that says
        # nothing is exactly as broken as one cut off mid-sentence (real bug, 2026-09-16: a
        # misrouted run scored ok/clean_end YES/chars 0, which is silence dressed up as success).
        r.error = "empty reply -- stream ended cleanly but zero characters came back"
    r.ok = r.clean_end and not r.error
    return r


def _print_table(results: list):
    header = f"{'task':28s} {'mode':10s} {'ok':4s} {'clean_end':10s} {'ttfc':8s} {'total':8s} {'chars':7s} error"
    print(header)
    print("-" * len(header))
    for r in results:
        ttfc = f"{r.ttfc:.2f}s" if r.ttfc is not None else "n/a"
        total = f"{r.total_time:.2f}s" if r.total_time is not None else "n/a"
        clean = "n/a" if r.clean_end is None else ("YES" if r.clean_end else "**NO**")
        ok = "ok" if r.ok else "**FAIL**"
        print(f"{r.fixture.task:28s} {r.mode:10s} {ok:4s} {clean:10s} {ttfc:8s} {total:8s} {r.chars_returned:7d} {r.error[:80]}")


def run_self_test() -> bool:
    """Deliberately breaks the stub two different ways and confirms the runner reports BOTH as a
    failure, not a pass -- 'a test harness that can't fail is worthless.' Covers the two real,
    distinct failure modes found in production: a stream truncated mid-flight (no [DONE]), and a
    stream that ends cleanly but returns zero characters (2026-09-16: a misrouted run scored
    ok/clean_end YES/chars 0 -- silence dressed up as success)."""
    global STUB_BREAK_STREAM, STUB_EMPTY_REPLY
    print("--- self-test: deliberately breaking the stub two ways, expecting the runner to catch both ---")
    proxy.call_ollama_direct = _stub_direct
    proxy.call_ollama_streaming = _stub_streaming
    client = build_client()
    fixture = FIXTURES[0]

    STUB_BREAK_STREAM = False
    STUB_EMPTY_REPLY = False
    good = _run_streaming(client, fixture, "ollama/stub-self-test")

    STUB_BREAK_STREAM = True
    truncated = _run_streaming(client, fixture, "ollama/stub-self-test")
    STUB_BREAK_STREAM = False

    STUB_EMPTY_REPLY = True
    empty = _run_streaming(client, fixture, "ollama/stub-self-test")
    STUB_EMPTY_REPLY = False

    _print_table([good, truncated, empty])

    passed = (
        good.ok and good.clean_end and good.chars_returned > 0
        and (not truncated.ok) and (not truncated.clean_end)
        and (not empty.ok) and empty.chars_returned == 0 and "HTTP 502" in empty.error      # (the program refuses an empty reply with a 502 since 2026-10-07)
    )
    print()
    if passed:
        print("SELF-TEST PASSED")
    else:
        print("SELF-TEST FAILED -- the runner cannot be trusted to catch a real truncation or empty reply")
    return passed


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--provider", default="stub", help="'stub' (default, offline/free) or a real provider name: " + ", ".join(REAL_PROVIDER_DEFAULT_MODEL))
    parser.add_argument("--model", default=None, help="override the model id sent (with prefix), e.g. gemini/gemini-3.6-flash")
    parser.add_argument("--fixture", default=None, help="run only the named fixture (see tests/fixtures/skyrimnet_shapes.py)")
    parser.add_argument("--self-test", action="store_true", help="run the deliberate-truncation self-test and exit")
    args = parser.parse_args()

    if args.self_test:
        sys.exit(0 if run_self_test() else 1)

    fixtures = FIXTURES if not args.fixture else [f for f in FIXTURES if f.name == args.fixture]
    if not fixtures:
        print(f"No fixture named {args.fixture!r}. Known: {[f.name for f in FIXTURES]}")
        sys.exit(2)

    if args.provider == "stub":
        proxy.call_ollama_direct = _stub_direct
        proxy.call_ollama_streaming = _stub_streaming
        model = args.model or "ollama/stub-shapetest"
        print("Running OFFLINE against the local stub provider (routed through ollama/, patched -- no network calls).")
    else:
        if args.provider not in REAL_PROVIDER_DEFAULT_MODEL:
            print(f"Unknown provider {args.provider!r}. Known: {list(REAL_PROVIDER_DEFAULT_MODEL)} (or 'stub')")
            sys.exit(2)
        model = args.model or REAL_PROVIDER_DEFAULT_MODEL[args.provider]
        print(f"Running REAL calls against provider={args.provider!r}, model={model!r} -- this costs quota/money if configured.")

    client = build_client()
    results = []
    for fixture in fixtures:
        print(f"[{fixture.task}] {fixture.total_chars:,} chars total, synthetic={fixture.is_synthetic} ...")
        results.append(_run_direct(client, fixture, model))
        results.append(_run_streaming(client, fixture, model))

    print()
    _print_table(results)

    failures = [r for r in results if not r.ok]
    print()
    if failures:
        print(f"{len(failures)}/{len(results)} rows FAILED.")
        sys.exit(1)
    print(f"All {len(results)} rows passed.")


if __name__ == "__main__":
    main()
