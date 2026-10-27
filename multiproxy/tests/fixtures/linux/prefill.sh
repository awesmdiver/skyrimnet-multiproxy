#!/usr/bin/env bash
# What an update's questions start from. Usage: prefill.sh REPO_ROOT HOME_DIR CASE ; HOME_DIR is a scratch folder used as the whole home.
# Prints one line per answer: "name: value" (the folder or yes/no, then where it came from). Never touches anything outside HOME_DIR.
set -u
. "$1/linux/lib.sh"
export HOME="$(cd "$2" && pwd)"
unset XDG_CONFIG_HOME
CASE="$3"
POINTER="$HOME/.config/skyrimnet-multiproxy/install-info.json"
mkdir -p "$HOME/.config/skyrimnet-multiproxy" "$HOME/.local/bin"

report() {
    prefill_dir "$POINTER" multiproxy multiproxy.py "$HOME/.local/share/multiproxy"
    echo "dir: $DEFAULT_DIR from=${DIR_FROM:-default}"
    prefill_settings "$POINTER" multiproxy multiproxy.py "${1:-$DEFAULT_DIR}" "$HOME/.config/multiproxy"
    echo "settings: $DEFAULT_SETTINGS from=${SETTINGS_FROM:-default}"
    prefill_login "$POINTER" multiproxy.service
    echo "login: $DEFAULT_LOGIN"
}

case "$CASE" in
first)                  # nothing there yet
    report ;;
pointer)                # an earlier install left its pointer; both folders are there
    mkdir -p "$HOME/apps/mp" "$HOME/cfg/mp"
    printf '{"programFolder": "%s", "settingsFolder": "%s", "startAtLogin": false}\n' "$HOME/apps/mp" "$HOME/cfg/mp" > "$POINTER"
    report ;;
newer-settings)         # the program's own settings-location.txt names a newer folder than the pointer does
    mkdir -p "$HOME/apps/mp" "$HOME/cfg/old" "$HOME/cfg/new"
    printf '{"programFolder": "%s", "settingsFolder": "%s"}\n' "$HOME/apps/mp" "$HOME/cfg/old" > "$POINTER"
    printf '%s\n' "$HOME/cfg/new" > "$HOME/apps/mp/settings-location.txt"
    report ;;
stale)                  # the pointer names folders that are gone; they are offered, not replaced, and the question says so
    printf '{"programFolder": "%s", "settingsFolder": "%s"}\n' "$HOME/gone/mp" "$HOME/gone/cfg" > "$POINTER"
    report
    ASSUME_YES=yes
    echo "yes-answer: $(ask_folder 'Where?' "$DEFAULT_DIR" '' install pointer 2>&1)" ;;
fresh-no-note)          # a default that does not exist yet is NOT flagged (first install)
    ASSUME_YES=yes
    echo "answer: $(ask_folder 'Where?' "$HOME/.local/share/multiproxy" '' install '' 2>&1)" ;;
damaged)                # every file in a bad state: nothing crashes, the next place is tried
    printf '{ this is not json' > "$POINTER"
    mkdir -p "$HOME/apps/mp"
    printf 'not a folder\n' > "$HOME/apps/mp/settings-location.txt"
    report "$HOME/apps/mp"
    printf '["a", "list"]' > "$POINTER"
    report "$HOME/apps/mp"
    printf '{"programFolder": "relative/path", "settingsFolder": 7, "startAtLogin": "maybe"}' > "$POINTER"
    report "$HOME/apps/mp" ;;
older-install)          # no pointer, but the command link of an older install is there
    mkdir -p "$HOME/old/mp"
    : > "$HOME/old/mp/multiproxy.py"; printf '#!/bin/sh\n' > "$HOME/old/mp/multiproxy"; chmod +x "$HOME/old/mp/multiproxy"
    printf '%s\n' "$HOME/old/cfg" > "$HOME/old/mp/settings-location.txt"
    ln -s "$HOME/old/mp/multiproxy" "$HOME/.local/bin/multiproxy"
    [ -L "$HOME/.local/bin/multiproxy" ] || { echo nosymlink; exit 0; }
    prefill_dir "$POINTER" multiproxy multiproxy.py "$HOME/.local/share/multiproxy"
    echo "dir: $DEFAULT_DIR from=${DIR_FROM:-default}"
    prefill_settings "$POINTER" multiproxy multiproxy.py "$HOME/somewhere/else" "$HOME/.config/multiproxy"
    echo "settings: $DEFAULT_SETTINGS from=${SETTINGS_FROM:-default}" ;;
remember)               # remember_install merges, keeps what is there, replaces a damaged file, and is private
    printf '{"token": "keep-me"}' > "$POINTER"
    remember_install "$POINTER" "programFolder=$HOME/p q" "startAtLogin=no"
    cat "$POINTER"; echo
    printf 'garbage' > "$POINTER"
    remember_install "$POINTER" "startAtLogin=yes"
    cat "$POINTER"; echo
    remember_install "$HOME/new-dir/x/info.json" "a=b"
    cat "$HOME/new-dir/x/info.json"; echo ;;
prompt)                 # the question shows the remembered folder, and Enter takes it
    mkdir -p "$HOME/apps/mp"
    have_keyboard() { return 0; }
    read_answer() { printf ''; }
    got="$(ask_folder 'Where?' "$HOME/apps/mp" '' install pointer 2>"$HOME/prompt.txt")"
    echo "answer: $got"
    sed "s|$HOME|HOME|g" "$HOME/prompt.txt" ;;
esac
