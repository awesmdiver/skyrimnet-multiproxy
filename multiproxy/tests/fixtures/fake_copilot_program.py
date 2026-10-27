"""A stand-in for GitHub's `copilot` program, for tests (2026-10-06). Never signs in to anything real.

`--version` prints a version. `login --device-code` prints a sign-in code and waits until the file named by `FAKE_COPILOT_APPROVE` exists (the "person approved it at
GitHub" step), then saves a made-up token in `$COPILOT_HOME/config.json`, like the real program's plain-text fallback. `logout` removes it. `--list-models` prints a few
names. Anything else is a request: with no saved token (and none in the environment, the way the real program would also take one from `GH_TOKEN`) it says it is not
signed in; otherwise it answers as JSONL events, or as plain text when no `--output-format` was given.

`FAKE_COPILOT_MODE`: ok | limit | no_plan | unknown_flag (refuses --deny-tool / --output-format like an older program) | plain | silent_fail.
`FAKE_COPILOT_RECORD` (a file path): what each run looked like, as names and yes/no only (never a value of a token).
"""
import json
import os
import sys
import time

VERSION = os.environ.get("FAKE_COPILOT_VERSION", "9.9.9")
FAKE_TOKEN = "gho_" + "F" * 36


def record(extra: dict) -> None:
    path = os.environ.get("FAKE_COPILOT_RECORD")
    if not path:
        return
    names = sorted(k for k in os.environ if k.startswith(("COPILOT_", "GH_", "GITHUB_")))
    facts = {"names": names, "home": os.environ.get("COPILOT_HOME", ""), "gh_config": os.environ.get("GH_CONFIG_DIR", ""), "cwd": os.getcwd(), "argv": [a if len(a) < 200 else "<long>" for a in sys.argv[1:]],
             "auto_update": os.environ.get("COPILOT_AUTO_UPDATE", ""), **extra}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(facts) + "\n")


def config_path() -> str:
    return os.path.join(os.environ.get("COPILOT_HOME", "."), "config.json")


def have_token() -> bool:
    if os.environ.get("COPILOT_GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"):
        return True
    return os.path.isfile(config_path())


def emit(ev: dict) -> None:
    sys.stdout.write(json.dumps(ev) + "\n")
    sys.stdout.flush()


def main() -> int:
    args = sys.argv[1:]
    mode = os.environ.get("FAKE_COPILOT_MODE", "ok")
    if "--version" in args:
        print(f"GitHub Copilot CLI {VERSION}")
        return 0
    if args[:1] == ["login"]:
        record({"kind": "login"})
        if mode == "login_fail":
            print("Could not start the sign-in.")
            return 1
        print("To sign in, use a web browser to open the page https://github.com/login/device")
        print("and enter the code ABCD-1234 when prompted.", flush=True)
        approve = os.environ.get("FAKE_COPILOT_APPROVE", "")
        deadline = time.time() + float(os.environ.get("FAKE_COPILOT_WAIT", "20"))
        while approve and not os.path.exists(approve) and time.time() < deadline:
            time.sleep(0.05)
        if approve and not os.path.exists(approve):
            print("The code expired.")
            return 1
        os.makedirs(os.environ.get("COPILOT_HOME", "."), exist_ok=True)
        with open(config_path(), "w", encoding="utf-8") as f:
            json.dump({"token": FAKE_TOKEN}, f)
        print("Signed in.")
        return 0
    if args[:1] == ["logout"]:
        record({"kind": "logout"})
        try:
            os.remove(config_path())
        except OSError:
            pass
        return 0
    if "--list-models" in args:
        record({"kind": "models"})
        print("Available models:")
        print("  gpt-5.2")
        print("  claude-sonnet-5-5")
        print("  gemini-3-pro")
        return 0
    # a request
    prompt = args[args.index("-p") + 1] if "-p" in args else sys.stdin.read()
    record({"kind": "ask", "prompt_len": len(prompt), "via_stdin": "-p" not in args, "model": args[args.index("--model") + 1] if "--model" in args else "", "override_seen": bool(
        os.environ.get("COPILOT_GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")), "prompt_has_system": "Instructions:" in prompt})
    if mode == "unknown_flag" and any(a.startswith(("--deny-tool", "--output-format", "--no-custom")) for a in args):
        print("error: unknown option '--deny-tool=shell,write'", file=sys.stderr)
        return 1
    if not have_token():
        print("You are not logged in. Run `copilot login` to sign in.", file=sys.stderr)
        return 1
    if mode == "limit":
        print("Error: You've reached your monthly limit of Copilot credits. It resets on the 1st.", file=sys.stderr)
        return 1
    if mode == "no_plan":
        print("Error: You do not have access to GitHub Copilot with this account.", file=sys.stderr)
        return 1
    if mode == "silent_fail":
        return 1
    if mode == "token_in_error":
        print("Error: request failed for " + FAKE_TOKEN + " (connection reset)", file=sys.stderr)
        return 1
    reply = "Hello"
    if "--output-format" in args and mode != "plain":
        emit({"type": "session.start", "data": {}})
        emit({"type": "assistant.message_delta", "data": {"messageId": "m1", "deltaContent": "Hel"}})
        emit({"type": "assistant.message_delta", "data": {"messageId": "m1", "deltaContent": "lo"}})
        emit({"type": "assistant.message", "data": {"messageId": "m1", "content": reply}})
        emit({"type": "result", "sessionId": "s1", "exitCode": 0})
    else:
        print(reply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
