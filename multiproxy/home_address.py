"""Which of this PC's addresses is the one on the player's real home network (4.1, 2026-10-26).

`other_pcs.local_private_ips()` lists every private address Windows has, including the virtual ones WSL, Hyper-V, Docker, VirtualBox and VMware make: right for deciding where
to LISTEN, wrong for the one address to SHOW or to put in a pairing file (the other PC can never reach a virtual adapter). This module picks that one address.

The order: an adapter that is up and has a default gateway; among several, the one whose /24 also holds a paired PC, then the lowest route metric. A self-assigned
169.254 address is never one, nor an adapter without a gateway named like a virtual one (WSL, Hyper-V, vEthernet, VirtualBox, VMware, Docker, Hamachi, Tailscale);
a Tailscale address only when a paired PC is on that Tailscale network. When the adapters cannot be read, the address Windows itself would use to reach the internet
(the UDP connect trick: it sends nothing) is the one candidate.
"""
import ipaddress
import re
import socket
import sys
from typing import Callable, Optional

import hidden_process
import other_pcs

_VIRTUAL = ("wsl", "hyper-v", "vethernet", "virtualbox", "vmware", "docker", "hamachi", "tailscale", "virtual", "veth", "br-", "virbr")
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_LINK_LOCAL = ipaddress.ip_network("169.254.0.0/16")


def read_adapters(run=hidden_process.run) -> list:
    """[{"name", "desc", "ip", "gateway", "metric"}] for every adapter that is up and has an IPv4 address; [] when it cannot be read. Read-only."""
    out: list = []
    try:
        if sys.platform.startswith("linux"):
            addrs = run(["ip", "-o", "-4", "addr", "show", "up"], capture_output=True, text=True, timeout=5).stdout
            routes = run(["ip", "-4", "route", "show", "default"], capture_output=True, text=True, timeout=5).stdout
            gate: dict = {}
            for m in re.finditer(r"^default via (\S+) dev (\S+)(?:.*?metric (\d+))?", routes, re.M):
                gate.setdefault(m.group(2), (m.group(1), int(m.group(3) or 0)))
            for m in re.finditer(r"^\d+:\s+(\S+)\s+inet (\d+\.\d+\.\d+\.\d+)/", addrs, re.M):
                g, metric = gate.get(m.group(1), ("", 0))
                out.append({"name": m.group(1), "desc": "", "ip": m.group(2), "gateway": g, "metric": metric})
        elif sys.platform == "win32":
            cmd = ("Get-NetIPConfiguration | ForEach-Object { $c = $_; foreach ($a in $c.IPv4Address) { $g = ''; if ($c.IPv4DefaultGateway) { $g = $c.IPv4DefaultGateway.NextHop }; "
                   "$m = ''; if ($c.NetIPv4Interface) { $m = $c.NetIPv4Interface.InterfaceMetric }; "
                   "$c.InterfaceAlias + '|' + $c.InterfaceDescription + '|' + $a.IPAddress + '|' + $g + '|' + $m } }")
            text = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd], capture_output=True, text=True, timeout=15).stdout
            for line in text.splitlines():
                parts = line.strip().split("|")
                if len(parts) < 5:
                    continue
                metric = int(parts[-1]) if parts[-1].isdigit() else 0
                out.append({"name": parts[0], "desc": "|".join(parts[1:-3]), "ip": parts[-3], "gateway": parts[-2], "metric": metric})
    except Exception:
        return []
    return out


def default_route_ip() -> str:
    """The address this PC would send from to reach the internet. A UDP connect only picks the route; nothing is sent."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            return s.getsockname()[0]
    except OSError:
        return ""


def _virtual(a: dict) -> bool:
    text = f"{a.get('name', '')} {a.get('desc', '')}".lower()
    return any(w in text for w in _VIRTUAL)


def _same_24(a: str, b: str) -> bool:
    try:
        return ipaddress.ip_network(f"{a}/24", strict=False) == ipaddress.ip_network(f"{b}/24", strict=False)
    except ValueError:
        return False


def _valid(ip: str) -> Optional[ipaddress.IPv4Address]:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    if not isinstance(addr, ipaddress.IPv4Address) or addr in _LINK_LOCAL or other_pcs.ip_class(ip) != "private":
        return None
    return addr


def candidates(adapters: list, paired_ips=(), route_ip: str = "") -> list:
    """The addresses a PC on the home network could reach, best first: [{"ip", "name", "samePc"}]. Empty when this PC has no home network address."""
    paired = [p for p in paired_ips or [] if _valid(p)]
    tailscale_paired = any(ipaddress.ip_address(p) in _CGNAT for p in paired)
    rows: list = []
    seen: set = set()
    for a in adapters or []:
        ip = str(a.get("ip", ""))
        addr = _valid(ip)
        if addr is None or ip in seen:
            continue
        if addr in _CGNAT:
            if not (other_pcs.is_tailscale_adapter(a.get("name", "")) and tailscale_paired):
                continue
        elif not a.get("gateway") and _virtual(a):
            continue
        seen.add(ip)
        rows.append({"ip": ip, "name": a.get("name", ""), "gw": bool(a.get("gateway")), "metric": int(a.get("metric") or 0) or 9999,
                     "samePc": any(_same_24(ip, p) for p in paired)})
    if any(r["gw"] for r in rows):
        rows = [r for r in rows if r["gw"] or r["ip"] and ipaddress.ip_address(r["ip"]) in _CGNAT]
    rows.sort(key=lambda r: (not r["samePc"], not r["gw"], r["metric"], r["ip"]))
    if not rows and route_ip and _valid(route_ip) and ipaddress.ip_address(route_ip) not in _CGNAT:
        rows = [{"ip": route_ip, "name": "", "gw": True, "metric": 0, "samePc": any(_same_24(route_ip, p) for p in paired)}]
    return [{"ip": r["ip"], "name": r["name"], "samePc": r["samePc"]} for r in rows]


def home_candidates(paired_ips=(), adapters_fn: Callable[[], list] = read_adapters, route_fn: Callable[[], str] = default_route_ip) -> list:
    """Blocking (it asks the system): `candidates` for this PC as it is now."""
    return candidates(adapters_fn() or [], paired_ips, route_fn())


def home_ip(paired_ips=(), **kw) -> str:
    """The one address to show or to put in a pairing file; "" when there is none."""
    found = home_candidates(paired_ips, **kw)
    return found[0]["ip"] if found else ""


def label(c: dict) -> str:
    return f"{c['name']}: {c['ip']}" if c.get("name") else c["ip"]
