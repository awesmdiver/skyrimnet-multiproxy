#!/usr/bin/env bash
# usage: folder_problem.sh PROJECT_ROOT HOME_DIR SCRATCH_DIR  -- prints one "label: answer" line per case (answer is empty when the folder is fine).
mkdir -p "$2" "$3"
ROOT="$1"; export HOME="$(cd "$2" && pwd -P)"; S="$(cd "$3" && pwd -P)"
. "$ROOT/linux/lib.sh"
mkdir -p "$S/empty" "$S/foreign" "$S/oldinstall" "$S/oursettings" "$S/marked"
echo mine > "$S/foreign/notes.txt"
echo x > "$S/oldinstall/multiproxy.py"
echo x > "$S/oursettings/config.json"
echo x > "$S/marked/whatever"; : > "$S/marked/$MARKER"
show() { printf '%s: %s\n' "$1" "$(folder_problem "$2" "$3")"; }
show home "$HOME" install
show root / install
show etc /etc settings
show downloads "$HOME/Downloads" install
show new "$S/brand-new" install
show empty "$S/empty" install
show foreign-install "$S/foreign" install
show foreign-settings "$S/foreign" settings
show old-install "$S/oldinstall" install
show our-settings "$S/oursettings" settings
show marked "$S/marked" install
show unchecked "$S/foreign" ""
show relative-home "$HOME/../$(basename "$HOME")" install
