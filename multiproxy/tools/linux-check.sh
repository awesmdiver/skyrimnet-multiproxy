#!/usr/bin/env bash
# MultiProxy Linux check: run this on a real Linux server (or your own Linux PC) to see whether the Courier works there.
#
#   bash linux-check.sh --installer ./courier/install-courier.sh --archive ./MultiProxy-Courier.zip
#   bash linux-check.sh                      (uses the Courier's release download; needs this repo's courier/install-courier.sh or --installer)
#
# It never rents or creates anything, never needs a cloud key, and never signs in to anything real. It installs the Courier the way a person
# would (with the real install-courier.sh), checks it, and writes a short report you can paste into a Discord message or a GitHub issue.
# Run it twice and nothing breaks. `--uninstall` removes everything it installed.
#
# Options:
#   --installer PATH|URL   the real install-courier.sh (default: courier/install-courier.sh next to this tools folder)
#   --archive PATH|URL     the Courier package to install (default: the installer's own download of the latest release)
#   --dir DIR              where to install (default ~/multiproxy-linux-check)
#   --port N               the Courier's port (default 8110)
#   --discovery-port N     the Courier's "I'm here" port (default 8099)
#   --with-model           also install the small decision model through the real route and ask it one question (needs about 6 GB free, downloads from the internet)
#   --with-voice           also install the voice and speak one line (about 10 GB; needs --accept-voice-license, the voice's licence is yours to accept)
#   --accept-voice-license you have read and accept the voice model's licence
#   --with-engines         also check the model engines THROUGH the Courier's own routes: Ollama (if it is installed) and the built-in llama.cpp (finds models, downloads
#                          the smallest one, loads it, asks one question, unloads, deletes it, and starts and cancels one download); needs internet and a few GB
#   --with-claude          also install Claude through the Courier, start its sign-in (it never signs in, no account is used), try a made-up code, sign out, and remove it
#   --with-copilot         the same for Copilot (its own steps: a sign-in code is asked for and cancelled)
#   --and-uninstall        at the end run the real uninstall and check that nothing is left (a local run only)
#   --remote HOST:PORT     check a Courier on ANOTHER machine from here, through the same routes the main device's dashboard uses (so something that works on the machine
#                          itself but not over the network is caught). Needs --code. This machine then stands in for the main device: a Courier that was not paired
#                          before shows as paired (with no name) afterwards, and pairing it from your main device replaces that. Nothing is installed here.
#   --code CODE            the Courier's pairing code, for --remote (or set MULTIPROXY_CODE: it is never printed or written to the report)
#   --install-prereqs      install missing basics (python venv, curl, OpenMP) with this distro's package manager (needs root or sudo)
#   --timeout SECONDS      how long to wait for a download or an engine start (default 1800)
#   --report FILE          where the report goes (default ./multiproxy-linux-report.txt)
#   --dry-run              say what would be done, change nothing
#   --uninstall            remove everything this script installed, then stop
#   --help                 this text
#
# Needs: bash, python3 (3.10 or newer) with venv, curl or wget. Written for Ubuntu 22.04 / 24.04 and Debian 12 first; Fedora and Arch are best-effort.

set -u
VERSION="1"

# ---------------------------------------------------------------- options
SELF="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
SELF_DIR="$(cd "$(dirname "$SELF")" && pwd)"
REPO_DIR="$(cd "$SELF_DIR/.." && pwd)"
INSTALLER="$REPO_DIR/courier/install-courier.sh"
ARCHIVE=""
INSTALL_DIR="$HOME/multiproxy-linux-check"
PORT=8110
DISCOVERY_PORT=8099
WITH_MODEL=no
WITH_VOICE=no
ACCEPT_VOICE=no
WITH_ENGINES=no
WITH_CLAUDE=no
WITH_COPILOT=no
AND_UNINSTALL=no
REMOTE=""
CODE_OPT=""
LOCAL=yes
BASE=""
GPU_NVIDIA=no; GPU_AMD=no; GPU_INTEL=no; VULKAN_OK=no; ROCM_OK=no; CUDA_V=""
OS_NAME="unknown"; DISK_FREE_GB=""; INSTALL_DIR_SET=no
HAS_CARD=unknown
PREREQS=no
TIMEOUT=1800
REPORT="$(pwd)/multiproxy-linux-report.txt"
DRY=no
UNINSTALL=no
STATE_FILE="$HOME/.multiproxy-linux-check.state"

usage() { sed -n '2,/^# Needs:/p' "$SELF" | sed 's/^# \{0,1\}//' | sed '$d'; }
while [ $# -gt 0 ]; do
    case "$1" in
        --installer) INSTALLER="${2:?--installer needs a path or address}"; shift 2 ;;
        --archive) ARCHIVE="${2:?--archive needs a path or address}"; shift 2 ;;
        --dir) INSTALL_DIR="${2:?--dir needs a folder}"; shift 2 ;;
        --port) PORT="${2:?}"; shift 2 ;;
        --discovery-port) DISCOVERY_PORT="${2:?}"; shift 2 ;;
        --with-model) WITH_MODEL=yes; shift ;;
        --with-voice) WITH_VOICE=yes; shift ;;
        --accept-voice-license) ACCEPT_VOICE=yes; shift ;;
        --with-engines) WITH_ENGINES=yes; shift ;;
        --with-claude) WITH_CLAUDE=yes; shift ;;
        --with-copilot) WITH_COPILOT=yes; shift ;;
        --and-uninstall) AND_UNINSTALL=yes; shift ;;
        --remote) REMOTE="${2:?--remote needs the Courier's address, like 192.168.1.50:8110}"; shift 2 ;;
        --code) CODE_OPT="${2:?--code needs the Courier's pairing code}"; shift 2 ;;
        --install-prereqs) PREREQS=yes; shift ;;
        --timeout) TIMEOUT="${2:?}"; shift 2 ;;
        --report) REPORT="${2:?}"; shift 2 ;;
        --dry-run) DRY=yes; shift ;;
        --uninstall) UNINSTALL=yes; shift ;;
        --help|-h) usage; exit 0 ;;
        *) echo "The option '$1' isn't recognized. Try --help." >&2; exit 2 ;;
    esac
done

WORK="$(mktemp -d)"
chmod 700 "$WORK"
RAW="$WORK/report.raw"
: > "$RAW"
trap 'rm -rf "$WORK"' EXIT

# ---------------------------------------------------------------- output + results
NAMES=(); RESULTS=(); DETAILS=(); FKEYS=(); FEAT=""
say()  { printf '%s\n' "$*"; printf '%s\n' "$*" >> "$RAW"; }
head_() { printf '\n== %s\n' "$*"; printf '\n== %s\n' "$*" >> "$RAW"; }
quiet() { printf '%s\n' "$*" >> "$RAW"; }                # into the report only
shown() { printf '%s\n' "$*"; }                          # onto the screen only (never into the report)
record() { NAMES+=("$1"); RESULTS+=("$2"); DETAILS+=("${3:-}"); FKEYS+=("${FEAT:-}"); printf '  [%s] %s%s\n' "$2" "$1" "${3:+ - $3}"; printf '  [%s] %s%s\n' "$2" "$1" "${3:+ - $3}" >> "$RAW"; }
pass() { record "$1" PASS "${2:-}"; }
fail() { record "$1" FAIL "${2:-}"; }
skip() { record "$1" SKIP "${2:-}"; }
note() { record "$1" NOTE "${2:-}"; }
person() { record "$1" PERSON "${2:-}"; }              # a thing only a person can check: what to look at is in the detail
have() { command -v "$1" >/dev/null 2>&1; }
run()  { # mutating command: in a dry run it is only described
    if [ "$DRY" = yes ]; then say "  (dry run) would run: $*"; return 0; fi
    "$@"
}
die_early() { # one plain sentence, nothing installed
    printf '\nStopped before installing anything:\n  %s\n' "$*" >&2
    printf '\nStopped before installing anything:\n  %s\n' "$*" >> "$RAW"
    write_report_and_exit 1
}

# ---------------------------------------------------------------- the report (nothing private leaves in it)
redact() {
    local me host
    me="$(id -un 2>/dev/null || echo user)"; host="$(hostname 2>/dev/null || echo host)"
    sed -E \
        -e "s#$HOME#~#g" \
        -e "s#/home/$me#~#g" \
        -e "s#/root#~#g" \
        -e "s#\\b$me\\b#<user>#g" \
        -e "s#\\b$host\\b#<host>#g" \
        -e 's#\b[A-Z2-9]{3}-[A-Z2-9]{3}\b#<code>#g' \
        -e 's#(sk-ant-[A-Za-z0-9_-]+)#<key>#g' \
        -e 's#(Bearer|Basic) +[A-Za-z0-9._~+/=-]{6,}#\1 <key>#g' \
        -e 's#([A-Za-z0-9_]*(TOKEN|SECRET|PASSWORD|API_?KEY)[A-Za-z0-9_]*=)[^ ]+#\1<hidden>#g' |
    { if have python3; then python3 -c '
import re, sys, ipaddress
def keep(m):
    s = m.group(0)
    try:
        a = ipaddress.ip_address(s)
    except ValueError:
        return s
    if a.is_loopback or a.is_private or a in ipaddress.ip_network("100.64.0.0/10") or a.is_unspecified or a.is_link_local:
        return s
    return "<public-ip>"
for line in sys.stdin:
    line = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", keep, line)
    line = re.sub(r"\b[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}\b", "<mac>", line)
    sys.stdout.write(line)
'; else cat; fi; }
}

declare -A FEATURE_LABEL=(
    [install]="Install" [boot]="Starts at boot and after a restart" [network]="Listens on private addresses only" [pair]="Pairing code is required"
    [connected]="Connected, and reconnect" [report]="The device report" [older]="The older-Courier check (build number)" [gpu]="Graphics card detection"
    [awake]="Keep awake and start at login" [folder]="Models folder and Change folder" [log]="The Log view" [ollama]="Ollama" [llamacpp]="Built-in model engine (llama.cpp)"
    [lmstudio]="LM Studio" [download]="Pause and cancel a download" [decisions]="Local decision model" [voice]="Voice engine" [claude]="Claude row" [copilot]="Copilot row"
    [update]="Update by the update zip" [addremove]="Add and remove the device" [uninstall]="Full uninstall")
declare -A FEATURE_HINT=(
    [connected]="run with --remote to check it" [ollama]="add --with-engines" [llamacpp]="add --with-engines" [download]="add --with-engines" [decisions]="add --with-model"
    [voice]="add --with-voice --accept-voice-license" [claude]="add --with-claude" [copilot]="add --with-copilot" [uninstall]="add --and-uninstall (local runs)"
    [x]="")
FEATURE_KEYS=(install boot network pair connected report older gpu awake folder log ollama llamacpp lmstudio download decisions voice claude copilot update addremove uninstall)
RAN_ON=""
COURIER_BUILD_SEEN="not installed"
PY=""          # the Courier's own Python once installed
MP_VERSION="unknown"
CODE=""        # the pairing code: only ever in this variable, never printed into the report
write_report_and_exit() {
    local rc="${1:-0}" npass=0 nfail=0 nskip=0 nnote=0 nperson=0 i k
    for i in "${!RESULTS[@]}"; do
        case "${RESULTS[$i]}" in PASS) npass=$((npass+1)) ;; FAIL) nfail=$((nfail+1)) ;; SKIP) nskip=$((nskip+1)) ;; NOTE) nnote=$((nnote+1)) ;; PERSON) nperson=$((nperson+1)) ;; esac
    done
    local table="$WORK/table"
    {
        printf '\n== Feature table (one line for each thing the Courier does)\n'
        printf '  %-40s %-14s %s\n' "Feature" "Result" "Ran on / what happened"
        local ran="${RAN_ON:-$OS_NAME}"
        for k in "${FEATURE_KEYS[@]}"; do
            local f_fail=0 f_pass=0 f_person=0 f_skip=0 f_note=0 f_detail="" f_n=0 f_notrun=0
            for i in "${!FKEYS[@]}"; do
                [ "${FKEYS[$i]}" = "$k" ] || continue
                f_n=$((f_n+1))
                case "${RESULTS[$i]}" in FAIL) f_fail=$((f_fail+1)); [ -z "$f_detail" ] || [ "${f_detail:0:5}" != "FAIL:" ] && f_detail="FAIL: ${NAMES[$i]}${DETAILS[$i]:+ ($(printf '%s' "${DETAILS[$i]}" | cut -c1-90))}" ;;
                    PASS) f_pass=$((f_pass+1)) ;; PERSON) f_person=$((f_person+1)) ;; SKIP) f_skip=$((f_skip+1)); [ -n "$f_detail" ] || f_detail="${DETAILS[$i]}"; case "${DETAILS[$i]}" in *"add --"*) f_notrun=$((f_notrun+1)) ;; esac ;; NOTE) f_note=$((f_note+1)); [ -n "$f_detail" ] || f_detail="${NAMES[$i]}" ;; esac
            done
            local res
            if [ "$f_n" -eq 0 ]; then
                res="not run"; f_detail="${FEATURE_HINT[$k]:-}"
                case "$k" in install|boot|network|update) f_detail="not run"; [ "$LOCAL" = no ] && f_detail="a remote check installs nothing there" ;; esac
            elif [ "$f_fail" -gt 0 ]; then res="FAIL"
            elif [ "$f_pass" -gt 0 ]; then
                res="PASS"; [ "$f_person" -gt 0 ] && res="PASS + person"; f_detail="$ran"
                [ "$f_notrun" -gt 0 ] && { res="PASS (partly)"; f_detail="${FEATURE_HINT[$k]:-more checks need an option}"; }
            elif [ "$f_person" -gt 0 ]; then res="needs a person"; f_detail="see the list below"
            elif [ "$f_skip" -gt 0 ]; then res="skipped"
            else res="note"; fi
            printf '  %-40s %-14s %s\n' "${FEATURE_LABEL[$k]}" "$res" "$f_detail"
        done
        printf '\n== Needs a person (what to look at)\n'
        local any=0
        for i in "${!RESULTS[@]}"; do [ "${RESULTS[$i]}" = PERSON ] && { any=1; printf '  - %s: %s\n' "${NAMES[$i]}" "${DETAILS[$i]}"; }; done
        [ "$any" = 1 ] || printf '  (nothing)\n'
        printf '\n== Result\n'
        for i in "${!NAMES[@]}"; do printf '  %-6s %s%s\n' "${RESULTS[$i]}" "${NAMES[$i]}" "${DETAILS[$i]:+ - ${DETAILS[$i]}}"; done
        printf '\n  %d passed, %d failed, %d skipped, %d notes, %d need a person\n' "$npass" "$nfail" "$nskip" "$nnote" "$nperson"
    } > "$table"
    cat "$table"
    local out="$WORK/final"
    {
        printf 'MultiProxy Linux check %s - Courier build %s - %s\n' "$VERSION" "$COURIER_BUILD_SEEN" "$(date -u +%Y-%m-%d)"
        cat "$RAW" "$table"
    } > "$out"
    redact < "$out" > "$REPORT" 2>/dev/null
    if ! have python3; then printf '\n(python3 is missing, so addresses were not shortened; read the report before sharing it)\n' >> "$REPORT"; fi
    printf '\nThe report (safe to share: no keys, pairing codes, names or private paths) is here:\n  %s\n' "$REPORT"
    [ "$nfail" -eq 0 ] || rc=1
    exit "$rc"
}

# ---------------------------------------------------------------- helpers: talking to the Courier on this machine
# The pairing code is read from the Courier's own settings the way the installer does (`courier.py code`) and used only for calls to
# 127.0.0.1; it is handed over in an environment variable of one short-lived process and is never printed or written to the report.
api() { # api METHOD PATH [JSON]  ->  prints "HTTP_CODE" on the first line, then the body
    local method="$1" path="$2" body="${3:-}"
    COURIER_CODE="${API_CODE-$CODE}" "$PY" - "$method" "$BASE$path" "$body" <<'PYEND'
import os, sys, urllib.request, urllib.error
method, url, body = sys.argv[1], sys.argv[2], sys.argv[3]
headers = {"Authorization": "Bearer " + os.environ.get("COURIER_CODE", "")}
data = None
if body:
    data = body.encode(); headers["Content-Type"] = "application/json"
req = urllib.request.Request(url, data=data, method=method, headers=headers)
try:
    with urllib.request.urlopen(req, timeout=int(os.environ.get("COURIER_TIMEOUT", "60"))) as r:
        print(r.status); sys.stdout.write(r.read().decode("utf-8", "replace"))
except urllib.error.HTTPError as e:
    print(e.code); sys.stdout.write(e.read().decode("utf-8", "replace"))
except Exception as e:
    print(0); print(type(e).__name__)
PYEND
}
http_code() { api "$@" | head -n 1; }
json_get() { # json_get 'expression on d' < json  (python expression; prints "" on any error)
    python3 -c 'import sys, json
try:
    d = json.load(sys.stdin)
    v = eval(sys.argv[1])
    print("" if v is None else v)
except Exception:
    print("")' "$1"
}
body_of() { tail -n +2; }
wait_for() { # wait_for SECONDS 'command'  -> 0 when the command succeeded in time
    local limit="$1" cmd="$2" t0; t0=$SECONDS
    while [ $((SECONDS - t0)) -lt "$limit" ]; do
        if eval "$cmd" >/dev/null 2>&1; then return 0; fi
        sleep 2
    done
    return 1
}

dec_state() { api GET "/courier/v1/decisions/status?fresh=1" | body_of | json_get 'd.get("state")'; }
dec_settled() { local s; s="$(dec_state)"; [ -n "$s" ] && [ "$s" != installing ] && [ "$s" != not_installed ]; }
dec_ready() { [ "$(dec_state)" = ready ]; }
voice_state() { api GET /courier/v1/voice/status | body_of | json_get 'd.get("state")'; }
voice_settled() { [ "$(voice_state)" != installing ]; }
voice_ready() { [ "$(voice_state)" = ready ]; }
restarted_after_kill() { local np; np="$(systemctl --user show -p MainPID --value multiproxy-courier.service 2>/dev/null)"; [ -n "$np" ] && [ "$np" != 0 ] && [ "$np" != "$MP" ] && courier_up; }

# ---------------------------------------------------------------- the address: this machine, or another one
BASE="http://127.0.0.1:$PORT"
if [ -n "$REMOTE" ]; then
    LOCAL=no
    BASE="http://${REMOTE#http://}"; BASE="${BASE%/}"
    CODE="${CODE_OPT:-${MULTIPROXY_CODE:-}}"
    PY=python3
    [ "$UNINSTALL" = yes ] && die_early "--uninstall removes this machine's own install; it does not go with --remote."
    [ -n "$CODE" ] || die_early "--remote needs the Courier's pairing code: add --code CODE (or set MULTIPROXY_CODE)."
    have python3 || die_early "python3 is needed here to talk to the Courier."
    [ "$AND_UNINSTALL" = no ] || die_early "--and-uninstall removes this machine's own install; it does not go with --remote."
    [ "$DRY" = no ] || { say "(dry run) would check the Courier at $BASE through its own routes: report, refusals, log, settings, folder, engines, Claude and Copilot rows, pairing, Connected and reconnect."; record "Dry run" NOTE "nothing was changed"; write_report_and_exit 0; }
fi

# ---------------------------------------------------------------- uninstall
unit_dir="$HOME/.config/systemd/user"
if [ "$UNINSTALL" = yes ]; then
    head_ "Uninstall"
    if [ ! -f "$STATE_FILE" ] && [ ! -f "$INSTALL_DIR/courier.py" ]; then say "Nothing of this script's is installed here ($INSTALL_DIR). Nothing to do."; record "Uninstall" PASS "nothing was installed"; write_report_and_exit 0; fi
    # shellcheck disable=SC1090
    [ -f "$STATE_FILE" ] && . "$STATE_FILE"
    DIR_TO_REMOVE="${STATE_DIR:-$INSTALL_DIR}"
    if [ -d "$DIR_TO_REMOVE" ] && [ ! -f "$DIR_TO_REMOVE/courier.py" ]; then
        record "Uninstall" FAIL "$DIR_TO_REMOVE is not a Courier folder, so I left it alone"; write_report_and_exit 1
    fi
    if have systemctl && systemctl --user show-environment >/dev/null 2>&1; then
        run systemctl --user stop multiproxy-courier.service 2>/dev/null
        run systemctl --user disable multiproxy-courier.service >/dev/null 2>&1
    fi
    [ -x "$DIR_TO_REMOVE/multiproxy-courier" ] && run "$DIR_TO_REMOVE/multiproxy-courier" stop >/dev/null 2>&1
    for p in $(pgrep -f "courier.py" 2>/dev/null); do [ "$(readlink -f "/proc/$p/cwd" 2>/dev/null)" = "$DIR_TO_REMOVE" ] && run kill "$p" 2>/dev/null; done
    run rm -f "$unit_dir/multiproxy-courier.service"
    have systemctl && systemctl --user daemon-reload >/dev/null 2>&1
    [ "$(readlink -f "$HOME/.local/bin/multiproxy-courier" 2>/dev/null)" = "$DIR_TO_REMOVE/multiproxy-courier" ] && run rm -f "$HOME/.local/bin/multiproxy-courier"
    [ -n "${STATE_MODELS_DIR:-}" ] && [ -d "$STATE_MODELS_DIR" ] && [ "${STATE_MODELS_CREATED:-no}" = yes ] && run rm -rf "$STATE_MODELS_DIR"
    [ -d "$DIR_TO_REMOVE" ] && run rm -rf "$DIR_TO_REMOVE"
    if [ "${STATE_LINGER_WAS:-}" = no ] && have loginctl; then run loginctl disable-linger "$(id -un)" 2>/dev/null; fi
    run rm -f "$STATE_FILE"
    record "Uninstall" PASS "the Courier, its service, its launcher and what this script downloaded are gone"
    write_report_and_exit 0
fi

[ "$LOCAL" = no ] || [ "$(uname -s)" = Linux ] || { printf 'This is %s, not Linux. Run this on a Linux machine.\n' "$(uname -s)"; exit 1; }
if [ "$LOCAL" = yes ]; then

# ---------------------------------------------------------------- 1. this machine
head_ "1. This machine"
FEAT=install
OS_ID="unknown"; OS_VER=""; OS_NAME="unknown"; OS_LIKE=""
if [ -r /etc/os-release ]; then
    OS_ID="$(. /etc/os-release; echo "${ID:-unknown}")"; OS_VER="$(. /etc/os-release; echo "${VERSION_ID:-}")"
    OS_NAME="$(. /etc/os-release; echo "${PRETTY_NAME:-$ID}")"; OS_LIKE="$(. /etc/os-release; echo "${ID_LIKE:-}")"
fi
say "Distro:    $OS_NAME"
say "Kernel:    $(uname -sr) ($(uname -m))"
say "Memory:    $(awk '/MemTotal/ {printf "%.1f GB", $2/1048576}' /proc/meminfo 2>/dev/null)   CPUs: $(nproc 2>/dev/null)"
DISK_FREE_GB="$(df -Pk "$HOME" 2>/dev/null | awk 'NR==2 {printf "%d", $4/1048576}')"
say "Disk free: ${DISK_FREE_GB:-?} GB (in the home folder)"

IN_CONTAINER=no
if [ -f /.dockerenv ] || [ -f /run/.containerenv ] || { have systemd-detect-virt && systemd-detect-virt -c >/dev/null 2>&1; }; then IN_CONTAINER=yes; fi
SYSTEMD_RUNNING=no; [ -d /run/systemd/system ] && SYSTEMD_RUNNING=yes
say "Container:  $IN_CONTAINER   Service manager (systemd) running: $SYSTEMD_RUNNING"
USER_BUS=no
if [ "$SYSTEMD_RUNNING" = yes ] && have systemctl; then
    export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
    systemctl --user show-environment >/dev/null 2>&1 && USER_BUS=yes
fi
say "systemd user services usable: $USER_BUS"
LINGER_WAS="n/a"
if have loginctl && [ "$SYSTEMD_RUNNING" = yes ]; then LINGER_WAS="$(loginctl show-user "$(id -un)" -p Linger --value 2>/dev/null || echo n/a)"; fi
say "Linger (keeps user services running after you log out): $LINGER_WAS"
PYV="$(python3 --version 2>&1 || true)"; have python3 || PYV="none"
say "Python:    ${PYV:-none}"
say "curl: $(have curl && echo yes || echo no)   wget: $(have wget && echo yes || echo no)   OpenMP library (libgomp): $( { have ldconfig && ldconfig -p 2>/dev/null | grep -q 'libgomp.so.1'; } && echo yes || echo no)"
FW="none found"
have ufw && ufw status 2>/dev/null | grep -qi 'status: active' && FW="ufw (active)"
have firewall-cmd && firewall-cmd --state 2>/dev/null | grep -q running && FW="firewalld (running)"
say "Firewall:  $FW"

# ---- graphics, as the machine reports them (the Courier's own view comes after it is installed)
head_ "2. Graphics cards (what this machine reports)"
FEAT=gpu
GPU_NVIDIA=no; GPU_AMD=no; GPU_INTEL=no
if have lspci; then
    lspci -nn 2>/dev/null | grep -Ei 'vga|3d controller|display controller' | sed 's/^[0-9a-f:.]* //' | while read -r l; do say "  $l"; done
    lspci -nn 2>/dev/null | grep -Ei 'vga|3d controller|display controller' | grep -qi nvidia && GPU_NVIDIA=yes
    lspci -nn 2>/dev/null | grep -Ei 'vga|3d controller|display controller' | grep -Eqi 'amd|ati|advanced micro' && GPU_AMD=yes
    lspci -nn 2>/dev/null | grep -Ei 'vga|3d controller|display controller' | grep -qi intel && GPU_INTEL=yes
else
    say "  (lspci is not installed, so cards without a driver cannot be listed)"
fi
if have nvidia-smi; then
    GPU_NVIDIA=yes
    nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>/dev/null | while read -r l; do say "  NVIDIA: $l"; done
    CUDA_V="$(nvidia-smi 2>/dev/null | sed -n 's/.*CUDA Version: *\([0-9.]*\).*/\1/p' | head -1)"
    say "  NVIDIA driver supports CUDA: ${CUDA_V:-unknown}"
fi
ROCM_OK=no; VULKAN_OK=no
[ -e /dev/kfd ] && have rocm-smi && rocm-smi --showproductname >/dev/null 2>&1 && ROCM_OK=yes
if have vulkaninfo; then
    VK="$(vulkaninfo --summary 2>/dev/null | grep -E 'deviceName|driverName' | sed 's/^ *//' | head -6)"
    [ -n "$VK" ] && VULKAN_OK=yes
    [ -n "$VK" ] && printf '%s\n' "$VK" | while read -r l; do say "  Vulkan: $l"; done
fi
say "  Display devices in /dev/dri: $(ls /dev/dri 2>/dev/null | tr '\n' ' ')"
[ "$GPU_AMD" = yes ] && say "  AMD card: ROCm usable = $ROCM_OK (needs /dev/kfd and rocm-smi), Vulkan usable = $VULKAN_OK (needs vulkaninfo to list a device)"
[ "$GPU_INTEL" = yes ] && say "  Intel card: Vulkan usable = $VULKAN_OK"
if [ "$GPU_NVIDIA$GPU_AMD$GPU_INTEL" = nonono ]; then say "  No graphics card found: the Courier will use the processor only."; fi

# ---------------------------------------------------------------- 3. can this distro install the Courier at all?
head_ "3. Can this machine install the Courier?"
[ "$(uname -s)" = Linux ] || die_early "This is $(uname -s), not Linux. Run this on a Linux machine."
DISTRO_NOTE=""
case "$OS_ID:$OS_VER" in
    ubuntu:22.04|ubuntu:24.04) DISTRO_NOTE="supported (tested)" ;;
    debian:12) DISTRO_NOTE="supported; the python3-venv package is not installed by default, so --install-prereqs (or apt install python3-venv) is needed first" ;;
    fedora:*) DISTRO_NOTE="best-effort: Python and venv are included; libgomp comes from the 'libgomp' package" ;;
    arch:*|manjaro:*|endeavouros:*|cachyos:*) DISTRO_NOTE="best-effort (rolling): Python is current, 'python' provides python3; systemd user services work" ;;
    *) case " $OS_LIKE " in *" debian "*|*" ubuntu "*|*" fedora "*|*" rhel "*|*" arch "*) DISTRO_NOTE="not tested: it looks like a relative of a supported distro, so it may work" ;; *) DISTRO_NOTE="not tested" ;; esac ;;
esac
say "Distro status: $DISTRO_NOTE"
INIT_PID1="$(ps -p 1 -o comm= 2>/dev/null | tr -d ' ')"
if [ "$SYSTEMD_RUNNING" = no ] && [ "$IN_CONTAINER" = no ]; then
    case "$INIT_PID1" in
        systemd) : ;;
        *) say "Init system is '${INIT_PID1:-unknown}', not systemd: the Courier will run, but it cannot start itself at boot (the installer says so)." ;;
    esac
fi

pkg_cmd=""
if have apt-get; then pkg_cmd="apt-get install -y python3-venv curl libgomp1"
elif have dnf; then pkg_cmd="dnf install -y python3 curl libgomp"
elif have pacman; then pkg_cmd="pacman -Sy --noconfirm python curl gcc-libs"
fi
SUDO=""; [ "$(id -u)" -ne 0 ] && have sudo && SUDO="sudo"
python_ok() { have python3 && python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; }
venv_ok() { python3 -c 'import venv, ensurepip' >/dev/null 2>&1; }
fetch_ok() { have curl || have wget; }
if [ "$PREREQS" = yes ] && [ -n "$pkg_cmd" ] && { ! python_ok || ! venv_ok || ! fetch_ok; }; then
    say "Installing the missing basics with: $SUDO $pkg_cmd"
    if have apt-get; then run $SUDO apt-get update -y >/dev/null 2>&1; fi
    run $SUDO $pkg_cmd >/dev/null 2>&1 || say "  (that did not finish cleanly; the checks below say what is still missing)"
fi
if [ "$DRY" = no ]; then
    python_ok || die_early "Python 3.10 or newer is missing here (found: ${PYV:-none}). Install it with: ${pkg_cmd:-your package manager}, then run this again (or add --install-prereqs)."
    venv_ok || die_early "Python's venv module is missing here. Install it with: ${pkg_cmd:-your package manager} (on Debian and Ubuntu the package is python3-venv), then run this again."
    fetch_ok || die_early "Neither curl nor wget is installed here. Install one (${pkg_cmd:-your package manager}), then run this again."
fi
PYV="none"; have python3 && PYV="$(python3 --version 2>&1)"
if python_ok && venv_ok && fetch_ok; then pass "This machine can run the installer" "$OS_NAME, ${PYV}"; else note "This machine can run the installer" "not yet: python 3.10+, venv or curl is missing (add --install-prereqs); this was a dry run"; fi

# ---------------------------------------------------------------- 4. install
head_ "4. Installing the Courier with the real install-courier.sh"
FEAT=install
INSTALLER_FILE="$INSTALLER"
case "$INSTALLER" in
    http://*|https://*)
        INSTALLER_FILE="$WORK/install-courier.sh"
        if [ "$DRY" = yes ]; then say "  (dry run) would download $INSTALLER"; else
            if have curl; then curl -fsSL "$INSTALLER" -o "$INSTALLER_FILE"; else wget -q "$INSTALLER" -O "$INSTALLER_FILE"; fi || die_early "Couldn't download the installer from $INSTALLER."
        fi ;;
    *) [ -f "$INSTALLER_FILE" ] || die_early "I can't find the installer at $INSTALLER. Pass --installer PATH_OR_ADDRESS." ;;
esac
if [ -n "$ARCHIVE" ]; then
    case "$ARCHIVE" in http://*|https://*|file://*) ARCHIVE_URL="$ARCHIVE" ;; *) [ -f "$ARCHIVE" ] || die_early "I can't find the package at $ARCHIVE."; ARCHIVE_URL="file://$(readlink -f "$ARCHIVE")" ;; esac
fi
if [ "$DRY" = no ] && [ "$INSTALL_DIR" != "$(readlink -f "$INSTALL_DIR" 2>/dev/null || echo "$INSTALL_DIR")" ]; then INSTALL_DIR="$(readlink -f "$INSTALL_DIR" 2>/dev/null || echo "$INSTALL_DIR")"; fi
ALREADY=no; [ -f "$INSTALL_DIR/courier.py" ] && ALREADY=yes
say "Install folder: $INSTALL_DIR$([ "$ALREADY" = yes ] && echo ' (already installed: it is updated in place)')"
if [ "$DRY" = yes ]; then
    say "  (dry run) would run: ${ARCHIVE:+MULTIPROXY_ARCHIVE_URL=$ARCHIVE_URL }COURIER_PORT=$PORT COURIER_DISCOVERY_PORT=$DISCOVERY_PORT bash $INSTALLER_FILE --yes --dir $INSTALL_DIR"
    say "  (dry run) would then check: service, restart, listening addresses, report, graphics, sign-in link, uninstall"
    [ "$WITH_MODEL" = yes ] && say "  (dry run) would install the small decision model and ask it one question"
    [ "$WITH_VOICE" = yes ] && say "  (dry run) would install the voice and speak one line"
    record "Dry run" NOTE "nothing was changed"
    write_report_and_exit 0
fi
if [ "$ALREADY" = no ]; then
    cat > "$STATE_FILE" <<STATE
STATE_DIR="$INSTALL_DIR"
STATE_LINGER_WAS="$LINGER_WAS"
STATE
    chmod 600 "$STATE_FILE"
fi
INSTALL_LOG="$WORK/install.log"
(
    export COURIER_PORT="$PORT" COURIER_DISCOVERY_PORT="$DISCOVERY_PORT"
    [ -n "$ARCHIVE" ] && export MULTIPROXY_ARCHIVE_URL="$ARCHIVE_URL"
    bash "$INSTALLER_FILE" --yes --dir "$INSTALL_DIR" < /dev/null
) > "$INSTALL_LOG" 2>&1
IRC=$?
# The installer prints the pairing code at its end: it goes to the screen the way the real installer shows it, never into the report.
sed -E 's#^(  Pairing code:  ).*#\1<shown on screen only>#' "$INSTALL_LOG" | tail -n 25 >> "$RAW"
tail -n 12 "$INSTALL_LOG"
if [ "$IRC" -ne 0 ]; then
    fail "Install with install-courier.sh" "it stopped (exit $IRC); the last lines are above"
    write_report_and_exit 1
fi
pass "Install with install-courier.sh" "$([ "$ALREADY" = yes ] && echo updated in place || echo installed)"
PY="$INSTALL_DIR/.venv/bin/python"
[ -x "$PY" ] || { fail "The Courier's own Python exists" "missing: $PY"; write_report_and_exit 1; }
CODE="$(cd "$INSTALL_DIR" && "$PY" -c 'import json, sys
try:
    print(json.load(open("courier.json")).get("code", ""))
except Exception:
    print("")' 2>/dev/null)"
[ -n "$CODE" ] || { fail "The pairing code can be read" "courier.py code printed nothing"; write_report_and_exit 1; }

# ---------------------------------------------------------------- 5. it runs as a service
head_ "5. Running as a service"
FEAT=boot
UNIT=multiproxy-courier.service
courier_up() { (cd "$INSTALL_DIR" && "$PY" courier.py status 2>/dev/null | grep -q 'is running'); }
if wait_for 60 courier_up; then pass "The Courier is running" ""; else fail "The Courier is running" "status says it is not running after a minute"; fi
if [ "$USER_BUS" = yes ]; then
    systemctl --user is-enabled "$UNIT" >/dev/null 2>&1 && pass "Service is enabled (starts with the machine)" "" || fail "Service is enabled (starts with the machine)" "systemctl says it is not enabled"
    systemctl --user is-active "$UNIT" >/dev/null 2>&1 && pass "Service is active" "" || fail "Service is active" "$(systemctl --user is-active "$UNIT" 2>&1)"
    LING="$(loginctl show-user "$(id -un)" -p Linger --value 2>/dev/null || echo unknown)"
    if [ "$LING" = yes ]; then pass "Keeps running after you log out" "linger is on"; else fail "Keeps running after you log out" "linger is $LING: the service would stop when you log out (the installer tries 'loginctl enable-linger')"; fi
    systemctl --user restart "$UNIT" >/dev/null 2>&1
    if wait_for 60 courier_up; then pass "Survives a restart of the service" ""; else fail "Survives a restart of the service" "not running a minute after a restart"; fi
    MP="$(systemctl --user show -p MainPID --value "$UNIT" 2>/dev/null)"
    if [ -n "$MP" ] && [ "$MP" != 0 ]; then
        kill -9 "$MP" 2>/dev/null
        sleep 1
        if wait_for 45 restarted_after_kill; then
            pass "Comes back by itself after a crash" "the service restarted it"
        else fail "Comes back by itself after a crash" "still down 45 seconds after the process was killed"; fi
    else note "Comes back by itself after a crash" "could not read the service's process number"; fi
else
    if [ "$IN_CONTAINER" = yes ] || [ "$SYSTEMD_RUNNING" = no ]; then
        skip "Service checks (enabled, restart, crash, linger)" "no systemd user service here ($([ "$IN_CONTAINER" = yes ] && echo 'a container' || echo 'no systemd running'))"
        # Without a service the installer runs it in the background: check the launcher's own stop and start instead.
        "$INSTALL_DIR/multiproxy-courier" stop >/dev/null 2>&1; sleep 2
        "$INSTALL_DIR/multiproxy-courier" start >/dev/null 2>&1
        if wait_for 60 courier_up; then pass "Launcher: stop then start works (background mode)" ""; else fail "Launcher: stop then start works (background mode)" "not running a minute after start"; fi
    else
        fail "systemd user services work" "systemd is running but 'systemctl --user' has no session (typical over ssh). Try: loginctl enable-linger $(id -un) and log in again, then run this again"
    fi
fi

# ---------------------------------------------------------------- 6. where it listens
head_ "6. Where the Courier listens"
FEAT=network
python3 - "$PORT" <<'PYEND' > "$WORK/listen.txt" 2>/dev/null
import sys, ipaddress, re, subprocess
port = int(sys.argv[1])
addrs = set()
try:
    out = subprocess.run(["ss", "-H", "-ltn"], capture_output=True, text=True, timeout=10).stdout
    for line in out.splitlines():
        cols = line.split()
        if len(cols) >= 4 and cols[3].rsplit(":", 1)[-1] == str(port):
            addrs.add(cols[3].rsplit(":", 1)[0].strip("[]").split("%")[0])
except Exception:
    pass
if not addrs:                                    # no ss: read the kernel's own table
    for path, v6 in (("/proc/net/tcp", False), ("/proc/net/tcp6", True)):
        try:
            for line in open(path).read().splitlines()[1:]:
                f = line.split()
                if f[3] != "0A":
                    continue
                host, p = f[1].split(":")
                if int(p, 16) != port:
                    continue
                if v6:
                    b = bytes.fromhex(host)
                    b = b"".join(b[i:i+4][::-1] for i in range(0, 16, 4))
                    addrs.add(str(ipaddress.IPv6Address(b)))
                else:
                    addrs.add(str(ipaddress.IPv4Address(bytes.fromhex(host)[::-1])))
        except OSError:
            pass
for a in sorted(addrs):
    print(a)
PYEND
LISTEN="$(cat "$WORK/listen.txt")"
if [ -z "$LISTEN" ]; then
    fail "Listening where expected" "nothing is listening on port $PORT"
else
    say "  Listening on port $PORT at: $(echo $LISTEN)"
    BAD=""
    for a in $LISTEN; do
        cls="$(python3 -c '
import sys, ipaddress
a = ipaddress.ip_address(sys.argv[1])
if a.is_unspecified: print("wildcard")
elif a.is_loopback: print("loopback")
elif a.is_private or a.is_link_local or a in ipaddress.ip_network("100.64.0.0/10"): print("private")
else: print("public")' "$a" 2>/dev/null)"
        case "$cls" in wildcard|public) BAD="$BAD $a($cls)" ;; esac
    done
    if [ -z "$BAD" ]; then pass "Listens on private addresses only" "$(echo $LISTEN)"; else fail "Listens on private addresses only" "reachable from outside at:$BAD"; fi
fi
# Calls to this machine's own addresses stand in for the main device. They carry NO pairing code, on purpose: a call with the right code from a
# non-loopback address would pair the Courier with this machine and hide the code the real main device needs. The Courier's two refusals tell the
# cases apart: "Incorrect pairing code" means the address was accepted as a private one; "only talks to devices on your private home network" means it was not.
for ip in $(hostname -I 2>/dev/null); do
    case "$ip" in *:*) continue ;; esac
    verdict="$(python3 - "$ip" "$PORT" <<'PYEND'
import sys, urllib.request, urllib.error
ip, port = sys.argv[1], sys.argv[2]
try:
    urllib.request.urlopen(urllib.request.Request(f"http://{ip}:{port}/courier/v1/report"), timeout=8)
    print("answered")
except urllib.error.HTTPError as e:
    body = e.read().decode("utf-8", "replace")
    print("network-refused" if "private home network" in body else ("code-refused" if "pairing code" in body else "http-%s" % e.code))
except Exception:
    print("not-listening")
PYEND
)"
    cls="$(python3 -c '
import sys, ipaddress
a = ipaddress.ip_address(sys.argv[1])
if a.is_private: print("home-network")
elif a in ipaddress.ip_network("100.64.0.0/10"): print("tailscale-style")
else: print("public")' "$ip")"
    case "$cls" in
        home-network) [ "$verdict" = code-refused ] && pass "Accepted as a private address: $ip" "" || fail "Accepted as a private address: $ip" "$verdict" ;;
        public) case "$verdict" in not-listening|network-refused) pass "Not reachable on the public address" "$verdict" ;; *) fail "Not reachable on the public address" "$ip: $verdict" ;; esac ;;
        tailscale-style) if [ "$verdict" = code-refused ]; then pass "Accepted as a private address: $ip (100.64.0.0/10, a Tailscale-style link)" ""; else note "Private link $ip (100.64.0.0/10, Tailscale-style): $verdict" "a Courier built before build 3 does not treat this link as private, so it is refused or not listened on. Update the Courier on this machine, then run the check again." ; fi ;;
    esac
done
if ! echo "$LISTEN" | grep -Eq '^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.|100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.)'; then
    note "No private address to listen on" "the Courier listens on the machine's 10.x / 172.16-31.x / 192.168.x addresses (and a Tailscale link's 100.64-127.x when it is up) and this machine has none, so only itself can reach it"
fi

fi    # (end of the parts that only make sense on the machine the Courier is installed on)

# ---------------------------------------------------------------- 7. what it reports
head_ "7. What the Courier reports"
FEAT=report
R="$(api GET /courier/v1/report)"
RC="$(printf '%s\n' "$R" | head -n 1)"
if [ "$RC" = 200 ]; then
    printf '%s\n' "$R" | body_of > "$WORK/report.json"
    COURIER_BUILD_SEEN="$(json_get 'd["courier"]["build"]' < "$WORK/report.json")"
    MP_VERSION="$(json_get 'd["courier"]["version"]' < "$WORK/report.json")"
    pass "The report answers" "Courier $MP_VERSION, build $COURIER_BUILD_SEEN, platform $(json_get 'd["courier"]["platform"]' < "$WORK/report.json")"
    MISSING="$(python3 - "$WORK/report.json" <<'PYEND'
import json, sys
d = json.load(open(sys.argv[1])); m = []
if not (d.get("courier") or {}).get("name"):
    m.append("the device's name")
for k, label in (("programs", "the programs found"), ("gpus", "the graphics cards"), ("disk", "the disk"), ("pc_memory", "the memory"), ("settings", "the settings")):
    if k not in d or d[k] is None:
        m.append(label)
print(", ".join(m))
PYEND
)"
    CARD_COUNT="$(json_get 'len(d.get("gpus") or [])' < "$WORK/report.json")"
    CARD_TEXT="$(json_get '", ".join(str(g.get("name")) for g in (d.get("gpus") or []))' < "$WORK/report.json")"
    if [ "${CARD_COUNT:-0}" -gt 0 ] 2>/dev/null; then HAS_CARD=yes; else HAS_CARD=no; fi
    [ "$LOCAL" = yes ] || DISK_FREE_GB="$(json_get 'int(d["disk"]["free_gb"])' < "$WORK/report.json")"
    RAN_ON="${OS_NAME:-unknown}$([ "$LOCAL" = no ] && echo " (over the network: $(json_get 'd["courier"]["platform"]' < "$WORK/report.json"))"), graphics: ${CARD_TEXT:-none}"
    [ "$LOCAL" = yes ] || RAN_ON="$(json_get 'd["courier"]["platform"]' < "$WORK/report.json") over the network, graphics: ${CARD_TEXT:-none}"
    [ -z "$MISSING" ] && pass "The report names the device, its programs, cards, disk and memory" "" || fail "The report names the device, its programs, cards, disk and memory" "missing: $MISSING"
    FEAT=older
    if [ -n "$COURIER_BUILD_SEEN" ] && [ "$COURIER_BUILD_SEEN" -ge 0 ] 2>/dev/null; then
        pass "The report carries the Courier's build number" "build $COURIER_BUILD_SEEN (the main device uses it to tell an old Courier from a new one)"
    else
        fail "The report carries the Courier's build number" "none: the main device would call this Courier older than it is"
    fi
    FEAT=report
    python3 - "$WORK/report.json" <<'PYEND' | while IFS= read -r l; do say "$l"; done
import json, sys
d = json.load(open(sys.argv[1]))
print("  Graphics cards the Courier sees:")
for g in d.get("gpus") or []:
    print("    - %s, %s MB memory" % (g.get("name"), g.get("memory_total_mb") or g.get("total_mb") or g.get("memory_mb") or "?"))
if not d.get("gpus"):
    print("    (none: the processor will be used)")
print("  Programs found:")
for p in d.get("programs") or []:
    print("    - %s: installed=%s running=%s %s" % (p.get("id"), p.get("installed"), p.get("running"), ("version " + str(p.get("version"))) if p.get("version") else ""))
m = d.get("pc_memory") or {}
print("  Memory (MB): %s" % json.dumps({k: v for k, v in m.items() if isinstance(v, (int, float))}))
PYEND
else
    fail "The report answers" "got $RC from /courier/v1/report"
fi
FEAT=pair
NOCODE="$(API_CODE="" api GET /courier/v1/report | head -n 1)"
[ "$NOCODE" = 403 ] && pass "Refuses a request with no pairing code" "" || fail "Refuses a request with no pairing code" "got $NOCODE, expected 403"
WRONG="$(API_CODE="XXX-XXX" api GET /courier/v1/report | head -n 1)"
[ "$WRONG" = 403 ] && pass "Refuses a wrong pairing code" "" || fail "Refuses a wrong pairing code" "got $WRONG, expected 403"
FEAT=report
# The real Courier's own status lines: they show the pairing code only while nothing is paired, and that is the only place it appears.
if [ "$LOCAL" = yes ]; then
shown ""
shown "  What 'multiproxy-courier status' says (this is the one place the pairing code is shown; it is not in the report file):"
(cd "$INSTALL_DIR" && "$PY" courier.py status 2>&1) | sed 's/^/    /' | while IFS= read -r l; do shown "$l"; done
(cd "$INSTALL_DIR" && "$PY" courier.py status 2>&1) | sed -E 's#[A-Z0-9]{3}-[A-Z0-9]{3}#<code>#g' | sed 's/^/    /' >> "$RAW"
fi

# ---------------------------------------------------------------- 8. graphics, as the Courier sees them
head_ "8. Graphics, as MultiProxy's own detection sees them"
FEAT=gpu
KIND=""; OPTS=""
if [ "$LOCAL" = yes ]; then
( cd "$INSTALL_DIR" && "$PY" - <<'PYEND' ) 2>&1 | while IFS= read -r l; do say "$l"; done
import dataclasses, json
try:
    import local_models
    g = local_models.detect_gpu()
    print("  detect_gpu():", json.dumps(dataclasses.asdict(g)) if g else "no card")
except Exception as e:
    print("  detect_gpu() failed:", type(e).__name__, e)
try:
    import gpu_choice
    for c in gpu_choice.list_cards():
        print("  card:", json.dumps(dataclasses.asdict(c)) if dataclasses.is_dataclass(c) else c)
except Exception as e:
    print("  list_cards() failed:", type(e).__name__, e)
PYEND
fi
if [ "$HAS_CARD" = yes ]; then pass "The Courier sees a graphics card" "$CARD_TEXT"; else pass "The Courier looked for a graphics card and found none" "the processor will be used; the parts that need a card say 'not applicable (no graphics card)'"; fi
E="$(api GET /courier/v1/programs/llamacpp/engine)"
if [ "$(printf '%s\n' "$E" | head -n 1)" = 200 ]; then
    printf '%s\n' "$E" | body_of > "$WORK/engine.json"
    KIND="$(json_get 'd.get("kind")' < "$WORK/engine.json")"
    OPTS="$(json_get '", ".join(str(o.get("kind")) for o in d.get("options") or [])' < "$WORK/engine.json")"
    say "  Engine path MultiProxy would use: ${KIND:-none installed yet}   (offered for this card: ${OPTS:-none})"
    # Which one is usable, in words
    [ "$GPU_NVIDIA" = yes ] && say "  NVIDIA: CUDA path ${CUDA_V:+(driver supports CUDA $CUDA_V)}; Vulkan usable = $VULKAN_OK"
    [ "$GPU_AMD" = yes ] && say "  AMD: ROCm usable = $ROCM_OK, Vulkan usable = $VULKAN_OK -> $([ "$ROCM_OK" = yes ] && echo 'ROCm or Vulkan' || { [ "$VULKAN_OK" = yes ] && echo 'Vulkan only' || echo 'neither: install Vulkan drivers (mesa-vulkan-drivers) or ROCm'; })"
    [ "$GPU_INTEL" = yes ] && say "  Intel: Vulkan usable = $VULKAN_OK"
    [ -n "$OPTS" ] && pass "An engine path is offered for this machine" "$OPTS" || note "An engine path is offered for this machine" "none offered (processor only?)"
else
    note "Engine path" "the engine route did not answer"
fi

# ---------------------------------------------------------------- 9. sign-in without a browser (a stand-in program, never a real sign-in)
head_ "9. Sign-in link and code with no browser (stand-in program)"
FEAT=claude
if [ "$LOCAL" = no ]; then
    skip "Sign-in link and code through a real pseudo-terminal (stand-in)" "run this check on the Courier's own machine; the Claude row below tests the real routes"
else
STAND="$WORK/stand-in-signin.py"
cat > "$STAND" <<'PYEND'
import sys, time
LINK = "https://stand-in.invalid/oauth/authorize?state=STANDIN123"
sys.stdout.write("\x1b[2J\x1b[HStand-in sign-in (made up, nothing real)\r\n\r\n\x1b]8;;" + LINK + "\x1b\\" + LINK + "\x1b]8;;\x1b\\\r\n\r\nPaste code here if prompted > ")
sys.stdout.flush()
line = sys.stdin.readline().strip()
if line.endswith("#STANDIN123"):
    sys.stdout.write("\r\nSTAND-IN-LONG-CODE-0123456789\r\nStore this securely.\r\n"); sys.stdout.flush()
    sys.exit(0)
time.sleep(5)
sys.exit(1)
PYEND
SIGNIN_OUT="$( cd "$INSTALL_DIR" && BROWSER=true "$PY" - "$STAND" <<'PYEND' 2>&1
import sys, time, os
import terminal_process, claude_courier
stand = sys.argv[1]
try:
    t = terminal_process.open_terminal([sys.executable, stand], dict(os.environ), None)
except Exception as e:
    print("NOTERMINAL", type(e).__name__); sys.exit(0)
raw, link, t0 = b"", "", time.time()
while time.time() - t0 < 10 and not link:
    raw += t.read(); link = claude_courier.find_link(raw.decode("utf-8", "replace")); time.sleep(0.2)
if not link:
    print("NOLINK"); t.kill(); sys.exit(0)
print("LINK", link.split("?")[0])
t.write("fakecode#STANDIN123\r")
t0 = time.time(); raw2 = b""
while time.time() - t0 < 10:
    raw2 += t.read()
    if b"STAND-IN-LONG-CODE" in raw2: break
    time.sleep(0.2)
print("CODE-OK" if b"STAND-IN-LONG-CODE" in raw2 else "NOCODE")
t.kill()
PYEND
)"
case "$SIGNIN_OUT" in
    *CODE-OK*) pass "Sign-in link and code through a real pseudo-terminal" "link found, code typed, long code read back; no browser involved" ;;
    *NOTERMINAL*) fail "Sign-in link and code through a real pseudo-terminal" "this machine cannot open a pseudo-terminal ($SIGNIN_OUT)" ;;
    *NOLINK*) fail "Sign-in link and code through a real pseudo-terminal" "the link was not found in the program's output" ;;
    *) fail "Sign-in link and code through a real pseudo-terminal" "$(printf '%s' "$SIGNIN_OUT" | tail -n 1)" ;;
esac
fi
note "Real Claude sign-in" "not tested here (only a stand-in is used; a real sign-in needs your account)"

# ---------------------------------------------------------------- 10. a small model, through the real route (optional)
if [ "$WITH_MODEL" = yes ]; then
    head_ "10. The small decision model (real download, real route)"
FEAT=decisions
    if [ "${DISK_FREE_GB:-0}" -lt 6 ]; then
        skip "Install the decision model and ask it a question" "only ${DISK_FREE_GB:-?} GB free; it needs about 6 GB"
    else
        api POST /courier/v1/decisions/install '{"source":"internet"}' | head -n 1 | grep -q '^200$' || say "  (the install request was not accepted; the status below says why)"
        ST=""
        wait_for "$TIMEOUT" dec_settled
        ST="$(dec_state)"
        say "  Decision engine state after the install: ${ST:-unknown}"
        if [ "$ST" = installing ] || [ "$ST" = not_installed ] || [ -z "$ST" ]; then
            fail "Install the decision model" "state is '${ST:-unknown}' after waiting"
        else
            pass "Install the decision model" "state: $ST"
            api POST /courier/v1/decisions/start >/dev/null
            wait_for 300 dec_ready && pass "The decision engine starts" "" || fail "The decision engine starts" "not ready in 5 minutes (state: $(dec_state))"
            A="$(api POST /courier/v1/decisions/answer '{"state":"The guard stands at the gate.","questions":{"q":{"type":"noul","instructions":"Is someone at a gate?"}},"limit":120}')"
            if [ "$(printf '%s\n' "$A" | head -n 1)" = 200 ] && [ -n "$(printf '%s\n' "$A" | body_of | json_get 'd["answers"]["q"]')" ]; then pass "The model answers one test question" ""; else fail "The model answers one test question" "answer: $(printf '%s\n' "$A" | head -n 1) $(printf '%s\n' "$A" | body_of | head -c 200 | tr '\n' ' ')"; fi
        fi
    fi
fi

# ---------------------------------------------------------------- 11. the voice engine (optional)
if [ "$WITH_VOICE" = yes ]; then
    head_ "11. The voice engine"
FEAT=voice
    if [ "$ACCEPT_VOICE" != yes ]; then
        skip "Voice engine" "add --accept-voice-license once you have read and accept the voice model's licence"
    elif [ "$HAS_CARD" = no ]; then
        skip "Voice engine" "not applicable (no graphics card): the voice is only offered for a card"
    elif [ "${DISK_FREE_GB:-0}" -lt 12 ]; then
        skip "Voice engine" "only ${DISK_FREE_GB:-?} GB free; it needs about 12 GB"
    else
        VS="$(api GET /courier/v1/voice/status | body_of)"
        VM="$(printf '%s' "$VS" | json_get 'd["supportedModels"][0]')"
        VLV="$(printf '%s' "$VS" | json_get 'd.get("licenseVersion")')"
        api POST /courier/v1/voice/license "{\"from\":\"linux-check\",\"licenseVersion\":\"$VLV\",\"model\":\"$VM\"}" >/dev/null
        api POST /courier/v1/voice/install "{\"source\":\"internet\",\"model\":\"$VM\"}" >/dev/null
        wait_for "$TIMEOUT" voice_settled
        api POST /courier/v1/voice/start >/dev/null
        if wait_for 600 voice_ready; then
            pass "The voice engine starts" "$VM"
            BYTES="$(COURIER_CODE="$CODE" python3 - "$PORT" <<'PYEND'
import os, sys, json, urllib.request
req = urllib.request.Request(f"http://127.0.0.1:{sys.argv[1]}/courier/v1/voice/speak", data=json.dumps({"text": "Hello there."}).encode(),
                             headers={"Authorization": "Bearer " + os.environ["COURIER_CODE"], "Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=300) as r:
        print(len(r.read()))
except Exception as e:
    print(0)
PYEND
)"
            [ "${BYTES:-0}" -gt 1000 ] && pass "The voice speaks one line" "$BYTES bytes of audio (not kept)" || fail "The voice speaks one line" "no audio came back"
        else
            fail "The voice engine starts" "not ready in 10 minutes (state: $(api GET /courier/v1/voice/status | body_of | json_get 'd.get("state")'))"
        fi
    fi
fi

if [ "$WITH_VOICE" = no ] && [ "$HAS_CARD" = no ]; then FEAT=voice; skip "Voice engine" "not applicable (no graphics card)"; fi

# ---------------------------------------------------------------- 12. everything else the Courier does, through its own routes
# Each line below calls the same route the main device's dashboard calls (over the network with --remote), so a feature that only works on the machine itself is caught.
head_ "12. The rest of the Courier's features"
prog_get() { # prog_get PROGRAM FIELD  -> that program's field in a fresh report ("" when the program is not in it)
    api GET /courier/v1/report | body_of | json_get "next((p.get('$2') for p in d['programs'] if p['id']=='$1'), '')"
}
rep_get() { api GET /courier/v1/report | body_of | json_get "$1"; }
code_of() { printf '%s\n' "$1" | head -n 1; }
json_body() { printf '%s\n' "$1" | body_of; }

FEAT=log
LG="$(api GET "/courier/v1/log?lines=40")"
if [ "$(code_of "$LG")" = 200 ]; then
    LN="$(json_body "$LG" | json_get 'len(d.get("lines") or [])')"
    if [ "${LN:-0}" -gt 0 ] 2>/dev/null; then pass "The Log view answers with the device's recent log" "$LN lines (masked by the Courier)"; else fail "The Log view answers with the device's recent log" "it answered but had no lines"; fi
else fail "The Log view answers with the device's recent log" "got $(code_of "$LG")"; fi

FEAT=awake
KA="$(api POST /courier/v1/settings '{"keep_awake":true}')"
if [ "$(code_of "$KA")" = 200 ]; then
    if [ "$(json_body "$KA" | json_get 'd.get("keep_awake")')" = True ]; then pass "Keep awake can be switched on" "active now: $(json_body "$KA" | json_get 'd.get("keep_awake_active")')"; else fail "Keep awake can be switched on" "the setting did not stick"; fi
    api POST /courier/v1/settings '{"keep_awake":false}' >/dev/null
    [ "$(api GET /courier/v1/report | body_of | json_get 'd["settings"]["keep_awake"]')" = False ] && pass "Keep awake can be switched off again" "" || fail "Keep awake can be switched off again" "it stayed on"
else skip "Keep awake" "not applicable here: the system refused it (HTTP $(code_of "$KA")): usually no desktop session to keep awake"; fi
if [ "$(rep_get 'd["settings"].get("start_with_windows_supported")')" = True ]; then pass "Start at login is offered" ""; else skip "Start at login" "not applicable on Linux: the service starts at boot instead (see 'Starts at boot')"; fi

FEAT=folder
DR="$(api GET /courier/v1/drives)"
if [ "$(code_of "$DR")" = 200 ]; then
    CUR="$(json_body "$DR" | json_get 'd.get("current") or d.get("default")')"
    NDR="$(json_body "$DR" | json_get 'len(d.get("drives") or [])')"
    pass "Lists the drives and the models folder" "$NDR places, folder: ${CUR:-none}"
    if [ "$LOCAL" = yes ]; then TARGET="$INSTALL_DIR/models-check"; mkdir -p "$TARGET"; else TARGET="$CUR"; fi
    MF="$(api POST /courier/v1/models-folder "{\"path\":\"$TARGET\"}")"
    if [ "$(code_of "$MF")" = 200 ] && [ -n "$(json_body "$MF" | json_get 'd.get("folder")')" ]; then pass "Change folder: a new models folder is accepted" "$([ "$LOCAL" = yes ] && echo 'a new folder' || echo 'the current folder, re-applied (a folder cannot be made on another machine)')"; else fail "Change folder: a new models folder is accepted" "got $(code_of "$MF") $(json_body "$MF" | head -c 160 | tr '\n' ' ')"; fi
    [ -z "$CUR" ] || api POST /courier/v1/models-folder "{\"path\":\"$CUR\"}" >/dev/null
    [ "$LOCAL" = yes ] && rmdir "$TARGET" 2>/dev/null
else fail "Lists the drives and the models folder" "got $(code_of "$DR")"; fi

FEAT=lmstudio
if [ -n "$(prog_get lmstudio id)" ]; then pass "LM Studio is found" "state $(prog_get lmstudio state)"; else skip "LM Studio" "not applicable: LM Studio is a desktop app and is not installed here"; fi

# ---- Ollama (the Courier finds it, starts and stops it and pulls models; the person installs it)
FEAT=ollama
if [ -z "$(prog_get ollama id)" ]; then
    person "Ollama: install it and use it" "install Ollama on this machine with its own installer (ollama.com/download), then run this again with --with-engines. The Courier finds Ollama and starts, stops and pulls models for it; it does not install it"
else
    OL_WAS="$(prog_get ollama state)"
    pass "Ollama is found" "version $(prog_get ollama version), state $OL_WAS"
    if [ "$WITH_ENGINES" = yes ]; then
        ol_state_is() { [ "$(prog_get ollama state)" = "$1" ] || { [ "$1" = running ] && [ "$(prog_get ollama state)" = ready ]; }; }
        ol_up() { case "$(prog_get ollama state)" in ready|busy|running|loading) return 0 ;; *) return 1 ;; esac; }
        if ! ol_up; then api POST /courier/v1/programs/ollama/start >/dev/null; fi
        if wait_for 90 ol_up; then pass "Ollama starts through the Courier" ""; else fail "Ollama starts through the Courier" "state: $(prog_get ollama state)"; fi
        PULL="$(COURIER_TIMEOUT="$TIMEOUT" api POST /courier/v1/programs/ollama/models/download '{"model":"smollm2:135m","wait":true}')"
        if [ "$(code_of "$PULL")" = 200 ]; then
            pass "Ollama pulls a tiny model" "smollm2:135m"
            CH="$(COURIER_TIMEOUT=300 api POST /v1/chat/completions '{"model":"ollama/smollm2:135m","messages":[{"role":"user","content":"Say hi."}],"max_tokens":8,"stream":false}')"
            if [ "$(code_of "$CH")" = 200 ] && [ -n "$(json_body "$CH" | json_get 'd["choices"][0]["message"]["content"]')" ]; then pass "Ollama answers one request" "through the Courier's /v1/chat/completions"; else fail "Ollama answers one request" "got $(code_of "$CH") $(json_body "$CH" | head -c 160 | tr '\n' ' ')"; fi
            api POST /courier/v1/programs/ollama/models/delete '{"model":"smollm2:135m"}' >/dev/null
        else fail "Ollama pulls a tiny model" "got $(code_of "$PULL") $(json_body "$PULL" | head -c 200 | tr '\n' ' ')"; fi
        api POST /courier/v1/programs/ollama/stop >/dev/null
        wait_for 60 '[ "$(prog_get ollama state)" = stopped ]' && pass "Ollama stops through the Courier" "" || note "Ollama stops through the Courier" "state is $(prog_get ollama state) (Ollama run as a system service may start itself again)"
        [ "$OL_WAS" = stopped ] || api POST /courier/v1/programs/ollama/start >/dev/null
    else skip "Ollama: start, pull a model, answer, stop" "add --with-engines"; fi
fi

# ---- the built-in engine (llama.cpp): find models, download, pause (cancel) a download, load, ask, unload, delete
FEAT=llamacpp
FD="$(api POST /courier/v1/models/find '{}')"
if [ "$(code_of "$FD")" = 200 ]; then pass "Find models already on this machine" "$(json_body "$FD" | json_get 'len(d.get("found") or [])') found in $(json_body "$FD" | json_get 'len(d.get("searched") or [])') places"; else fail "Find models already on this machine" "got $(code_of "$FD")"; fi
if [ -z "$OPTS" ] && [ "$KIND" != existing ] && [ "$KIND" != builtin ]; then
    skip "Model engine (llama.cpp): download, load, answer" "not applicable here: no engine path is offered for this machine ($([ "$HAS_CARD" = no ] && echo 'no graphics card' || echo 'none offered'))"
elif [ "$WITH_ENGINES" = yes ]; then
    TINY="$(api GET /courier/v1/report | body_of | json_get "min(((a['size_bytes'], a['id']) for p in d['programs'] if p['id']=='llamacpp' for a in p.get('available', []) if not a.get('installed')), default=(0, ''))[1]")"
    if [ -z "$TINY" ]; then
        note "Model engine (llama.cpp): download, load, answer" "no model is on offer to download (every one is already here?)"
    elif [ "${DISK_FREE_GB:-99}" -lt 4 ] 2>/dev/null; then
        skip "Model engine (llama.cpp): download, load, answer" "only $DISK_FREE_GB GB free"
    else
        FEAT=download
        DL="$(api POST /courier/v1/programs/llamacpp/models/download "{\"model\":\"$TINY\"}")"
        if [ "$(code_of "$DL")" = 200 ]; then
            sleep 4
            api POST /courier/v1/programs/llamacpp/downloads/cancel "{\"model\":\"$TINY\"}" >/dev/null
            dl_running() { [ "$(api GET /courier/v1/report | body_of | json_get "any(x.get('state')=='running' for p in d['programs'] if p['id']=='llamacpp' for x in p.get('downloads', []))")" = True ]; }
            if wait_for 60 '! dl_running'; then pass "A download can be started and cancelled" "$TINY"; else fail "A download can be started and cancelled" "it was still running a minute after Cancel"; fi
        else fail "A download can be started and cancelled" "got $(code_of "$DL") $(json_body "$DL" | head -c 160 | tr '\n' ' ')"; fi
        FEAT=llamacpp
        have_model() { [ "$(api GET /courier/v1/report | body_of | json_get "any(m['id']=='$TINY' for p in d['programs'] if p['id']=='llamacpp' for m in p.get('models', []))")" = True ]; }
        api POST /courier/v1/programs/llamacpp/models/download "{\"model\":\"$TINY\"}" >/dev/null
        if wait_for "$TIMEOUT" have_model; then
            pass "Model engine (llama.cpp): the smallest model downloads" "$TINY"
            COURIER_TIMEOUT="$TIMEOUT" api POST /courier/v1/programs/llamacpp/models/load "{\"model\":\"$TINY\",\"wait\":true}" >/dev/null
            CH="$(COURIER_TIMEOUT=300 api POST /v1/chat/completions "{\"model\":\"local/$TINY\",\"messages\":[{\"role\":\"user\",\"content\":\"Say hi.\"}],\"max_tokens\":8,\"stream\":false}")"
            if [ "$(code_of "$CH")" = 200 ] && [ -n "$(json_body "$CH" | json_get 'd["choices"][0]["message"]["content"]')" ]; then pass "Model engine (llama.cpp): loads it and answers one request" "through the Courier's /v1/chat/completions"; else fail "Model engine (llama.cpp): loads it and answers one request" "got $(code_of "$CH") $(json_body "$CH" | head -c 200 | tr '\n' ' ')"; fi
            api POST /courier/v1/programs/llamacpp/models/unload "{\"model\":\"$TINY\"}" >/dev/null
            api POST /courier/v1/programs/llamacpp/models/delete "{\"model\":\"$TINY\"}" >/dev/null
            wait_for 60 '! have_model' && pass "Model engine (llama.cpp): unloads and deletes it" "" || fail "Model engine (llama.cpp): unloads and deletes it" "still listed a minute later"
        else fail "Model engine (llama.cpp): the smallest model downloads" "not listed after waiting"; fi
    fi
else skip "Model engine (llama.cpp): download, load, answer" "add --with-engines (downloads the smallest model)"; fi

# ---- the Claude row (never a real sign-in: it only starts one and cancels it)
FEAT=claude
if [ "$WITH_CLAUDE" = yes ]; then
    cl_state() { api GET /courier/v1/claude/status | body_of | json_get 'd.get("state")'; }
    cl_settled() { case "$(cl_state)" in installing|"") return 1 ;; *) return 0 ;; esac; }
    api POST /courier/v1/claude/install >/dev/null
    wait_for "$TIMEOUT" cl_settled
    CS="$(cl_state)"
    if [ "$CS" = needs_signin ] || [ "$CS" = ready ]; then
        pass "Claude installs through the Courier" "state: $CS"
        SS="$(api POST /courier/v1/claude/signin/start)"
        if [ "$(code_of "$SS")" = 200 ] && [ -n "$(json_body "$SS" | json_get 'd["signin"]["url"]')" ]; then
            pass "Claude sign-in by link and code: the Courier starts it and gets a link" "$(json_body "$SS" | json_get 'd["signin"].get("browser")') (no account used)"
        else fail "Claude sign-in by link and code: the Courier starts it and gets a link" "got $(code_of "$SS") $(json_body "$SS" | head -c 200 | tr '\n' ' ')"; fi
        api POST /courier/v1/claude/signin/cancel >/dev/null
        FAKE="sk-ant-oat01-$(python3 -c 'print("x" * 95)')"
        TW="$(api POST /courier/v1/claude/signin "{\"code\":\"$FAKE\"}")"
        case "$(code_of "$TW")" in 4*|5*) if [ "$(cl_state)" = needs_signin ]; then pass "Claude sign-in by the terminal way: a made-up code is refused and not kept" "HTTP $(code_of "$TW")"; else fail "Claude sign-in by the terminal way: a made-up code is refused and not kept" "state is $(cl_state)"; fi ;; *) fail "Claude sign-in by the terminal way: a made-up code is refused and not kept" "the Courier took it (HTTP $(code_of "$TW"))" ;; esac
        api POST /courier/v1/claude/signout >/dev/null
        [ "$(cl_state)" = needs_signin ] && pass "Claude signs out" "" || fail "Claude signs out" "state is $(cl_state)"
        api POST /courier/v1/claude/uninstall >/dev/null
        [ "$(cl_state)" = not_installed ] && pass "Claude uninstalls" "" || fail "Claude uninstalls" "state is $(cl_state)"
    else fail "Claude installs through the Courier" "state: ${CS:-unknown} - $(api GET /courier/v1/claude/status | body_of | json_get 'd.get("warning")' | sed 's/<PC>/this machine/g')"; fi
else skip "Claude row: install, sign-in, sign out, uninstall" "add --with-claude (downloads Claude, never signs in)"; fi
person "Claude: a real sign-in" "on the main device press Sign in on this device's Claude row, approve in a browser (or use the link and code, or the terminal way) and check the row turns Ready by itself; then press Test"

# ---- the Copilot row (its own steps: a sign-in code is asked for and cancelled)
FEAT=copilot
if [ "$WITH_COPILOT" = yes ]; then
    cp_state() { api GET /courier/v1/copilot/status | body_of | json_get 'd.get("state")'; }
    cp_settled() { case "$(cp_state)" in installing|"") return 1 ;; *) return 0 ;; esac; }
    api POST /courier/v1/copilot/install >/dev/null
    wait_for "$TIMEOUT" cp_settled
    PS="$(cp_state)"
    if [ "$PS" = needs_signin ] || [ "$PS" = ready ]; then
        pass "Copilot installs through the Courier" "state: $PS"
        api POST /courier/v1/copilot/signin >/dev/null
        cp_code() { [ -n "$(api GET /courier/v1/copilot/status | body_of | json_get 'd["signin"]["code"]')" ]; }
        if wait_for 60 cp_code; then pass "Copilot sign-in: a sign-in code is shown (to type at GitHub)" "(no account used)"; else fail "Copilot sign-in: a sign-in code is shown (to type at GitHub)" "no code after a minute"; fi
        api POST /courier/v1/copilot/cancelsignin >/dev/null
        api POST /courier/v1/copilot/signout >/dev/null
        api POST /courier/v1/copilot/uninstall >/dev/null
        [ "$(cp_state)" = not_installed ] && pass "Copilot uninstalls" "" || fail "Copilot uninstalls" "state is $(cp_state)"
    else fail "Copilot installs through the Courier" "state: ${PS:-unknown} - $(api GET /courier/v1/copilot/status | body_of | json_get 'd.get("warning")' | sed 's/<PC>/this machine/g')"; fi
else skip "Copilot row: install, sign-in code, uninstall" "add --with-copilot (downloads Copilot, never signs in)"; fi
person "Copilot: a real sign-in" "on the main device press Sign in on this device's Copilot row, type the code it shows at github.com/login/device, and check the row turns Ready by itself; then press Test"

# ---- pairing, "Connected", and connecting again after the main device restarts (this machine stands in for the main device: only with --remote)
FEAT=connected
if [ "$LOCAL" = yes ]; then
    person "Pairing and Connected" "on the main device open Other devices, find this machine, type its pairing code and press Pair; the card must show Connected"
    person "Reconnect after the main device restarts" "restart MultiProxy on the main device (or the whole PC) and check this machine's card goes back to Connected by itself, with no new pairing"
else
    WSPY="$PY"; "$WSPY" -c 'import aiohttp' >/dev/null 2>&1 || WSPY=""
    if [ -z "$WSPY" ]; then
        person "Connected and reconnect" "this machine has no aiohttp for Python ('python3 -m pip install --user aiohttp'); install it and run again, or check on the main device: pair, see Connected, restart the main device, see Connected again"
    else
        CONN="$(COURIER_CODE="$CODE" "$WSPY" - "$BASE" <<'PYEND' 2>&1
import asyncio, os, sys
import aiohttp
base, code = sys.argv[1], os.environ["COURIER_CODE"]
H = {"Authorization": "Bearer " + code}
async def connected(s):
    async with s.get(base + "/courier/v1/report", headers=H) as r:
        return str(((await r.json()).get("courier") or {}).get("connected_to") or "")
async def main():
    out = []
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(base + "/courier/v1/ws", headers=H, heartbeat=20) as ws:
            hello = await ws.receive_json(timeout=20)
            first = await ws.receive_json(timeout=20)
            out.append("hello=%s report=%s connected=%s" % (hello.get("type"), first.get("type"), bool(await connected(s))))
        await asyncio.sleep(2)
        out.append("after_close=%s" % bool(await connected(s)))
        async with s.ws_connect(base + "/courier/v1/ws", headers=H, heartbeat=20) as ws:
            await ws.receive_json(timeout=20)
            out.append("again=%s" % bool(await connected(s)))
    print(";".join(out))
try:
    asyncio.run(main())
except Exception as e:
    print("ERROR " + type(e).__name__)
PYEND
)"
        case "$CONN" in
            *"hello=hello report=report connected=True"*) pass "Pairing and Connected: the live connection opens and the report says connected" "" ;;
            *) fail "Pairing and Connected: the live connection opens and the report says connected" "$(printf '%s' "$CONN" | tail -n 1)" ;;
        esac
        case "$CONN" in
            *"again=True"*) pass "Reconnect after the main device restarts: the live connection opens again" "closed, then opened a second time" ;;
            *) fail "Reconnect after the main device restarts: the live connection opens again" "$(printf '%s' "$CONN" | tail -n 1)" ;;
        esac
    fi
fi
FEAT=addremove
person "Add and remove the device" "on the main device: Other devices, add this machine by address and code (or from Found on your network), check its card, then press Remove and check the Courier on this machine keeps running"

# ---------------------------------------------------------------- 13. update by the update zip (this machine only)
FEAT=update
if [ "$LOCAL" = yes ]; then
    head_ "13. Update by the update zip (the installer run again over the install)"
    CODE_BEFORE="$CODE"
    ( export COURIER_PORT="$PORT" COURIER_DISCOVERY_PORT="$DISCOVERY_PORT"; [ -n "$ARCHIVE" ] && export MULTIPROXY_ARCHIVE_URL="$ARCHIVE_URL"; bash "$INSTALLER_FILE" --yes --dir "$INSTALL_DIR" < /dev/null ) > "$WORK/update.log" 2>&1
    URC=$?
    CODE_AFTER="$(cd "$INSTALL_DIR" && "$PY" -c 'import json; print(json.load(open("courier.json")).get("code", ""))' 2>/dev/null)"
    if [ "$URC" -ne 0 ]; then fail "Updating over an existing install" "the installer stopped (exit $URC)"
    elif ! wait_for 90 courier_up; then fail "Updating over an existing install" "not running 90 seconds after the update"
    elif [ "$CODE_AFTER" != "$CODE_BEFORE" ]; then fail "Updating over an existing install" "the pairing code changed (a paired main device would have to pair again)"
    else pass "Updating over an existing install keeps it running and keeps the pairing code" ""; fi
fi

# ---------------------------------------------------------------- 14. the full uninstall (this machine only)
if [ "$AND_UNINSTALL" = yes ]; then
    head_ "14. Full uninstall"
    FEAT=uninstall
    bash "$SELF" --uninstall --dir "$INSTALL_DIR" --report "$WORK/uninstall-report.txt" > "$WORK/uninstall.out" 2>&1
    LEFT=""
    [ -d "$INSTALL_DIR" ] && LEFT="$LEFT the folder"
    [ -f "$unit_dir/multiproxy-courier.service" ] && LEFT="$LEFT the service file"
    [ -e "$HOME/.local/bin/multiproxy-courier" ] && LEFT="$LEFT the launcher"
    if [ "$(python3 -c "import socket; s = socket.socket(); s.settimeout(2); print('open' if s.connect_ex(('127.0.0.1', $PORT)) == 0 else 'closed')" 2>/dev/null)" = open ]; then LEFT="$LEFT a Courier still answering on port $PORT"; fi
    if [ -z "$LEFT" ]; then pass "The full uninstall leaves nothing behind" "the folder, the service, the launcher and the port are gone"; else fail "The full uninstall leaves nothing behind" "left:$LEFT"; fi
else
    FEAT=uninstall
    if [ "$LOCAL" = yes ]; then
        person "Full uninstall" "run again with --and-uninstall, or run 'bash linux-check.sh --uninstall', then check there is no multiproxy-courier folder, service (systemctl --user status multiproxy-courier) or open port 8110 left"
    else
        person "Full uninstall" "run the check on the Courier's own machine with --and-uninstall: an uninstall cannot be checked from here"
    fi
fi

FEAT=
# ---------------------------------------------------------------- done
head_ "Done"
if [ "$LOCAL" = yes ]; then say "Run '$0 --uninstall' to remove everything this installed."; else say "Nothing was installed on this machine."; fi
write_report_and_exit 0
