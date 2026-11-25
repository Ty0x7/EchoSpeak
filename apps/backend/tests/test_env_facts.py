"""env_facts: network and app discovery for system_info, from fake sysfs/proc trees (campaign round 3)."""
from __future__ import annotations

from pathlib import Path

from agent import env_facts

ROUTE_HEAD = "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"


def _iface(sys_root: Path, name: str, *, up=True, physical=False, devtype="", tun=False, carrier=True) -> None:
    node = sys_root / "class" / "net" / name
    node.mkdir(parents=True)
    (node / "flags").write_text("0x1003" if up else "0x1002")
    (node / "operstate").write_text("unknown" if not physical else ("up" if carrier else "down"))
    (node / "uevent").write_text((f"DEVTYPE={devtype}\n" if devtype else "") + f"INTERFACE={name}\n")
    if physical:
        (node / "device").mkdir()
    if tun:
        (node / "tun_flags").write_text("0x1001")


def _routes(proc_root: Path, *rows: tuple[str, int]) -> None:
    (proc_root / "net").mkdir(parents=True)
    lines = [f"{name}\t00000000\t0101A8C0\t0003\t0\t0\t{metric}\t00000000\t0\t0\t0" for name, metric in rows]
    (proc_root / "net" / "route").write_text(ROUTE_HEAD + "\n".join(lines) + "\n")


def test_a_wireguard_vpn_carrying_the_default_route_is_a_vpn(tmp_path):
    sys_root, proc_root = tmp_path / "sys", tmp_path / "proc"
    _iface(sys_root, "lo")
    _iface(sys_root, "wlp4s0", physical=True, devtype="wlan")
    _iface(sys_root, "enp5s0", physical=True, carrier=False)   # switched on, no cable
    _iface(sys_root, "eno1", physical=True, up=False)
    _iface(sys_root, "wg0-mullvad", devtype="wireguard")
    _iface(sys_root, "vpnroute0")                      # a VPN client's own virtual route interface
    _routes(proc_root, ("vpnroute0", 98), ("wlp4s0", 600))
    text = env_facts.network_summary(str(sys_root), str(proc_root))
    assert text.startswith("Yes, traffic goes through a VPN (Mullvad)")
    assert "wg0-mullvad (wireguard)" in text and "wlp4s0 (Wi-Fi)" in text
    assert "enp5s0" not in text and "eno1" not in text


def test_plain_wifi_with_docker_is_not_a_vpn(tmp_path):
    sys_root, proc_root = tmp_path / "sys", tmp_path / "proc"
    _iface(sys_root, "lo")
    _iface(sys_root, "wlan0", physical=True, devtype="wlan")
    _iface(sys_root, "docker0", devtype="bridge")
    _iface(sys_root, "veth12ab")
    _routes(proc_root, ("wlan0", 600))
    text = env_facts.network_summary(str(sys_root), str(proc_root))
    assert text.startswith("No VPN"), text
    assert "docker0" not in text and "veth" not in text


def test_a_tunnel_up_without_the_default_route_says_so(tmp_path):
    sys_root, proc_root = tmp_path / "sys", tmp_path / "proc"
    _iface(sys_root, "eth0", physical=True)
    _iface(sys_root, "tun0", tun=True)
    _routes(proc_root, ("eth0", 100))
    text = env_facts.network_summary(str(sys_root), str(proc_root))
    assert text.startswith("A VPN tunnel is up, but the default route doesn't go through it"), text


def test_installed_apps_reads_visible_desktop_entries_and_user_overrides(tmp_path):
    system, user = tmp_path / "system", tmp_path / "user"
    system.mkdir(); user.mkdir()
    (system / "gimp.desktop").write_text("[Desktop Entry]\nName=GIMP\nExec=gimp\n")
    (system / "helper.desktop").write_text("[Desktop Entry]\nName=Hidden Helper\nNoDisplay=true\n")
    (system / "firefox.desktop").write_text("[Desktop Entry]\nName=Firefox\n[Desktop Action new]\nName=New Window\n")
    (user / "firefox.desktop").write_text("[Desktop Entry]\nName=Firefox (custom)\n")
    assert env_facts.installed_apps([str(system), str(user)]) == ["Firefox (custom)", "GIMP"]


def test_system_info_adds_network_and_apps_on_this_machine():
    from agent.tools import system_info
    text = system_info.invoke({}) if hasattr(system_info, "invoke") else system_info()
    if Path("/sys/class/net").exists():
        assert "Network: " in text
