"""VPN links: a 100.x address is listened on only on a real Tailscale adapter, and a short list of other private networks (refused when public, a wildcard or malformed)."""
import pytest

import other_pcs as op


@pytest.fixture(autouse=True)
def clean_list():
    op.set_extra_networks([])
    yield
    op.set_extra_networks([])


class NoUdp:
    def __init__(self, *a, **k): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def connect(self, *a): raise OSError("no route")


@pytest.fixture
def quiet(monkeypatch):
    monkeypatch.setattr(op.socket, "socket", NoUdp)
    monkeypatch.setattr(op.sys, "platform", "win32")
    monkeypatch.setattr(op.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("192.168.1.5", 0))])


def test_a_tailscale_named_adapter_with_a_100_address_is_listened_on(quiet):
    assert op.local_private_ips(lambda: [("Tailscale", "100.101.102.103"), ("Ethernet", "192.168.1.5")]) == ["100.101.102.103", "192.168.1.5"]
    assert op.local_private_ips(lambda: [("tailscale0", "100.64.0.9")]) == ["100.64.0.9", "192.168.1.5"]


def test_an_ordinary_adapter_with_a_100_address_is_not(quiet, monkeypatch):
    monkeypatch.setattr(op.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("192.168.1.5", 0)), (2, 1, 6, "", ("100.70.1.2", 0))])   # the host's own names list it too
    assert op.local_private_ips(lambda: [("Ethernet", "100.70.1.2"), ("Wi-Fi", "192.168.1.5")]) == ["192.168.1.5"]
    assert op.local_private_ips(lambda: []) == ["192.168.1.5"]


def test_a_connection_from_a_100_peer_stays_allowed():
    assert op.ip_class("100.100.1.2") == "private"


def test_adapters_are_read_from_ip_addr_on_linux(monkeypatch):
    monkeypatch.setattr(op.sys, "platform", "linux")
    out = "1: lo    inet 127.0.0.1/8 scope host lo\n2: eth0    inet 192.168.1.5/24 brd x scope global eth0\n5: tailscale0    inet 100.90.1.1/32 scope global tailscale0\n"
    run = lambda *a, **k: type("R", (), {"stdout": out})()
    assert ("tailscale0", "100.90.1.1") in op.adapter_addresses(run) and ("eth0", "192.168.1.5") in op.adapter_addresses(run)


def test_a_listed_range_is_honoured_for_who_may_connect_and_removing_it_removes_access():
    assert op.ip_class("198.18.4.5") == "other"
    op.set_extra_networks(op.clean_private_networks("198.18.0.0/15"))
    assert op.ip_class("198.18.4.5") == "private" and op.ip_class("198.20.0.1") == "other"
    op.check_host_allowed("198.18.4.5", allow_loopback=False)
    op.set_extra_networks([])
    with pytest.raises(op.OtherPcsError):
        op.check_host_allowed("198.18.4.5", allow_loopback=False)


def test_a_listed_range_is_listened_on_only_where_an_adapter_has_an_address_in_it(quiet):
    op.set_extra_networks(["198.18.0.0/15"])
    assert op.local_private_ips(lambda: [("MyVPN", "198.18.0.7"), ("Other", "198.50.0.1")]) == ["192.168.1.5", "198.18.0.7"]
    assert op.local_private_ips(lambda: []) == ["192.168.1.5"]


@pytest.mark.parametrize("bad,words", [("8.8.8.0/24", "public internet"), ("1.2.3.4", "public internet"), ("100.0.0.0/8", "public internet"), ("0.0.0.0/0", "any address in"),
                                       ("::/0", "any address in"), ("10.0.0.0/4", "any address in"), ("not a network", "doesn't look like a network"), ("10.8.0.0/99", "doesn't look like a network"),
                                       ("127.0.0.0/8", "public internet")])
def test_public_wildcard_and_malformed_entries_are_refused(bad, words):
    with pytest.raises(op.OtherPcsError) as e:
        op.clean_private_networks([bad])
    assert e.value.status == 400 and words in e.value.message


def test_good_entries_are_written_the_plain_way_without_duplicates():
    assert op.clean_private_networks("10.8.0.9/16, 10.8.0.0/16 ; 198.18.0.0/15") == ["10.8.0.0/16", "198.18.0.0/15"]
    assert op.clean_private_networks([]) == [] and op.clean_private_networks("") == []


def test_a_saved_bad_entry_is_skipped_when_loaded():
    op.set_extra_networks(["8.8.8.0/24", "0.0.0.0/0", "198.18.0.0/15"])
    assert op.extra_networks() == ["198.18.0.0/15"]


# ---- a real Courier takes the list through its settings route ----
from tests.test_courier import env  # noqa: E402,F401


@pytest.mark.asyncio
async def test_a_courier_keeps_the_list_refuses_a_public_range_and_forgets_a_removed_one(env):
    st = await env.call("POST", "/courier/v1/settings", {"private_networks": ["198.18.0.0/15"]})
    assert st["private_networks"] == ["198.18.0.0/15"] and op.ip_class("198.18.9.9") == "private"
    assert env.courier.settings.data["private_networks"] == ["198.18.0.0/15"]
    status, message = await env.err("POST", "/courier/v1/settings", {"private_networks": ["8.8.8.0/24"]})
    assert status == 400 and "public internet" in message and op.extra_networks() == ["198.18.0.0/15"]
    st = await env.call("POST", "/courier/v1/settings", {"private_networks": []})
    assert st["private_networks"] == [] and op.ip_class("198.18.9.9") == "other"


# ---- the same list on the Courier device itself: the vpn-networks command and the ready window's field (2026-10-06) ----
import json
import socket
from pathlib import Path

import courier as cr
import courier_ui

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def stopped(tmp_path, monkeypatch):
    """A Courier's settings with nothing running on its port (a spare one), and no call reaching any real Courier."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    settings = cr.CourierSettings(tmp_path / "courier.json")
    settings.set(port=port)
    return settings


class Running:
    """Stands in for the running Courier: answers the report, and takes the list like its settings route does."""
    def __init__(self, settings, monkeypatch):
        self.settings, self.posted = settings, []
        monkeypatch.setattr(cr, "local_call", self.call)

    def call(self, settings, method, path, timeout=5, body=None):
        if method == "POST":
            self.posted.append(body)
            return {"private_networks": body["private_networks"]}
        return {"courier": {}}


def run(settings, *words):
    return cr.vpn_networks_command(settings, *words)


def test_the_command_adds_lists_and_removes_with_the_courier_stopped(stopped):
    assert run(stopped, "list") == ("No other VPN or Tailscale networks are listed.", 0)
    assert run(stopped, "add", "10.8.0.0/16") == ("10.8.0.0/16", 0)
    assert run(stopped, "add", "192.0.2.0/24")[0] == "10.8.0.0/16\n192.0.2.0/24"
    assert run(stopped, "add", "10.8.0.0/16")[0] == "10.8.0.0/16\n192.0.2.0/24", "no duplicates"
    saved = json.loads(Path(stopped.path).read_text(encoding="utf-8"))["private_networks"]
    assert saved == ["10.8.0.0/16", "192.0.2.0/24"], "the same value the card writes to courier.json"
    assert run(stopped, "remove", "10.8.0.0/16") == ("192.0.2.0/24", 0)
    assert run(stopped, "remove", "10.9.0.0/16") == ("That network isn't in the list.", 1)
    assert run(stopped, "remove", "192.0.2.0/24") == ("No other VPN or Tailscale networks are listed.", 0)


def test_the_command_refuses_with_the_dashboards_own_lines(stopped):
    assert run(stopped, "add", "8.8.8.0/24") == (op.PUBLIC_NETWORK, 1)
    assert run(stopped, "add", "0.0.0.0/0") == (op.WILDCARD_NETWORK, 1)
    assert run(stopped, "add", "banana") == (op.BAD_NETWORK, 1)
    assert run(stopped, "remove", "banana") == (op.BAD_NETWORK, 1)
    assert run(stopped, "add") == (cr.VPN_NETWORKS_USAGE, 2) and run(stopped, "shake", "x")[1] == 2
    assert not stopped.data.get("private_networks"), "a refused entry changes nothing"


def test_the_command_tells_a_running_courier_instead_of_writing_its_file(stopped, monkeypatch):
    running = Running(stopped, monkeypatch)
    assert run(stopped, "add", "10.8.0.0/16") == ("10.8.0.0/16", 0)
    assert running.posted == [{"private_networks": ["10.8.0.0/16"]}], "it takes effect without a restart: the Courier's own route starts listening"
    assert not stopped.data.get("private_networks"), "the running Courier is the one that saves its settings"
    monkeypatch.setattr(cr, "local_call", lambda s, m, p, timeout=5, body=None: {"courier": {}} if m == "GET" else None)
    assert run(stopped, "add", "10.9.0.0/16") == (cr.NOT_SAVED, 1)


def test_main_runs_the_command_and_exits_with_its_code(stopped, tmp_path, capsys):
    base = ["--config-dir", str(tmp_path)]
    assert cr.main(["vpn-networks", "add", "10.8.0.0/16"] + base) == 0
    assert capsys.readouterr().out.strip() == "10.8.0.0/16"
    assert cr.main(["vpn-networks"] + base) == 0 and capsys.readouterr().out.strip() == "10.8.0.0/16"
    assert cr.main(["vpn-networks", "add", "8.8.8.0/24"] + base) == 1 and op.PUBLIC_NETWORK in capsys.readouterr().out


def test_the_ready_window_field_saves_the_same_list_and_refuses_the_same_way(stopped, monkeypatch):
    running = Running(stopped, monkeypatch)
    assert courier_ui.save_vpn_text(stopped, "10.8.0.0/16, 192.0.2.0/24") == ("10.8.0.0/16, 192.0.2.0/24", "")
    assert running.posted == [{"private_networks": ["10.8.0.0/16", "192.0.2.0/24"]}]
    assert courier_ui.save_vpn_text(stopped, "8.8.8.0/24") == ("8.8.8.0/24", op.PUBLIC_NETWORK)
    assert courier_ui.save_vpn_text(stopped, "x")[1] == op.BAD_NETWORK and len(running.posted) == 1, "a refused list is never sent"


def test_the_ready_window_never_writes_the_settings_itself(stopped):
    assert courier_ui.save_vpn_text(stopped, "10.8.0.0/16") == ("10.8.0.0/16", cr.NOT_RUNNING_NOT_SAVED)
    assert not stopped.data.get("private_networks")


def test_the_ready_window_words_match_the_settings_page_and_the_device_card():
    f = courier_ui.ready_window_text("ABC-123", None)["vpn"]
    settings_page = (ROOT / "settings_ui.py").read_text(encoding="utf-8")
    card = (ROOT / "other_pcs_ui.py").read_text(encoding="utf-8")
    for source in (settings_page, card):
        assert f["hint"] in source and f["placeholder"] in source
    assert f["label"] in settings_page and f["label"] in card
    assert f["line"].endswith("before you pair.")


def test_the_linux_command_and_both_installers_say_how_to_add_a_vpn_network():
    wrapper = (ROOT / "linux/multiproxy-courier").read_text(encoding="utf-8")
    assert 'vpn-networks) shift; courier vpn-networks "$@" --config-dir "$SETTINGS"' in wrapper and "multiproxy-courier vpn-networks" in wrapper
    line = "Reaching this device over a VPN? Add its network first:  multiproxy-courier vpn-networks add 10.8.0.0/16"
    assert (ROOT / "linux/courier-install.sh").read_text(encoding="utf-8").count(line) == 2, "both closing messages (paired and not paired)"
    older = (ROOT / "courier/install-courier.sh").read_text(encoding="utf-8")
    assert line in older and 'vpn-networks) shift;' in older
