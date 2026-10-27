#!/usr/bin/env bash
# Runs INSIDE a container, as an ordinary user, with the two tarballs in /pkg. Driven by linux_packages_containers.py; prints PASS/FAIL lines.
#   linux_packages_scenario.sh multiproxy-defaults | multiproxy-typed | multiproxy-tray | courier-defaults | courier-typed | multiproxy-update | courier-update | security
set -u
SCEN="$1"
HAS_SYSTEMD=no
systemctl --user show-environment >/dev/null 2>&1 && HAS_SYSTEMD=yes
FAILED=0
ok()   { echo "PASS: $*"; }
bad()  { echo "FAIL: $*"; FAILED=1; }
check() { local what="$1"; shift; if "$@" >/dev/null 2>&1; then ok "$what"; else bad "$what"; fi; }
has()  { grep -qF -- "$2" <<<"$1"; }
expect_has() { if has "$2" "$3"; then ok "$1"; else bad "$1 (wanted: $3)"; echo "---- got:"; echo "$2" | tail -n 15; echo "----"; fi; }

WORK="$(mktemp -d)"
cd "$WORK"
export HOME="${HOME:-/home/tester}"
export PATH="$HOME/.local/bin:$PATH"

wait_for() { local n="$1"; shift; local i; for ((i = 0; i < n * 2; i++)); do "$@" >/dev/null 2>&1 && return 0; sleep 0.5; done; return 1; }

# Answers are typed through a real terminal (script makes one), the way a person would type them.
typed() { local answers="$1"; shift; printf '%b' "$answers" | script -qec "$*" /dev/null; }

case "$SCEN" in
multiproxy-defaults|multiproxy-typed)
    tar xzf /pkg/MultiProxy-[0-9]*-linux.tar.gz
    cd multiproxy
    if [ "$SCEN" = multiproxy-defaults ]; then
        OUT="$(./install.sh --yes 2>&1)"; RC=$?
        IDIR="$HOME/.local/share/multiproxy"; SDIR="$HOME/.config/multiproxy"
    else
        IDIR="$WORK/my program"; SDIR="$WORK/my settings"
        OUT="$(typed "$IDIR\n$SDIR\nn\n" ./install.sh 2>&1)"; RC=$?
    fi
    echo "$OUT" | tail -n 20
    check "install exits 0" test "$RC" = 0
    expect_has "tells you it is running" "$OUT" "MultiProxy is running. Dashboard: http://127.0.0.1:8000"
    expect_has "points at the help" "$OUT" "multiproxy help"
    check "program installed in $IDIR" test -f "$IDIR/multiproxy.py"
    check "settings folder exists ($SDIR)" test -d "$SDIR"
    check "settings-location points at it" grep -qxF "$SDIR" "$IDIR/settings-location.txt"
    check "command on the PATH" command -v multiproxy
    check "the install never wrote into a Skyrim folder (none here)" test ! -e "$HOME/.steam"
    if [ "$SCEN" = multiproxy-typed ]; then
        # "n" to start at login: the service exists but is not enabled
        if [ "$HAS_SYSTEMD" = yes ]; then
            check "typed no: not enabled at login" bash -c '! systemctl --user is-enabled multiproxy.service'
        fi
    elif [ "$HAS_SYSTEMD" = yes ]; then
        check "default yes: enabled at login" systemctl --user is-enabled multiproxy.service
    fi
    check "pointer file written" test -f "$HOME/.config/skyrimnet-multiproxy/install-info.json"
    ST="$(multiproxy status 2>&1)"; echo "status: $ST"
    expect_has "status says running with a count" "$ST" "is running."
    expect_has "status counts the AIs" "$ST" "AIs ready"
    check "dashboard answers" python3 -c 'import urllib.request as u; u.urlopen("http://127.0.0.1:8000/health", timeout=5)'
    check "log file is written" test -s "$SDIR/multiproxy.log"
    LOGS="$(timeout 3 multiproxy logs 2>&1)"
    expect_has "logs follows the log" "$LOGS" "Following"
    URL="$(multiproxy open 2>&1)"
    expect_has "open gives the address when there is no desktop" "$URL" "http://127.0.0.1:8000"
    HELP="$(multiproxy help 2>&1)"
    for w in start stop restart status logs open uninstall help; do expect_has "help lists $w" "$HELP" "multiproxy $w"; done
    multiproxy stop; sleep 2
    if multiproxy status >/dev/null 2>&1; then bad "stop stops it"; else ok "stop stops it"; fi
    OUT2="$(multiproxy start 2>&1)"; expect_has "start brings it back" "$OUT2" "is running."
    OUT3="$(multiproxy restart 2>&1)"; expect_has "restart brings it back" "$OUT3" "is running."
    if [ "$HAS_SYSTEMD" = yes ]; then
        ST2="$(multiproxy status 2>&1)"; expect_has "status shows the service" "$ST2" "Service: active"
        # the service restarts itself when it crashes
        PID="$(systemctl --user show -p MainPID --value multiproxy.service)"
        kill -9 "$PID"; sleep 9
        check "service restarts after a crash" bash -c 'python3 -c "import urllib.request as u; u.urlopen(\"http://127.0.0.1:8000/health\", timeout=5)"'
    fi
    # re-running the installer updates in place and keeps the settings
    echo '{"marker": "kept"}' > "$SDIR/marker.json"
    OUT4="$(cd "$WORK/multiproxy" && ./install.sh --yes --dir "$IDIR" --settings "$SDIR" 2>&1)"
    expect_has "update runs" "$OUT4" "MultiProxy is running."
    check "update keeps settings" grep -q kept "$SDIR/marker.json"
    OUT5="$(multiproxy uninstall --yes 2>&1)"; echo "$OUT5" | tail -n 5
    expect_has "uninstall says so" "$OUT5" "MultiProxy is removed."
    check "program folder is gone" test ! -e "$IDIR"
    check "settings are kept" test -f "$SDIR/marker.json"
    check "command is gone" bash -c '! command -v multiproxy'
    if [ "$HAS_SYSTEMD" = yes ]; then check "service file is gone" test ! -e "$HOME/.config/systemd/user/multiproxy.service"; fi
    check "pointer file removed" test ! -e "$HOME/.config/skyrimnet-multiproxy/install-info.json"
    check "nothing still answering" bash -c '! python3 -c "import urllib.request as u; u.urlopen(\"http://127.0.0.1:8000/health\", timeout=2)"'
    ;;
multiproxy-tray)
    # A desktop with a (virtual) screen: the installer adds the tray's files and a menu entry, and `multiproxy tray` hands the server to the tray app and back.
    command -v Xvfb >/dev/null || { echo "FAIL: this scenario needs Xvfb in the container"; exit 1; }
    Xvfb :99 -screen 0 1024x768x24 >/dev/null 2>&1 &
    sleep 2
    export DISPLAY=:99
    tar xzf /pkg/MultiProxy-[0-9]*-linux.tar.gz
    cd multiproxy
    OUT="$(./install.sh --yes 2>&1)"; RC=$?
    echo "$OUT" | tail -n 8
    check "install exits 0" test "$RC" = 0
    check "a menu entry for the tray was made" test -f "$HOME/.local/share/applications/skyrimnet-multiproxy.desktop"
    check "the tray's files were installed" "$HOME/.local/share/multiproxy/.venv/bin/python" -c "import pystray, PIL"
    TR="$(multiproxy tray 2>&1)"; echo "$TR"
    expect_has "tray says where to look" "$TR" "icon"
    check "the tray app is running" wait_for 60 pgrep -f "[m]ultiproxy_tray.py"
    check "the server answers while the tray owns it" wait_for 90 multiproxy status
    if [ "$HAS_SYSTEMD" = yes ]; then check "the service stepped aside" bash -c '! systemctl --user is-active --quiet multiproxy.service'; fi
    ps -eo pid,cmd | grep -E "multiproxy(_tray)?\.py" | grep -v grep
    "$HOME/.local/share/multiproxy/.venv/bin/python" "$HOME/.local/share/multiproxy/multiproxy_tray.py" quit
    check "the tray app has gone" wait_for 60 bash -c '! pgrep -f "[m]ultiproxy_tray.py"'
    if [ "$HAS_SYSTEMD" = yes ]; then check "the service is back" wait_for 90 systemctl --user is-active --quiet multiproxy.service; fi
    check "the server answers again (what was running comes back)" wait_for 90 multiproxy status
    multiproxy uninstall --yes >/dev/null 2>&1
    check "the menu entry is gone after uninstall" test ! -e "$HOME/.local/share/applications/skyrimnet-multiproxy.desktop"
    ;;
courier-defaults|courier-typed)
    tar xzf /pkg/MultiProxy-Courier-*-linux.tar.gz
    cd multiproxy-courier
    if [ "$SCEN" = courier-defaults ]; then
        OUT="$(./install.sh --yes 2>&1)"; RC=$?
        IDIR="$HOME/.local/share/multiproxy-courier"; SDIR="$HOME/.config/multiproxy-courier"
    else
        IDIR="$WORK/my courier"; SDIR="$WORK/my courier settings"
        OUT="$(typed "$IDIR\n$SDIR\nn\n" ./install.sh 2>&1)"; RC=$?
    fi
    echo "$OUT" | tail -n 20
    check "install exits 0" test "$RC" = 0
    expect_has "says all set" "$OUT" "All set! This computer is now a MultiProxy Courier."
    expect_has "shows a pairing code" "$OUT" "Pairing code:"
    check "program installed" test -f "$IDIR/courier.py"
    check "settings folder holds courier.json" test -f "$SDIR/courier.json"
    check "command on the PATH" command -v multiproxy-courier
    ST="$(multiproxy-courier status 2>&1)"; echo "status: $ST"
    expect_has "status says running" "$ST" "is running"
    CODE="$(multiproxy-courier code 2>&1)"
    expect_has "code shows the same pairing code" "$OUT" "$CODE"
    check "log file is written" test -s "$SDIR/courier.log"
    LOGS="$(timeout 3 multiproxy-courier logs 2>&1)"; expect_has "logs follows the log" "$LOGS" "Following"
    multiproxy-courier stop; sleep 2
    if multiproxy-courier status 2>&1 | grep -q 'is running'; then bad "stop stops it"; else ok "stop stops it"; fi
    OUT2="$(multiproxy-courier start 2>&1)"; expect_has "start brings it back" "$OUT2" "is running"
    OUT3="$(multiproxy-courier restart 2>&1)"; expect_has "restart brings it back" "$OUT3" "is running"
    CODE2="$(multiproxy-courier code 2>&1)"; check "the pairing code survives a restart" test "$CODE" = "$CODE2"
    OUT4="$(cd "$WORK/multiproxy-courier" && ./install.sh --yes --dir "$IDIR" --settings "$SDIR" 2>&1)"
    expect_has "update runs" "$OUT4" "All set!"
    CODE3="$(multiproxy-courier code 2>&1)"; check "update keeps the pairing code" test "$CODE" = "$CODE3"
    # -- a main device pairs (a stand-in client coming from this machine's own non-loopback address with the right code): every line tells the same story
    multiproxy-courier status | grep -q 'Not paired yet' && ok "status says not paired before anyone pairs" || bad "status says not paired before anyone pairs"
    MYIP="$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.)' | head -1)"
    CPORT="$("$IDIR/.venv/bin/python" -c 'import json,sys;print(json.load(open(sys.argv[1])).get("port") or 8765)' "$SDIR/courier.json")"
    if [ -n "$MYIP" ]; then
        "$IDIR/.venv/bin/python" - "$MYIP" "$CPORT" "$CODE" <<'PY'
import sys, urllib.request
req = urllib.request.Request(f"http://{sys.argv[1]}:{sys.argv[2]}/courier/v1/report", headers={"Authorization": "Bearer " + sys.argv[3], "X-Courier-Client": "Gaming-Rig"})
urllib.request.urlopen(req, timeout=10).read()
PY
        sleep 2
        PST="$(multiproxy-courier status 2>&1)"; echo "paired status: $PST"
        expect_has "status says Connected to the main device" "$PST" "Connected to Gaming-Rig"
        check "status no longer shows a pairing code" bash -c '! grep -q "Pairing code" <<<"$1"' _ "$PST"
        CODEP="$(multiproxy-courier code 2>&1)"
        expect_has "code says already paired" "$CODEP" "already paired with Gaming-Rig"
        check "code does not print the used code" bash -c '! grep -qF -- "$1" <<<"$2"' _ "$CODE" "$CODEP"
        OUTP="$(cd "$WORK/multiproxy-courier" && ./install.sh --yes --dir "$IDIR" --settings "$SDIR" 2>&1)"
        expect_has "the install's last screen says already paired" "$OUTP" "Already paired with Gaming-Rig"
        check "the install's last screen shows no code when paired" bash -c '! grep -q "Pairing code:" <<<"$1"' _ "$OUTP"
        NEWC="$(multiproxy-courier new-code 2>&1 | tail -n 1)"; sleep 2
        check "new-code makes a different code" test -n "$NEWC" -a "$NEWC" != "$CODE"
        expect_has "status shows the new code again" "$(multiproxy-courier status 2>&1)" "Not paired yet"
    else
        echo "(no home-network address in this container: the pairing step was skipped)"
    fi
    OUT5="$(multiproxy-courier uninstall --yes 2>&1)"; echo "$OUT5" | tail -n 4
    expect_has "uninstall says so" "$OUT5" "The Courier is removed."
    check "program folder is gone" test ! -e "$IDIR"
    check "settings are kept" test -f "$SDIR/courier.json"
    check "command is gone" bash -c '! command -v multiproxy-courier'
    ;;
multiproxy-update|courier-update)
    # An update offers the earlier answers: typed folders and "no" to start at login are shown as [current: ...], Enter keeps them, --yes takes them,
    # a newer settings-location.txt beats the pointer, a settings folder that is gone is flagged (not swapped), a damaged pointer never stops the install.
    if [ "$SCEN" = multiproxy-update ]; then
        tar xzf /pkg/MultiProxy-[0-9]*-linux.tar.gz; cd multiproxy; PKG=multiproxy; CMD=multiproxy; MAIN=multiproxy.py; DONE="MultiProxy is running."
        PTR="$HOME/.config/skyrimnet-multiproxy/install-info.json"; DEFI="$HOME/.local/share/multiproxy"
    else
        tar xzf /pkg/MultiProxy-Courier-*-linux.tar.gz; cd multiproxy-courier; PKG=multiproxy-courier; CMD=multiproxy-courier; MAIN=courier.py; DONE="All set!"
        PTR="$HOME/.config/skyrimnet-multiproxy-courier/install-info.json"; DEFI="$HOME/.local/share/multiproxy-courier"
    fi
    IDIR="$WORK/my program"; SDIR="$WORK/my settings"; NEWS="$WORK/newer settings"
    OUT="$(typed "$IDIR\n$SDIR\nn\n" ./install.sh 2>&1)"; RC=$?
    check "first install with typed answers exits 0" test "$RC" = 0
    check "the pointer file remembers the answers" test -f "$PTR"
    # Enter on every question: the earlier answers are shown and kept
    OUT="$(typed "\n\n\n" ./install.sh 2>&1)"; RC=$?
    echo "$OUT" | head -n 6
    check "update with Enter exits 0" test "$RC" = 0
    expect_has "the install question shows the earlier folder" "$OUT" "[current: $IDIR]"
    expect_has "the settings question shows the earlier folder" "$OUT" "[current: $SDIR]"
    check "the update went to the same install folder" test -f "$IDIR/$MAIN"
    check "no folder was made at the default place" test ! -e "$DEFI"
    if [ "$HAS_SYSTEMD" = yes ]; then check "the answer 'no' to start at login was kept" bash -c "! systemctl --user is-enabled $CMD.service"; fi
    # --yes takes the remembered values
    OUT="$(./install.sh --yes 2>&1)"; RC=$?
    check "update with --yes exits 0" test "$RC" = 0
    check "--yes kept the install folder" grep -qxF "$SDIR" "$IDIR/settings-location.txt"
    check "--yes made no folder at the default place" test ! -e "$DEFI"
    # a newer value in the program's own settings beats the pointer
    "$IDIR/$CMD" stop >/dev/null 2>&1       # (a running copy keeps its pid in the old settings folder: stop it first, as a person moving the folder would)
    printf '%s\n' "$NEWS" > "$IDIR/settings-location.txt"
    OUT="$(./install.sh --yes 2>&1)"; RC=$?
    check "update after the settings folder was changed exits 0" test "$RC" = 0
    [ "$RC" = 0 ] || { echo "$OUT" | tail -n 5; tail -n 15 "$NEWS"/courier.out "$NEWS"/courier.log 2>/dev/null; }
    check "the newer settings folder won over the pointer" grep -qxF "$NEWS" "$IDIR/settings-location.txt"
    # a settings folder that is gone is flagged in one line and made again, not swapped for another
    "$IDIR/$CMD" stop >/dev/null 2>&1; rm -rf "$NEWS"
    OUT="$(./install.sh --yes 2>&1)"; RC=$?
    expect_has "a missing folder is said so" "$OUT" "That folder is missing. Pick another, or one will be created."
    check "the missing folder is still the one used" test -d "$NEWS"
    # a damaged pointer is skipped: the older install's command link still finds the install folder
    printf '{ not json' > "$PTR"
    "$IDIR/$CMD" stop >/dev/null 2>&1
    OUT="$(./install.sh --yes 2>&1)"; RC=$?
    echo "$OUT" | tail -n 4
    check "update with a damaged pointer exits 0" test "$RC" = 0
    check "...and still went to the same install folder" test -f "$IDIR/$MAIN" -a ! -e "$DEFI"
    "$IDIR/$CMD" uninstall --yes >/dev/null 2>&1
    check "uninstall removed the pointer file" test ! -e "$PTR"
    check "uninstall kept the settings" test -d "$NEWS"
    ;;
security)
    # The Linux security review's checks (docs/security-review-linux-*.md). Needs systemd (the service files are part of it).
    [ "$HAS_SYSTEMD" = yes ] || { echo "FAIL: this scenario needs the systemd container"; exit 1; }
    FAKEKEY="FAKEKEY1234567890abcdef.ZZZZZZZZZZZZZZZZ"
    mode() { stat -c %a "$1" 2>/dev/null; }
    private() { local m; m="$(mode "$1")"; [ -n "$m" ] && [ "${m: -2}" = 00 ]; }      # nothing for group or others
    tar xzf /pkg/MultiProxy-[0-9]*-linux.tar.gz; tar xzf /pkg/MultiProxy-Courier-*-linux.tar.gz
    (cd multiproxy && ./install.sh --yes >/dev/null 2>&1); (cd multiproxy-courier && ./install.sh --yes > "$WORK/courier-install.txt" 2>&1)
    MPS="$HOME/.config/multiproxy"; CRS="$HOME/.config/multiproxy-courier"; PTR="$HOME/.config/skyrimnet-multiproxy"
    wait_for 60 multiproxy status
    curl -s -o /dev/null -X POST http://127.0.0.1:8000/config/glm-key -H 'X-MultiProxy: 1' -H 'Content-Type: application/json' -d "{\"key\": \"$FAKEKEY\"}"
    sleep 2
    echo "--- modes:"; ls -ld "$MPS" "$CRS" "$PTR" "$MPS"/* "$CRS"/* "$PTR"/* 2>/dev/null
    check "MultiProxy settings folder is private" private "$MPS"
    check "config.json (holds keys) is private" private "$MPS/config.json"
    check "the pointer file (holds the token) is private" private "$PTR/install-info.json"
    check "the pointer file's folder is private" private "$PTR"
    check "the Courier settings folder is private" private "$CRS"
    check "courier.json (holds the pairing code) is private" private "$CRS/courier.json"
    echo "--- listeners:"; ss -ltnH | awk '{print $4}' | sort
    check "the dashboard and interceptor listen on 127.0.0.1 only" bash -c '! ss -ltnH | awk "{print \$4}" | grep -E ":(8000|9999)\$" | grep -v "^127\.0\.0\.1:"'
    check "the Courier listens on no public or wildcard address" bash -c '! ss -ltnH | awk "{print \$4}" | grep -E ":8110\$" | grep -E "^(0\.0\.0\.0|\*|\[::\]|::)"'
    CODE="$(multiproxy-courier code | tail -n 1)"
    JOURNAL="$(journalctl --user --no-pager 2>/dev/null; journalctl --no-pager 2>/dev/null)"
    ALLTXT="$(cat "$MPS"/*.log* "$CRS"/*.log* "$CRS"/*.out 2>/dev/null; multiproxy status 2>&1; timeout 3 multiproxy logs 2>&1 </dev/null)"
    check "the API key is in config.json only (not in any log, status or the journal)" bash -c '! grep -rF -- "$1" "$2" "$3" 2>/dev/null | grep -v config.json | grep -q . && ! grep -qF -- "$1" <<<"$4"' _ "$FAKEKEY" "$MPS" "$CRS" "$JOURNAL$ALLTXT"
    check "the key is not in the journal" bash -c '! grep -qF -- "$1" <<<"$2"' _ "$FAKEKEY" "$JOURNAL"
    check "the pairing code is not in courier.log" bash -c '! cat "$2"/courier.log* | grep -qF -- "$1"' _ "$CODE" "$CRS"
    check "the pairing code is not in courier.out" bash -c '! cat "$2"/courier.out 2>/dev/null | grep -qF -- "$1"' _ "$CODE" "$CRS"
    check "the pairing code is not in the journal" bash -c '! grep -qF -- "$1" <<<"$2"' _ "$CODE" "$JOURNAL"
    check "the pairing code is not in the status line" bash -c '! multiproxy-courier status | grep -qF -- "$1"' _ "$CODE"
    echo "--- unit files:"; cat "$HOME/.config/systemd/user/multiproxy.service" "$HOME/.config/systemd/user/multiproxy-courier.service"
    for u in multiproxy multiproxy-courier; do
        check "$u unit: NoNewPrivileges" grep -q '^NoNewPrivileges=yes' "$HOME/.config/systemd/user/$u.service"
        check "$u unit: UMask=0077" grep -q '^UMask=0077' "$HOME/.config/systemd/user/$u.service"
    done
    check "the program folders are not writable by others" bash -c '[ -z "$(find "$1" "$2" -maxdepth 2 -perm /022 ! -type l 2>/dev/null | grep -v "/.venv/")" ]' _ "$HOME/.local/share/multiproxy" "$HOME/.local/share/multiproxy-courier"
    # -- a path with quotes and shell marks in it changes no command
    ODD="$WORK/o'; touch pwned1; echo 'x"
    (cd multiproxy-courier && ./install.sh --yes --dir "$ODD" --settings "$WORK/oddset" 2>&1 | tail -n 4)
    check "odd folder name (a quote and a command in it): the Courier installed there" test -f "$ODD/courier.py"
    check "odd folder name: no command ran" bash -c '[ ! -e "$1/multiproxy-courier/pwned1" ] && [ ! -e "$1/pwned1" ]' _ "$WORK"
    "$ODD/multiproxy-courier" uninstall --yes >/dev/null 2>&1
    check "odd folder name: uninstall removed it" test ! -e "$ODD"
    # -- what the programs print on their own (a service's output goes to the journal): neither the key nor the pairing code
    multiproxy stop >/dev/null 2>&1; multiproxy-courier stop >/dev/null 2>&1; sleep 2
    FG="$WORK/fg"; mkdir -p "$FG"
    (cd "$HOME/.local/share/multiproxy" && MULTIPROXY_TRAY=1 MULTIPROXY_PORT=18000 MULTIPROXY_INTERCEPTOR_PORT=19999 MULTIPROXY_SETTINGS_DIR="$FG/mp" exec .venv/bin/python multiproxy.py) > "$FG/mp.out" 2>&1 &
    MPPID=$!
    wait_for 60 curl -sf http://127.0.0.1:18000/health
    curl -s -o /dev/null -X POST http://127.0.0.1:18000/config/glm-key -H 'X-MultiProxy: 1' -H 'Content-Type: application/json' -d "{\"key\": \"$FAKEKEY\"}"
    curl -s -o /dev/null -X POST http://127.0.0.1:18000/config/glm-key -H 'X-MultiProxy: 1' -H 'Content-Type: text/plain' -d "{\"key\": \"$FAKEKEY\"}"
    sleep 2; kill $MPPID 2>/dev/null; wait $MPPID 2>/dev/null
    check "MultiProxy's own output was captured" test -s "$FG/mp.out"
    check "the API key is not in MultiProxy's output" bash -c '! grep -qF -- "$1" "$2"' _ "$FAKEKEY" "$FG/mp.out"
    COURIER_CODE="$(cd "$HOME/.local/share/multiproxy-courier" && .venv/bin/python courier.py code --config-dir "$FG/cr" --port 18110 | tail -n 1)"
    (cd "$HOME/.local/share/multiproxy-courier" && exec .venv/bin/python courier.py run --no-tray --no-ready-window --config-dir "$FG/cr" --port 18110 --discovery-port 18099) > "$FG/cr.out" 2>&1 &
    CRPID=$!
    sleep 6; kill $CRPID 2>/dev/null; wait $CRPID 2>/dev/null
    check "the Courier's own output was captured" test -s "$FG/cr.out"
    check "the pairing code is not in the Courier's output" bash -c '! grep -qF -- "$1" "$2"' _ "$COURIER_CODE" "$FG/cr.out"
    # -- the installers refuse folders an uninstall could wreck
    mkdir -p "$HOME/Documents"; echo precious > "$HOME/Documents/a.txt"
    (cd multiproxy && ./install.sh --yes --dir "$HOME" --settings "$WORK/s1" >/dev/null 2>&1); R1=$?
    check "install into the home folder is refused" test "$R1" != 0
    (cd multiproxy && ./install.sh --yes --dir "$HOME/Documents" --settings "$WORK/s2" >/dev/null 2>&1); R2=$?
    check "install into a folder that holds someone else's files is refused" test "$R2" != 0
    (cd multiproxy && ./install.sh --yes --dir "$WORK/ok" --settings "$HOME" >/dev/null 2>&1); R3=$?
    check "settings in the home folder itself are refused" test "$R3" != 0
    check "nothing of the person's was touched" bash -c 'grep -q precious "$1/Documents/a.txt"' _ "$HOME"
    # -- uninstall cannot be pointed somewhere else
    mkdir -p "$WORK/victim" && echo keep > "$WORK/victim/k.txt"
    MULTIPROXY_SETTINGS_DIR="$WORK/victim" multiproxy uninstall --yes --remove-settings >/dev/null 2>&1
    check "uninstall --remove-settings with a crafted settings variable leaves a foreign folder alone" test -f "$WORK/victim/k.txt"
    check "uninstall of MultiProxy removed its program" test ! -e "$HOME/.local/share/multiproxy"
    ;;
security-odd)
    # Plain container (no systemd): the install reaches the "wait until it answers" step, where a folder name with a quote and a command in it once became part of a command.
    tar xzf /pkg/MultiProxy-Courier-*-linux.tar.gz
    ODD="$WORK/o'; touch pwned1; echo 'x"
    (cd multiproxy-courier && ./install.sh --yes --dir "$ODD" --settings "$WORK/oddset" 2>&1 | tail -n 3)
    check "the Courier installed in the odd folder" test -f "$ODD/courier.py"
    check "and no command ran" bash -c '[ ! -e "$1/multiproxy-courier/pwned1" ] && [ ! -e "$1/pwned1" ]' _ "$WORK"
    "$ODD/multiproxy-courier" uninstall --yes >/dev/null 2>&1
    ;;
*) echo "unknown scenario $SCEN"; exit 2 ;;
esac
echo "RESULT: $([ "$FAILED" = 0 ] && echo all-passed || echo some-failed)"
exit "$FAILED"
