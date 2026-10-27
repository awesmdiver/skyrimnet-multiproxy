#!/usr/bin/env bash
# Shared by the two Linux installers (install.sh in MultiProxy's tarball and in the Courier's). Sourced, never run on its own.
#
# Plain-spoken helpers and the questions the installers ask. A question shows its default in [brackets]; Enter takes it, and
# --yes takes every default without asking. With no keyboard at all (a script, a pipe) every question takes its default.

say()  { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
die()  { printf '\nSetup stopped: %s\n' "$*" >&2; exit 1; }

ASSUME_YES="${ASSUME_YES:-no}"
ME="${USER:-$(id -un)}"       # (USER isn't set in every environment: cron, a container, a service)

# /home/you/x shown as ~/x (the way the question reads), and ~/x typed back as /home/you/x.
tilde() { case "$1" in "$HOME") printf '~' ;; "$HOME"/*) printf '~/%s' "${1#"$HOME"/}" ;; *) printf '%s' "$1" ;; esac; }
untilde() { case "$1" in "~") printf '%s' "$HOME" ;; "~/"*) printf '%s/%s' "$HOME" "${1#"~/"}" ;; *) printf '%s' "$1" ;; esac; }
trim() { local s="$1"; s="${s#"${s%%[![:space:]]*}"}"; s="${s%"${s##*[![:space:]]}"}"; printf '%s' "$s"; }

have_keyboard() { [ -t 0 ] || { [ -r /dev/tty ] && : 2>/dev/null </dev/tty; }; }
read_answer() {
    # The answer comes from the keyboard even when the script itself is piped in.
    local a=""
    if [ -t 0 ]; then read -r a || true; else read -r a </dev/tty || true; fi
    printf '%s' "$a"
}
asking() { [ "$ASSUME_YES" != yes ] && have_keyboard; }

MARKER=".multiproxy-folder"      # written into every folder the installers use: uninstall deletes a folder only when this is in it

folder_problem() {
    # folder_problem DIR [install|settings] : the plain reason a folder can't be used (nothing printed when it can be made and written to).
    # Uninstall deletes the install folder, so a folder is only used when it is new, empty, or one these installers made before: never your home
    # folder, a system folder, or a folder that already holds your own files.
    local d="$1" kind="${2:-}" err real
    case "$d" in /*) ;; *) d="$PWD/$d" ;; esac
    real="$(cd "$d" 2>/dev/null && pwd -P || printf '%s' "$d")"
    case "${real%/}" in
        ""|/bin|/boot|/dev|/etc|/home|/lib|/lib32|/lib64|/media|/mnt|/opt|/proc|/root|/run|/sbin|/srv|/sys|/tmp|/usr|/var|"$HOME"|"$HOME/.config"|"$HOME/.local"|"$HOME/.local/share"|"$HOME/.local/bin"|"$HOME/.cache"|"$HOME/.ssh"|"$HOME/Desktop"|"$HOME/Documents"|"$HOME/Downloads")
            printf '%s' "it is too important to use"; return ;;
    esac
    if [ -n "$kind" ] && [ -d "$d" ] && [ -n "$(ls -A "$d" 2>/dev/null)" ] && [ ! -f "$d/$MARKER" ]; then
        case "$kind" in
            install) [ -f "$d/multiproxy.py" ] || [ -f "$d/courier.py" ] || { printf '%s' "it already has other files in it"; return; } ;;
            settings) [ -f "$d/config.json" ] || [ -f "$d/courier.json" ] || [ -f "$d/multiproxy.ini" ] || { printf '%s' "it already has other files in it"; return; } ;;
        esac
    fi
    err="$(mkdir -p "$d" 2>&1)" || { printf '%s' "${err##*: }"; return; }
    [ -d "$d" ] && [ -w "$d" ] || printf '%s' "Permission denied"
}

mark_folder() { : > "$1/$MARKER"; }

lock_settings() {
    # The settings folder holds API keys and pairing codes: only the person may enter it, and the files in it are theirs alone.
    local d="$1" f
    chmod 700 "$d" && mark_folder "$d"
    for f in "$d"/config.json "$d"/courier.json "$d"/multiproxy.ini "$d"/speed_history.db "$d"/endpoint.json "$d"/*.log "$d"/*.log.* "$d"/*.out "$d"/*.pid; do
        [ -f "$f" ] && chmod 600 "$f"
    done
    return 0
}

json_field() {
    # json_field FILE KEY : the text (or yes/no for a true/false) stored under a top-level key of a JSON file. Prints nothing when the file is
    # missing, damaged, or has no such value: a settings file in any state must never stop an install, the next place to look is tried instead.
    [ -f "$1" ] || return 0
    python3 -I -c '
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as f:
        d = json.load(f)
    v = d.get(sys.argv[2]) if isinstance(d, dict) else None
    if isinstance(v, bool):
        print("yes" if v else "no")
    elif isinstance(v, str) and "\n" not in v and "\r" not in v:
        print(v.strip())
except Exception:
    pass
' "$1" "$2" 2>/dev/null || true
}

remember_install() {
    # remember_install FILE key=value... : adds those answers to the small file an install leaves behind (made if it isn't there; a damaged one is
    # started over). Only the person can read it. yes/no are saved as true/false. A failure here is never a reason to stop.
    python3 -I -c '
import json, os, sys
path = sys.argv[1]
d = {}
try:
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    if not isinstance(d, dict):
        d = {}
except Exception:
    d = {}
for a in sys.argv[2:]:
    k, _, v = a.partition("=")
    d[k] = True if v == "yes" else False if v == "no" else v
os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
tmp = path + ".tmp"
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump(d, f, indent=2)
os.replace(tmp, path)
' "$@" >/dev/null 2>&1 || true
}

full_path() { case "$1" in /*) local p="${1%/}"; printf '%s' "${p:-/}" ;; esac; }       # the folder as a clean full path, or nothing when it isn't one

location_in() {
    # location_in DIR : the settings folder an install names in its settings-location.txt (nothing when there is none, or it isn't a full path).
    [ -f "$1/settings-location.txt" ] || return 0
    full_path "$(trim "$(tr -d '"\r\n' < "$1/settings-location.txt" 2>/dev/null || true)")"
}

# An update asks the same questions as a first install, but each answer starts as what the person chose last time. Where a value comes from,
# first hit wins: (a) the settings the program itself follows now, (b) the small file the earlier install left (its pointer), (c) an older install
# found on this computer, (d) the defaults. A newer value (a) is never overridden by an older one (b). A remembered folder that is gone is still
# offered (never swapped for another one without saying so): ask_folder says so.
# DIR_FROM and SETTINGS_FROM say where a remembered value came from (empty: it is the default), and the question shows "[current: ...]" for it.
DIR_FROM=""
SETTINGS_FROM=""

prefill_dir() {
    # prefill_dir POINTER COMMAND PROGRAM_FILE DEFAULT : sets DEFAULT_DIR.
    local pointer="$1" cmd="$2" prog="$3" p link
    DEFAULT_DIR="$4"; DIR_FROM=""
    p="$(full_path "$(json_field "$pointer" programFolder)")"
    if [ -n "$p" ]; then DEFAULT_DIR="$p"; DIR_FROM=pointer; return 0; fi
    link="$(readlink -f "$HOME/.local/bin/$cmd" 2>/dev/null || true)"
    if [ -n "$link" ] && [ -f "$(dirname "$link")/$prog" ]; then DEFAULT_DIR="$(dirname "$link")"; DIR_FROM=install; fi
}

prefill_settings() {
    # prefill_settings POINTER COMMAND PROGRAM_FILE INSTALL_DIR DEFAULT : sets DEFAULT_SETTINGS (INSTALL_DIR is the folder just chosen).
    local pointer="$1" cmd="$2" prog="$3" idir="$4" p link
    DEFAULT_SETTINGS="$5"; SETTINGS_FROM=""
    p="$(location_in "$idir")"
    if [ -n "$p" ]; then DEFAULT_SETTINGS="$p"; SETTINGS_FROM=settings; return 0; fi
    p="$(full_path "$(json_field "$pointer" settingsFolder)")"
    if [ -n "$p" ]; then DEFAULT_SETTINGS="$p"; SETTINGS_FROM=pointer; return 0; fi
    link="$(readlink -f "$HOME/.local/bin/$cmd" 2>/dev/null || true)"
    if [ -n "$link" ] && [ -f "$(dirname "$link")/$prog" ]; then
        p="$(location_in "$(dirname "$link")")"
        if [ -n "$p" ]; then DEFAULT_SETTINGS="$p"; SETTINGS_FROM=install; fi
    fi
}

prefill_login() {
    # prefill_login POINTER UNIT : sets DEFAULT_LOGIN (yes or no). What the background service really does now is the newest answer; then the pointer.
    local pointer="$1" unit="$2" state="" p
    DEFAULT_LOGIN=yes
    if [ -f "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/$unit" ] && command -v systemctl >/dev/null 2>&1; then
        state="$(systemctl --user is-enabled "$unit" 2>/dev/null || true)"
    fi
    case "$state" in enabled) DEFAULT_LOGIN=yes; return 0 ;; disabled) DEFAULT_LOGIN=no; return 0 ;; esac
    p="$(json_field "$pointer" startAtLogin)"
    case "$p" in yes|no) DEFAULT_LOGIN="$p" ;; esac
}

ask_folder() {
    # ask_folder "The question" DEFAULT [also-refuse-inside] [install|settings] [remembered-from]  -> prints the chosen folder (full path).
    # With a 5th word the default is what an earlier install used: the question says "[current: ...]", and a folder that is gone is said so, once.
    local question="$1" default="$2" inside="${3:-}" kind="${4:-}" from="${5:-}" typed why shown
    shown="$(tilde "$default")"; [ -z "$from" ] || shown="current: $shown"
    if [ -n "$from" ] && [ ! -d "$default" ]; then say "That folder is missing. Pick another, or one will be created." >&2; fi
    if ! asking; then printf '%s' "$default"; return; fi
    while true; do
        printf '%s [%s] ' "$question" "$shown" >&2
        typed="$(trim "$(read_answer)")"
        [ -n "$typed" ] || typed="$default"
        typed="$(untilde "$typed")"
        case "$typed" in /*) ;; *) say "Please type a full folder path, like /home/you/multiproxy." >&2; continue ;; esac
        typed="${typed%/}"; [ -n "$typed" ] || typed="/"
        if [ -n "$inside" ]; then
            case "$typed/" in "$inside"/*) say "That folder is inside the program folder and would be deleted by an update. Please pick another." >&2; continue ;; esac
            case "$inside/" in "$typed"/*) say "That folder contains the program folder. Please pick another." >&2; continue ;; esac
        fi
        why="$(folder_problem "$typed" "$kind")"
        if [ -n "$why" ]; then say "Can't save to that folder ($why). Please pick another." >&2; continue; fi
        printf '%s' "$typed"; return
    done
}

ask_yes() {
    # ask_yes "The question" yes|no  -> exit status 0 for yes. The default is the capital letter: [Y/n] or [y/N].
    local question="$1" default="$2" a hint
    if [ "$default" = yes ]; then hint="Y/n"; else hint="y/N"; fi
    if ! asking; then [ "$default" = yes ]; return; fi
    while true; do
        printf '%s [%s] ' "$question" "$hint" >&2
        a="$(trim "$(read_answer)")"
        case "$a" in
            "") [ "$default" = yes ]; return ;;
            [Yy]|[Yy][Ee][Ss]) return 0 ;;
            [Nn]|[Nn][Oo]) return 1 ;;
            *) say "Please answer y or n." >&2 ;;
        esac
    done
}

check_python() {
    command -v python3 >/dev/null 2>&1 || die "Python 3 is missing. Please install it and try again."
    python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || die "Python is too old (you need version 3.10 or newer). Please update it and try again."
}

systemd_user_ok() {
    # Can this computer run a background service as the person who is installing? (systemd with a user session.)
    command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1
}

unit_quote() {
    # A path as one word in a systemd unit's Exec line or Environment line: a folder name with a space in it (or a % or $, which systemd reads as
    # its own marks) must not split into two. Prints the path double-quoted with those escaped.
    local s="${1//%/%%}"
    s="${s//\$/\$\$}"
    s="${s//\\/\\\\}"
    s="${s//\"/\\\"}"
    printf '"%s"' "$s"
}

linger_on() { [ "$(loginctl show-user "$ME" --property=Linger --value 2>/dev/null || true)" = yes ]; }

has_desktop() {
    # A graphical session (so a tray icon could be shown). Never a reason to fail an install.
    [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]
}

make_venv() {
    # make_venv INSTALL_DIR REQUIREMENTS_FILE...
    local dir="$1"; shift
    local req=()
    for r in "$@"; do req+=(-r "$dir/$r"); done
    if [ ! -x "$dir/.venv/bin/python" ]; then
        python3 -m venv "$dir/.venv" >/dev/null 2>&1 || die "Cannot set up Python. Please install python3-venv (Debian and Ubuntu) and try again."
    fi
    "$dir/.venv/bin/python" -m pip install -q --disable-pip-version-check --upgrade pip >/dev/null 2>&1 || true
    local log; log="$(mktemp)"
    if ! "$dir/.venv/bin/python" -m pip install -q --disable-pip-version-check "${req[@]}" >"$log" 2>&1; then
        say "$(tail -n 8 "$log")" >&2; rm -f "$log"
        die "Cannot install the files MultiProxy needs. Check your internet connection and try again."
    fi
    rm -f "$log"
}

copy_program() {
    # copy_program FROM TO : the package's files into the install folder (an update replaces the program, never the player's settings, which live elsewhere).
    local from="$1" to="$2"
    mkdir -p "$to"
    if [ "$(cd "$from" && pwd -P)" = "$(cd "$to" && pwd -P)" ]; then return 0; fi     # already there: an update run from the install folder itself
    cp -R "$from"/. "$to"/
}

wait_until() {
    # wait_until SECONDS command... : true as soon as the command succeeds.
    local n="$1"; shift
    local i
    for ((i = 0; i < n * 2; i++)); do
        if "$@" >/dev/null 2>&1; then return 0; fi
        sleep 0.5
    done
    return 1
}

pid_is() {
    # pid_is PID WORD : is PID a running program whose command line has WORD in it? (A pid file can be stale, and the number may now be someone else's program.)
    case "$1" in ""|*[!0-9]*) return 1 ;; esac
    [ -r "/proc/$1/cmdline" ] && tr '\0' ' ' < "/proc/$1/cmdline" | grep -qF -- "$2"
}

link_command() {
    # link_command TARGET NAME : puts NAME in ~/.local/bin (and says so when that folder isn't on the PATH yet).
    mkdir -p "$HOME/.local/bin" || return 0
    local dest="$HOME/.local/bin/$2"
    # Only ever replace our own link: a program of the same name that is yours is left alone.
    if [ -e "$dest" ] || [ -L "$dest" ]; then
        case "$(basename "$(readlink -f "$dest" 2>/dev/null)")" in "$2") ;; *) say "(~/.local/bin/$2 is something else, so it was left alone. Run it as $1.)"; return 0 ;; esac
    fi
    ln -sf "$1" "$dest"
    case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) PATH_HINT=yes ;; esac
}

hint_libgomp() {
    # The built-in AI engine needs the OpenMP library; a minimal Linux install may not have it.
    if command -v ldconfig >/dev/null 2>&1 && ! ldconfig -p 2>/dev/null | grep -q 'libgomp.so.1'; then
        say ""
        say "Almost done: running Local models requires one more system library."
        say "Install it using your package manager (search for libgomp1 or libgomp)."
    fi
}
