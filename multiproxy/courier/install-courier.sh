#!/usr/bin/env bash
# MultiProxy Courier: Linux installer.
#
#   curl -fsSL https://github.com/awesmdiver/skyrimnet-multiproxy/releases/latest/download/install-courier.sh | bash
#
# What it does: downloads MultiProxy Courier, sets it up in its own folder with its own private Python
# environment, makes it start with the computer (a systemd user service), starts it, and prints the
# pairing code your main device needs. There is no web page and no tray icon on Linux: after this you
# manage everything from your main device.
#
# Options (add them after `bash -s --` when piping from curl):
#   --dir DIR       where to put it. Without this, setup asks first, offering ~/.local/share/multiproxy-courier (or the
#                   folder of an existing install, so running setup again updates it in place)
#   --yes, -y       ask nothing: keep the suggested folder (for scripted installs)
#
# Run from inside the folder the Courier is already installed in (after copying newer files over it), it
# skips the download and updates in place. Afterwards: multiproxy-courier start|stop|restart|status
#   --no-service    don't set up start-with-the-computer; just start it now
#   --help          this text
#
# Settings you can pass as environment variables, mostly for testing:
#   MULTIPROXY_ARCHIVE_URL   where to download the program from (a file:// address works too)
#   COURIER_PORT             the Courier's port on this computer (default 8110)
#   COURIER_DISCOVERY_PORT   the "I'm here" finder port (default 8099)
set -euo pipefail

ARCHIVE_URL="${MULTIPROXY_ARCHIVE_URL:-https://github.com/awesmdiver/skyrimnet-multiproxy/releases/latest/download/MultiProxy-Courier.zip}"
INSTALL_DIR="${MULTIPROXY_DIR:-}"          # empty: ask (or keep the suggestion with --yes)
ASSUME_YES=no
PORT="${COURIER_PORT:-8110}"
DISCOVERY_PORT="${COURIER_DISCOVERY_PORT:-8099}"
SERVICE=yes

while [ $# -gt 0 ]; do
    case "$1" in
        --dir) INSTALL_DIR="$2"; shift 2 ;;
        --yes|-y) ASSUME_YES=yes; shift ;;
        --no-service) SERVICE=no; shift ;;
        --help|-h) sed -n '2,/^set -euo/p' "$0" 2>/dev/null | grep '^#' | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "The option '$1' isn't recognized. Try --help." >&2; exit 2 ;;
    esac
done

say()  { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
die()  { printf '\nSetup stopped: %s\n' "$*" >&2; exit 1; }

# ---- Where to install ---------------------------------------------------------------------
SELF_DIR=""
if [ -f "${BASH_SOURCE[0]:-}" ]; then SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"; fi     # (empty when piped in: curl ... | bash)
default_install_dir() {
    # An existing install (the folder this script is in, here, or where older versions put it) is offered first so running setup again
    # updates it in place.
    if [ -n "$SELF_DIR" ] && [ -f "$SELF_DIR/courier.py" ] && [ -x "$SELF_DIR/.venv/bin/python" ]; then printf '%s' "$SELF_DIR"; return; fi
    for d in "$HOME/.local/share/multiproxy-courier" "$HOME/multiproxy-courier"; do
        if [ -f "$d/courier.py" ]; then printf '%s' "$d"; return; fi
    done
    printf '%s' "$HOME/.local/share/multiproxy-courier"
}
folder_problem() {
    # Prints the plain reason a folder can't be used (nothing when it can be made and written to).
    local d="$1" err
    err="$(mkdir -p "$d" 2>&1)" || { printf '%s' "${err##*: }"; return; }
    [ -d "$d" ] && [ -w "$d" ] || printf '%s' "Permission denied"
}
have_keyboard() { [ -t 0 ] || { [ -r /dev/tty ] && : 2>/dev/null </dev/tty; }; }
read_answer() {
    # The answer comes from the keyboard even when this script itself is piped in (curl ... | bash).
    local a=""
    if [ -t 0 ]; then read -r a || true; else read -r a </dev/tty || true; fi
    printf '%s' "$a"
}
resolve_install_dir() {
    # $INSTALL_DIR if it was given, else the suggestion (with --yes or no keyboard), else what was typed (Enter keeps the suggestion).
    local default why typed
    default="$(default_install_dir)"
    if [ -n "$INSTALL_DIR" ]; then
        why="$(folder_problem "$INSTALL_DIR")"; [ -z "$why" ] || die "Cannot install to $INSTALL_DIR: $why"
        return
    fi
    if [ "$ASSUME_YES" = yes ] || ! have_keyboard; then          # no keyboard means no question
        why="$(folder_problem "$default")"; [ -z "$why" ] || die "Cannot install to $default: $why"
        INSTALL_DIR="$default"; return
    fi
    while true; do
        say ""
        say "Where should the Courier be installed?"
        if [ -f "$default/courier.py" ]; then say "Courier is already installed here. Press Enter to update:"; else say "Press Enter to use:"; fi
        say "  $default"
        say "Or type another folder and press Enter:"
        typed="$(read_answer)" || { INSTALL_DIR="$default"; return; }
        typed="${typed#"${typed%%[![:space:]]*}"}"; typed="${typed%"${typed##*[![:space:]]}"}"
        [ -n "$typed" ] || typed="$default"
        case "$typed" in "~") typed="$HOME" ;; "~/"*) typed="$HOME/${typed#"~/"}" ;; esac
        case "$typed" in /*) ;; *) say "Please type a full folder path, like /home/you/courier."; continue ;; esac
        why="$(folder_problem "$typed")"
        if [ -n "$why" ]; then say "Can't save to that folder ($why). Please pick another."; continue; fi
        INSTALL_DIR="$typed"; return
    done
}
in_place() {
    # Is the folder being installed into the folder this script (and the new files beside it) already sits in? Then there is nothing to copy.
    [ -n "$SELF_DIR" ] && [ -f "$SELF_DIR/courier.py" ] && [ "$(cd "$INSTALL_DIR" 2>/dev/null && pwd -P)" = "$SELF_DIR" ]
}
write_launcher() {
    # `multiproxy-courier start|stop|restart|status`, in the install folder and linked into ~/.local/bin.
    cat > "$INSTALL_DIR/multiproxy-courier" <<'LAUNCHER'
#!/usr/bin/env bash
# multiproxy-courier start|stop|restart|status : manage the MultiProxy Courier on this computer.
DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
PY="$DIR/.venv/bin/python"
UNIT=multiproxy-courier.service
have_unit() { command -v systemctl >/dev/null 2>&1 && systemctl --user cat "$UNIT" >/dev/null 2>&1; }
running() { (cd "$DIR" && "$PY" courier.py status 2>/dev/null | grep -q 'is running'); }
start() {
    if have_unit; then systemctl --user start "$UNIT"; return; fi
    if running; then echo "Courier is already running."; return; fi
    (cd "$DIR" && "$PY" - run --no-tray --no-ready-window <<'PYEND'
import subprocess, sys
out = open("courier.out", "ab")
subprocess.Popen([sys.executable, "courier.py"] + sys.argv[1:], stdin=subprocess.DEVNULL, stdout=out, stderr=out, start_new_session=True)
PYEND
    )
}
stop() {
    if have_unit; then systemctl --user stop "$UNIT"; return; fi
    for p in $(pgrep -f "courier.py run" 2>/dev/null); do
        [ "$(readlink -f "/proc/$p/cwd" 2>/dev/null)" = "$DIR" ] && kill "$p"
    done
}
case "${1:-}" in
    start) start ;;
    stop) stop ;;
    restart) stop; sleep 1; start ;;
    status) (cd "$DIR" && "$PY" courier.py status); have_unit && echo "Service: $(systemctl --user is-active "$UNIT")" ;;
    vpn-networks) shift; (cd "$DIR" && "$PY" courier.py vpn-networks "$@") ;;
    *) echo "Usage: multiproxy-courier start|stop|restart|status|vpn-networks"; exit 2 ;;
esac
LAUNCHER
    chmod +x "$INSTALL_DIR/multiproxy-courier"
    mkdir -p "$HOME/.local/bin" && ln -sf "$INSTALL_DIR/multiproxy-courier" "$HOME/.local/bin/multiproxy-courier"
}
[ "${COURIER_INSTALL_LIB:-}" = 1 ] && return 0 2>/dev/null     # (tests load the functions above without running setup)

say "Setting up MultiProxy Courier on this computer."
say "This computer will run AI models for your main device, using its graphics card."

# ---- Where to put it (asked before anything is copied) -------------------------------------
resolve_install_dir

# ---- Python -------------------------------------------------------------------------------
step "Checking Python…"
command -v python3 >/dev/null 2>&1 || die "Python 3 is missing. Please install it and try again."
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || die "Python is too old (Courier needs version 3.10 or newer). Please update it and try again."
say "Found $(python3 --version)."

# ---- Download -----------------------------------------------------------------------------
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
if in_place; then
    step "Updating Courier in this folder."
    say "(The new files are already here, so nothing needs downloading.)"
else
step "Downloading MultiProxy Courier…"
if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$ARCHIVE_URL" -o "$TMP/package.zip" || die "Cannot download $ARCHIVE_URL. Please check your internet connection."
elif command -v wget >/dev/null 2>&1; then
    wget -q "$ARCHIVE_URL" -O "$TMP/package.zip" || die "Cannot download $ARCHIVE_URL. Please check your internet connection."
else
    die "Either curl or wget is needed to download files. Please install one and try again."
fi

# ---- Unpack (keeping your own settings if this is an update) -----------------------------------
step "Extracting files to $INSTALL_DIR..."
python3 - "$TMP/package.zip" "$TMP/unpacked" <<'PY' || die "The downloaded file is invalid or corrupted."
import sys, zipfile
src, dest = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(src) as z:
    z.extractall(dest)
PY
SRC="$TMP/unpacked"
[ -f "$SRC/courier.py" ] || { sub="$(find "$SRC" -maxdepth 2 -name courier.py | head -1)"; [ -n "$sub" ] && SRC="$(dirname "$sub")"; }
[ -f "$SRC/courier.py" ] || die "The download is missing files. Please try again."
mkdir -p "$INSTALL_DIR"
cp -r "$SRC"/. "$INSTALL_DIR"/
fi
write_launcher

# ---- Private Python environment ---------------------------------------------------------------
step "Setting up Python…"
if [ ! -x "$INSTALL_DIR/.venv/bin/python" ]; then
    python3 -m venv "$INSTALL_DIR/.venv" || die "Cannot set up Python. Please install python3-venv and try again."
fi
"$INSTALL_DIR/.venv/bin/python" -m pip install -q --upgrade pip >/dev/null 2>&1 || true
"$INSTALL_DIR/.venv/bin/python" -m pip install -q -r "$INSTALL_DIR/requirements-courier.txt" || die "Cannot install the required files. Check your internet connection and try again."

# ---- Settings + pairing code -------------------------------------------------------------------
step "Generating this computer's pairing code…"
CODE="$(cd "$INSTALL_DIR" && .venv/bin/python courier.py code --port "$PORT" | tail -n 1)"
[ -n "$CODE" ] || die "Cannot save settings on this computer."

RUN_ARGS="run --no-tray --no-ready-window --discovery-port $DISCOVERY_PORT"

# ---- Start with the computer ---------------------------------------------------------------
STARTED=no
if [ "$SERVICE" = yes ] && command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
    step "Setting Courier to start automatically on boot..."
    UNIT_DIR="$HOME/.config/systemd/user"
    mkdir -p "$UNIT_DIR"
    cat > "$UNIT_DIR/multiproxy-courier.service" <<UNIT
[Unit]
Description=MultiProxy Courier
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/.venv/bin/python $INSTALL_DIR/courier.py $RUN_ARGS
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
UNIT
    systemctl --user daemon-reload
    systemctl --user enable multiproxy-courier.service >/dev/null 2>&1
    systemctl --user restart multiproxy-courier.service
    STARTED=service
    loginctl enable-linger "$USER" >/dev/null 2>&1 || true
else
    step "Starting Courier…"
    if [ "$SERVICE" = yes ]; then
        say "(This computer cannot start Courier automatically on boot.)"
    fi
    # Started from Python in its own session, so it is fully detached: a shell `&` here kept this script (and anything it was
    # piped into, like `curl ... | bash`) waiting for it to exit.
    (cd "$INSTALL_DIR" && .venv/bin/python - $RUN_ARGS <<'PY'
import subprocess, sys
out = open("courier.out", "ab")
subprocess.Popen([sys.executable, "courier.py"] + sys.argv[1:], stdin=subprocess.DEVNULL, stdout=out, stderr=out, start_new_session=True)
PY
    )
    STARTED=background
fi

# ---- Wait for it, then show the code ------------------------------------------------------------
step "Waiting for Courier to start..."
UP=no
for _ in $(seq 1 60); do
    if (cd "$INSTALL_DIR" && .venv/bin/python courier.py status 2>/dev/null | grep -q 'is running'); then UP=yes; break; fi
    sleep 0.5
done
[ "$UP" = yes ] || die "Courier didn't start. Check the logs in $INSTALL_DIR or try setup again."

# The built-in AI engine needs the OpenMP library; a minimal Linux install may not have it.
if command -v ldconfig >/dev/null 2>&1 && ! ldconfig -p 2>/dev/null | grep -q 'libgomp.so.1'; then
    say ""
    say "Almost done: running Local models requires one more system library."
    say "Install it using your package manager (search for libgomp1 or libgomp)."
fi
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -qi 'status: active'; then
    say ""
    # This computer's real home-network range, from its own address (not a fixed 192.168.x: homes on 10.x or 172.16-31.x are common too)
    NET="$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.)' | head -1 | awk -F. '{print $1"."$2"."$3".0/24"}')"
    NET="${NET:-192.168.0.0/16}"
    # Already allowed (an update, or a re-install)? `ufw status` lists the rules; only the two this Courier needs are looked for.
    if ufw status 2>/dev/null | grep -Eq "^${PORT}/tcp[[:space:]]+ALLOW" && ufw status 2>/dev/null | grep -Eq "^${DISCOVERY_PORT}/udp[[:space:]]+ALLOW"; then
        say "The firewall rule is already in place."
    else
        say "Your firewall is on. Let your main device reach this one by running:"
        say "  sudo ufw allow from $NET to any port $PORT proto tcp"
        say "  sudo ufw allow from $NET to any port $DISCOVERY_PORT proto udp"
    fi
fi

cat <<DONE

All set! This computer is now a MultiProxy Courier.

  Pairing code:  $CODE

Next: open Other devices in MultiProxy on your main device. Type the code above and click Pair.

(Courier will keep running in the background$( [ "$STARTED" = service ] && echo ' and will start with your computer' ).)
To start or stop it later, run: multiproxy-courier start | stop
(If that command isn't found, run it as $INSTALL_DIR/multiproxy-courier.)
Reaching this device over a VPN? Add its network first:  multiproxy-courier vpn-networks add 10.8.0.0/16
DONE
