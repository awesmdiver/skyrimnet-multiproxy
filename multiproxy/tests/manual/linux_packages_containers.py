"""Proves the two Linux packages in containers (needs Docker; not part of the pytest suite because it downloads and installs for real).

    py -3 tests/manual/linux_packages_containers.py [--only plain|systemd] [--scenario NAME ...] [--out DIR]

Builds the tarballs, makes two small images (plain Ubuntu, and Ubuntu running systemd) with Python and a normal user, then runs each scenario in
linux_packages_scenario.sh in a fresh container: install with defaults, install with typed answers, start/stop/restart/status/logs/open,
an update in place (offering the earlier answers back), uninstall. Containers have no GPU, publish no ports and are removed afterwards (only the ones this script starts, by name).
"""
import argparse
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
SCENARIOS = ["multiproxy-defaults", "multiproxy-typed", "multiproxy-tray", "courier-defaults", "courier-typed", "multiproxy-update", "courier-update", "security", "security-odd"]
PACKAGES = "python3 python3-venv python3-pip util-linux libgomp1 curl sudo ca-certificates xvfb iproute2"

DOCKERFILES = {
    "plain": f"FROM ubuntu:24.04\nENV DEBIAN_FRONTEND=noninteractive\nRUN apt-get update -qq && apt-get install -y -qq {PACKAGES} >/dev/null && "
             "useradd -m -s /bin/bash tester\n",
    "systemd": f"FROM jrei/systemd-ubuntu:24.04\nENV DEBIAN_FRONTEND=noninteractive\nRUN apt-get update -qq && apt-get install -y -qq {PACKAGES} dbus-user-session >/dev/null && "
               "useradd -m -s /bin/bash tester\n",
}


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)


def build_images() -> None:
    for kind, text in DOCKERFILES.items():
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "Dockerfile").write_text(text, encoding="utf-8")
            r = run(["docker", "build", "-q", "-t", f"mp-linux-test-{kind}", d])
            if r.returncode != 0:
                sys.exit(f"Couldn't build the {kind} image:\n{r.stderr[-1500:]}")


def scenario_env(kind: str, pkg: Path, name: str):
    """Start a container, run one scenario, return (exit code, output). Removes the container."""
    cname = f"mp-linux-{kind}-{uuid.uuid4().hex[:6]}"
    mounts = ["-v", f"{pkg}:/pkg:ro", "-v", f"{HERE / 'linux_packages_scenario.sh'}:/scenario.sh:ro"]
    try:
        if kind == "plain":
            r = run(["docker", "run", "--rm", "--name", cname, "-u", "tester", *mounts, "mp-linux-test-plain", "bash", "/scenario.sh", name], timeout=1500)
            return r.returncode, r.stdout + r.stderr
        r = run(["docker", "run", "-d", "--name", cname, "--privileged", "--cgroupns=host", "-v", "/sys/fs/cgroup:/sys/fs/cgroup:rw", *mounts, "mp-linux-test-systemd"])
        if r.returncode != 0:
            return 1, "Couldn't start the systemd container:\n" + r.stderr
        for _ in range(60):                     # wait for systemd to be up
            if run(["docker", "exec", cname, "systemctl", "is-system-running"]).stdout.strip() in ("running", "degraded"):
                break
            time.sleep(1)
        run(["docker", "exec", cname, "loginctl", "enable-linger", "tester"])      # starts tester's own user manager, which runs user services
        uid = run(["docker", "exec", cname, "id", "-u", "tester"]).stdout.strip()
        for _ in range(30):
            if run(["docker", "exec", cname, "test", "-S", f"/run/user/{uid}/bus"]).returncode == 0:
                break
            time.sleep(1)
        r = run(["docker", "exec", "-u", "tester", "-e", f"XDG_RUNTIME_DIR=/run/user/{uid}", "-e", f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus",
                 cname, "bash", "/scenario.sh", name], timeout=1500)
        return r.returncode, r.stdout + r.stderr
    finally:
        run(["docker", "rm", "-f", cname])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["plain", "systemd"])
    ap.add_argument("--scenario", action="append", choices=SCENARIOS)
    ap.add_argument("--out", default=r"D:\scratch\linux-packages\dist")
    args = ap.parse_args()
    if run(["docker", "info"]).returncode != 0:
        sys.exit("Docker isn't running.")
    pkg = Path(args.out)
    r = run([sys.executable, str(ROOT / "scripts" / "build_linux_packages.py"), "--out", str(pkg)])
    print(r.stdout.strip() or r.stderr.strip())
    if r.returncode != 0:
        return 1
    build_images()
    failed = False
    for kind in ([args.only] if args.only else ["plain", "systemd"]):
        for name in (args.scenario or SCENARIOS):
            if (name == "security" and kind == "plain") or (name == "security-odd" and kind == "systemd"):
                continue                    # (security needs the service files: systemd only; security-odd needs the no-service path: plain only)
            print(f"\n######## {kind} / {name}", flush=True)
            code, out = scenario_env(kind, pkg, name)
            print(out)
            print(f"######## {kind} / {name}: {'PASSED' if code == 0 else 'FAILED'}", flush=True)
            failed |= code != 0
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
