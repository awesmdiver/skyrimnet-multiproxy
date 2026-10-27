"""A stand-in for Anthropic's `claude` program, for tests (2026-10-05). Never signs in to anything real.

`--version` prints a version. `--print ...` behaves like the real one where the Courier cares: it sends ONE request to `ANTHROPIC_BASE_URL` with
`Authorization: Bearer <CLAUDE_CODE_OAUTH_TOKEN>` and a body shaped like the real program's (system blocks, a first user message that carries a
system-reminder block, tools, thinking), prints what it got back, and exits non-zero with the real program's own wording when the answer was refused.

`FAKE_CLAUDE_RECORD` (a file path): what this process's environment looked like, as names and yes/no only (never a value of the code).
"""
import json
import os
import sys
import urllib.error
import urllib.request

VERSION = os.environ.get("FAKE_CLAUDE_VERSION", "9.9.9")


def record() -> None:
    path = os.environ.get("FAKE_CLAUDE_RECORD")
    if not path:
        return
    names = sorted(k for k in os.environ if k.startswith(("ANTHROPIC_", "CLAUDE_CODE_", "CLAUDE_CONFIG_DIR", "DISABLE_AUTOUPDATER")))
    facts = {"names": names, "has_oauth": bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")), "config_dir": os.environ.get("CLAUDE_CONFIG_DIR", ""),
             "autoupdater_disabled": os.environ.get("DISABLE_AUTOUPDATER") == "1", "cwd": os.getcwd(), "argv": sys.argv[1:]}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(facts) + "\n")


def main() -> int:
    args = sys.argv[1:]
    if "--version" in args:
        print(f"{VERSION} (Claude Code)")
        return 0
    record()
    base, token = os.environ.get("ANTHROPIC_BASE_URL", ""), os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
    if not base:
        print("No base URL.")
        return 1
    body = {"model": args[args.index("--model") + 1] if "--model" in args else "claude-sonnet-5-5", "max_tokens": 32000, "stream": True,
            "system": [{"type": "text", "text": "x-anthropic-billing-header: cc_version=" + VERSION}, {"type": "text", "text": "Say ok"}],
            "messages": [{"role": "user", "content": [{"type": "text", "text": "<system-reminder>reminder</system-reminder>"}, {"type": "text", "text": "ok"}]}],
            "tools": [{"name": "Bash"}], "thinking": {"type": "enabled", "budget_tokens": 1024}, "context_management": {"edits": []}}
    req = urllib.request.Request(base + "/v1/messages?beta=true", data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "anthropic-beta": "oauth-2025-04-20",
                                          "x-app": "cli", "user-agent": f"claude-cli/{VERSION}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            r.read()
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", "replace")
        if e.code in (401, 403):
            print("Failed to authenticate. API Error: 401 Invalid bearer token")
        else:
            print(text)
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
