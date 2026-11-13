"""A stand-in for `codex app-server` (JSON-RPC over standard input and output), for tests. On `model/list` it does what the real Codex does: rewrites its own models_cache.json
(new fetched_at / client_version) and answers. Usage: fake_codex_app_server.py <cache file> [mode]; modes: ok | hang | die | error."""
import json
import sys
import time

cache, mode = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "ok")
for line in sys.stdin:
    try:
        o = json.loads(line)
    except ValueError:
        continue
    if mode == "die":
        sys.exit(1)
    if o.get("method") == "initialize":
        print(json.dumps({"id": o["id"], "result": {"userAgent": "fake"}}), flush=True)
    elif o.get("method") == "model/list":
        if mode == "hang":
            time.sleep(60)
        if mode == "error":
            print(json.dumps({"id": o["id"], "error": {"code": -1, "message": "no"}}), flush=True)
            continue
        with open(cache, "w", encoding="utf-8") as f:
            json.dump({"fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S.000000000Z", time.gmtime()), "client_version": "9.9.9",
                       "models": [{"slug": "gpt-6-astra", "display_name": "Astra", "visibility": "list", "supported_in_api": True, "priority": 0}]}, f)
        print(json.dumps({"id": o["id"], "result": {"data": []}}), flush=True)
