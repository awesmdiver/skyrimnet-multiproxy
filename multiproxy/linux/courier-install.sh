#!/usr/bin/env bash
# MultiProxy Courier: Linux installer (packaged as install.sh in the Courier's tarball). Run it from the folder the package was unpacked into:
#
#   tar xzf MultiProxy-Courier-<version>-linux.tar.gz && cd multiproxy-courier && ./install.sh
#
# This computer will run AI models for your main device, using its graphics card. The installer asks where to install, where settings
# and the pairing code go, and whether to start the Courier when you log in (Enter takes the answer shown in [brackets]); gives it its
# own private Python; sets it up as a background service (a systemd user service); starts it; and prints the pairing code your gaming
# device needs. There is no web page and no tray icon on Linux: after this you manage everything from your main device.
# Afterwards the `multiproxy-courier` command looks after it: multiproxy-courier help
#
# Options:
#   --yes, -y         ask nothing: take every answer shown
#   --dir DIR         where to install (skips that question)
#   --settings DIR    where settings and the pairing code go (skips that question)
#   --login           start it when you log in (skips that question)
#   --no-login        don't start it when you log in
#   --no-service      don't set up a background service; `multiproxy-courier start` runs it
#   --help            this text
#
# Run again from a newer package to update: it replaces the program and keeps your settings and pairing code. The questions then show
# your earlier answers as "[current: ...]"; Enter keeps them, and --yes takes them.
#
# Settings you can pass as environment variables, mostly for testing:
#   COURIER_PORT             the Courier's port on this computer (default 8110)
#   COURIER_DISCOVERY_PORT   the "I'm here" finder port (default 8099)
set -euo pipefail

SELF_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd -P)"
# shellcheck source=lib.sh
. "$SELF_DIR/lib.sh"
umask 077        # everything this installs, and everything it starts, is for the person alone (settings hold API keys and pairing codes)

INSTALL_DIR="${MULTIPROXY_DIR:-}"
SETTINGS_DIR="${COURIER_SETTINGS_DIR:-}"
PORT="${COURIER_PORT:-8110}"
DISCOVERY_PORT="${COURIER_DISCOVERY_PORT:-8099}"
LOGIN=""
SERVICE=yes
PATH_HINT=no

while [ $# -gt 0 ]; do
    case "$1" in
        --yes|-y) ASSUME_YES=yes; shift ;;
        --dir) INSTALL_DIR="$2"; shift 2 ;;
        --settings) SETTINGS_DIR="$2"; shift 2 ;;
        --login) LOGIN=yes; shift ;;
        --no-login) LOGIN=no; shift ;;
        --no-service) SERVICE=no; shift ;;
        --help|-h) sed -n '2,/^set -euo/p' "$0" | grep '^#' | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) say "The option '$1' isn't recognized. Try --help." >&2; exit 2 ;;
    esac
done

[ -f "$SELF_DIR/courier.py" ] || die "Run this from inside the unpacked Courier folder (the one with courier.py in it)."
check_python

# The Courier has no pointer file of its own for other programs, so the install leaves a small one just for itself: the earlier answers.
POINTER="${COURIER_INSTALL_INFO:-${XDG_CONFIG_HOME:-$HOME/.config}/skyrimnet-multiproxy-courier/install-info.json}"
prefill_dir "$POINTER" multiproxy-courier courier.py "$HOME/.local/share/multiproxy-courier"

say "Setting up MultiProxy Courier on this computer."
say "This computer will run AI models for your main device, using its graphics card."
say ""
if [ -z "$INSTALL_DIR" ]; then INSTALL_DIR="$(ask_folder "Where should it be installed?     " "$DEFAULT_DIR" "" install "$DIR_FROM")"; fi
INSTALL_DIR="$(untilde "$INSTALL_DIR")"
why="$(folder_problem "$INSTALL_DIR" install)"; [ -z "$why" ] || die "Cannot install to $INSTALL_DIR: $why"
INSTALL_DIR="$(cd "$INSTALL_DIR" && pwd -P)"
prefill_settings "$POINTER" multiproxy-courier courier.py "$INSTALL_DIR" "${XDG_CONFIG_HOME:-$HOME/.config}/multiproxy-courier"
if [ -z "$SETTINGS_DIR" ]; then SETTINGS_DIR="$(ask_folder "Where should settings and logs go?" "$DEFAULT_SETTINGS" "$INSTALL_DIR" settings "$SETTINGS_FROM")"; fi
SETTINGS_DIR="$(untilde "$SETTINGS_DIR")"
why="$(folder_problem "$SETTINGS_DIR" settings)"; [ -z "$why" ] || die "Cannot save settings in $SETTINGS_DIR: $why"
SETTINGS_DIR="$(cd "$SETTINGS_DIR" && pwd -P)"
prefill_login "$POINTER" multiproxy-courier.service
if [ "$SERVICE" = no ]; then LOGIN=no
elif [ -z "$LOGIN" ]; then if ask_yes "Start it when you log in?              " "$DEFAULT_LOGIN"; then LOGIN=yes; else LOGIN=no; fi; fi
say ""
printf 'Installing... '

copy_program "$SELF_DIR" "$INSTALL_DIR"
mark_folder "$INSTALL_DIR"
lock_settings "$SETTINGS_DIR"
chmod +x "$INSTALL_DIR/multiproxy-courier" "$INSTALL_DIR/install.sh"
printf '%s\n' "$SETTINGS_DIR" > "$INSTALL_DIR/settings-location.txt"
make_venv "$INSTALL_DIR" requirements-courier.txt
remember_install "$POINTER" "programFolder=$INSTALL_DIR" "settingsFolder=$SETTINGS_DIR" "startAtLogin=$LOGIN"       # the next update offers these again
link_command "$INSTALL_DIR/multiproxy-courier" multiproxy-courier
CODE="$(cd "$INSTALL_DIR" && .venv/bin/python courier.py code --config-dir "$SETTINGS_DIR" --port "$PORT" | tail -n 1)"
[ -n "$CODE" ] || die "Cannot save this computer's settings."
PAIRED_WITH="$(cd "$INSTALL_DIR" && .venv/bin/python courier.py paired --config-dir "$SETTINGS_DIR" | tail -n 1)"      # an update keeps its pairing: never show a code that was already used

STARTED=no
if [ "$SERVICE" = yes ] && systemd_user_ok; then
    UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
    mkdir -p "$UNIT_DIR"
    cat > "$UNIT_DIR/multiproxy-courier.service" <<UNIT
[Unit]
Description=MultiProxy Courier
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR
ExecStart=$(unit_quote "$INSTALL_DIR/.venv/bin/python") $(unit_quote "$INSTALL_DIR/courier.py") run --no-tray --no-ready-window --config-dir $(unit_quote "$SETTINGS_DIR") --discovery-port $DISCOVERY_PORT
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
UMask=0077

[Install]
WantedBy=default.target
UNIT
    systemctl --user daemon-reload
    if [ "$LOGIN" = yes ]; then systemctl --user enable multiproxy-courier.service >/dev/null 2>&1 || true; else systemctl --user disable multiproxy-courier.service >/dev/null 2>&1 || true; fi
    systemctl --user restart multiproxy-courier.service
    STARTED=service
    # A computer that shares its graphics card usually has nobody logged in: keep the service running without a login.
    if [ "$LOGIN" = yes ]; then loginctl enable-linger "$ME" >/dev/null 2>&1 || true; fi
fi
if [ "$STARTED" = no ]; then
    say ""
    if [ "$SERVICE" = yes ]; then say "This computer can't run background services (no systemd), so the Courier won't start by itself."; fi
    if ask_yes "Start it now?" yes; then
        COURIER_DISCOVERY_PORT="$DISCOVERY_PORT" "$INSTALL_DIR/multiproxy-courier" restart >/dev/null 2>&1 || true
        STARTED=plain
    fi
fi

if [ "$STARTED" != no ]; then
    # (the folders go in as arguments, never into the command text: a folder name with a quote in it must not become part of a command)
    if ! wait_until 60 bash -c 'cd "$1" && .venv/bin/python courier.py status --config-dir "$2" | grep -q "is running"' _ "$INSTALL_DIR" "$SETTINGS_DIR"; then
        say "done."
        die "Courier installed but isn't responding yet. Check what happened with: $INSTALL_DIR/multiproxy-courier logs"
    fi
fi
say "done."
hint_libgomp
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -qi 'status: active'; then
    say ""
    # This computer's real home-network range, from its own address (homes on 10.x or 172.16-31.x are common too).
    NET="$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.)' | head -1 | awk -F. '{print $1"."$2"."$3".0/24"}')"
    NET="${NET:-192.168.0.0/16}"
    if ufw status 2>/dev/null | grep -Eq "^${PORT}/tcp[[:space:]]+ALLOW" && ufw status 2>/dev/null | grep -Eq "^${DISCOVERY_PORT}/udp[[:space:]]+ALLOW"; then
        say "The firewall rule is already in place."
    else
        say "Your firewall is on. Let your main device reach this one by running:"
        say "  sudo ufw allow from $NET to any port $PORT proto tcp"
        say "  sudo ufw allow from $NET to any port $DISCOVERY_PORT proto udp"
    fi
fi

if [ -n "$PAIRED_WITH" ]; then
cat <<DONE

All set! This computer is now a MultiProxy Courier.

  Already paired with $PAIRED_WITH. Nothing more to do.

To pair with a different main device, run  multiproxy-courier new-code  to make a new pairing code.
Type  multiproxy-courier help  for what it can do.
Reaching this device over a VPN? Add its network first:  multiproxy-courier vpn-networks add 10.8.0.0/16
DONE
else
cat <<DONE

All set! This computer is now a MultiProxy Courier.

  Pairing code:  $CODE

Next: open Other devices in MultiProxy on your main device. Type the code above and click Pair.
Type  multiproxy-courier help  for what it can do.
Reaching this device over a VPN? Add its network first:  multiproxy-courier vpn-networks add 10.8.0.0/16
DONE
fi
if [ "$PATH_HINT" = yes ]; then
    say ""
    say "(If 'multiproxy-courier' isn't found, add ~/.local/bin to your PATH, or run it as $INSTALL_DIR/multiproxy-courier.)"
fi
if [ "$STARTED" = service ] && [ "$LOGIN" = yes ] && command -v loginctl >/dev/null 2>&1 && ! linger_on; then
    say ""
    say "To run it automatically even when nobody is logged in, run once:  loginctl enable-linger $ME"
fi
