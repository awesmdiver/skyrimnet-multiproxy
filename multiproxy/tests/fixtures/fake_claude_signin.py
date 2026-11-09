"""A stand-in for `claude setup-token`, for tests (2026-10-06). Never signs in to anything real; every link, code and token here is made up.

It behaves the way the research says the real program does:
  * it needs a real terminal: with plain pipes it prints nothing and waits (the real one "emits zero bytes and hangs until killed");
  * on a terminal it clears the screen, prints the sign-in link (as an OSC 8 hyperlink AND as text, hard-wrapped at 80 columns like a narrow terminal would), and a
    `Paste code here if prompted >` prompt;
  * it reads one line (the pasted code). The right code ends in `#<the link's state>`. A wrong code is answered with SILENCE (no error, no re-prompt, no exit);
  * the right code prints `✓ Long-lived authentication token created successfully!` and the token wrapped over TWO lines at 79 columns, then exits 0.

Knobs (environment): FAKE_SIGNIN_MODE = normal | no_link (never prints the link) | die (prints the link, then exits 1 at once) | no_osc8 (text link only) |
unwrapped (link and token on one line each) | slow (waits 2 s after the right code) | no_tty_ok (does not insist on a terminal) |
auto (the sign-in is approved in a browser on this device: after FAKE_SIGNIN_DELAY seconds it prints the token and exits 0 with nobody pasting anything) |
end (after FAKE_SIGNIN_DELAY seconds it exits 1 with no token: the browser was closed) |
alive (prints the code, then waits for a key and never ends) | inline (the code is followed by more text on its line) | noisy (colour and carriage returns around it) |
wrapped3 (the code wrapped over three lines) | short_end (a too-short code-like string, then it ends) | enter (asks to press Enter, then prints the code and ends);
FAKE_SIGNIN_CODE (the right code, default `fakecode123#STATE456`), FAKE_SIGNIN_TOKEN, FAKE_SIGNIN_HOST (link host, default claude.com),
FAKE_SIGNIN_RECORD (a file: this process's environment as names and yes/no only, never a value of a code).
"""
import json
import os
import sys
import time

MODE = os.environ.get("FAKE_SIGNIN_MODE", "normal")
STATE = "STATE456"
CODE = os.environ.get("FAKE_SIGNIN_CODE", "fakecode123#" + STATE)
TOKEN = os.environ.get("FAKE_SIGNIN_TOKEN", "sk-ant-oat01-" + "fakeTOKEN_" * 9 + "abcde")      # 108 characters
HOST = os.environ.get("FAKE_SIGNIN_HOST", "claude.com")
LINK = (f"https://{HOST}/cai/oauth/authorize?code=true&client_id=00000000-fake-0000&response_type=code&redirect_uri=https%3A%2F%2Fplatform.claude.com%2Foauth%2Fcode%2Fcallback"
        f"&scope=user%3Ainference&code_challenge=FAKECHALLENGE0123456789&code_challenge_method=S256&state={STATE}")


def out(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


def wrap(text: str, width: int) -> str:
    return "\r\n".join(text[i:i + width] for i in range(0, len(text), width))


def record() -> None:
    path = os.environ.get("FAKE_SIGNIN_RECORD")
    if not path:
        return
    names = sorted(k for k in os.environ if k.startswith(("ANTHROPIC_", "CLAUDE_CODE_", "CLAUDE_CONFIG_DIR", "DISABLE_AUTOUPDATER", "BROWSER")))
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"names": names, "config_dir": os.environ.get("CLAUDE_CONFIG_DIR", ""), "tty": sys.stdin.isatty(), "argv": sys.argv[1:],
                            "columns": os.environ.get("COLUMNS", "")}) + "\n")


def main() -> int:
    record()
    if not sys.stdin.isatty() and MODE != "no_tty_ok":
        time.sleep(3600)                  # the real program: no bytes, no exit
        return 1
    if MODE == "no_link":
        time.sleep(3600)
        return 1
    out("\x1b[2J\x1b[H\x1b[1mClaude Code long-lived token\x1b[0m\r\n\r\nOpening browser to sign in...\r\n\r\nBrowser didn't open? Use the url below to sign in:\r\n\r\n")
    if MODE == "unwrapped":
        out(f"\x1b]8;;{LINK}\x1b\\{LINK}\x1b]8;;\x1b\\\r\n")
    elif MODE == "no_osc8":
        out(wrap(LINK, 80) + "\r\n")
    else:
        out("\x1b]8;;" + LINK + "\x1b\\" + wrap(LINK, 80) + "\x1b]8;;\x1b\\\r\n")
    out("\r\nPaste code here if prompted > ")
    if MODE == "die":
        return 1
    if MODE in ("auto", "end"):
        time.sleep(float(os.environ.get("FAKE_SIGNIN_DELAY", "1")))
        if MODE == "end":
            return 1
        out("\r\n\u2713 Long-lived authentication token created successfully!\r\n\r\nYour OAuth token (valid for 1 year):\r\n\r\n" + wrap(TOKEN, 79) + "\r\n\r\nStore this token securely.\r\n")
        return 0
    if MODE in ("alive", "inline", "noisy", "wrapped3", "short_end", "enter"):
        delay = float(os.environ.get("FAKE_SIGNIN_DELAY", "0.6"))
        if MODE == "enter":
            out("\r\nApproved in the browser. Press Enter to continue\r\n")
            sys.stdin.readline()
        else:
            time.sleep(delay)
        if MODE == "short_end":                      # a code-like string that is too short to be a real one, then the program ends: nothing is kept, the shape is logged
            out("\r\nYour OAuth token (valid for 1 year):\r\n\r\nsk-ant-oat01-SHORTSECRETVALUE123\r\n")
            return 1
        head = "\r\n\u2713 Long-lived authentication token created successfully!\r\n\r\nYour OAuth token (valid for 1 year):\r\n\r\n"
        if MODE == "inline":
            out(head.replace("\r\n\r\nYour OAuth token (valid for 1 year):\r\n\r\n", "\r\nYour OAuth token: ") + TOKEN + " Store this token securely.\r\n")
            return 0
        if MODE == "noisy":
            out(head + "\x1b[32m" + TOKEN + "\x1b[0m\r\r\n\x1b[2K\r\n")
            return 0
        if MODE == "wrapped3":
            out(head + "\r\n".join(TOKEN[i:i + 40] for i in range(0, len(TOKEN), 40)) + "\r\n\r\nStore this token securely.\r\n")
            return 0
        out(head + wrap(TOKEN, 79) + "\r\n")        # alive / enter: the code is printed, then the program waits for a key and never ends
        if MODE == "enter":
            return 0
        time.sleep(3600)
        return 0
    while True:
        line = sys.stdin.readline()
        if not line:
            return 1
        if "".join(line.split()) != CODE:
            continue                      # a wrong code: silence, as the real one
        if MODE == "slow":
            time.sleep(2)
        out("\r\n\x1b[32m✓ Long-lived authentication token created successfully!\x1b[0m\r\n\r\nYour OAuth token (valid for 1 year):\r\n\r\n")
        out((TOKEN if MODE == "unwrapped" else wrap(TOKEN, 79)) + "\r\n\r\nStore this token securely. You won't be able to see it again.\r\n"
            "Use this token by setting: export CLAUDE_CODE_OAUTH_TOKEN=<token>\r\n")
        return 0


if __name__ == "__main__":
    sys.exit(main())
