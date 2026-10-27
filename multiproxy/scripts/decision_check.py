"""Check llama.cpp's own decision models for real (a maintainer's tool; not part of the product).

    python scripts/decision_check.py --ollama <model name, e.g. clef-flash> --work <scratch folder> [--port 11434] [--disk-bytes N]   (Ollama's own /v1/systemone; unloads the model at the end)
    python scripts/decision_check.py --engine <llama-server[.exe]> --model <decision model .gguf> --work <scratch folder> [--run-on gpu|cpu]
                                            [--port 18970] [--label Kev-4B-Q8_0] [--sets easy,hard] [--skip-bench] [--ctx 16384]

Starts ONE llama-server on a spare port with the model, asks it through its own `POST /v1/systemone` (the endpoint MultiProxy passes decisions to), runs
`tests/fixtures/jev_decisions.json` (52) and `tests/fixtures/decisions/hard_set.json` (44), then times one question, a six-question request, a burst of 8 and a
15-option question, and records disk, graphics memory and the time to load. Writes `<work>/<label>-<run-on>.json` and prints the same. Only writes into `--work`;
stops only the llama-server it started.
"""
import argparse
import asyncio
import collections
import json
import os
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
SETS = {"easy": FIXTURES / "jev_decisions.json", "hard": FIXTURES / "decisions" / "hard_set.json"}

MOODS = ["calm", "tense", "angry", "sad", "afraid", "joyful", "curious", "bored", "suspicious", "proud", "ashamed", "hopeful", "jealous", "relieved", "determined"]


OLLAMA_MODEL = ""


def post(port: int, body: dict, timeout: float = 120) -> dict:
    if OLLAMA_MODEL:
        body = {"model": OLLAMA_MODEL, **body}
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/systemone", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def gpu_used_mb():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10).stdout.split()
        return int(out[0])
    except Exception:
        return None


def judge(case: dict, answer: dict) -> tuple:
    t = case["question"]["type"]
    if t == "noul":
        p = answer["noul"]
        yes = case["expected"] == "yes"
        return (p > 0.5) == yes, (p if yes else 1 - p)
    probs = answer["probabilities"]
    if t == "choice":
        return max(probs, key=probs.get) == case["expected"], probs.get(case["expected"], 0.0)
    return int(max(probs, key=probs.get)) in case["expected"], sum(probs.get(str(i), 0.0) for i in case["expected"])


def six_questions() -> tuple:
    state = "Lydia and Serana are camped on the road. Lydia is sharpening her sword. A wolf howls somewhere in the dark and Serana is staring at the tree line."
    qs = {"combat": {"type": "noul", "instructions": "Is combat still going on?"}, "afraid": {"type": "noul", "instructions": "Is Serana afraid right now?"},
          "armed": {"type": "noul", "instructions": "Is Lydia holding a weapon?"},
          "mood": {"type": "choice", "instructions": "What is Lydia's most likely mood?", "criteria": {"calm": "relaxed", "tense": "on edge", "angry": "furious", "sad": "sorrowful"}},
          "topic": {"type": "choice", "instructions": "What will they talk about next?", "criteria": {"danger": None, "food": None, "past": None, "plans": None}},
          "urgency": {"type": "score", "instructions": "How urgent is the situation?", "criteria": ["not at all", "slightly", "somewhat", "very", "extremely"]}}
    return state, qs


def stats(v: list) -> dict:
    v = sorted(v)
    return {"median_ms": round(statistics.median(v), 1), "p90_ms": round(v[min(len(v) - 1, int(len(v) * 0.9))], 1), "min": round(v[0], 1), "max": round(v[-1], 1), "n": len(v)}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine")
    ap.add_argument("--model")
    ap.add_argument("--ollama", default="", help="measure this model on a running Ollama (default port 11434) instead of starting llama-server")
    ap.add_argument("--disk-bytes", type=int, default=0, help="Ollama mode: the model's size on disk")
    ap.add_argument("--work", required=True)
    ap.add_argument("--run-on", default="gpu", choices=["gpu", "cpu"])
    ap.add_argument("--port", type=int, default=18970)
    ap.add_argument("--label", default="")
    ap.add_argument("--sets", default="easy,hard")
    ap.add_argument("--skip-bench", action="store_true")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--parallel", type=int, default=2)
    ap.add_argument("--ctx", type=int, default=8192)
    ap.add_argument("--server-args", default="", help="extra llama-server flags to try, as one quoted string")
    a = ap.parse_args()
    global OLLAMA_MODEL
    OLLAMA_MODEL = a.ollama
    if a.ollama:
        a.port = a.port if a.port != 18970 else 11434
    elif not (a.engine and a.model):
        ap.error("give --engine and --model, or --ollama")
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    label = a.label or (a.ollama or Path(a.model).stem)
    exe = Path(a.engine or ".")
    base_mb = gpu_used_mb() if a.run_on == "gpu" else None
    cmd = [str(exe), "-m", a.model, "--host", "127.0.0.1", "--port", str(a.port), "--no-webui", "-ngl", "99" if a.run_on == "gpu" else "0", "-t", str(a.threads), "-np", str(a.parallel)]
    if a.ctx:
        cmd += ["-c", str(a.ctx)]
    cmd += a.server_args.split()
    log = open(work / f"{label}-{a.run_on}.server.log", "wb")
    t_start = time.time()
    proc = None if a.ollama else subprocess.Popen(cmd, cwd=str(exe.parent), stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    out = {"label": label, "runOn": a.run_on, "diskBytes": a.disk_bytes if a.ollama else os.path.getsize(a.model), "cmd": f"ollama {a.ollama}" if a.ollama else " ".join(cmd[1:])}
    try:
        while not a.ollama:
            if proc.poll() is not None:
                raise SystemExit(f"llama-server stopped ({proc.returncode}); see the log")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{a.port}/health", timeout=2) as r:
                    if r.status == 200:
                        break
            except Exception:
                pass
            if time.time() - t_start > 300:
                raise SystemExit("took too long to load")
            await asyncio.sleep(0.25)
        out["loadSeconds"] = round(time.time() - t_start, 1)
        t0 = time.perf_counter()
        post(a.port, {"state": "The guard stands at the gate.", "questions": {"q": {"type": "noul", "instructions": "Is someone at a gate?"}}})
        out["firstAnswerMs"] = round((time.perf_counter() - t0) * 1000)
        if a.run_on == "gpu":
            used = gpu_used_mb()
            out["gpuMemoryMb"] = used - base_mb if used is not None and base_mb is not None else None
        for name in [s for s in a.sets.split(",") if s]:
            cases = json.loads(SETS[name].read_text(encoding="utf-8"))["cases"]
            rows = []
            for c in cases:
                t0 = time.perf_counter()
                try:
                    ans = post(a.port, {"state": c["state"], "questions": {"q": c["question"]}})["answers"]["q"]
                    ok, pc = judge(c, ans)
                    err = ""
                except Exception as e:
                    ok, pc, err = False, 0.0, f"{e.__class__.__name__}: {e}"
                rows.append({"id": c["id"], "type": c["question"]["type"], "category": c["category"], "ok": ok, "pc": round(pc, 4), "ms": (time.perf_counter() - t0) * 1000, "err": err})
            by, cat = collections.defaultdict(lambda: [0, 0]), collections.defaultdict(lambda: [0, 0])
            for r in rows:
                by[r["type"]][0] += r["ok"]; by[r["type"]][1] += 1
                cat[r["category"]][0] += r["ok"]; cat[r["category"]][1] += 1
            out[name] = {"total": f"{sum(r['ok'] for r in rows)}/{len(rows)}", **{k: f"{v[0]}/{v[1]}" for k, v in by.items()},
                         "meanPCorrect": round(sum(r["pc"] for r in rows) / len(rows), 4), **{"time": stats([r["ms"] for r in rows])},
                         "byCategory": {k: f"{v[0]}/{v[1]}" for k, v in sorted(cat.items())}, "wrong": [r["id"] for r in rows if not r["ok"]], "errors": [r["err"] for r in rows if r["err"]][:3]}
        if not a.skip_bench:
            state, six = six_questions()
            one = {"q": {"type": "noul", "instructions": "Is combat still going on?"}}
            for _ in range(3):
                post(a.port, {"state": state, "questions": one})
            n = [0]

            def timed(qs):
                n[0] += 1
                t0 = time.perf_counter()
                post(a.port, {"state": f"[scene {n[0]}] " + state, "questions": qs})
                return (time.perf_counter() - t0) * 1000
            out["oneQuestion"] = stats([timed(one) for _ in range(15)])
            out["sixQuestions"] = stats([timed(six) for _ in range(15)])
            mood = {"mood": {"type": "choice", "instructions": "What is Lydia's most likely mood?", "criteria": {m: None for m in MOODS}}}
            out["fifteenOptions"] = stats([timed(mood) for _ in range(10)])
            loop = asyncio.get_running_loop()
            t0 = time.perf_counter()
            each = await asyncio.gather(*(loop.run_in_executor(None, timed, six) for _ in range(8)))
            out["burstOf8SixQuestionRequests"] = {"wholeRoundMs": round((time.perf_counter() - t0) * 1000), **stats(list(each))}
            if a.run_on == "gpu":
                used = gpu_used_mb()
                out["gpuMemoryMbAfterBench"] = used - base_mb if used is not None and base_mb is not None else None
    finally:
        if a.ollama:
            try:  # unload only the model this run loaded (a request with keep_alive 0 and one real question: an empty one is refused and unloads nothing)
                post(a.port, {"state": "ok", "questions": {"q": {"type": "noul", "instructions": "Is it ok?"}}, "keep_alive": 0}, timeout=120)
            except Exception:
                pass
        elif os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        else:
            proc.kill()
        log.close()
    (work / f"{label}-{a.run_on}.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
