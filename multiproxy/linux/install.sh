#!/usr/bin/env bash
# SkyrimNet MultiProxy: Linux installer. Run it from the folder the package was unpacked into:
#
#   tar xzf MultiProxy-<version>-linux.tar.gz && cd multiproxy && ./install.sh
#
# It asks three things (Enter takes the answer shown in [brackets]): where to install, where settings and logs go, and
# whether to start MultiProxy when you log in. Then it gives MultiProxy its own private Python, sets it up as a background
# service (a systemd user service) and starts it. Afterwards the `multiproxy` command looks after it: multiproxy help
#
# Options:
#   --yes, -y         ask nothing: take every answer shown
#   --dir DIR         where to install (skips that question)
#   --settings DIR    where settings and logs go (skips that question)
#   --login           start it when you log in (skips that question)
#   --no-login        don't start it when you log in
#   --no-service      don't set up a background service; `multiproxy start` runs it
#   --help            this text
#
# Run again from a newer package to update: it replaces the program and keeps your settings. The questions then show your earlier
# answers as "[current: ...]"; Enter keeps them, and --yes takes them.
set -euo pipefail

SELF_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd -P)"
# shellcheck source=lib.sh
. "$SELF_DIR/lib.sh"
umask 077        # everything this installs, and everything it starts, is for the person alone (settings hold API keys and pairing codes)

INSTALL_DIR="${MULTIPROXY_DIR:-}"
SETTINGS_DIR="${MULTIPROXY_SETTINGS_DIR:-}"
LOGIN=""            # yes | no | (empty: ask)
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

[ -f "$SELF_DIR/multiproxy.py" ] || die "Run this from inside the unpacked MultiProxy folder (the one with multiproxy.py in it)."
check_python

# An existing install is offered first, so running this again updates it in place.
POINTER="${MULTIPROXY_INSTALL_INFO:-${XDG_CONFIG_HOME:-$HOME/.config}/skyrimnet-multiproxy/install-info.json}"     # the file the install leaves for the mod and the tray: also its memory
prefill_dir "$POINTER" multiproxy multiproxy.py "$HOME/.local/share/multiproxy"

say "Setting up SkyrimNet MultiProxy on this computer."
say ""
if [ -z "$INSTALL_DIR" ]; then INSTALL_DIR="$(ask_folder "Where should it be installed?     " "$DEFAULT_DIR" "" install "$DIR_FROM")"; fi
INSTALL_DIR="$(untilde "$INSTALL_DIR")"
why="$(folder_problem "$INSTALL_DIR" install)"; [ -z "$why" ] || die "Cannot install to $INSTALL_DIR: $why"
INSTALL_DIR="$(cd "$INSTALL_DIR" && pwd -P)"
prefill_settings "$POINTER" multiproxy multiproxy.py "$INSTALL_DIR" "${XDG_CONFIG_HOME:-$HOME/.config}/multiproxy"
if [ -z "$SETTINGS_DIR" ]; then SETTINGS_DIR="$(ask_folder "Where should settings and logs go?" "$DEFAULT_SETTINGS" "$INSTALL_DIR" settings "$SETTINGS_FROM")"; fi
SETTINGS_DIR="$(untilde "$SETTINGS_DIR")"
why="$(folder_problem "$SETTINGS_DIR" settings)"; [ -z "$why" ] || die "Cannot save settings in $SETTINGS_DIR: $why"
SETTINGS_DIR="$(cd "$SETTINGS_DIR" && pwd -P)"
prefill_login "$POINTER" multiproxy.service
if [ "$SERVICE" = no ]; then LOGIN=no
elif [ -z "$LOGIN" ]; then if ask_yes "Start it when you log in?              " "$DEFAULT_LOGIN"; then LOGIN=yes; else LOGIN=no; fi; fi
say ""
printf 'Installing... '

copy_program "$SELF_DIR" "$INSTALL_DIR"
mark_folder "$INSTALL_DIR"
lock_settings "$SETTINGS_DIR"
chmod +x "$INSTALL_DIR/multiproxy" "$INSTALL_DIR/install.sh"
printf '%s\n' "$SETTINGS_DIR" > "$INSTALL_DIR/settings-location.txt"       # the program, the tray app and the Skyrim mod all follow this
export MULTIPROXY_SETTINGS_DIR="$SETTINGS_DIR"
make_venv "$INSTALL_DIR" requirements.txt
# The tray icon's files, only where a desktop could show one; a failure here is never a reason to stop.
if has_desktop; then "$INSTALL_DIR/.venv/bin/python" -m pip install -q --disable-pip-version-check -r "$INSTALL_DIR/requirements-tray.txt" >/dev/null 2>&1 || true; fi
(cd "$INSTALL_DIR" && .venv/bin/python multiproxy_cli.py pointer) || die "Cannot save MultiProxy's address file."
remember_install "$POINTER" "startAtLogin=$LOGIN"       # the next update offers this answer again
link_command "$INSTALL_DIR/multiproxy" multiproxy

STARTED=no
if [ "$SERVICE" = yes ] && systemd_user_ok; then
    UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
    mkdir -p "$UNIT_DIR"
    cat > "$UNIT_DIR/multiproxy.service" <<UNIT
[Unit]
Description=SkyrimNet MultiProxy
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR
Environment=MULTIPROXY_TRAY=1
Environment=$(unit_quote "MULTIPROXY_SETTINGS_DIR=$SETTINGS_DIR")
ExecStart=$(unit_quote "$INSTALL_DIR/.venv/bin/python") $(unit_quote "$INSTALL_DIR/multiproxy.py")
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
UMask=0077

[Install]
WantedBy=default.target
UNIT
    systemctl --user daemon-reload
    if [ "$LOGIN" = yes ]; then systemctl --user enable multiproxy.service >/dev/null 2>&1 || true; else systemctl --user disable multiproxy.service >/dev/null 2>&1 || true; fi
    systemctl --user restart multiproxy.service
    STARTED=service
fi
if has_desktop; then
    APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
    mkdir -p "$APPS"
    cat > "$APPS/skyrimnet-multiproxy.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=SkyrimNet MultiProxy
Comment=Show the MultiProxy tray icon
Exec=$(unit_quote "$INSTALL_DIR/multiproxy") tray
Icon=$INSTALL_DIR/app-icon.png
Terminal=false
Categories=Network;
DESKTOP
fi
if [ "$STARTED" = no ]; then
    say ""
    if [ "$SERVICE" = yes ]; then say "This computer can't run background services (no systemd), so MultiProxy won't start by itself."; fi
    if ask_yes "Start it now?" yes; then "$INSTALL_DIR/multiproxy" restart >/dev/null 2>&1 || true; STARTED=plain; fi      # (restart: an update must not leave the old program running)
fi

if [ "$STARTED" != no ]; then
    if wait_until 90 "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/multiproxy_cli.py" status; then
        PORT="$("$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/multiproxy_cli.py" port)"
        say "done."
        say "MultiProxy is running. Dashboard: http://127.0.0.1:$PORT"
    else
        say "done."
        die "MultiProxy installed but isn't responding yet. Check what happened with: $INSTALL_DIR/multiproxy logs"
    fi
else
    say "done."
    say "MultiProxy is installed. Start it with:  $INSTALL_DIR/multiproxy start"
fi
say "Type  multiproxy help  to see what it can do."

if [ "$PATH_HINT" = yes ]; then
    say ""
    say "(If 'multiproxy' isn't found, add ~/.local/bin to your PATH, or run it as $INSTALL_DIR/multiproxy.)"
fi
if [ "$STARTED" = service ] && [ "$LOGIN" = yes ] && command -v loginctl >/dev/null 2>&1 && ! linger_on; then
    say ""
    say "To start it automatically on boot, before anyone logs in, run once:  loginctl enable-linger $ME"
fi
hint_libgomp
